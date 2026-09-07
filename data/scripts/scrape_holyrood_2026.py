#!/usr/bin/env python3
"""Scrape the 2026 Scottish Parliament election results from Wikipedia.

Parses the rendered HTML of the "Results of the 2026 Scottish Parliament
election" article and emits the two JSON files the map front-end consumes:

* ``holyrood-2026.json`` — 73 constituency results (winner + per-party votes)
* ``holyrood-2026-list.json`` — 8 regions (list votes, constituency seats won,
  and the d'Hondt list-seat allocation computed here)

Two Wikipedia tables back this: ``wikitable`` index 0 is the constituency grid
(one row per seat, a colour swatch and a data cell per party), and index 1 is
the regional list vote totals.

The winner of a constituency is the single data cell carrying a
``style="background:…"`` attribute. Bold is *not* a reliable signal — several
rows carry stray bold on a losing party's number, and winning cells sometimes
split the bold across the candidate name and the vote figure.

Usage from CLI:
    python scripts/scrape_holyrood_2026.py --dry-run

    python scripts/scrape_holyrood_2026.py \
        --out-dir old_data/files/holyrood
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from polls.importers.wikipedia_common import clean_text, fetch_html

# ── Constants ─────────────────────────────────────────────────────────────────

WIKI_URL = (
    "https://en.wikipedia.org/wiki/"
    "Results_of_the_2026_Scottish_Parliament_election"
)

DEFAULT_MAP_NAME = "Scottish Parliament Constituencies 2026"

DEFAULT_OUT_DIR = (
    Path(__file__).resolve().parent.parent / "old_data" / "files" / "holyrood"
)

CONSTITUENCY_FILENAME = "holyrood-2026.json"
LIST_FILENAME = "holyrood-2026-list.json"

# Number of constituency rows expected in the constituency table.
EXPECTED_CONSTITUENCIES = 73

# Number of regions expected in the regional list-vote table.
EXPECTED_REGIONS = 8

# List seats allocated per region by d'Hondt.
LIST_SEATS_PER_REGION = 7

# Number of party columns in the constituency and region tables.
EXPECTED_PARTY_COLUMNS = 7

# Lowercased Wikipedia column header → our canonical party key. The
# constituency table and the region table disagree on two headers ("Greens"
# vs "Green", "Lib Dem" vs "Lib Dems"), so both spellings are accepted.
HEADER_TO_PARTY_KEY: dict[str, str] = {
    "snp": "snp",
    "labour": "labour",
    "conservative": "conservative",
    "greens": "green",  # constituency table
    "green": "green",  # also accept
    "lib dem": "libdems",  # constituency table
    "lib dems": "libdems",  # region table
    "reform uk": "reform",
    "other": "others",
}

# Header text that names a non-party column to be skipped entirely.
SKIPPED_REGION_HEADERS = frozenset({"total"})

# The "Other" bucket is excluded from d'Hondt: it is not a single party, and
# including it produces a demonstrably wrong allocation.
OTHERS_KEY = "others"

# Seats in the 2026 map whose names contain this marker are regional list
# placeholder rows, not constituencies.
LIST_SEAT_MARKER = " List "

# How far a region's party votes may fall short of its stated "Total" before
# the row is rejected, as a fraction of that total. The checksum exists to
# catch a *column shift*, which misplaces a whole column — the smallest of
# them ("Other") is several percent of a region's vote. Wikipedia's own
# arithmetic is occasionally out by a handful of votes, which is not worth
# failing a run over, so small gaps are reported rather than raised.
TOTAL_TOLERANCE_FRACTION = 0.005

_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")
_VOTES_RE = re.compile(r"^(.*?)\s*([\d,]+)$")
_OTHER_VOTES_RE = re.compile(r",\s*([\d,]+)\s*\)")


# ── Parsed row containers ─────────────────────────────────────────────────────


@dataclass
class PartyResult:
    """A single party's result within one constituency.

    Attributes:
        key: Canonical party key (e.g. ``"snp"``, ``"others"``).
        candidate_name: Candidate name, footnote markers stripped. For the
            ``others`` bucket this is the first listed candidate.
        votes: Total votes. For ``others`` this is the sum across all listed
            minor candidates.
    """

    key: str
    candidate_name: str
    votes: int


@dataclass
class ConstituencyRow:
    """One parsed row of the constituency results table.

    Attributes:
        seat_name: Constituency name as printed on Wikipedia.
        winner_key: Party key of the winning candidate.
        parties: Results for every party that stood and reported a vote total.
        others_missing: True if an "Other" candidate was listed with no vote
            figure at all, so the ``others`` key was omitted entirely.
        others_partial: True if the "Other" cell listed several candidates and
            only some carried a vote figure, so the ``others`` total is an
            under-count.
    """

    seat_name: str
    winner_key: str
    parties: list[PartyResult] = field(default_factory=list)
    others_missing: bool = False
    others_partial: bool = False


@dataclass
class RegionRow:
    """One parsed row of the regional list-vote table.

    Attributes:
        region_name: Region name as printed on Wikipedia.
        votes: Mapping of party key → regional list votes.
        total_discrepancy: Stated "Total" minus the sum of the party columns.
            Non-zero means Wikipedia's own arithmetic is out; anything beyond
            :data:`TOTAL_TOLERANCE_FRACTION` is rejected at parse time.
    """

    region_name: str
    votes: dict[str, int]
    total_discrepancy: int = 0


@dataclass
class ScrapeResult:
    """Everything parsed from the page, before DB name resolution.

    Attributes:
        constituencies: The 73 parsed constituency rows.
        regions: The 8 parsed regional list-vote rows.
    """

    constituencies: list[ConstituencyRow]
    regions: list[RegionRow]

    @property
    def others_without_votes(self) -> list[str]:
        """Seats where the ``others`` key was omitted for want of a figure."""
        return [row.seat_name for row in self.constituencies if row.others_missing]

    @property
    def others_partial(self) -> list[str]:
        """Seats whose ``others`` total under-counts an unnumbered candidate."""
        return [row.seat_name for row in self.constituencies if row.others_partial]


# ── Text helpers ──────────────────────────────────────────────────────────────


def normalize_name(value: str) -> str:
    """Normalise a name string for fuzzy matching.

    Lowercases the value, replaces ``&`` with ``and``, and strips all
    non-alphanumeric characters, so that "Caithness, Sutherland and Ross" and
    "Caithness Sutherland & Ross" compare as equal.

    Args:
        value: The raw name string to normalise.

    Returns:
        Normalised lowercase alphanumeric string with no whitespace or
        punctuation.
    """
    value = value.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", value)


def strip_footnotes(value: str) -> str:
    """Remove Wikipedia footnote markers from a string.

    Rendered ``<sup class="mw-ref">`` markers become bracketed text such as
    ``[a]``. Because cell text is extracted with ``separator=" "``, the marker
    may arrive spaced out as ``[ a ]`` — the pattern tolerates both.

    Critically this must run over the *whole* cell text before the trailing
    vote number is split off: some cells place the footnote **after** the
    number (``"Arthur Keith 3,315 [ a ]"``), which would otherwise defeat the
    trailing-number match.

    Args:
        value: Raw cell text, typically already whitespace-normalised.

    Returns:
        The text with all bracketed footnote markers removed and surrounding
        whitespace collapsed.
    """
    return clean_text(_FOOTNOTE_RE.sub(" ", value))


def cell_text(cell: Tag) -> str:
    """Extract whitespace-normalised, footnote-free text from a table cell.

    Args:
        cell: A ``<td>`` or ``<th>`` element.

    Returns:
        The cell's text with ``<br/>`` boundaries preserved as spaces,
        whitespace collapsed, and footnote markers removed.
    """
    return strip_footnotes(clean_text(cell.get_text(separator=" ")))


def parse_int(value: str) -> int:
    """Parse a comma-formatted integer such as ``"11,974"``.

    The *whole* string must be the number. Stripping non-digits instead would
    silently fuse a cell like ``"86,809 32.4 %"`` into ``86809324`` — a
    plausible-looking figure that would flow into the vote totals and the seat
    allocation with no error, so trailing text is rejected outright.

    Args:
        value: Digit string, optionally containing thousands separators.

    Returns:
        The integer value.

    Raises:
        ValueError: If *value* is not composed solely of digits and thousands
            separators, or contains no digit at all.
    """
    text = value.strip()
    if re.fullmatch(r"[\d,]+", text) is None or not any(c.isdigit() for c in text):
        raise ValueError(f"Expected an integer, got {value!r}")
    return int(text.replace(",", ""))


# ── Cell classification ───────────────────────────────────────────────────────


def _attr_str(cell: Tag, name: str) -> str | None:
    """Return an attribute of *cell* as a string, or None.

    BeautifulSoup types attribute values as ``str | list[str] | None`` because
    some HTML attributes are multi-valued; this narrows to the single-valued
    case the callers care about.

    Args:
        cell: The element to read the attribute from.
        name: Attribute name.

    Returns:
        The attribute value if it is a plain string, otherwise None.
    """
    value = cell.get(name)
    return value if isinstance(value, str) else None


def is_swatch_cell(cell: Tag) -> bool:
    """Return whether *cell* is a party colour swatch rather than a data cell.

    Swatches are identified structurally — ``width="1"`` **and** a ``bgcolor``
    attribute — never by position, because one row is missing a trailing data
    cell and positional indexing would silently misalign it.

    Args:
        cell: A ``<td>`` or ``<th>`` element from a constituency row.

    Returns:
        True if the cell is a colour swatch.
    """
    return _attr_str(cell, "width") == "1" and cell.has_attr("bgcolor")


def has_background_style(cell: Tag) -> bool:
    """Return whether *cell* carries an inline ``background`` style.

    This is the winner marker in the constituency table. It is deliberately
    colour-agnostic: winning cells appear in at least five different shades,
    one per party.

    Args:
        cell: A data cell from a constituency row.

    Returns:
        True if the cell has a ``style`` attribute mentioning ``background``.
    """
    style = _attr_str(cell, "style")
    return style is not None and "background" in style


def row_cells(row: Tag) -> list[Tag]:
    """Return the ``<td>``/``<th>`` children of a table row.

    Args:
        row: A ``<tr>`` element.

    Returns:
        List of cell elements in document order.
    """
    return [c for c in row.find_all(["td", "th"]) if isinstance(c, Tag)]


# ── Table parsing ─────────────────────────────────────────────────────────────


def find_wikitables(soup: BeautifulSoup) -> list[Tag]:
    """Return all ``wikitable`` elements on the page in document order.

    Args:
        soup: Parsed BeautifulSoup tree of the results article.

    Returns:
        List of ``<table class="wikitable">`` elements.
    """
    return [t for t in soup.find_all("table", class_="wikitable") if isinstance(t, Tag)]


def parse_header_party_keys(header_row: Tag) -> list[str]:
    """Map a results-table header row onto ordered party keys.

    The first header cell names the seat/region column and is skipped; every
    remaining cell must map through :data:`HEADER_TO_PARTY_KEY`. Headers listed
    in :data:`SKIPPED_REGION_HEADERS` (currently just "Total") are dropped.

    Args:
        header_row: The ``<tr>`` holding the table's column headers.

    Returns:
        Party keys in column order, e.g.
        ``["snp", "labour", "conservative", "green", "libdems", "reform",
        "others"]``.

    Raises:
        ValueError: If a header cannot be mapped to a party key, or if the row
            does not yield exactly :data:`EXPECTED_PARTY_COLUMNS` keys.
    """
    headers = [clean_text(c.get_text(separator=" ")) for c in row_cells(header_row)]
    if not headers:
        raise ValueError("Header row contains no cells")

    keys: list[str] = []
    for raw in headers[1:]:
        lowered = strip_footnotes(raw).lower()
        if lowered in SKIPPED_REGION_HEADERS:
            continue
        key = HEADER_TO_PARTY_KEY.get(lowered)
        if key is None:
            raise ValueError(
                f"Unrecognised party column header {raw!r}; "
                f"table layout has changed (headers={headers})"
            )
        keys.append(key)

    if len(keys) != EXPECTED_PARTY_COLUMNS:
        raise ValueError(
            f"Expected {EXPECTED_PARTY_COLUMNS} party columns, got "
            f"{len(keys)}: {keys} (headers={headers})"
        )
    return keys


def parse_party_cell(text: str, seat_name: str, party_key: str) -> PartyResult:
    """Parse a named-party data cell into a candidate name and vote total.

    The cell renders as the candidate name, a ``<br/>``, then the vote figure,
    which ``get_text(separator=" ")`` flattens to ``"Jack Middleton 11,974"``.
    The trailing number is the vote total; everything before it is the name.

    Args:
        text: Footnote-stripped, whitespace-normalised cell text.
        seat_name: Constituency name, used only for error messages.
        party_key: Canonical party key, used only for error messages.

    Returns:
        The parsed :class:`PartyResult`.

    Raises:
        ValueError: If no trailing vote number can be extracted.
    """
    match = _VOTES_RE.match(text)
    if match is None:
        raise ValueError(
            f"No vote total found in {party_key!r} cell for {seat_name!r}: {text!r}"
        )
    return PartyResult(
        key=party_key,
        candidate_name=clean_text(match.group(1)),
        votes=parse_int(match.group(2)),
    )


def parse_other_cell(text: str) -> tuple[PartyResult | None, bool]:
    """Parse an "Other" data cell into a summed minor-party result.

    Other cells list one or more minor candidates in the form
    ``Name (Party, votes)``, multi-candidate cells having been expanded from
    ``{{ubl|…}}`` into a flat run of such groups. All vote figures are summed
    into a single ``others`` bucket; the first candidate's name is kept as the
    representative name, matching the convention in the 2021 data files.

    A small number of cells name a candidate but report no vote figure. Those
    yield None rather than raising, so the caller can omit the ``others`` key
    and carry on.

    Args:
        text: Footnote-stripped, whitespace-normalised cell text.

    Returns:
        A ``(result, complete)`` tuple. ``result`` is None when the cell holds
        no parseable vote figure at all. ``complete`` is False when *some* of
        the listed candidates carried a figure and others did not, so the
        caller can report a partial total rather than under-counting silently.
    """
    figures = _OTHER_VOTES_RE.findall(text)
    if not figures:
        return None, False
    total = sum(parse_int(f) for f in figures)
    name = clean_text(text.split("(")[0])
    # Every candidate is introduced by "(", so a shortfall means at least one
    # was listed without a vote figure and its votes are missing from the sum.
    complete = text.count("(") == len(figures)
    return PartyResult(key=OTHERS_KEY, candidate_name=name, votes=total), complete


def pair_columns_with_cells(
    cells: list[Tag],
    party_keys: list[str],
    seat_name: str,
) -> list[tuple[str, Tag | None]]:
    """Pair every party column with its data cell, anchored on the swatches.

    Each party is laid out as a colour swatch followed by a data cell. One real
    row omits its *trailing* data cell, and nothing stops a future edit omitting
    one mid-row, so columns are anchored to the swatch stream rather than to a
    cell's position in a swatch-filtered list: positional mapping would silently
    shift every party after a gap and could report the wrong winner.

    A swatch immediately followed by another swatch (or by nothing) means that
    column has no data cell.

    Args:
        cells: Row cells *after* the leading seat cell.
        party_keys: Ordered party keys from :func:`parse_header_party_keys`.
        seat_name: Constituency name, used only for error messages.

    Returns:
        One ``(party_key, data_cell_or_None)`` pair per party column, in order.

    Raises:
        ValueError: If a data cell appears before any swatch, or the row does
            not carry exactly one swatch per party column.
    """
    slots: list[Tag | None] = []
    index = 0
    while index < len(cells):
        if not is_swatch_cell(cells[index]):
            raise ValueError(
                f"Row {seat_name!r}: expected a colour swatch at cell "
                f"{index + 1}, found a data cell"
            )
        following = cells[index + 1] if index + 1 < len(cells) else None
        if following is not None and not is_swatch_cell(following):
            slots.append(following)
            index += 2
        else:
            slots.append(None)
            index += 1

    if len(slots) != len(party_keys):
        raise ValueError(
            f"Row {seat_name!r} has {len(slots)} party columns, expected "
            f"{len(party_keys)}"
        )
    return list(zip(party_keys, slots))


def parse_constituency_row(
    row: Tag,
    party_keys: list[str],
) -> ConstituencyRow:
    """Parse one data row of the constituency results table.

    Args:
        row: A ``<tr>`` from the constituency table body.
        party_keys: Ordered party keys from :func:`parse_header_party_keys`.

    Returns:
        The parsed :class:`ConstituencyRow`, whose ``others_missing`` and
        ``others_partial`` flags record any "Other" votes that could not be
        read.

    Raises:
        ValueError: If the row has no cells, does not carry one swatch per
            party column, does not have exactly one background-styled data
            cell, has a named-party cell with text but no extractable vote
            total, or names a winner that is not the leading party.
    """
    cells = row_cells(row)
    if not cells:
        raise ValueError("Constituency row contains no cells")

    seat_name = cell_text(cells[0])
    columns = pair_columns_with_cells(cells[1:], party_keys, seat_name)

    winners = [
        key for key, cell in columns if cell is not None and has_background_style(cell)
    ]
    if len(winners) != 1:
        raise ValueError(
            f"Row {seat_name!r} has {len(winners)} background-styled data "
            f"cells, expected exactly 1 (found {winners})"
        )

    parties: list[PartyResult] = []
    others_missing = False
    others_partial = False
    for key, cell in columns:
        if cell is None:
            continue
        text = cell_text(cell)
        if not text:
            # Party did not stand in this seat.
            continue
        if key == OTHERS_KEY:
            other, complete = parse_other_cell(text)
            if other is None:
                others_missing = True
                continue
            others_partial = not complete
            parties.append(other)
        else:
            parties.append(parse_party_cell(text, seat_name, key))

    _check_winner_leads(seat_name, winners[0], parties)

    return ConstituencyRow(
        seat_name=seat_name,
        winner_key=winners[0],
        parties=parties,
        others_missing=others_missing,
        others_partial=others_partial,
    )


def _check_winner_leads(
    seat_name: str,
    winner_key: str,
    parties: list[PartyResult],
) -> None:
    """Assert the styled winner is also the party with the most votes.

    Under first-past-the-post these cannot disagree, so a mismatch means the
    row was misread — which is the entire failure this scraper exists to
    correct. The ``others`` bucket is excluded from the comparison: it
    aggregates several candidates and so is not a single contender.

    Args:
        seat_name: Constituency name, used only for error messages.
        winner_key: Party key taken from the background-styled cell.
        parties: Parsed results for the row.

    Raises:
        ValueError: If a named party out-polls the declared winner.
    """
    contenders = [p for p in parties if p.key != OTHERS_KEY]
    if not contenders:
        return
    leader = max(contenders, key=lambda p: p.votes)
    if winner_key != leader.key:
        raise ValueError(
            f"Row {seat_name!r}: winner is marked {winner_key!r} but "
            f"{leader.key!r} polled most ({leader.votes:,})"
        )


def parse_constituency_table(table: Tag) -> list[ConstituencyRow]:
    """Parse the full constituency results table.

    Args:
        table: The ``wikitable`` holding one row per constituency.

    Returns:
        One :class:`ConstituencyRow` per constituency.

    Raises:
        ValueError: If the table has no rows, the header does not map onto the
            expected party columns, the body does not yield exactly
            :data:`EXPECTED_CONSTITUENCIES` rows, or two rows share a name.
    """
    rows = [r for r in table.find_all("tr") if isinstance(r, Tag)]
    if not rows:
        raise ValueError("Constituency table contains no rows")

    party_keys = parse_header_party_keys(rows[0])
    parsed = [parse_constituency_row(row, party_keys) for row in rows[1:]]

    if len(parsed) != EXPECTED_CONSTITUENCIES:
        raise ValueError(
            f"Expected {EXPECTED_CONSTITUENCIES} constituency rows, got {len(parsed)}"
        )

    # The payload is keyed by seat name, so a duplicate would silently collapse
    # two constituencies into one entry.
    names = {row.seat_name for row in parsed}
    if len(names) != len(parsed):
        raise ValueError(
            f"Constituency table has {len(parsed)} rows but only "
            f"{len(names)} distinct seat names"
        )
    return parsed


def parse_region_table(table: Tag) -> list[RegionRow]:
    """Parse the regional list-vote table.

    Args:
        table: The ``wikitable`` holding one row per electoral region.

    Returns:
        One :class:`RegionRow` per region, with the "Total" column dropped.

    Raises:
        ValueError: If the table has no rows, the header does not map onto the
            expected party columns, a row's cell count does not match the
            header, a row's party votes do not sum to its stated total, or the
            body does not yield exactly :data:`EXPECTED_REGIONS` rows.
    """
    rows = [r for r in table.find_all("tr") if isinstance(r, Tag)]
    if not rows:
        raise ValueError("Region table contains no rows")

    header_cells = [clean_text(c.get_text(separator=" ")) for c in row_cells(rows[0])]
    party_keys = parse_header_party_keys(rows[0])

    # Column indices to read, skipping the seat column and any "Total" column.
    value_indices = [
        i
        for i, raw in enumerate(header_cells)
        if i > 0 and strip_footnotes(raw).lower() not in SKIPPED_REGION_HEADERS
    ]
    total_indices = [
        i
        for i, raw in enumerate(header_cells)
        if i > 0 and strip_footnotes(raw).lower() in SKIPPED_REGION_HEADERS
    ]

    parsed: list[RegionRow] = []
    for row in rows[1:]:
        cells = row_cells(row)
        # Strict equality: unlike poll tables, region rows have no legitimate
        # short form. A row missing one cell would shift the columns left and
        # silently file the "Total" figure as another party's votes.
        if len(cells) != len(header_cells):
            raise ValueError(
                f"Region row has {len(cells)} cells, expected "
                f"{len(header_cells)} to match the header"
            )
        region_name = cell_text(cells[0])
        votes = {
            party_keys[n]: parse_int(cell_text(cells[i]))
            for n, i in enumerate(value_indices)
        }
        # The "Total" column is a free checksum on the seven party columns.
        discrepancy = 0
        for index in total_indices:
            stated = parse_int(cell_text(cells[index]))
            discrepancy = stated - sum(votes.values())
            if abs(discrepancy) > stated * TOTAL_TOLERANCE_FRACTION:
                raise ValueError(
                    f"Region {region_name!r} party votes sum to "
                    f"{sum(votes.values()):,} but the table states {stated:,} "
                    f"— a gap of {discrepancy:,}, too large to be a rounding "
                    f"error, so the columns are probably misaligned"
                )
        parsed.append(
            RegionRow(
                region_name=region_name,
                votes=votes,
                total_discrepancy=discrepancy,
            )
        )

    if len(parsed) != EXPECTED_REGIONS:
        raise ValueError(f"Expected {EXPECTED_REGIONS} region rows, got {len(parsed)}")
    return parsed


def parse_page(html: str) -> ScrapeResult:
    """Parse both results tables out of the article HTML.

    Args:
        html: Rendered HTML of the results article.

    Returns:
        The parsed :class:`ScrapeResult`.

    Raises:
        ValueError: If the page does not contain at least two ``wikitable``
            elements, or either table fails its structural checks.
    """
    soup = BeautifulSoup(html, "lxml")
    tables = find_wikitables(soup)
    if len(tables) < 2:
        raise ValueError(
            f"Expected at least 2 wikitables on the page, found {len(tables)}"
        )

    return ScrapeResult(
        constituencies=parse_constituency_table(tables[0]),
        regions=parse_region_table(tables[1]),
    )


# ── Seat allocation ───────────────────────────────────────────────────────────


def dhondt_allocate(
    regional_votes: dict[str, int],
    constituency_seats_won: dict[str, int],
    total_list_seats: int,
) -> dict[str, int]:
    """Allocate regional list seats using the d'Hondt method.

    Each round awards a seat to the party with the highest quotient
    ``votes / (seats_already_held + 1)``, where seats already held includes
    constituency seats — this is what makes the Additional Member System
    broadly proportional.

    The ``others`` bucket must be excluded by the caller: it aggregates several
    unrelated minor parties, and treating it as one party distorts the result.

    Args:
        regional_votes: Mapping of party_key → list vote total.
        constituency_seats_won: Mapping of party_key → constituency seats won.
        total_list_seats: Number of list seats to allocate (normally 7).

    Returns:
        Mapping of party_key → list seats awarded, omitting parties that won
        no list seats.
    """
    list_seats: dict[str, int] = {p: 0 for p in regional_votes}
    total_seats: dict[str, int] = dict(constituency_seats_won)
    for _ in range(total_list_seats):
        best = max(
            regional_votes,
            key=lambda p: regional_votes[p] / (total_seats.get(p, 0) + 1),
        )
        list_seats[best] = list_seats.get(best, 0) + 1
        total_seats[best] = total_seats.get(best, 0) + 1
    return {p: s for p, s in list_seats.items() if s > 0}


# ── Name resolution ───────────────────────────────────────────────────────────


@dataclass
class MapIndex:
    """Canonical seat and region names for a map, indexed for fuzzy matching.

    Attributes:
        region_by_key: Normalised region name → canonical region name.
        region_for_seat: Normalised seat name → canonical region name.
    """

    region_by_key: dict[str, str]
    region_for_seat: dict[str, str]


def build_map_index(db: Database, map_name: str) -> MapIndex:
    """Load canonical seat and region names for *map_name* from the database.

    The 2026 map holds regional list placeholder seats alongside the real
    constituencies (129 rows in total), so rows whose names contain
    :data:`LIST_SEAT_MARKER` are filtered out. No seat count is asserted.

    Args:
        db: Open database connection.
        map_name: Exact name of the map to load.

    Returns:
        A populated :class:`MapIndex`.

    Raises:
        ValueError: If no map of that name exists.
    """
    map_row = db.get_map_by_name(map_name)
    if map_row is None:
        raise ValueError(f"Map {map_name!r} not found in the database")

    regions = db.get_regions_for_map(map_row.id)
    region_name_by_id = {region.id: region.name for region in regions}
    region_by_key = {normalize_name(region.name): region.name for region in regions}

    region_for_seat: dict[str, str] = {}
    for seat in db.get_seats_for_map(map_row.id):
        if LIST_SEAT_MARKER in seat.seat_name:
            continue
        region_name = region_name_by_id.get(seat.region_id) if seat.region_id else None
        if region_name is None:
            continue
        region_for_seat[normalize_name(seat.seat_name)] = region_name

    return MapIndex(region_by_key=region_by_key, region_for_seat=region_for_seat)


def resolve_region_for_seat(index: MapIndex, seat_name: str) -> str:
    """Return the canonical region name owning *seat_name*.

    Args:
        index: Map index built by :func:`build_map_index`.
        seat_name: Constituency name as scraped from Wikipedia.

    Returns:
        The canonical region name.

    Raises:
        ValueError: If the seat cannot be matched against the map.
    """
    region = index.region_for_seat.get(normalize_name(seat_name))
    if region is None:
        raise ValueError(
            f"Constituency {seat_name!r} does not match any seat in the map"
        )
    return region


def resolve_region_name(index: MapIndex, region_name: str) -> str:
    """Return the canonical database name for a scraped region name.

    Wikipedia's region labels can pick up markup artefacts, so the canonical
    database spelling is always preferred for the output keys.

    Args:
        index: Map index built by :func:`build_map_index`.
        region_name: Region name as scraped from Wikipedia.

    Returns:
        The canonical region name.

    Raises:
        ValueError: If the region cannot be matched against the map.
    """
    canonical = index.region_by_key.get(normalize_name(region_name))
    if canonical is None:
        raise ValueError(f"Region {region_name!r} does not match any region in the map")
    return canonical


# ── Payload building ──────────────────────────────────────────────────────────


def build_constituency_payload(
    rows: list[ConstituencyRow],
) -> dict[str, dict[str, object]]:
    """Build the ``holyrood-2026.json`` payload.

    Args:
        rows: Parsed constituency rows.

    Returns:
        Mapping of seat name → ``{"seatInfo": …, "partyInfo": …}``. The
        ``electorate`` field is always None: the results article does not
        publish electorate figures.
    """
    payload: dict[str, dict[str, object]] = {}
    for row in rows:
        party_info = {
            party.key: {"total": party.votes, "name": party.candidate_name}
            for party in row.parties
        }
        payload[row.seat_name] = {
            "seatInfo": {"current": row.winner_key, "electorate": None},
            "partyInfo": party_info,
        }
    return payload


def count_constituency_seats(
    rows: list[ConstituencyRow],
    index: MapIndex,
) -> dict[str, dict[str, int]]:
    """Tally constituency seats won per region.

    Args:
        rows: Parsed constituency rows.
        index: Map index used to resolve each seat to its region.

    Returns:
        Mapping of canonical region name → ``{party_key: seats_won}``.

    Raises:
        ValueError: If any constituency fails to resolve against the map.
    """
    tally: dict[str, dict[str, int]] = {}
    for row in rows:
        region = resolve_region_for_seat(index, row.seat_name)
        per_region = tally.setdefault(region, {})
        per_region[row.winner_key] = per_region.get(row.winner_key, 0) + 1
    return tally


def build_list_payload(
    regions: list[RegionRow],
    seats_by_region: dict[str, dict[str, int]],
    index: MapIndex,
) -> dict[str, dict[str, object]]:
    """Build the ``holyrood-2026-list.json`` payload.

    Args:
        regions: Parsed regional list-vote rows.
        seats_by_region: Constituency seats won, keyed by canonical region name.
        index: Map index used to canonicalise the region names.

    Returns:
        Mapping of canonical region name → ``{"regionVotes": …, "seats": …,
        "constituencySeatsWon": …}``.

    Raises:
        ValueError: If any region fails to resolve against the map.
    """
    payload: dict[str, dict[str, object]] = {}
    for region in regions:
        canonical = resolve_region_name(index, region.region_name)
        constituency_seats = seats_by_region.get(canonical, {})
        named_party_votes = {
            key: votes for key, votes in region.votes.items() if key != OTHERS_KEY
        }
        payload[canonical] = {
            "regionVotes": dict(region.votes),
            "seats": dhondt_allocate(
                named_party_votes,
                constituency_seats,
                LIST_SEATS_PER_REGION,
            ),
            "constituencySeatsWon": dict(constituency_seats),
        }
    return payload


# ── CLI ───────────────────────────────────────────────────────────────────────


def _report(
    result: ScrapeResult,
    constituency_payload: dict[str, dict[str, object]],
    list_payload: dict[str, dict[str, object]],
) -> None:
    """Print a human-readable summary of the scrape.

    Args:
        result: The parsed scrape result.
        constituency_payload: Built constituency payload.
        list_payload: Built regional list payload.
    """
    entries = [p for row in result.constituencies for p in row.parties]
    others = [p for p in entries if p.key == OTHERS_KEY]
    unnamed = sum(1 for p in entries if not p.candidate_name)

    print(f"Constituencies: {len(constituency_payload)}")
    print(f"Regions: {len(list_payload)}")
    print(f"Party entries: {len(entries)}")
    print(f"Total constituency votes: {sum(p.votes for p in entries):,}")
    print(f"Entries missing a candidate name: {unnamed}")
    others_votes = sum(p.votes for p in others)
    print(f"Seats with an 'others' entry: {len(others)} ({others_votes:,} votes)")

    totals: dict[str, int] = {}
    for block in list_payload.values():
        for field_name in ("seats", "constituencySeatsWon"):
            source = block[field_name]
            if not isinstance(source, dict):
                raise TypeError(f"Expected {field_name!r} to be a dict")
            for party, count in source.items():
                totals[party] = totals.get(party, 0) + int(count)

    print("\nSeat totals:")
    for party, count in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {party:<13} {count:>3}")
    print(f"  {'TOTAL':<13} {sum(totals.values()):>3}")

    if result.others_without_votes:
        print(
            f"\nWarning: {len(result.others_without_votes)} seat(s) list an "
            f"'Other' candidate with no vote figure; 'others' key omitted:"
        )
        for seat in result.others_without_votes:
            print(f"  - {seat}")

    if result.others_partial:
        print(
            f"\nWarning: {len(result.others_partial)} seat(s) list an 'Other' "
            f"candidate with no vote figure alongside others that have one, so "
            f"the 'others' total under-counts:"
        )
        for seat in result.others_partial:
            print(f"  - {seat}")

    unbalanced = [r for r in result.regions if r.total_discrepancy]
    if unbalanced:
        print(
            f"\nWarning: {len(unbalanced)} region(s) whose party votes do not "
            f"match the stated 'Total' (Wikipedia's arithmetic, within "
            f"tolerance):"
        )
        for region in unbalanced:
            print(f"  - {region.region_name}: off by {region.total_discrepancy:+,}")


def main() -> None:
    """CLI entry point for the 2026 Holyrood results scraper.

    Fetches and parses the Wikipedia results article, resolves seat and region
    names against the database map, computes the d'Hondt list allocation, and
    — unless ``--dry-run`` is passed — writes the two JSON output files.

    CLI arguments:
        --url (str, optional): Results article URL; defaults to
            :data:`WIKI_URL`.
        --map-name (str, optional): Map used for seat and region resolution;
            defaults to :data:`DEFAULT_MAP_NAME`.
        --out-dir (Path, optional): Directory to write the JSON files into;
            defaults to ``data/old_data/files/holyrood/``.
        --dry-run (flag): If set, parse and print only — no files are written.
    """
    parser = argparse.ArgumentParser(
        description="Scrape the 2026 Scottish Parliament election results"
    )
    parser.add_argument("--url", default=WIKI_URL, help="Results article URL")
    parser.add_argument(
        "--map-name",
        default=DEFAULT_MAP_NAME,
        help="Map name used for seat and region resolution",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory to write the JSON output files into",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print only, don't write any files",
    )
    args = parser.parse_args()

    html = fetch_html(args.url)
    result = parse_page(html)

    db = Database()
    index = build_map_index(db, args.map_name)

    constituency_payload = build_constituency_payload(result.constituencies)
    seats_by_region = count_constituency_seats(result.constituencies, index)
    list_payload = build_list_payload(result.regions, seats_by_region, index)

    _report(result, constituency_payload, list_payload)

    if args.dry_run:
        print("\nDry run — no files written.")
        return

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    constituency_path = out_dir / CONSTITUENCY_FILENAME
    list_path = out_dir / LIST_FILENAME

    # Serialise both payloads before writing either: the two files are imported
    # as a pair, so a failure part-way through must not leave a fresh
    # constituency file beside a stale list file.
    documents = [
        (path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        for path, payload in (
            (constituency_path, constituency_payload),
            (list_path, list_payload),
        )
    ]
    for path, text in documents:
        path.write_text(text, encoding="utf-8")

    print(f"\nWrote {constituency_path}")
    print(f"Wrote {list_path}")


if __name__ == "__main__":
    main()
