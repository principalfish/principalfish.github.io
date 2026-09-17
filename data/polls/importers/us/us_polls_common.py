#!/usr/bin/env python3
"""Shared Wikipedia parser + DB importer for US national two-party polling.

The three US election types (House / President / Senate) each read national
two-party polling from Wikipedia, but the pages use different table shapes, all
handled here by detecting columns from header labels:

- **poll-aggregation tables** (the "Opinion polling" section of the House and
  Senate election pages): rows are aggregators (Decision Desk HQ, …) with
  plural "Democrats" / "Republicans" columns; the "Dates updated" column is the
  snapshot date. Repeated imports over time accumulate the trend series.
- **head-to-head matchup tables** (the presidential nationwide-polling page):
  one wikitable per hypothetical pairing, candidate names suffixed ``(D)``/``(R)``
  as columns. All matchup tables parse, and readings from the same pollster +
  fieldwork window are averaged into one two-party reading by
  :func:`merge_polls_by_fieldwork`.
- **classic pollster lists** (dates / pollster / sample / party columns).

Each importer supplies only its URL, its map, and its per-type
pollster-identifier suffix.

Rows are inserted as **national** ``PollRow`` records (``region_id = NULL``), which
is exactly what the national-uniform-swing forecast runners in ``models/us``
consume. Adding sub-national rows later (with ``region_id`` set) needs no change
here or in the model — see ``models/us/_common.py``.

The **Table structure** section is the replacement for the positional parsing
above: a rowspan-aware grid, the heading path a table sits under, whether
Wikipedia collapsed it, and strict pollster/date column detection. The
**Candidates, matchups and rows** section reads the contents of such a table:
who the candidates are, the canonical matchup label they form, and one
:class:`ParsedPollRow` per poll. The contest layer builds seat- and
matchup-scoped rows on top of them; the legacy functions stay only until the
three wrapper scripts move across.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Tag

# ``data/`` root — home of db.py / models.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db import Database
from models import Poll, Pollster
from sqlalchemy import select

# Default Wikipedia column header (lowercased) → canonical DB party name. Minor
# parties/undecided are intentionally omitted: the national-swing model only moves
# Democrat and Republican, holding every other party at its baseline.
DEFAULT_PARTY_COLUMN_MAP: dict[str, str] = {
    "democratic": "Democratic",
    "democrat": "Democratic",
    "dem": "Democratic",
    "d": "Democratic",
    "republican": "Republican",
    "rep": "Republican",
    "gop": "Republican",
    "r": "Republican",
}

_MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


@dataclass
class ParsedUsPoll:
    """A single national poll row parsed from a US Wikipedia polling table.

    Attributes:
        fieldwork_start: First day of polling fieldwork.
        fieldwork_end: Last day of polling fieldwork.
        pollster_name: Pollster label as shown in the Wikipedia table.
        sample_size: Number of respondents, or ``None`` if not shown.
        party_percentages: Canonical party name → national voting-intention percentage.
    """

    fieldwork_start: date
    fieldwork_end: date
    pollster_name: str
    sample_size: int | None
    party_percentages: dict[str, float] = field(default_factory=dict)


# ── HTML helpers ──────────────────────────────────────────────────────────────


def _clean(value: str) -> str:
    """Collapse whitespace and strip a string."""
    return re.sub(r"\s+", " ", value).strip()


def fetch_html(url: str) -> str:
    """Fetch HTML from ``url`` with a browser-like User-Agent (UTF-8 decoded)."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; us-poll-importer/1.0)"})
    with urlopen(req, timeout=30) as response:
        body: str = response.read().decode("utf-8", errors="replace")
    return body


def parse_date_range(raw: str) -> tuple[date, date] | None:
    """Parse a US-format fieldwork date string into ``(start, end)``.

    Handles the US Wikipedia date conventions:
    - Single day: ``"June 3, 2026"``
    - Same-month range: ``"June 1–3, 2026"``
    - Cross-month range: ``"May 28 – June 3, 2026"`` / ``"May 28–June 3, 2026"``

    En/em-dashes are normalised to hyphens before matching. Returns ``None`` when
    the string cannot be parsed.
    """
    text = re.sub(r"[–—]", "-", raw)
    text = re.sub(r"\s+", " ", text).strip()

    # Fully explicit: "January 9, 2025 - June 29, 2026" (both years given; used by
    # the aggregation tables' "Dates administered" column).
    explicit = re.match(
        r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\s*-\s*([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})",
        text,
    )
    if explicit:
        m1_str, d1, y1_str, m2_str, d2, y2_str = explicit.groups()
        month1 = _MONTH_MAP.get(m1_str.lower())
        month2 = _MONTH_MAP.get(m2_str.lower())
        if month1 and month2:
            try:
                return date(int(y1_str), month1, int(d1)), date(int(y2_str), month2, int(d2))
            except ValueError:
                return None

    # Cross-month: "May 28 - June 3, 2026"
    cross = re.match(
        r"([A-Za-z]+)\s+(\d{1,2})\s*-\s*([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})",
        text,
    )
    if cross:
        m1_str, d1, m2_str, d2, yr_str = cross.groups()
        month1 = _MONTH_MAP.get(m1_str.lower())
        month2 = _MONTH_MAP.get(m2_str.lower())
        if month1 and month2:
            year = int(yr_str)
            year1 = year if month1 <= month2 else year - 1
            try:
                return date(year1, month1, int(d1)), date(year, month2, int(d2))
            except ValueError:
                return None

    # Same-month range: "June 1 - 3, 2026"
    same = re.match(
        r"([A-Za-z]+)\s+(\d{1,2})\s*-\s*(\d{1,2}),?\s+(\d{4})",
        text,
    )
    if same:
        mon_str, d1, d2, yr_str = same.groups()
        month = _MONTH_MAP.get(mon_str.lower())
        if month:
            year = int(yr_str)
            try:
                return date(year, month, int(d1)), date(year, month, int(d2))
            except ValueError:
                return None

    # Single day: "June 3, 2026"
    single = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if single:
        mon_str, d, yr_str = single.groups()
        month = _MONTH_MAP.get(mon_str.lower())
        if month:
            try:
                d_obj = date(int(yr_str), month, int(d))
                return d_obj, d_obj
            except ValueError:
                return None

    return None


