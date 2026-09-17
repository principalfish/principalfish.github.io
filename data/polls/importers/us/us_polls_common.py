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
Wikipedia collapsed it, and strict pollster/date column detection. The contest
layer builds seat- and matchup-scoped rows on top of it; the legacy functions
stay only until the three wrapper scripts move across.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
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
