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
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
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