# ── Table structure ───────────────────────────────────────────────────────────
#
# Everything in this section reads a wikitable's *shape*: its cell grid, the
# headings it sits under, whether Wikipedia collapsed it, and which columns hold
# the pollster, the fieldwork dates and the sample size.
#
# It is deliberately stricter than the legacy parser below. A table counts as a
# polling table only when its header names **both** a pollster/source column and
# a date column, so candidate lists, "Predictions", redistricting and seat-count
# tables are rejected on structure — not on whether one of their cells happens
# to hold a parseable date.


# Heading levels a section path is built from. h1 is the page title.
_HEADING_NAMES: tuple[str, ...] = ("h2", "h3", "h4", "h5", "h6")

# How many leading grid rows are tried as the header row. Presidential matchup
# tables put a row of empty colour cells under the real header.
MAX_HEADER_ROW_SCAN = 3

# Upper bound on a single cell's rowspan/colspan, so hand-edited markup with an
# absurd span can't blow the grid up.
_MAX_SPAN = 64

_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")


@dataclass(frozen=True, slots=True)
class Cell:
    """One position in an expanded table grid.

    A cell that spans several rows or columns appears at every position it
    covers: the copies share ``text`` and ``tag`` with the original and carry
    ``is_spanned=True``. Positions no cell reaches (ragged rows, which the grid
    pads to a rectangle) carry ``tag=None``.

    Attributes:
        text: Cell text, whitespace-collapsed, with ``<br>`` rendered as a space.
        is_header: True for a ``<th>``.
        tag: The source ``<td>``/``<th>``, or None for a padding cell. Kept so
            later stages can read a candidate header's ``<a title>`` or a
            pollster cell's link.
        is_spanned: True where this position is covered by a cell declared in an
            earlier row or column.
    """

    text: str
    is_header: bool
    tag: Tag | None = None
    is_spanned: bool = False


_EMPTY_CELL = Cell(text="", is_header=False)


@dataclass(frozen=True, slots=True)
class Heading:
    """A section heading above a table.

    Attributes:
        level: 2–6, from the ``h2``–``h6`` tag name.
        text: Heading text, with the ``[edit]`` link stripped.
        anchor: The heading's HTML id, used to build a ``page_url#anchor``
            source link, or None when the markup carries no id.
    """

    level: int
    text: str
    anchor: str | None = None


@dataclass(frozen=True, slots=True)
class TableColumns:
    """Where a polling table keeps its pollster, dates and sample size.

    Attributes:
        pollster: Index of the pollster / poll-source column.
        date: Index of the fieldwork-date column ("Dates updated" wins over
            "Dates administered" when a table has both).
        sample: Index of the sample-size column, or None when there is none.
        is_aggregation: True for a poll-average table (the House/Senate pages'
            "Source of poll aggregation" shape), whose rows are other people's
            averages rather than individual polls.
        header_row: Index of the grid row these columns were read from.
    """

    pollster: int
    date: int
    sample: int | None
    is_aggregation: bool
    header_row: int


@dataclass(frozen=True, slots=True)
class TableInfo:
    """A polling table with everything the later stages need to place it.

    Attributes:
        table: The source ``<table>``.
        grid: The rowspan/colspan-expanded cell grid.
        columns: The detected pollster/date/sample layout.
        headings: The section path, outermost first.
        collapsed: True when Wikipedia hides the table behind a "show" toggle
            (its hypothetical-matchup sections).
    """

    table: Tag
    grid: list[list[Cell]]
    columns: TableColumns
    headings: tuple[Heading, ...]
    collapsed: bool

    @property
    def data_rows(self) -> list[list[Cell]]:
        """The grid rows below the header row."""
        return self.grid[self.columns.header_row + 1 :]


def _attr_text(tag: Tag, name: str) -> str | None:
    """Read an attribute as a single string, or None when absent or empty."""
    value = tag.get(name)
    if isinstance(value, list):
        value = " ".join(value)
    return value or None


def _classes(tag: Tag) -> list[str]:
    """Return an element's CSS classes as a list."""
    value = tag.get("class")
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return list(value)


def _span_value(tag: Tag, name: str, *, zero: int) -> int:
    """Read a rowspan/colspan attribute, defaulting to 1 when it is unusable.

    ``rowspan="0"`` means "to the end of the section" in HTML, so callers pass
    the number of remaining rows as ``zero``.
    """
    raw = _attr_text(tag, name)
    if raw is None:
        return 1
    try:
        value = int(raw.strip())
    except ValueError:
        return 1
    if value <= 0:
        return zero
    return min(value, _MAX_SPAN)


def expand_table_grid(table: Tag) -> list[list[Cell]]:
    """Expand a table into a rectangular grid, resolving rowspan and colspan.

    Reading cells by position is wrong on Wikipedia's polling tables: a pollster
    that asked both a likely-voter and a registered-voter question is written
    once with ``rowspan="2"``, so the second row's first physical cell is the
    sample size, not the pollster. (Michigan's Senate nominee table has 25 such
    cells.) Here a spanning cell's value is repeated into every row and column
    it covers, so ``grid[row][column]`` always means the same thing.

    Rows of nested tables are ignored, and short rows are padded with empty
    cells so every row has the same length.

    Args:
        table: The ``<table>`` element.

    Returns:
        One list of :class:`Cell` per ``<tr>``, all of the same length.
    """
    rows = [
        row
        for row in table.find_all("tr")
        if isinstance(row, Tag) and row.find_parent("table") is table
    ]
    placed: list[dict[int, Cell]] = [{} for _ in rows]

    for row_index, row in enumerate(rows):
        column = 0
        for tag in row.find_all(["td", "th"], recursive=False):
            if not isinstance(tag, Tag):
                continue
            while column in placed[row_index]:
                column += 1
            row_span = _span_value(tag, "rowspan", zero=len(rows) - row_index)
            col_span = _span_value(tag, "colspan", zero=1)
            text = _clean(tag.get_text(" ", strip=True))
            is_header = tag.name == "th"
            for row_offset in range(row_span):
                target_row = row_index + row_offset
                if target_row >= len(rows):
                    break
                for col_offset in range(col_span):
                    placed[target_row][column + col_offset] = Cell(
                        text=text,
                        is_header=is_header,
                        tag=tag,
                        is_spanned=row_offset > 0 or col_offset > 0,
                    )
            column += col_span

    width = max((max(cells) + 1 for cells in placed if cells), default=0)
    return [
        [cells.get(index, _EMPTY_CELL) for index in range(width)] for cells in placed
    ]


