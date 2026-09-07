"""Scrape the Wikipedia Westminster voting-intention index into typed rows.

This module reads the "National poll results" section of the page at
:data:`WIKI_URL` and returns one :class:`WikipediaPollRow` per GB/UK poll,
carrying the fieldwork dates, the pollster slug and the source-document URL
resolved from the row's citation.

It is **metadata only**: it never downloads a poll document, never touches the
database, and never imports the console's importer registry. Deciding which rows
have an importer, and which are already in the database, belongs to
``console/services/wikipedia_queue.py``.

Two details of Wikipedia's markup are easy to get wrong and are handled
explicitly here:

- References are keyed on the **full** ``<li>`` id (``"cite_note-FONEC-7apr26-137"``),
  not on a numeric suffix. Roughly one reference in seven is a *named* reference
  with no number in its id, and keying on ``cite_note-(\\d+)`` silently drops them.
- A citation's **visible number is not its id**: a row rendering ``[30][b][a]``
  can link to ``#cite_note-31``. The href fragment is authoritative, and the note
  markers (``[a]``, ``[b]``) resolve to nothing and are skipped rather than
  treated as a failure.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from datetime import date

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

from polls.importers.wikipedia_common import clean_text, fetch_html, parse_date_range

WIKI_URL = (
    "https://en.wikipedia.org/wiki/"
    "Opinion_polling_for_the_next_United_Kingdom_general_election"
)

SECTION_HEADING_ID = "National_poll_results"
NATIONAL_AREAS = frozenset({"GB", "UK"})

_CITATION_MARKER_RE = re.compile(r"\[[^\]]+\]")
_YEAR_RE = re.compile(r"(20\d{2})")
_WHITESPACE_RE = re.compile(r"\s+")
_PARENTHETICAL_RE = re.compile(r"\(.*?\)")
_NON_SLUG_RE = re.compile(r"[^a-zA-Z0-9_ ]")
_UNDERSCORE_RUN_RE = re.compile(r"_+")
_CITE_ANCHOR_SELECTOR = 'a[href^="#cite_note"]'

_COL_DATE = 0
_COL_POLLSTER = 1
_COL_CLIENT = 2
_COL_AREA = 3
_COL_SAMPLE_SIZE = 4

# Every column above must be present for a row to be readable at all.
_MIN_CELLS = _COL_SAMPLE_SIZE + 1


class WikipediaIndexError(RuntimeError):
    """Raised when the Wikipedia page is not shaped the way this scraper expects.

    Wikipedia has already restructured this page once under this code (year
    tables moved into nested ``<section>`` elements). A silent empty result
    would be indistinguishable from "no new polls", which is precisely the
    false negative the catch-up queue exists to prevent — so a structural
    surprise is raised rather than swallowed.
    """


class WikipediaPollRow(BaseModel):
    """One GB/UK poll as listed in the Wikipedia national-results tables.

    Attributes:
        fieldwork_start: First day of fieldwork, from the date cell plus the
            year of the enclosing section.
        fieldwork_end: Last day of fieldwork.
        date_label: Raw date-cell text, for display (carries no year).
        pollster_label: Pollster name with citation markers stripped.
        pollster_identifier: Canonical snake_case pollster slug.
        client: Commissioning client, column 2. Display only — the ``polls``
            table has no client column.
        sample_size_label: Raw sample-size cell text, column 4. Display only.
        source_url: External URL resolved from the row's citation, or ``""``
            when no citation on the row resolves to one.
        citation_id: The ``cite_note`` href fragment (e.g. ``"cite_note-31"``),
            never the visible citation number. ``""`` when the row cites nothing.
    """

    fieldwork_start: date
    fieldwork_end: date
    date_label: str
    pollster_label: str
    pollster_identifier: str
    client: str
    sample_size_label: str
    source_url: str
    citation_id: str


class PollIndex(BaseModel):
    """The parsed index, plus counts of what was dropped along the way.

    Attributes:
        rows: Every GB/UK poll row parsed, in Wikipedia's own document order
            (which is *not* date order — callers must sort by parsed dates).
        skipped_rows: Full-width rows whose date cell could not be parsed.
        unresolved_citations: Rows in ``rows`` whose ``source_url`` is empty.
        unrecognised_areas: Histogram of area-column values that were not
            ``GB``/``UK``, for the rows wide enough to be poll rows. Expected
            to hold a few header rows every run; a sudden spike of
            percentage-shaped keys means Wikipedia has shifted the columns.
    """

    rows: list[WikipediaPollRow]
    skipped_rows: int
    unresolved_citations: int
    unrecognised_areas: dict[str, int]


def normalize_pollster_name(label: str) -> str:
    """Normalise a raw Wikipedia pollster label to a canonical snake_case slug.

    Strips citation brackets, parenthetical qualifiers and punctuation, then
    lowercases and underscores the remainder. A ladder of substring aliases then
    resolves the common multi-word and ambiguous names, so that (for example)
    every ``"YouGov"`` variant collapses to ``"yougov"`` and
    ``"Lord Ashcroft Polls"`` reaches ``"lord_ashcroft"``. Unrecognised
    pollsters fall through to their raw slug.

    Args:
        label: Raw pollster label text as scraped from the table cell.

    Returns:
        A canonical snake_case pollster identifier, e.g. ``"yougov"``,
        ``"more_in_common"``, ``"bmg_research"``.
    """
    cleaned = _CITATION_MARKER_RE.sub("", label)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    cleaned = _PARENTHETICAL_RE.sub("", cleaned).strip()
    cleaned = cleaned.replace("/", "_")
    cleaned = _NON_SLUG_RE.sub("", cleaned)
    cleaned = cleaned.lower().replace(" ", "_")
    cleaned = _UNDERSCORE_RUN_RE.sub("_", cleaned).strip("_")

    if "find_out_now" in cleaned and "electoral_calculus" in cleaned:
        return "find_out_now_electoral_calculus"
    if "yougov" in cleaned:
        return "yougov"
    if "more_in_common" in cleaned:
        return "more_in_common"
    if "opinium" in cleaned:
        return "opinium"
    if "survation" in cleaned:
        return "survation"
    if "techne" in cleaned:
        return "techne"
    if "bmg" in cleaned:
        return "bmg_research"
    if "focaldata" in cleaned:
        return "focaldata"
    if "freshwater" in cleaned:
        return "freshwater_strategy"
    if "j_l_partners" in cleaned or "jl_partners" in cleaned:
        return "jl_partners"
    if "ipsos" in cleaned:
        return "ipsos"
    if "deltapoll" in cleaned:
        return "deltapoll"
    if "lord_ashcroft" in cleaned:
        return "lord_ashcroft"
    return cleaned


def extract_reference_url_map(soup: BeautifulSoup) -> dict[str, str]:
    """Map every Wikipedia reference id on the page to its external URL.

    Keys are the **full** ``<li>`` element id, e.g. ``"cite_note-31"`` but also
    ``"cite_note-FONEC-7apr26-137"`` and ``"cite_note-:14-72"``. Named
    references like the latter two make up around one in seven of the page's
    references, so keying on a numeric suffix would silently lose them — and
    with them the source URL of every row that cites them.

    Args:
        soup: Parsed document for the full Wikipedia page.

    Returns:
        Mapping of reference id to external URL. References with no resolvable
        external link are omitted.
    """
    ref_map: dict[str, str] = {}
    for li in soup.select("li[id^=cite_note-]"):
        li_id = li.get("id")
        if not isinstance(li_id, str) or not li_id:
            continue

        external_link = (
            li.select_one("a.external.text")
            or li.select_one("span.reference-text a.external")
            or li.select_one("a.external")
        )
        if external_link is None:
            continue

        href = external_link.get("href")
        if isinstance(href, str) and href:
            ref_map[li_id] = href
    return ref_map


def fetch_poll_index(*, url: str = WIKI_URL, html: str | None = None) -> PollIndex:
    """Fetch and parse the Wikipedia Westminster polling index.

    Args:
        url: Page to fetch. Ignored when ``html`` is supplied.
        html: Pre-fetched page source. Pass this to parse without network
            access — tests always do.

    Returns:
        A :class:`PollIndex` holding every GB/UK poll row found, in document
        order, plus the drop counts.

    Raises:
        WikipediaIndexError: If the national-results section cannot be found,
            or contains no year subsection with a parseable year.
        urllib.error.URLError: If ``html`` is not supplied and the fetch fails.
    """
    source = fetch_html(url) if html is None else html
    soup = BeautifulSoup(source, "lxml")
    ref_map = extract_reference_url_map(soup)

    rows: list[WikipediaPollRow] = []
    skipped_rows = 0
    unrecognised_areas: Counter[str] = Counter()
    year_sections = 0

    for year, year_section in _iter_year_sections(soup):
        year_sections += 1
        for table in year_section.find_all("table", class_="wikitable"):
            if not isinstance(table, Tag):
                continue
            for tr in table.find_all("tr"):
                if not isinstance(tr, Tag):
                    continue
                row, skipped, unrecognised_area = _parse_row(
                    tr, year=year, ref_map=ref_map
                )
                if row is not None:
                    rows.append(row)
                skipped_rows += skipped
                if unrecognised_area is not None:
                    unrecognised_areas[unrecognised_area] += 1

    if year_sections == 0:
        raise WikipediaIndexError(
            f"No year subsections found under #{SECTION_HEADING_ID} — "
            "the Wikipedia page layout has changed."
        )

    unresolved = sum(1 for row in rows if not row.source_url)
    return PollIndex(
        rows=rows,
        skipped_rows=skipped_rows,
        unresolved_citations=unresolved,
        unrecognised_areas=dict(unrecognised_areas),
    )


def _iter_year_sections(soup: BeautifulSoup) -> Iterator[tuple[int, Tag]]:
    """Yield each per-year subsection of the national poll results.

    Wikipedia now wraps every heading level in a real ``<section>`` element, so
    the section for a year is a direct child ``<section>`` of the one holding
    the ``National_poll_results`` heading. (The older sibling-walk from the
    heading node no longer finds anything.)

    Args:
        soup: Parsed document for the full Wikipedia page.

    Yields:
        ``(year, section)`` pairs, one per year subsection whose heading
        contains a 20xx year.

    Raises:
        WikipediaIndexError: If the heading, or the section enclosing it, is
            absent.
    """
    heading = soup.find(id=SECTION_HEADING_ID)
    if not isinstance(heading, Tag):
        raise WikipediaIndexError(
            f"Heading #{SECTION_HEADING_ID} not found — "
            "the Wikipedia page layout has changed."
        )

    section = heading.find_parent("section")
    if section is None:
        raise WikipediaIndexError(
            f"Heading #{SECTION_HEADING_ID} has no enclosing <section> — "
            "the Wikipedia page layout has changed."
        )

    for year_section in section.find_all("section", recursive=False):
        if not isinstance(year_section, Tag):
            continue
        year_heading = year_section.find(["h3", "h2"])
        if not isinstance(year_heading, Tag):
            continue
        match = _YEAR_RE.search(year_heading.get_text())
        if match is None:
            continue
        yield int(match.group(1)), year_section


def _parse_row(
    tr: Tag,
    *,
    year: int,
    ref_map: dict[str, str],
) -> tuple[WikipediaPollRow | None, int, str | None]:
    """Parse one table row into a :class:`WikipediaPollRow`.

    Rows narrower than :data:`_MIN_CELLS` are discarded structurally and
    silently: they are the header rows and the two-cell event rows recording
    by-elections and leadership changes, none of which are polls. Every
    remaining row is wide enough to *be* a poll, so a non-GB/UK area is
    reported back rather than dropped quietly — that is the channel through
    which a Wikipedia column shift becomes visible.

    Args:
        tr: The ``<tr>`` element.
        year: Year of the enclosing section, appended to the date label since
            Westminster date cells carry no year.
        ref_map: Reference id to URL mapping from
            :func:`extract_reference_url_map`.

    Returns:
        ``(row, skipped, unrecognised_area)``. ``row`` is the parsed row or
        ``None``; ``skipped`` is ``1`` when a full-width row had an unparseable
        date; ``unrecognised_area`` is the area-cell text when a full-width row
        was not GB/UK, else ``None``.
    """
    cells = tr.find_all(["td", "th"], recursive=False)
    if len(cells) < _MIN_CELLS:
        return None, 0, None

    area = clean_text(cells[_COL_AREA].get_text())
    if area not in NATIONAL_AREAS:
        return None, 0, area

    # Date cells carry footnotes for split fieldwork; parse_date_range anchors
    # its match, so a stray "[12]" would make the whole row unparseable.
    date_label = clean_text(_CITATION_MARKER_RE.sub("", cells[_COL_DATE].get_text()))
    parsed_dates = parse_date_range(f"{date_label} {year}")
    if parsed_dates is None:
        return None, 1, None
    fieldwork_start, fieldwork_end = parsed_dates

    pollster_cell = cells[_COL_POLLSTER]
    pollster_label = _CITATION_MARKER_RE.sub(
        "", clean_text(pollster_cell.get_text())
    ).strip()
    citation_id, source_url = _resolve_citation(pollster_cell, ref_map)

    return (
        WikipediaPollRow(
            fieldwork_start=fieldwork_start,
            fieldwork_end=fieldwork_end,
            date_label=date_label,
            pollster_label=pollster_label,
            pollster_identifier=normalize_pollster_name(pollster_label),
            client=clean_text(cells[_COL_CLIENT].get_text()),
            sample_size_label=clean_text(cells[_COL_SAMPLE_SIZE].get_text()),
            source_url=source_url,
            citation_id=citation_id,
        ),
        0,
        None,
    )


def _resolve_citation(pollster_cell: Tag, ref_map: dict[str, str]) -> tuple[str, str]:
    """Resolve a row's citation to its source-document URL.

    Only the pollster cell is searched. It can hold several ``<sup>`` anchors:
    alongside the source citation sit note markers such as ``[a]`` and ``[b]``,
    which point at footnotes with no external link. Those are skipped, not
    treated as a failure, and the first anchor that maps to a URL wins.

    Widening the search to the whole row would be actively harmful. Other
    columns carry their own citations — the client column in particular cites
    news write-ups of the poll rather than the data tables — and attaching one
    of those would feed the wrong document to the importer. Every row on the
    live page resolves inside the pollster cell, so the wider search buys
    nothing and risks a plausible-looking wrong answer. A row whose pollster
    cell cites nothing resolvable is left with an empty ``source_url``, which
    the queue reports rather than guessing at.

    Args:
        pollster_cell: The pollster ``<td>``.
        ref_map: Reference id to URL mapping from
            :func:`extract_reference_url_map`.

    Returns:
        ``(citation_id, source_url)``. When nothing resolves, ``source_url`` is
        ``""`` and ``citation_id`` is the first citation seen in the cell (or
        ``""`` if it cites nothing), so the failure can be traced.
    """
    first_citation_id = ""
    for anchor in pollster_cell.select(_CITE_ANCHOR_SELECTOR):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        citation_id = href.removeprefix("#")
        if not first_citation_id:
            first_citation_id = citation_id
        source_url = ref_map.get(citation_id, "")
        if source_url:
            return citation_id, source_url
    return first_citation_id, ""
