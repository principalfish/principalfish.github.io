"""Contest layer: Wikipedia pages → seat- and matchup-scoped US poll rows.

The parser in ``us_polls_common`` reads a *table*: its grid, the headings it
sits under, its candidates and its rows. It applies no editorial rule. This
module supplies the rules, one set per **contest**:

- which pages a contest lives on (a fixed list, or links discovered from an
  index page);
- which of a page's tables count (general-election sections only, no primaries,
  no poll averages, no collapsed hypotheticals unless the user opts in);
- which **seat** a table belongs to (the page slug for a Senate race, a
  "District N" heading for a House district, a "Statewide › Nevada" heading for
  the President);
- which table is the race's **lead** — the first visible one, whose matchup
  piece 7 promotes to the race's automatic tracked matchup.

Nothing here writes to the database. ``db`` is read only to turn a seat *name*
into the seat id on the contest's map, and every rule that fails to place a
table is reported on :class:`UsPollIndex` rather than dropped: the console's
summary page is the only place Wikipedia markup drift becomes visible.

Fetching is injectable end to end — every test passes a dict-backed fake
fetcher, so no test touches the network.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError

from bs4 import BeautifulSoup, Tag

# ``data/`` root — home of db.py / models.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from polls.importers.types import ScrapedPollRow
from polls.importers.us.us_geography import (
    AT_LARGE_STATES,
    HOUSE_DISTRICT_COUNTS,
    STATE_POSTAL,
    house_seat_name,
    president_seat_for_heading,
    state_from_page_slug,
)
from polls.importers.us.us_polls_common import (
    CandidateReading,
    Heading,
    ParsedTable,
    fetch_html,
    parse_poll_tables,
    pollster_identifier,
)

WIKIPEDIA_BASE = "https://en.wikipedia.org"

HOUSE_INDEX_URL = (
    f"{WIKIPEDIA_BASE}/wiki/2026_United_States_House_of_Representatives_elections"
)
SENATE_INDEX_URL = f"{WIKIPEDIA_BASE}/wiki/2026_United_States_Senate_elections"

# The presidential pages, in the order they are fetched. The Statewide article
# does not exist yet (it 404s, which :func:`fetch_pages` records as a note
# rather than an error), but it is the page the statewide polls will appear on,
# so it is listed here and this tuple is overridable per run.
PRESIDENT_PAGE_URLS: tuple[str, ...] = (
    f"{WIKIPEDIA_BASE}/wiki/"
    "Nationwide_opinion_polling_for_the_2028_United_States_presidential_election",
    f"{WIKIPEDIA_BASE}/wiki/"
    "Statewide_opinion_polling_for_the_2028_United_States_presidential_election",
)

#: A page fetcher: takes a URL, returns the page source, raises on failure.
Fetcher = Callable[[str], str]

#: Where a contest's pages come from beyond its fixed ``page_urls``.
PageDiscovery = Literal["none", "senate_index", "house_index"]

#: Which of a page's tables a contest accepts.
SectionRule = Literal["general_election", "generic_ballot"]

#: How a table's seat is worked out.
SeatRule = Literal["national", "senate_state", "house_district", "president"]

#: What the contest's matchups mean, which is piece 7's automatic-tracking rule.
MatchupPolicy = Literal["auto_lead", "national_setting", "none"]

# "District 3", "District 40" — the h2 a multi-district House page groups a
# race under.
_DISTRICT_HEADING_RE = re.compile(r"^district\s+(\d{1,3})\b", re.IGNORECASE)

# Senate race pages: "2026 United States Senate (special) election in <State>".
_SENATE_PAGE_RE = re.compile(
    r"^(?:https?://en\.wikipedia\.org)?/wiki/"
    r"(2026_United_States_Senate_(?:special_)?election_in_[^#?]+)$"
)

# House state pages: plural for multi-district states, singular for at-large.
_HOUSE_PAGE_RE = re.compile(
    r"^(?:https?://en\.wikipedia\.org)?/wiki/"
    r"(2026_United_States_House_of_Representatives_elections?_in_[^#?]+)$"
)

# Postal code → state name, so ``states=["TX"]`` filters as well as
# ``states=["Texas"]``.
_STATE_BY_KEY: dict[str, str] = {
    **{name.lower(): name for name in STATE_POSTAL},
    **{postal.lower(): name for name, postal in STATE_POSTAL.items()},
}

_HEADING_SEPARATOR = " › "


@dataclass(frozen=True, slots=True)
class UsContest:
    """One US contest: where its polls live and how to place them.

    Attributes:
        slug: Stable identifier, used on the CLI (``--contest senate_races``)
            and stored on every row.
        label: Human name for the console.
        map_name: The forecast map the contest's polls belong to.
        pollster_suffix: Appended to a pollster slug, so the same house polling
            two chambers keeps one weight per chamber (``_us_senate``).
        pollster_label: Shown in a newly created pollster's name,
            ``"YouGov (US Senate)"``.
        page_urls: Pages fetched unconditionally.
        index_url: Page whose links are scanned for race pages, or None.
        discovery: Which link pattern ``index_url`` is scanned for.
        section_rule: Which of a page's tables count.
        seat_rule: How a table's seat is worked out.
        matchup_policy: ``"auto_lead"`` — the lead table's matchup becomes the
            race's automatic tracked matchup; ``"national_setting"`` — the user
            picks one national matchup and the seats follow it (President);
            ``"none"`` — the contest has no matchups (the generic ballot).
        allow_party_labels: Accept "Republicans"/"Democrats" column headers,
            which the generic-ballot aggregation table uses in place of
            candidates.
    """

    slug: str
    label: str
    map_name: str
    pollster_suffix: str
    pollster_label: str
    page_urls: tuple[str, ...] = ()
    index_url: str | None = None
    discovery: PageDiscovery = "none"
    section_rule: SectionRule = "general_election"
    seat_rule: SeatRule = "national"
    matchup_policy: MatchupPolicy = "auto_lead"
    allow_party_labels: bool = False

    @property
    def is_per_state(self) -> bool:
        """True when each of the contest's pages covers one state's races."""
        return self.discovery != "none"


HOUSE_NATIONAL = UsContest(
    slug="house_national",
    label="House national generic ballot",
    map_name="US House Districts 2024",
    pollster_suffix="_us_house",
    pollster_label="US House",
    page_urls=(HOUSE_INDEX_URL,),
    section_rule="generic_ballot",
    seat_rule="national",
    matchup_policy="none",
    allow_party_labels=True,
)

HOUSE_DISTRICTS = UsContest(
    slug="house_districts",
    label="House district polls",
    map_name="US House Districts 2024",
    pollster_suffix="_us_house",
    pollster_label="US House",
    index_url=HOUSE_INDEX_URL,
    discovery="house_index",
    seat_rule="house_district",
)

SENATE_RACES = UsContest(
    slug="senate_races",
    label="Senate race polls",
    map_name="US Senate 2024",
    pollster_suffix="_us_senate",
    pollster_label="US Senate",
    index_url=SENATE_INDEX_URL,
    discovery="senate_index",
    seat_rule="senate_state",
)

PRESIDENT = UsContest(
    slug="president",
    label="Presidential polls",
    map_name="US Presidential 2024",
    pollster_suffix="_us_president",
    pollster_label="US President",
    page_urls=PRESIDENT_PAGE_URLS,
    seat_rule="president",
    matchup_policy="national_setting",
)

US_CONTESTS: tuple[UsContest, ...] = (
    HOUSE_NATIONAL,
    HOUSE_DISTRICTS,
    SENATE_RACES,
    PRESIDENT,
)

US_CONTESTS_BY_SLUG: Mapping[str, UsContest] = {
    contest.slug: contest for contest in US_CONTESTS
}


class UsPollRow(ScrapedPollRow):
    """One US poll, placed on a contest, a seat and a matchup.

    The queue reads the inherited fields (dates, pollster, sample label, source
    URL, matchup); everything below is what the US review step shows and what
    piece 7 turns into a ``Poll`` plus its ``PollRow``\\ s.

    Attributes:
        contest: The :class:`UsContest` slug the row was read under.
        page_url: The Wikipedia page it was read from.
        heading_path: The section path as display text, e.g.
            ``"General election › Polling"``.
        seat_name: The seat on ``map_name``, or None for a national reading.
        seat_id: That seat's database id, or None for a national reading.
        map_name: The contest's forecast map.
        readings: One entry per candidate column that held a number, as
            written (not rescaled). A party whose suffix is unknown carries
            ``party_name=None`` and is excluded at commit time.
        sample_size: Respondents, or None when the table showed none.
        population: Sampled population code — "LV", "RV", "A".
        is_lead: True on the rows of the race's lead table: the first visible,
            accepted table with a matchup. Piece 7 promotes its matchup to the
            race's automatic tracked matchup.
        collapsed: True when the row came from a hidden hypothetical table,
            which only happens under the collapsed-only opt-in.
        pollster_tags: Partisan sponsor tags stripped from the pollster cell,
            e.g. ``("R",)``.
        notes: Per-table warnings worth showing next to the row.
    """

    contest: str
    page_url: str
    heading_path: str
    seat_name: str | None
    seat_id: int | None
    map_name: str
    readings: tuple[CandidateReading, ...]
    sample_size: int | None = None
    population: str | None = None
    is_lead: bool = False
    collapsed: bool = False
    pollster_tags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UnmatchedSeat:
    """A table whose seat could not be resolved, so its rows were dropped.

    Attributes:
        contest: The contest slug.
        page_url: The page the table sits on.
        heading_path: The table's section path as display text.
        seat_name: The seat name the rules produced, or None when they produced
            none at all.
        reason: Why the table could not be placed.
        dropped_rows: How many parsed poll rows were lost with it.
    """

    contest: str
    page_url: str
    heading_path: str
    seat_name: str | None
    reason: str
    dropped_rows: int


@dataclass(frozen=True, slots=True)
class EmptyTable:
    """A table that classified as a polling table but yielded no poll rows.

    Normally a sign of markup drift: the table named a pollster column and a
    date column, the contest's section rule accepted it, and then every row
    failed to parse.

    Attributes:
        contest: The contest slug.
        page_url: The page the table sits on.
        heading_path: The table's section path as display text.
        collapsed: True when the table was a hidden hypothetical.
    """

    contest: str
    page_url: str
    heading_path: str
    collapsed: bool


@dataclass(frozen=True, slots=True)
class CollapsedOnlyRace:
    """A race whose only general-election tables are collapsed hypotheticals.

    Attributes:
        contest: The contest slug.
        page_url: The race's page.
        seat_name: The seat, or None for a national scope.
        available_rows: Rows the collapsed tables hold.
        included: True when ``include_collapsed_for_uncovered`` was set and
            those rows are in :attr:`UsPollIndex.rows`.
    """

    contest: str
    page_url: str
    seat_name: str | None
    available_rows: int
    included: bool


@dataclass(frozen=True, slots=True)
class FetchedPages:
    """The outcome of fetching a batch of pages.

    Attributes:
        pages: URL → page source, for the pages that came back.
        failures: URL → reason, for the pages that did not.
        notes: Human-readable remarks that are not failures — a 404 means the
            page has not been written yet, which is expected for a future
            cycle's article.
    """

    pages: Mapping[str, str]
    failures: Mapping[str, str]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageRows:
    """Everything one page contributed.

    Attributes:
        rows: The poll rows, in document order.
        unmatched_seats: Tables whose seat could not be resolved.
        empty_tables: Accepted tables that produced no rows.
        collapsed_only_races: Races on this page with no visible rows.
        unknown_suffixes: Party suffix → number of accepted tables carrying it.
        variants_dropped: Repeat rows (LV/RV, "with leaners") dropped.
    """

    rows: tuple[UsPollRow, ...]
    unmatched_seats: tuple[UnmatchedSeat, ...]
    empty_tables: tuple[EmptyTable, ...]
    collapsed_only_races: tuple[CollapsedOnlyRace, ...]
    unknown_suffixes: Mapping[str, int]
    variants_dropped: int


@dataclass(frozen=True, slots=True)
class UsPollIndex:
    """Every row scraped in one run, plus what the run could not place.

    The diagnostics are not incidental: the parser is deliberately strict, so a
    Wikipedia layout change shows up as rows moving into ``unmatched_seats`` or
    ``empty_tables`` rather than as silently missing polls. The console's
    summary page renders all of them.

    Attributes:
        rows: The poll rows, contest by contest in page order.
        page_failures: URL → reason for every page that could not be read or
            could not be placed (a second page claiming a seat another page
            already owns).
        notes: Remarks that are not failures, e.g. a page that is not written
            yet.
        collapsed_only_races: Races whose only tables are hypotheticals.
        unknown_suffixes: Party suffix → number of tables carrying it, for
            suffixes that map to no database party.
        variants_dropped: Repeat rows dropped across the whole run.
        unmatched_seats: Tables whose seat could not be resolved.
        empty_tables: Accepted tables that produced no rows.
        pages_fetched: How many pages were read, for the summary line.
    """

    rows: tuple[UsPollRow, ...]
    page_failures: Mapping[str, str]
    notes: tuple[str, ...]
    collapsed_only_races: tuple[CollapsedOnlyRace, ...]
    unknown_suffixes: Mapping[str, int]
    variants_dropped: int
    unmatched_seats: tuple[UnmatchedSeat, ...]
    empty_tables: tuple[EmptyTable, ...]
    pages_fetched: int


# ── Page discovery ────────────────────────────────────────────────────────────


def _href(link: Tag) -> str | None:
    """Read an ``<a href>`` as a single string, or None when it has none."""
    value = link.get("href")
    if isinstance(value, list):
        value = " ".join(value)
    return value or None


def _discovered_pages(
    html: str,
    pattern: re.Pattern[str],
    *,
    keep: Callable[[str], bool],
) -> list[str]:
    """Return the absolute URLs of an index page's race links, deduped.

    Args:
        html: The index page source.
        pattern: Matches an ``href`` and captures the page slug.
        keep: Predicate on the state a slug names — territory pages and, for
            the House, the District of Columbia are dropped here.

    Returns:
        One absolute ``https://en.wikipedia.org/wiki/…`` URL per race page, in
        the order the index first links it.
    """
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.find_all("a"):
        if not isinstance(link, Tag):
            continue
        href = _href(link)
        if href is None:
            continue
        match = pattern.match(href)
        if match is None:
            continue
        slug = match.group(1)
        if slug in seen:
            continue
        state = state_from_page_slug(slug)
        if state is None or not keep(state):
            continue
        seen.add(slug)
        urls.append(f"{WIKIPEDIA_BASE}/wiki/{slug}")
    return urls


def discover_senate_pages(html: str) -> list[str]:
    """Return the Senate race pages linked from the 2026 Senate index.

    The index links each race as an absolute URL matching
    ``/wiki/2026_United_States_Senate_(special_)?election_in_<State>``. In 2026
    that is 35 pages: the 33 regular class-2 races plus the Florida and Ohio
    specials.

    Args:
        html: The Senate index page source.

    Returns:
        One URL per race page, deduped, in index order.
    """
    return _discovered_pages(html, _SENATE_PAGE_RE, keep=lambda state: True)


def discover_house_pages(html: str) -> list[str]:
    """Return the per-state House pages linked from the 2026 House index.

    The index links 44 multi-district states as
    ``…_House_of_Representatives_elections_in_<State>`` and the 6 at-large
    states in the singular ``…_election_in_<State>`` form. It also links six
    territory/DC pages — and the Northern Mariana Islands twice — none of which
    has a seat on the House map, so they are dropped here rather than turning
    into unmatched seats later.

    Args:
        html: The House index page source.

    Returns:
        One URL per state page, deduped, in index order.
    """
    return _discovered_pages(
        html,
        _HOUSE_PAGE_RE,
        keep=lambda state: state in HOUSE_DISTRICT_COUNTS,
    )


# ── Fetching ──────────────────────────────────────────────────────────────────


def _fetch_one(url: str, fetcher: Fetcher) -> tuple[str | None, str | None, str | None]:
    """Fetch one page, turning every failure into text.

    Returns:
        ``(html, failure, note)`` — exactly one of the three is set.
    """
    try:
        return fetcher(url), None, None
    except HTTPError as err:
        if err.code == 404:
            return None, None, f"{url}: not present yet (HTTP 404)"
        return None, f"HTTP {err.code}: {err.reason}", None
    except Exception as err:  # noqa: BLE001 - per-URL boundary, see below
        # One unreachable page out of ~90 must not abort a whole import run,
        # and a fetcher is free to raise anything (socket, TLS, decoding), so
        # the boundary is deliberately broad. Nothing is swallowed: the reason
        # is reported on the index and rendered on the summary page.
        return None, f"{type(err).__name__}: {err}", None


def fetch_pages(
    urls: Sequence[str],
    *,
    fetcher: Fetcher = fetch_html,
    max_workers: int = 4,
) -> FetchedPages:
    """Fetch pages concurrently, recording failures instead of raising.

    A US import reads around 90 Wikipedia pages, so they are fetched in
    parallel (a small pool — this is someone else's server) with the 30-second
    timeout :func:`~polls.importers.us.us_polls_common.fetch_html` applies.

    A 404 is not treated as a failure: an article for a future cycle simply may
    not exist yet, which is the case for the presidential Statewide page today.

    Args:
        urls: Pages to fetch. Repeats are fetched once.
        fetcher: Injection point — tests pass a dict-backed fake.
        max_workers: Size of the thread pool.

    Returns:
        A :class:`FetchedPages` holding the sources, the failures and the
        notes, each keyed or ordered by the request order.
    """
    wanted = list(dict.fromkeys(urls))
    pages: dict[str, str] = {}
    failures: dict[str, str] = {}
    notes: list[str] = []
    if not wanted:
        return FetchedPages(pages=pages, failures=failures, notes=())

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures: list[Future[tuple[str | None, str | None, str | None]]] = [
            pool.submit(_fetch_one, url, fetcher) for url in wanted
        ]
        # Read the futures in request order, so the index is deterministic
        # whatever order the pool finishes them in.
        for url, future in zip(wanted, futures, strict=True):
            html, failure, note = future.result()
            if html is not None:
                pages[url] = html
            if failure is not None:
                failures[url] = failure
            if note is not None:
                notes.append(note)

    return FetchedPages(pages=pages, failures=failures, notes=tuple(notes))


# ── Section, seat and lead rules ──────────────────────────────────────────────


def _headings_text(headings: Sequence[Heading]) -> str:
    """Render a heading path for display: ``"General election › Polling"``."""
    return _HEADING_SEPARATOR.join(heading.text for heading in headings)


def _innermost_anchor(headings: Sequence[Heading]) -> str | None:
    """Return the deepest heading anchor, so a row can link to its section."""
    for heading in reversed(headings):
        if heading.anchor:
            return heading.anchor
    return None


def accepts_table(contest: UsContest, table: ParsedTable) -> bool:
    """Report whether a contest's section rule accepts a parsed table.

    ``house_national`` wants exactly one table: the generic-ballot poll
    *aggregation* table under the "Generic congressional ballot aggregate
    polls" h2. There are no individual generic-ballot polls on Wikipedia, so
    the aggregators' snapshot is the series.

    Every other contest wants the opposite: an individual-poll table in a
    **general election** section, never under a "Primary" heading (CA-40 nests
    its general-election hypotheticals under one) and never an aggregation
    table, whose rows are averages of the polls being imported alongside them.

    Collapsed hypotheticals are not decided here — the caller keeps them aside
    for the uncovered-race fallback.
    """
    if contest.section_rule == "generic_ballot":
        if not table.info.columns.is_aggregation:
            return False
        return any(
            heading.level == 2 and "generic" in heading.text.lower()
            for heading in table.headings
        )

    if table.info.columns.is_aggregation:
        return False
    texts = [heading.text.lower() for heading in table.headings]
    if any(text.startswith("primar") for text in texts):
        return False
    return any("general election" in text for text in texts)


def seat_name_for_table(
    contest: UsContest,
    page_url: str,
    headings: Sequence[Heading],
) -> tuple[str | None, str | None]:
    """Work out which seat a table belongs to.

    Each contest names its races differently:

    - **Senate:** one race per page, so the state comes from the page slug and
      is the seat name on the Senate map.
    - **House districts:** the state comes from the page slug and the district
      from the ``District N`` heading the race sits under, giving ``CA-40``. An
      at-large state's page carries no such heading and is always ``XX-01``.
    - **President:** a path through "Nationwide" is the national reading and
      has no seat; otherwise the deepest heading that names a state or a split
      district wins (``Statewide › Nevada › matchup`` → Nevada).
    - **House national:** national by definition.

    Args:
        contest: The contest being scraped.
        page_url: The page the table sits on.
        headings: The table's heading path, outermost first.

    Returns:
        ``(seat_name, reason)``. ``(None, None)`` is a national reading;
        ``(None, reason)`` means the rules could not place the table.
    """
    if contest.seat_rule == "national":
        return None, None

    if contest.seat_rule == "president":
        if any("nationwide" in heading.text.lower() for heading in headings):
            return None, None
        for heading in reversed(headings):
            seat = president_seat_for_heading(heading.text)
            if seat is not None:
                return seat, None
        return None, "no heading names a state or district"

    state = state_from_page_slug(page_url)
    if state is None:
        return None, "the page slug names no state"

    if contest.seat_rule == "senate_state":
        return state, None

    if state in AT_LARGE_STATES:
        seat = house_seat_name(state, None)
        if seat is None:
            return None, f"{state} has no House seat"
        return seat, None
    for heading in headings:
        match = _DISTRICT_HEADING_RE.match(heading.text)
        if match is None:
            continue
        district = int(match.group(1))
        seat = house_seat_name(state, district)
        if seat is None:
            return None, f"{state} has no district {district}"
        return seat, None
    return None, f"no 'District N' heading above the table ({state})"


@dataclass(frozen=True, slots=True)
class _SeatRef:
    """A resolved seat, or the reason the table could not be placed."""

    seat_name: str | None
    seat_id: int | None
    unmatched: UnmatchedSeat | None


def _resolve_seat(
    contest: UsContest,
    page_url: str,
    table: ParsedTable,
    seat_ids: Mapping[str, int],
) -> _SeatRef:
    """Name a table's seat and look its database id up on the contest's map."""
    seat_name, reason = seat_name_for_table(contest, page_url, table.headings)
    seat_id: int | None = None
    if reason is None and seat_name is not None:
        seat_id = seat_ids.get(seat_name)
        if seat_id is None:
            reason = f"no seat named {seat_name!r} on {contest.map_name!r}"
    if reason is None:
        return _SeatRef(seat_name=seat_name, seat_id=seat_id, unmatched=None)
    return _SeatRef(
        seat_name=None,
        seat_id=None,
        unmatched=UnmatchedSeat(
            contest=contest.slug,
            page_url=page_url,
            heading_path=_headings_text(table.headings),
            seat_name=seat_name,
            reason=reason,
            dropped_rows=len(table.rows),
        ),
    )


def is_lead_table(table: ParsedTable) -> bool:
    """Report whether a table can be a race's lead table.

    The lead table is what piece 7 reads the race's automatic tracked matchup
    off, so a table that forms no matchup cannot be one however early it
    appears: that rules out the generic-ballot party columns, a single-candidate
    table, and Wikipedia's occasional stray.
    """
    return bool(table.rows) and len(table.candidates) >= 2 and table.matchup is not None


def _sample_label(sample_size: int | None, population: str | None) -> str:
    """Render the sample cell for display, e.g. ``"1,000 (LV)"``.

    Rebuilt from the parsed size and population rather than kept verbatim —
    the parser normalises the cell, and this is the only display use.
    """
    if sample_size is None:
        return f"({population})" if population else ""
    size = f"{sample_size:,}"
    return f"{size} ({population})" if population else size


def _table_notes(table: ParsedTable, *, collapsed: bool) -> tuple[str, ...]:
    """Build the per-table warnings carried on each of its rows."""
    notes: list[str] = []
    if table.unknown_suffixes:
        suffixes = ", ".join(table.unknown_suffixes)
        notes.append(f"unrecognised party suffix: {suffixes}")
    if table.variants_dropped:
        notes.append(
            f"{table.variants_dropped} repeat row(s) dropped "
            "(likely-voter / with-leaners variants)"
        )
    if collapsed:
        notes.append("collapsed hypothetical table")
    return tuple(notes)


def _rows_for_table(
    contest: UsContest,
    page_url: str,
    table: ParsedTable,
    seat: _SeatRef,
    *,
    is_lead: bool,
    collapsed: bool,
) -> list[UsPollRow]:
    """Turn one accepted table's parsed rows into contest rows."""
    heading_path = _headings_text(table.headings)
    anchor = _innermost_anchor(table.headings)
    section_url = f"{page_url}#{anchor}" if anchor else page_url
    notes = _table_notes(table, collapsed=collapsed)
    return [
        UsPollRow(
            fieldwork_start=parsed.fieldwork_start,
            fieldwork_end=parsed.fieldwork_end,
            date_label=parsed.date_label,
            pollster_label=parsed.pollster_label,
            pollster_identifier=pollster_identifier(
                parsed.pollster_label, contest.pollster_suffix
            ),
            sample_size_label=_sample_label(parsed.sample_size, parsed.population),
            source_url=parsed.source_url or section_url,
            matchup=table.matchup,
            contest=contest.slug,
            page_url=page_url,
            heading_path=heading_path,
            seat_name=seat.seat_name,
            seat_id=seat.seat_id,
            map_name=contest.map_name,
            readings=parsed.readings,
            sample_size=parsed.sample_size,
            population=parsed.population,
            is_lead=is_lead,
            collapsed=collapsed,
            pollster_tags=parsed.pollster_tags,
            notes=notes,
        )
        for parsed in table.rows
    ]


def rows_for_page(
    contest: UsContest,
    page_url: str,
    html: str,
    *,
    seat_ids: Mapping[str, int],
    include_collapsed_for_uncovered: bool = False,
) -> PageRows:
    """Apply a contest's rules to one page and return what it contributes.

    Visible tables are taken in document order. The first accepted one per seat
    that forms a matchup is the race's **lead** — Wikipedia puts the nominee
    table first and hides the hypotheticals — and its rows carry ``is_lead``.

    A seat that ends up with no visible rows at all is a *collapsed-only* race:
    it is always reported, and under ``include_collapsed_for_uncovered`` its
    hidden tables' rows are imported too, flagged ``collapsed`` and never lead.

    Args:
        contest: The contest being scraped.
        page_url: The page's URL, used for the seat rule and row source links.
        html: The page source.
        seat_ids: Seat name → id on the contest's map.
        include_collapsed_for_uncovered: Opt in to the collapsed fallback.

    Returns:
        The page's rows and everything it could not place.
    """
    tables = parse_poll_tables(
        html,
        allow_party_labels=contest.allow_party_labels,
        keep_empty=True,
    )
    visible: list[ParsedTable] = []
    hidden: list[ParsedTable] = []
    for table in tables:
        if not accepts_table(contest, table):
            continue
        (hidden if table.info.collapsed else visible).append(table)

    rows: list[UsPollRow] = []
    unmatched: list[UnmatchedSeat] = []
    empty: list[EmptyTable] = []
    unknown: Counter[str] = Counter()
    variants_dropped = 0
    seats_with_rows: set[str | None] = set()
    seats_with_lead: set[str | None] = set()

    for table in visible:
        unknown.update(table.unknown_suffixes)
        variants_dropped += table.variants_dropped
        if not table.rows:
            empty.append(
                EmptyTable(
                    contest=contest.slug,
                    page_url=page_url,
                    heading_path=_headings_text(table.headings),
                    collapsed=False,
                )
            )
            continue
        seat = _resolve_seat(contest, page_url, table, seat_ids)
        if seat.unmatched is not None:
            unmatched.append(seat.unmatched)
            continue
        is_lead = seat.seat_name not in seats_with_lead and is_lead_table(table)
        if is_lead:
            seats_with_lead.add(seat.seat_name)
        rows.extend(
            _rows_for_table(
                contest, page_url, table, seat, is_lead=is_lead, collapsed=False
            )
        )
        seats_with_rows.add(seat.seat_name)

    collapsed_only, collapsed_variants = _collapsed_fallback(
        contest,
        page_url,
        hidden,
        seat_ids=seat_ids,
        seats_with_rows=seats_with_rows,
        include=include_collapsed_for_uncovered,
        rows=rows,
        empty=empty,
        unknown=unknown,
    )
    variants_dropped += collapsed_variants

    return PageRows(
        rows=tuple(rows),
        unmatched_seats=tuple(unmatched),
        empty_tables=tuple(empty),
        collapsed_only_races=tuple(collapsed_only),
        unknown_suffixes=dict(unknown),
        variants_dropped=variants_dropped,
    )


def _collapsed_fallback(
    contest: UsContest,
    page_url: str,
    hidden: Sequence[ParsedTable],
    *,
    seat_ids: Mapping[str, int],
    seats_with_rows: set[str | None],
    include: bool,
    rows: list[UsPollRow],
    empty: list[EmptyTable],
    unknown: Counter[str],
) -> tuple[list[CollapsedOnlyRace], int]:
    """Report, and optionally import, the hidden tables of uncovered races.

    A race with a visible table is left alone: its hypotheticals are answers to
    questions Wikipedia chose not to promote. A race with none has only its
    hypotheticals, so the user can opt in to them — never as the lead table,
    always flagged ``collapsed``, so the review step can say where they came
    from.

    ``rows``, ``empty`` and ``unknown`` are appended to in place.

    Returns:
        The uncovered races, and how many repeat rows their tables dropped
        (counted only when they were imported).
    """
    grouped: dict[str | None, list[tuple[_SeatRef, ParsedTable]]] = {}
    for table in hidden:
        seat = _resolve_seat(contest, page_url, table, seat_ids)
        if seat.unmatched is not None:
            # Not reported: nothing was going to be imported from it anyway,
            # and a page full of unplaceable hypotheticals would drown out the
            # visible tables that really did fail to place.
            continue
        if seat.seat_name in seats_with_rows:
            continue
        if not table.rows:
            empty.append(
                EmptyTable(
                    contest=contest.slug,
                    page_url=page_url,
                    heading_path=_headings_text(table.headings),
                    collapsed=True,
                )
            )
            continue
        grouped.setdefault(seat.seat_name, []).append((seat, table))

    races: list[CollapsedOnlyRace] = []
    variants_dropped = 0
    for seat_name, entries in grouped.items():
        races.append(
            CollapsedOnlyRace(
                contest=contest.slug,
                page_url=page_url,
                seat_name=seat_name,
                available_rows=sum(len(table.rows) for _, table in entries),
                included=include,
            )
        )
        if not include:
            continue
        for seat, table in entries:
            unknown.update(table.unknown_suffixes)
            variants_dropped += table.variants_dropped
            rows.extend(
                _rows_for_table(
                    contest, page_url, table, seat, is_lead=False, collapsed=True
                )
            )
    return races, variants_dropped


# ── The run ───────────────────────────────────────────────────────────────────


def normalise_states(states: Iterable[str]) -> tuple[list[str], list[str]]:
    """Resolve a state filter to canonical names.

    Accepts full names in any case and postal codes, so ``["tx", "Michigan"]``
    works.

    Returns:
        ``(canonical names, unrecognised inputs)``.
    """
    names: list[str] = []
    unknown: list[str] = []
    for value in states:
        canonical = _STATE_BY_KEY.get(value.strip().lower())
        if canonical is None:
            unknown.append(value)
        elif canonical not in names:
            names.append(canonical)
    return names, unknown


def _seat_ids_for_map(db: Database, map_name: str) -> Mapping[str, int] | None:
    """Return seat name → id for a map, or None when the map does not exist."""
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        return None
    return {seat.seat_name: seat.id for seat in db.get_seats_for_map(poll_map.id)}


def _contest_page_urls(
    contest: UsContest,
    *,
    index_html: str | None,
    wanted_states: Sequence[str] | None,
) -> list[str]:
    """List the pages a contest should read, honouring the state filter."""
    urls = list(contest.page_urls)
    if index_html is not None:
        if contest.discovery == "senate_index":
            urls.extend(discover_senate_pages(index_html))
        elif contest.discovery == "house_index":
            urls.extend(discover_house_pages(index_html))
    if wanted_states is None or not contest.is_per_state:
        return list(dict.fromkeys(urls))
    allowed = set(wanted_states)
    return [url for url in dict.fromkeys(urls) if state_from_page_slug(url) in allowed]


def fetch_us_poll_index(
    db: Database,
    contests: Sequence[UsContest],
    *,
    states: Sequence[str] | None = None,
    include_collapsed_for_uncovered: bool = False,
    fetcher: Fetcher = fetch_html,
    max_workers: int = 4,
) -> UsPollIndex:
    """Scrape every requested contest and return its rows plus its diagnostics.

    Runs in four passes. The maps come first, because a contest whose map is
    missing can import nothing and there is no point fetching its ~35 pages to
    find that out. Then the index pages (a contest's race pages are links on
    one), then every race page, then the parsing. A page fetched for one
    contest is reused by another — the House index is both
    ``house_national``'s only page and ``house_districts``' directory.

    ``db`` is read only: each contest's map supplies the seat names its rows can
    attach to. A table whose seat is not on that map is reported in
    ``unmatched_seats`` rather than imported without one.

    Args:
        db: Database to resolve seat names against. Nothing is written.
        contests: The contests to scrape, in the order to scrape them.
        states: Restrict the per-state race pages to these states, given as
            names or postal codes. None means every state.
        include_collapsed_for_uncovered: Import hidden hypothetical tables for
            races that have no visible table.
        fetcher: Injection point — tests pass a dict-backed fake.
        max_workers: Size of the fetch thread pool.

    Returns:
        A :class:`UsPollIndex`.
    """
    notes: list[str] = []
    page_failures: dict[str, str] = {}
    wanted_states: list[str] | None = None
    if states is not None:
        wanted_states, unknown_states = normalise_states(states)
        for value in unknown_states:
            notes.append(f"ignored unrecognised state filter: {value!r}")

    scoped: list[tuple[UsContest, Mapping[str, int]]] = []
    for contest in contests:
        seat_ids = _seat_ids_for_map(db, contest.map_name)
        if seat_ids is None:
            notes.append(
                f"{contest.slug}: skipped — no map named {contest.map_name!r}"
            )
            continue
        scoped.append((contest, seat_ids))

    index_urls = [
        contest.index_url for contest, _ in scoped if contest.index_url is not None
    ]
    fetched = fetch_pages(index_urls, fetcher=fetcher, max_workers=max_workers)
    pages: dict[str, str] = dict(fetched.pages)
    page_failures.update(fetched.failures)
    notes.extend(fetched.notes)

    urls_by_contest: dict[str, list[str]] = {}
    for contest, _ in scoped:
        index_html = pages.get(contest.index_url) if contest.index_url else None
        if contest.index_url is not None and index_html is None:
            notes.append(
                f"{contest.slug}: no race pages — its index could not be read"
            )
        urls_by_contest[contest.slug] = _contest_page_urls(
            contest, index_html=index_html, wanted_states=wanted_states
        )

    outstanding = [
        url
        for urls in urls_by_contest.values()
        for url in urls
        if url not in pages and url not in page_failures
    ]
    fetched = fetch_pages(outstanding, fetcher=fetcher, max_workers=max_workers)
    pages.update(fetched.pages)
    page_failures.update(fetched.failures)
    notes.extend(fetched.notes)

    rows: list[UsPollRow] = []
    unmatched: list[UnmatchedSeat] = []
    empty: list[EmptyTable] = []
    collapsed_only: list[CollapsedOnlyRace] = []
    unknown_suffixes: Counter[str] = Counter()
    variants_dropped = 0

    for contest, seat_ids in scoped:
        claimed: dict[str, str] = {}
        for url in urls_by_contest[contest.slug]:
            html = pages.get(url)
            if html is None:
                continue
            claim = state_from_page_slug(url) if contest.is_per_state else None
            if claim is not None:
                owner = claimed.get(claim)
                if owner is not None:
                    page_failures[url] = (
                        f"{claim} is already covered by {owner} — "
                        "two races for one seat cannot be told apart"
                    )
                    continue
                claimed[claim] = url
            page_rows = rows_for_page(
                contest,
                url,
                html,
                seat_ids=seat_ids,
                include_collapsed_for_uncovered=include_collapsed_for_uncovered,
            )
            rows.extend(page_rows.rows)
            unmatched.extend(page_rows.unmatched_seats)
            empty.extend(page_rows.empty_tables)
            collapsed_only.extend(page_rows.collapsed_only_races)
            unknown_suffixes.update(page_rows.unknown_suffixes)
            variants_dropped += page_rows.variants_dropped

    return UsPollIndex(
        rows=tuple(rows),
        page_failures=page_failures,
        notes=tuple(notes),
        collapsed_only_races=tuple(collapsed_only),
        unknown_suffixes=dict(unknown_suffixes),
        variants_dropped=variants_dropped,
        unmatched_seats=tuple(unmatched),
        empty_tables=tuple(empty),
        pages_fetched=len(pages),
    )