def _heading_text(heading: Tag) -> str:
    """Return a heading's text without its ``[edit]`` link."""
    parts: list[str] = []
    for child in heading.children:
        if isinstance(child, Tag):
            if "mw-editsection" in _classes(child):
                continue
            parts.append(child.get_text(" ", strip=True))
        else:
            parts.append(str(child))
    return _clean(" ".join(part for part in parts if part))


def _heading_anchor(heading: Tag) -> str | None:
    """Return the id a ``page_url#anchor`` link should use for a heading."""
    own_id = _attr_text(heading, "id")
    if own_id is not None:
        return own_id
    headline = heading.find("span", class_="mw-headline")
    if isinstance(headline, Tag):
        return _attr_text(headline, "id")
    return None


def heading_path(table: Tag) -> list[Heading]:
    """Return the section headings a table sits under, outermost first.

    Walks backwards through the h2–h6 elements preceding the table, keeping each
    heading that is strictly higher (a smaller number) than the last one kept
    and stopping once an h2 is taken. Walking the document rather than the
    element tree keeps this working whether the page wraps sections in
    ``<section>`` elements, wraps each heading in a ``div.mw-heading``, or does
    neither.

    Sibling sections are skipped for free: a table under "General election"
    follows the primaries' own h3/h4 headings in document order, but none of
    them is higher than the h3 already kept.

    Args:
        table: The ``<table>`` element.

    Returns:
        The heading path, e.g. "Opinion polling" › "General election" ›
        "Nationwide" › "JD Vance vs. Gavin Newsom". Empty when the table sits
        above every heading on the page.
    """
    path: list[Heading] = []
    last_level = 7
    for element in table.find_all_previous(list(_HEADING_NAMES)):
        if not isinstance(element, Tag) or element.name is None:
            continue
        level = int(element.name[1])
        if level >= last_level:
            continue
        path.append(
            Heading(
                level=level,
                text=_heading_text(element),
                anchor=_heading_anchor(element),
            )
        )
        last_level = level
        if level == 2:
            break
    path.reverse()
    return path


def is_collapsed(table: Tag) -> bool:
    """Report whether Wikipedia hides a table behind a "show" toggle.

    Hypothetical-matchup tables sit inside a ``div.mw-collapsible-content``;
    a table can also collapse itself with the ``mw-collapsed`` class. Those
    polls are real but unpromoted, so the contest layer imports them only when
    a race has no visible table and the user opts in.
    """
    if "mw-collapsed" in _classes(table):
        return True
    return any("mw-collapsible-content" in _classes(parent) for parent in table.parents)


def _header_key(text: str) -> str:
    """Normalise a header cell for matching: footnotes out, lowercased."""
    return _clean(_FOOTNOTE_RE.sub("", text)).lower()


def _is_pollster_header(key: str) -> bool:
    """Report whether a normalised header names the pollster column.

    A bare "Source" does not count: that is the "Predictions" tables' first
    column, and accepting it is what let non-poll tables through.
    """
    if "pollster" in key or "firm" in key:
        return True
    return "source" in key and ("poll" in key or "aggregat" in key)


def detect_columns(
    header_cells: Sequence[Cell],
    *,
    header_row: int = 0,
) -> TableColumns | None:
    """Detect the pollster / date / sample columns from a header row.

    Strict by design — there is no positional fallback. Both an explicit
    pollster column ("Poll source", "Source of poll aggregation", "Pollster",
    "…firm") and an explicit date column must be named, or the table is not a
    polling table.

    When a table has both a "Dates administered" and a "Dates updated" column
    (the poll-aggregation shape), "updated" wins: administered spans the whole
    cycle, while updated is the snapshot the row actually reports.

    Args:
        header_cells: One grid row.
        header_row: The index of that row, recorded on the result.

    Returns:
        The column layout, or None when the row names no pollster or no date
        column.
    """
    pollster_col: int | None = None
    date_col: int | None = None
    updated_col: int | None = None
    sample_col: int | None = None

    for index, cell in enumerate(header_cells):
        key = _header_key(cell.text)
        if not key:
            continue
        if "date" in key:
            if "updated" in key:
                if updated_col is None:
                    updated_col = index
            elif date_col is None:
                date_col = index
        elif pollster_col is None and _is_pollster_header(key):
            pollster_col = index
        elif sample_col is None and (key == "n" or "sample" in key):
            sample_col = index

    if updated_col is not None:
        date_col = updated_col
    if pollster_col is None or date_col is None:
        return None

    first_key = _header_key(header_cells[0].text) if header_cells else ""
    return TableColumns(
        pollster=pollster_col,
        date=date_col,
        sample=sample_col,
        is_aggregation="aggregat" in first_key or updated_col is not None,
        header_row=header_row,
    )


def classify_table(table: Tag) -> TableInfo | None:
    """Turn a ``<table>`` into a :class:`TableInfo`, or reject it.

    Tries the first few grid rows as the header row, so a table whose real
    header is preceded by a full-width caption row still classifies.

    Args:
        table: Any ``<table>`` on a polling page.

    Returns:
        The classified table, or None when no candidate header row names both a
        pollster and a date column.
    """
    grid = expand_table_grid(table)
    if not grid:
        return None
    for index, row in enumerate(grid[:MAX_HEADER_ROW_SCAN]):
        columns = detect_columns(row, header_row=index)
        if columns is None:
            continue
        return TableInfo(
            table=table,
            grid=grid,
            columns=columns,
            headings=tuple(heading_path(table)),
            collapsed=is_collapsed(table),
        )
    return None


# ── Candidates, matchups and rows ─────────────────────────────────────────────
#
# Reads the contents of a table the section above classified: the candidate
# columns named in its header, the canonical matchup label they form, and one
# row per poll. Nothing here knows about contests, seats or the database —
# that is the contest layer's job.


# Header suffix letter → canonical DB party name. A suffix that is not here
# (live examples: "(IA)", "(WCP)") is kept as raw text with no party, so the
# matchup label still reads correctly and the contest layer can warn about it.
PARTY_SUFFIXES: dict[str, str] = {
    "R": "Republican",
    "D": "Democratic",
    "DFL": "Democratic",
    "D-NPL": "Democratic",
    "I": "Independent",
    "L": "Libertarian",
    "G": "US Green",
}

# Plural (and singular) party headers, used by the House generic-ballot
# aggregation table, which has columns but no candidates. Only recognised when
# the caller passes ``allow_party_labels=True``.
_PARTY_LABEL_COLUMNS: dict[str, tuple[str, str]] = {
    "republicans": ("R", "Republican"),
    "republican": ("R", "Republican"),
    "democrats": ("D", "Democratic"),
    "democratic": ("D", "Democratic"),
}

# Party order inside a matchup label. Anything else (an unknown suffix) sorts
# last, and columns of equal rank keep their header order.
_MATCHUP_PARTY_ORDER: tuple[str, ...] = (
    "Republican",
    "Democratic",
    "Independent",
    "Libertarian",
    "US Green",
)

# "Ken Paxton (R)", "Vance (R)", "Peggy Flanagan (DFL)", "Jane Doe (WCP)".
_CANDIDATE_HEADER_RE = re.compile(
    r"^(?P<name>.*?)\s*\(\s*(?P<letter>[A-Za-z][A-Za-z.\-]{0,7})\s*\)$"
)

# Name suffixes that are never the surname.
_NAME_SUFFIXES: frozenset[str] = frozenset({"jr", "sr", "ii", "iii", "iv"})

# A partisan sponsor tag on a pollster cell: "Rasmussen Reports (R)".
_PARTY_TAG_RE = re.compile(
    r"\(\s*("
    + "|".join(re.escape(key) for key in sorted(PARTY_SUFFIXES, key=len, reverse=True))
    + r")\s*\)",
    re.IGNORECASE,
)

# A trailing Wikipedia disambiguator on a link title: "Dan Sullivan (politician)".
_TITLE_DISAMBIGUATOR_RE = re.compile(r"\s*\([^()]*\)\s*$")

# "1,000 (LV)" → 1000 respondents of population "LV".
_SAMPLE_SIZE_RE = re.compile(r"(\d[\d,]*)")
_POPULATION_RE = re.compile(r"\(\s*([A-Za-z]{1,4})\s*\)")


@dataclass(frozen=True, slots=True)
class CandidateColumn:
    """One candidate column of a polling table, read from its header.

    Attributes:
        index: The column's index in the expanded grid.
        party_name: Canonical DB party name, or None when the header's suffix
            is not one :data:`PARTY_SUFFIXES` knows.
        letter: The suffix exactly as the label should show it ("R", "DFL",
            "WCP").
        surname: The candidate's surname, used to build the matchup label.
        full_name: The candidate's full name, stored on the poll row. Empty for
            the generic-ballot party-label columns, which name no candidate.
    """

    index: int
    party_name: str | None
    letter: str
    surname: str
    full_name: str


@dataclass(frozen=True, slots=True)
class CandidateReading:
    """One candidate's percentage in one poll row.

    Attributes:
        party_name: Canonical DB party name, or None for an unknown suffix.
        candidate_name: The candidate's full name, empty for a party column.
        percentage: The reading as written, not rescaled.
    """

    party_name: str | None
    candidate_name: str
    percentage: float


@dataclass(frozen=True, slots=True)
class ParsedPollRow:
    """One poll, read from one row of a polling table.

    Attributes:
        pollster_label: The pollster name with footnotes and partisan tags
            removed.
        pollster_tags: The partisan tags stripped from the label, e.g.
            ``("R",)`` for "Rasmussen Reports (R)".
        fieldwork_start: First day of fieldwork.
        fieldwork_end: Last day of fieldwork.
        date_label: The date cell as written, kept for the review UI.
        sample_size: Respondents, or None when the table shows none.
        population: The sampled population code ("LV", "RV", "A"), or None.
        readings: One entry per candidate column holding a number; columns
            showing "—" or nothing are absent rather than zero.
        source_url: The poll's own link from the pollster cell, when the page
            gives one (the presidential tables do). Wiki-internal links are
            ignored: they point at the pollster's article, not the poll.
    """

    pollster_label: str
    pollster_tags: tuple[str, ...]
    fieldwork_start: date
    fieldwork_end: date
    date_label: str
    sample_size: int | None
    population: str | None
    readings: tuple[CandidateReading, ...]
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedTable:
    """A classified table together with everything parsed out of it.

    Attributes:
        info: The classified table.
        headings: The section path, outermost first (``info.headings``, lifted
            for convenience).
        candidates: The candidate columns, in header order.
        matchup: The canonical matchup label, or None when the table names
            fewer than two candidates or only party columns.
        rows: The parsed polls, first-variant-wins within this table.
        variants_dropped: How many later rows repeated an earlier row's
            pollster and dates (LV/RV, "with leaners", alternative candidates).
        unknown_suffixes: Header suffixes that map to no party, in header
            order and without repeats.
    """

    info: TableInfo
    headings: tuple[Heading, ...]
    candidates: tuple[CandidateColumn, ...]
    matchup: str | None
    rows: tuple[ParsedPollRow, ...]
    variants_dropped: int
    unknown_suffixes: tuple[str, ...]


def _candidate_link_title(cell: Cell, name_text: str) -> str | None:
    """Return the full name from a header cell's candidate link, if there is one.

    The presidential tables write the header as
    ``<a title="JD Vance">Vance</a><br /><small>(R)</small>``, so the full name
    is only in the link title. A cell can hold a second link for the party
    suffix; it is told apart by its text, which is the suffix rather than part
    of the candidate's name.

    Args:
        cell: The header cell.
        name_text: The header text with the party suffix removed.

    Returns:
        The link title without any trailing Wikipedia disambiguator, or None.
    """
    if cell.tag is None:
        return None
    wanted = {token.lower() for token in name_text.split()}
    for link in cell.tag.find_all("a"):
        if not isinstance(link, Tag):
            continue
        title = _attr_text(link, "title")
        if title is None:
            continue
        link_text = _clean(link.get_text(" ", strip=True))
        tokens = {token.lower() for token in link_text.split()}
        if not tokens or not tokens <= wanted:
            continue
        return _clean(_TITLE_DISAMBIGUATOR_RE.sub("", title)) or None
    return None


def _candidate_full_name(cell: Cell, name_text: str) -> str:
    """Resolve a candidate's full name from a header cell.

    The header's own text wins whenever it already holds more than one word:
    that is the Senate and House shape (``Brian<br />Fitzpatrick (R)``), and it
    is the only place Alaska's "Dan S. Sullivan" and "Dan J. Sullivan" are told
    apart — their link titles are disambiguated article names. A one-word
    header is the presidential shape, a surname, so the link title is used.
    """
    if len(name_text.split()) > 1:
        return name_text
    return _candidate_link_title(cell, name_text) or name_text


def surname(full_name: str) -> str:
    """Return the surname of a candidate's full name.

    Footnotes and the generational suffixes Jr./Sr./II/III/IV are dropped, then
    the last remaining token is the surname — including an apostrophe name
    ("Beto O'Rourke" → "O'Rourke").
    """
    tokens = _clean(_FOOTNOTE_RE.sub("", full_name)).replace(",", " ").split()
    while len(tokens) > 1 and tokens[-1].rstrip(".").lower() in _NAME_SUFFIXES:
        tokens.pop()
    return tokens[-1] if tokens else ""


def candidate_columns(
    header_cells: Sequence[Cell],
    *,
    allow_party_labels: bool = False,
) -> list[CandidateColumn]:
    """Read the candidate columns from a polling table's header row.

    A candidate column is a header of the form ``"{name} ({letter})"``; the
    letter gives the party via :data:`PARTY_SUFFIXES`, and an unrecognised one
    is kept as raw text with no party rather than guessed at. Every other
    header ("Sample size", "Other", "Undecided", "Margin of error") names no
    candidate and is skipped.

    Args:
        header_cells: One grid row — the row ``detect_columns`` accepted.
        allow_party_labels: Also accept the plural party headers
            "Republicans" / "Democrats". They name no candidate, so such
            columns carry an empty name and form no matchup. This is the House
            national generic-ballot aggregation table, which polls parties
            rather than people.

    Returns:
        One :class:`CandidateColumn` per candidate, in header order.
    """
    columns: list[CandidateColumn] = []
    seen_tags: set[int] = set()
    for index, cell in enumerate(header_cells):
        text = _clean(_FOOTNOTE_RE.sub("", cell.text))
        if not text:
            continue
        # A colspan/rowspan header repeats across the positions it covers; only
        # its first position is a column of its own.
        if cell.tag is not None:
            if id(cell.tag) in seen_tags:
                continue
            seen_tags.add(id(cell.tag))
        if allow_party_labels:
            label = _PARTY_LABEL_COLUMNS.get(text.lower())
            if label is not None:
                letter, party_name = label
                columns.append(
                    CandidateColumn(
                        index=index,
                        party_name=party_name,
                        letter=letter,
                        surname="",
                        full_name="",
                    )
                )
                continue
        match = _CANDIDATE_HEADER_RE.match(text)
        if match is None:
            continue
        name_text = _clean(match.group("name"))
        if not name_text:
            continue
        letter = match.group("letter").strip()
        suffix_party = PARTY_SUFFIXES.get(letter.upper())
        if suffix_party is not None:
            letter = letter.upper()
        full_name = _candidate_full_name(cell, name_text)
        columns.append(
            CandidateColumn(
                index=index,
                party_name=suffix_party,
                letter=letter,
                surname=surname(full_name),
                full_name=full_name,
            )
        )
    return columns


def _matchup_rank(party_name: str | None) -> int:
    """Return a party's position in a matchup label."""
    if party_name is None or party_name not in _MATCHUP_PARTY_ORDER:
        return len(_MATCHUP_PARTY_ORDER)
    return _MATCHUP_PARTY_ORDER.index(party_name)


def matchup_label(columns: Sequence[CandidateColumn]) -> str | None:
    """Build the canonical ``"{name} ({letter}) vs …"`` label for a table.

    The label is the key a race's polls are grouped and tracked by, so it must
    not depend on the order Wikipedia happens to list the candidates in: the
    columns are ordered R, D, I, L, G, then unknown suffixes, and header order
    within a party. A "Rogers (R) vs El-Sayed (D)" table and an
    "El-Sayed (D) vs Rogers (R)" table therefore label identically.

    The surname is used, unless two candidates in the same table share one — as
    Alaska's two Dan Sullivans do — in which case both sides use their full
    header name, so the label stays unambiguous.

    Returns:
        The label, or None when the table names fewer than two candidates, or
        when a column names a party rather than a candidate (the generic
        ballot, which has no matchup).
    """
    if len(columns) < 2 or any(not column.surname for column in columns):
        return None
    counts = Counter(column.surname.casefold() for column in columns)
    ordered = sorted(
        columns,
        key=lambda column: (_matchup_rank(column.party_name), column.index),
    )
    parts: list[str] = []
    for column in ordered:
        shared = counts[column.surname.casefold()] > 1
        name = column.full_name if shared else column.surname
        parts.append(f"{name} ({column.letter})")
    return " vs ".join(parts)


def clean_pollster_label(text: str) -> tuple[str, tuple[str, ...]]:
    """Split a pollster cell into its name and its partisan sponsor tags.

    Wikipedia marks a partisan poll by suffixing the sponsor's party:
    "Rasmussen Reports (R)", "GQR (D)". A jointly sponsored poll carries one
    tag per sponsor — "Fabrizio Ward (R)/ Impact Research (D)" — and both
    houses stay in the name, because the pair is the pollster.

    Returns:
        ``(label, tags)`` — the name with footnotes and tags removed, and the
        tags in the order they appeared, uppercased.
    """
    stripped = _FOOTNOTE_RE.sub("", text)
    tags = tuple(match.group(1).upper() for match in _PARTY_TAG_RE.finditer(stripped))
    cleaned = _clean(_PARTY_TAG_RE.sub("", stripped))
    return re.sub(r"\s+([/,;])", r"\1", cleaned).strip(), tags


def _reading_percentage(text: str) -> float | None:
    """Parse a candidate cell, or return None when it holds no reading.

    Deliberately strict: after footnotes, the ``%`` sign and whitespace are
    removed, what is left must be a number. A "—" or an empty cell means the
    candidate was not offered in that row, which is not the same as zero, and a
    colspan event row ("Primary election held") reads as no number at all.
    """
    cleaned = re.sub(r"\s+", "", _FOOTNOTE_RE.sub("", text)).replace("%", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_sample_cell(text: str) -> tuple[int | None, str | None]:
    """Split a sample cell such as ``"1,000 (LV)"`` into size and population."""
    cleaned = _FOOTNOTE_RE.sub("", text)
    size_match = _SAMPLE_SIZE_RE.search(cleaned)
    population_match = _POPULATION_RE.search(cleaned)
    return (
        int(size_match.group(1).replace(",", "")) if size_match else None,
        population_match.group(1).upper() if population_match else None,
    )


def _pollster_source_url(cell: Cell) -> str | None:
    """Return the poll's own link from a pollster cell, if it has one.

    Only absolute links count: the presidential tables link the pollster cell
    to the poll's own write-up, while other pages link it to the pollster's
    Wikipedia article, which is not a source for the row.
    """
    if cell.tag is None:
        return None
    for link in cell.tag.find_all("a"):
        if not isinstance(link, Tag):
            continue
        href = _attr_text(link, "href")
        if href is not None and href.startswith(("http://", "https://")):
            return href
    return None


def parse_table_rows(
    info: TableInfo,
    *,
    candidates: Sequence[CandidateColumn] | None = None,
) -> tuple[list[ParsedPollRow], int]:
    """Parse one classified table's data rows into polls.

    Rows are skipped when the pollster cell is empty, the date cell does not
    parse, or no candidate column holds a number — which is how the colspan
    event rows (``'' | August 18, 2026 | Primary election held``) drop out.

    Wikipedia writes a pollster's variants (likely vs registered voters, "with
    leaners", a different set of candidates) as extra rows under one rowspanned
    pollster and date. They are the same poll, so within a table the **first**
    row for a ``(pollster, start, end)`` wins and the rest are counted.

    Args:
        info: A table from :func:`classify_table`.
        candidates: The table's candidate columns. Defaults to reading them
            from the header without party labels.

    Returns:
        ``(rows, variants_dropped)``.
    """
    columns = info.columns
    if candidates is None:
        candidates = candidate_columns(info.grid[columns.header_row])

    rows: list[ParsedPollRow] = []
    seen: set[tuple[str, date, date]] = set()
    variants_dropped = 0

    for grid_row in info.data_rows:
        pollster_cell = grid_row[columns.pollster]
        label, tags = clean_pollster_label(pollster_cell.text)
        if not label:
            continue
        date_range = parse_date_range(grid_row[columns.date].text)
        if date_range is None:
            continue
        fieldwork_start, fieldwork_end = date_range

        readings: list[CandidateReading] = []
        for column in candidates:
            if column.index >= len(grid_row):
                continue
            percentage = _reading_percentage(grid_row[column.index].text)
            if percentage is None:
                continue
            readings.append(
                CandidateReading(
                    party_name=column.party_name,
                    candidate_name=column.full_name,
                    percentage=percentage,
                )
            )
        if not readings:
            continue

        key = (label.casefold(), fieldwork_start, fieldwork_end)
        if key in seen:
            variants_dropped += 1
            continue
        seen.add(key)

        sample_text = (
            grid_row[columns.sample].text
            if columns.sample is not None and columns.sample < len(grid_row)
            else ""
        )
        sample_size, population = _parse_sample_cell(sample_text)
        rows.append(
            ParsedPollRow(
                pollster_label=label,
                pollster_tags=tags,
                fieldwork_start=fieldwork_start,
                fieldwork_end=fieldwork_end,
                date_label=_clean(grid_row[columns.date].text),
                sample_size=sample_size,
                population=population,
                readings=tuple(readings),
                source_url=_pollster_source_url(pollster_cell),
            )
        )

    return rows, variants_dropped


def parse_poll_tables(
    html: str,
    *,
    allow_party_labels: bool = False,
) -> list[ParsedTable]:
    """Parse every polling table on a page, in document order.

    Classifies each table and keeps the ones that yield at least one poll. No
    contest rule is applied here: primary sections, collapsed hypotheticals and
    aggregation tables all come back, carrying the heading path, the collapsed
    flag and the aggregation flag the contest layer selects on.

    Args:
        html: A fetched Wikipedia page.
        allow_party_labels: Passed to :func:`candidate_columns`; set for the
            House generic-ballot page.

    Returns:
        One :class:`ParsedTable` per table that parsed.
    """
    soup = BeautifulSoup(html, "lxml")
    parsed: list[ParsedTable] = []
    for table in soup.find_all("table"):
        if not isinstance(table, Tag) or table.find_parent("table") is not None:
            continue
        info = classify_table(table)
        if info is None:
            continue
        candidates = candidate_columns(
            info.grid[info.columns.header_row],
            allow_party_labels=allow_party_labels,
        )
        rows, variants_dropped = parse_table_rows(info, candidates=candidates)
        if not rows:
            continue
        unknown: list[str] = []
        for column in candidates:
            if column.party_name is None and column.letter not in unknown:
                unknown.append(column.letter)
        parsed.append(
            ParsedTable(
                info=info,
                headings=info.headings,
                candidates=tuple(candidates),
                matchup=matchup_label(candidates),
                rows=tuple(rows),
                variants_dropped=variants_dropped,
                unknown_suffixes=tuple(unknown),
            )
        )
    return parsed


# ── Legacy parser ─────────────────────────────────────────────────────────────
#
# Superseded by the structural layer above and removed once the contest layer
# lands (piece 7 of the US poll queue plan); still in use by the three wrapper
# scripts until then.


def party_for_header(cell: str, party_column_map: dict[str, str]) -> str | None:
    """Resolve a header cell to a canonical party name, or ``None``.

    Matches three header conventions found on US polling pages:
    - literal party labels (``"Democratic"``, ``"Dem"``) via ``party_column_map``;
    - plural aggregate labels (``"Democrats"`` / ``"Republicans"``), used by the
      poll-aggregation tables on the House/Senate election pages;
    - candidate names suffixed with their party letter (``"JD Vance(R)"``,
      ``"Kamala Harris (D)"``), used by presidential head-to-head matchup tables.
    """
    key = re.sub(r"\[[^\]]+\]", "", cell).strip().lower()
    if key in party_column_map:
        return party_column_map[key]
    if key in ("democrats", "republicans"):
        return "Democratic" if key == "democrats" else "Republican"
    suffix = re.search(r"\(([dr])\)$", key)
    if suffix:
        return "Democratic" if suffix.group(1) == "d" else "Republican"
    return None


def identify_party_columns(header_cells: list[str], party_column_map: dict[str, str]) -> dict[int, str]:
    """Map column indices to canonical party names from header cell text."""
    result: dict[int, str] = {}
    for idx, cell in enumerate(header_cells):
        party = party_for_header(cell, party_column_map)
        if party is not None:
            result[idx] = party
    return result


def _parse_percentage(raw: str) -> float | None:
    """Extract a percentage from a cell, stripping footnotes and ``%``."""
    cleaned = re.sub(r"\[[^\]]+\]", "", raw)
    cleaned = cleaned.replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_sample_size(raw: str) -> int | None:
    """Parse a sample size, stripping commas, footnotes, and non-digits."""
    cleaned = re.sub(r"\[[^\]]+\]", "", raw)
    cleaned = re.sub(r"[^\d]", "", cleaned)
    return int(cleaned) if cleaned.isdigit() else None


def _header_cells(row: Tag) -> list[str]:
    """Return cleaned text of a header row's cells."""
    return [_clean(cell.get_text()) for cell in row.find_all(["th", "td"])]


def detect_layout(header_cells: list[str]) -> tuple[int, int, int | None]:
    """Detect the pollster / date / sample column indices from header labels.

    US polling tables put these columns in different positions per shape
    (classic pollster lists, the House/Senate poll-aggregation table, and
    presidential matchup tables), so positions are resolved by header text:

    - pollster: header containing "source", "pollster", "firm", or "aggregat";
    - date: header containing "date" — when both a "Dates administered" and a
      "Dates updated" column exist (the aggregation table), "updated" wins:
      administered spans the whole cycle while updated is the snapshot date;
    - sample: "n" / "sample …".

    Falls back to the classic ``date=0, pollster=1`` layout when the headers
    name neither a date nor a pollster column.
    """
    date_col: int | None = None
    updated_col: int | None = None
    pollster_col: int | None = None
    sample_col: int | None = None

    for idx, cell in enumerate(header_cells):
        key = re.sub(r"\[[^\]]+\]", "", cell).strip().lower()
        if "date" in key:
            if "updated" in key:
                updated_col = idx
            elif date_col is None:
                date_col = idx
        elif pollster_col is None and any(
            word in key for word in ("source", "pollster", "firm", "aggregat")
        ):
            pollster_col = idx
        elif sample_col is None and (key == "n" or "sample" in key):
            sample_col = idx

    if updated_col is not None:
        date_col = updated_col
    if date_col is None:
        date_col = 0
    if pollster_col is None:
        pollster_col = 1 if date_col == 0 else 0

    return pollster_col, date_col, sample_col


def find_party_tables(
    soup: BeautifulSoup, party_column_map: dict[str, str]
) -> list[tuple[Tag, dict[int, str], int]]:
    """Locate every wikitable whose header carries both a Dem and a Rep column.

    US polling pages contain many wikitables (candidate lists, seat maps,
    results); the polling ones are those whose header row maps to at least one
    Democratic and one Republican column. Presidential pages split polling
    across many matchup tables, so all matches are returned in page order.
    Single-party tables (e.g. primary matchups, two ``(D)`` candidates) carry no
    two-party reading and are skipped.

    Returns a list of ``(table, party_cols, header_row_index)`` triples.
    """
    found: list[tuple[Tag, dict[int, str], int]] = []
    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        if "wikitable" not in (table.get("class") or []):
            continue
        rows = table.find_all("tr")
        for header_index, header_row in enumerate(rows[:3]):
            party_cols = identify_party_columns(_header_cells(header_row), party_column_map)
            canonical = set(party_cols.values())
            if "Democratic" in canonical and "Republican" in canonical:
                found.append((table, party_cols, header_index))
                break
    return found


def _parse_table(
    table: Tag, party_cols: dict[int, str], header_index: int
) -> list[ParsedUsPoll]:
    """Extract polls from one polling table using its header-detected layout."""
    all_rows = table.find_all("tr")
    pollster_col, date_col, sample_col = detect_layout(_header_cells(all_rows[header_index]))

    results: list[ParsedUsPoll] = []
    for row in all_rows[header_index + 1:]:
        cells = [_clean(td.get_text()) for td in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue

        date_range = parse_date_range(cells[date_col]) if len(cells) > date_col else None
        if date_range is None:
            continue
        fieldwork_start, fieldwork_end = date_range

        pollster_name = (
            _clean(re.sub(r"\[[^\]]+\]", "", cells[pollster_col])) if len(cells) > pollster_col else ""
        )
        if not pollster_name:
            continue

        sample_size = (
            _parse_sample_size(cells[sample_col])
            if sample_col is not None and len(cells) > sample_col
            else None
        )

        party_percentages: dict[str, float] = {}
        for col_idx, party_name in party_cols.items():
            if len(cells) > col_idx:
                pct = _parse_percentage(cells[col_idx])
                if pct is not None and pct >= 0:
                    party_percentages[party_name] = pct

        if not party_percentages:
            continue

        results.append(
            ParsedUsPoll(
                fieldwork_start=fieldwork_start,
                fieldwork_end=fieldwork_end,
                pollster_name=pollster_name,
                sample_size=sample_size,
                party_percentages=party_percentages,
            )
        )

    return results


def parse_polls(html: str, party_column_map: dict[str, str] | None = None) -> list[ParsedUsPoll]:
    """Parse national two-party polls from a US Wikipedia polling page.

    Finds every polling wikitable (both a Dem and a Rep column in its header),
    detects each table's column layout from its header labels, and extracts one
    :class:`ParsedUsPoll` per data row with a parseable date and at least one
    party percentage. Rows from all matching tables are concatenated; callers
    that need one reading per pollster+fieldwork (presidential matchup pages ask
    several head-to-heads in one poll) should pass the result through
    :func:`merge_polls_by_fieldwork`.
    """
    party_column_map = party_column_map or DEFAULT_PARTY_COLUMN_MAP
    soup = BeautifulSoup(html, "lxml")
    results: list[ParsedUsPoll] = []
    for table, party_cols, header_index in find_party_tables(soup, party_column_map):
        results.extend(_parse_table(table, party_cols, header_index))
    return results


def merge_polls_by_fieldwork(polls: list[ParsedUsPoll]) -> list[ParsedUsPoll]:
    """Merge polls sharing a pollster and fieldwork window into one reading.

    Presidential pages report one poll as several hypothetical matchup tables
    (Vance–Harris, Vance–Newsom, …); each parses as its own :class:`ParsedUsPoll`
    with the same pollster and dates. The DB dedupe key is
    ``(pollster, map, fieldwork dates)``, so committing them separately would keep
    only the first matchup. This merges such groups by averaging each party's
    percentages across the group's matchups — a crude but serviceable two-party
    reading. Groups of one (every other page shape) pass through unchanged.

    Order follows each group's first appearance.
    """
    grouped: dict[tuple[str, date, date], list[ParsedUsPoll]] = {}
    for poll in polls:
        key = (poll.pollster_name.strip().lower(), poll.fieldwork_start, poll.fieldwork_end)
        grouped.setdefault(key, []).append(poll)

    merged: list[ParsedUsPoll] = []
    for group in grouped.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        sums: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for poll in group:
            for party, pct in poll.party_percentages.items():
                sums[party] += pct
                counts[party] += 1
        first = group[0]
        merged.append(
            ParsedUsPoll(
                fieldwork_start=first.fieldwork_start,
                fieldwork_end=first.fieldwork_end,
                pollster_name=first.pollster_name,
                sample_size=first.sample_size,
                party_percentages={party: sums[party] / counts[party] for party in sums},
            )
        )
    return merged


# ── DB import ─────────────────────────────────────────────────────────────────


def pollster_identifier(pollster_name: str, suffix: str) -> str:
    """Derive a ``<slug><suffix>`` pollster identifier (e.g. ``yougov_us_house``)."""
    slug = re.sub(r"[^a-z0-9]+", "_", pollster_name.lower()).strip("_")
    return f"{slug}{suffix}"


def _ensure_pollster(db: Database, identifier: str, name: str) -> Pollster:
    """Return an existing Pollster or create one if absent."""
    existing = db.get_pollster_by_identifier(identifier)
    if existing is not None:
        return existing
    return db.add_pollster(name=name, identifier=identifier)


def commit_polls(
    db: Database,
    polls: list[ParsedUsPoll],
    *,
    map_name: str,
    pollster_suffix: str,
    pollster_label: str,
    source_url: str,
    dry_run: bool = False,
) -> dict[str, int]:
    """Insert Poll + national PollRow records for each parsed poll.

    Polls already present (matched by pollster, map, and fieldwork dates) are
    skipped. Party names not found in the DB are counted and skipped.

    Returns counts ``{"created", "skipped", "unknown_parties"}``.
    """
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    party_by_name = {p.name: p for p in db.get_all_parties()}
    pollster_cache: dict[str, Pollster] = {}
    created = skipped = unknown_parties = 0

    for parsed in polls:
        identifier = pollster_identifier(parsed.pollster_name, pollster_suffix)

        if not dry_run:
            if identifier not in pollster_cache:
                pollster_cache[identifier] = _ensure_pollster(
                    db, identifier, f"{parsed.pollster_name} ({pollster_label})"
                )
            pollster = pollster_cache[identifier]

            with db.session() as session:
                existing = session.execute(
                    select(Poll).where(
                        Poll.pollster_id == pollster.id,
                        Poll.map_id == poll_map.id,
                        Poll.fieldwork_start == parsed.fieldwork_start,
                        Poll.fieldwork_end == parsed.fieldwork_end,
                    )
                ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue

        rows_to_insert: list[tuple[int, float]] = []
        for party_name, pct in parsed.party_percentages.items():
            party = party_by_name.get(party_name)
            if party is None:
                unknown_parties += 1
                print(f"  WARNING: party not found in DB: {party_name!r}")
                continue
            rows_to_insert.append((party.id, pct))

        if not rows_to_insert:
            continue

        if dry_run:
            print(
                f"  [dry-run] {parsed.fieldwork_start}–{parsed.fieldwork_end} "
                f"{parsed.pollster_name!r} ({identifier}) n={parsed.sample_size} "
                f"parties={list(parsed.party_percentages.keys())}"
            )
            created += 1
            continue

        poll = db.add_poll(
            pollster_id=pollster.id,
            map_id=poll_map.id,
            fieldwork_start=parsed.fieldwork_start,
            fieldwork_end=parsed.fieldwork_end,
            sample_size=parsed.sample_size,
            source_url=source_url,
        )
        for party_id, pct in rows_to_insert:
            db.add_poll_row(poll.id, party_id, pct)  # region_id=None → national row
        created += 1

    return {"created": created, "skipped": skipped, "unknown_parties": unknown_parties}


def build_arg_parser(default_url: str, map_name: str) -> argparse.ArgumentParser:
    """Build the shared CLI parser for a US poll importer."""
    parser = argparse.ArgumentParser(description="Import US national polls from Wikipedia.")
    parser.add_argument("--url", default=default_url, help=f"Wikipedia polling page URL (default: {default_url})")
    parser.add_argument("--map-name", default=map_name, help=f"US map name (default: {map_name!r})")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing to the database")
    return parser


def run_importer(
    *,
    default_url: str,
    map_name: str,
    pollster_suffix: str,
    pollster_label: str,
) -> None:
    """Shared CLI body: fetch, parse, and commit a type's national polls."""
    parser = build_arg_parser(default_url, map_name)
    args = parser.parse_args()

    print(f"Fetching: {args.url}")
    html = fetch_html(args.url)
    polls = merge_polls_by_fieldwork(parse_polls(html))
    print(f"Parsed {len(polls)} national two-party polls from Wikipedia table")
    if not polls:
        print("No polls found — check the page structure or URL")
        return

    db = Database()
    counts = commit_polls(
        db,
        polls,
        map_name=args.map_name,
        pollster_suffix=pollster_suffix,
        pollster_label=pollster_label,
        source_url=args.url,
        dry_run=args.dry_run,
    )
    print(
        f"Done: created={counts['created']} skipped={counts['skipped']} "
        f"unknown_parties={counts['unknown_parties']}"
    )
    if args.dry_run:
        print("Dry-run: no data written")
