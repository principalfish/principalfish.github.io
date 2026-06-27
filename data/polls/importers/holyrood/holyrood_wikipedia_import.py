#!/usr/bin/env python3
"""Import Scottish Parliament (Holyrood) constituency voting intention polls from Wikipedia.

Scrapes the Wikipedia opinion-polling page for the 2026 Scottish Parliament
election, parses the constituency VI table, and inserts Poll + PollRow records
into the database linked to the Holyrood constituency map.

Usage:
  python data/polls/importers/holyrood_wikipedia_import.py --dry-run
  python data/polls/importers/holyrood_wikipedia_import.py
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Tag
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster

WIKI_URL = (
    "https://en.wikipedia.org/wiki/"
    "Opinion_polling_for_the_2026_Scottish_Parliament_election"
)

DEFAULT_MAP_NAME = "Scottish Parliament Constituencies 2026"

# Wikipedia column header (lowercased) → canonical party name in DB
PARTY_COLUMN_MAP: dict[str, str] = {
    "snp": "Scottish National Party",
    "lab": "Labour",
    "con": "Conservative",
    "ld": "Liberal Democrats",
    "lib dems": "Liberal Democrats",
    "lib dem": "Liberal Democrats",
    "green": "Scottish Greens",
    "greens": "Scottish Greens",
    "alba": "Alba Party",
    "ref": "Reform UK",
    "reform": "Reform UK",
    "oth": "Others",
    "others": "Others",
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
class ParsedScottishPoll:
    """A single poll row parsed from the Wikipedia Scottish Parliament polling table.

    Attributes:
        fieldwork_start: First day of polling fieldwork.
        fieldwork_end: Last day of polling fieldwork.
        pollster_name: Pollster label as shown in the Wikipedia table.
        sample_size: Number of respondents, or None if not shown.
        party_percentages: Mapping of canonical party name → voting intention percentage.
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
    """Fetch HTML content from ``url`` using a browser-like User-Agent.

    Args:
        url: Fully-qualified URL to fetch.

    Returns:
        UTF-8 decoded response body.

    Raises:
        urllib.error.URLError: If the request fails.
    """
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; holyrood-poll-importer/1.0)"},
    )
    with urlopen(req, timeout=30) as response:
        body: str = response.read().decode("utf-8", errors="replace")
    return body


# ── Parsing helpers ───────────────────────────────────────────────────────────


def parse_date_range(raw: str) -> tuple[date, date] | None:
    """Parse a fieldwork date-range string into ``(start, end)`` date objects.

    Handles three formats:
    - Same-month range: ``"1–3 Feb 2026"``
    - Cross-month range: ``"28 Jan – 3 Feb 2026"``
    - Single day: ``"3 Feb 2026"``

    En-dashes (–) and em-dashes (—) are normalised to hyphens before matching.

    Args:
        raw: Raw date string extracted from a Wikipedia table cell.

    Returns:
        ``(start, end)`` tuple of :class:`datetime.date` objects, or ``None``
        if the string cannot be parsed.
    """
    text = re.sub(r"[–—]", "-", raw).strip()
    text = re.sub(r"\s+", " ", text)

    # Cross-month: "28 Jan - 3 Feb 2026"
    cross = re.match(
        r"(\d{1,2})\s+([A-Za-z]+)\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        text,
    )
    if cross:
        d1, m1_str, d2, m2_str, yr_str = cross.groups()
        month1 = _MONTH_MAP.get(m1_str.lower())
        month2 = _MONTH_MAP.get(m2_str.lower())
        if month1 and month2:
            year = int(yr_str)
            year1 = year if month1 <= month2 else year - 1
            try:
                return date(year1, month1, int(d1)), date(year, month2, int(d2))
            except ValueError:
                return None

    # Same-month range: "1-3 Feb 2026"
    same = re.match(
        r"(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        text,
    )
    if same:
        d1, d2, mon_str, yr_str = same.groups()
        month = _MONTH_MAP.get(mon_str.lower())
        if month:
            year = int(yr_str)
            try:
                return date(year, month, int(d1)), date(year, month, int(d2))
            except ValueError:
                return None

    # Single day: "3 Feb 2026"
    single = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if single:
        d, mon_str, yr_str = single.groups()
        month = _MONTH_MAP.get(mon_str.lower())
        if month:
            try:
                d_obj = date(int(yr_str), month, int(d))
                return d_obj, d_obj
            except ValueError:
                return None

    return None


def identify_party_columns(header_cells: list[str]) -> dict[int, str]:
    """Map column indices to canonical party names from table header cells.

    Matches each lowercased, stripped header string against ``PARTY_COLUMN_MAP``
    keys.  Columns that do not match any known party key are ignored.

    Args:
        header_cells: List of header cell text strings (case-insensitive).

    Returns:
        Mapping of column_index → canonical party name.
    """
    result: dict[int, str] = {}
    for idx, cell in enumerate(header_cells):
        key = cell.strip().lower()
        if key in PARTY_COLUMN_MAP:
            result[idx] = PARTY_COLUMN_MAP[key]
    return result


def _find_table_by_heading(soup: BeautifulSoup, heading_keyword: str) -> Tag | None:
    """Find the first wikitable under a heading containing ``heading_keyword``.

    Searches all ``<h2>`` and ``<h3>`` headings for one whose text contains
    ``heading_keyword`` (case-insensitive), then returns the first
    ``wikitable``-class ``<table>`` that follows it as a sibling.

    Args:
        soup: Parsed BeautifulSoup document.
        heading_keyword: Word or phrase to search for in heading text.

    Returns:
        The matching ``<table>`` :class:`bs4.element.Tag`, or ``None`` if not found.
    """
    for heading in soup.find_all(["h2", "h3"]):
        heading_text = _clean(heading.get_text()).lower()
        if heading_keyword.lower() not in heading_text:
            continue
        container = heading.parent if heading.parent else heading
        for sibling in container.find_next_siblings():
            if isinstance(sibling, Tag) and sibling.name in ("h2", "h3"):
                break
            if (
                isinstance(sibling, Tag)
                and sibling.name == "table"
                and "wikitable" in (sibling.get("class") or [])
            ):
                return sibling
    return None


def _parse_percentage(raw: str) -> float | None:
    """Extract a vote-share percentage from a table cell string.

    Strips Wikipedia footnote brackets (``[a]``, ``[1]``), removes ``%``, and
    converts to float.

    Args:
        raw: Raw text of a table cell.

    Returns:
        Float percentage, or ``None`` if the cell is not numeric.
    """
    cleaned = re.sub(r"\[[^\]]+\]", "", raw).strip()
    cleaned = cleaned.replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_sample_size(raw: str) -> int | None:
    """Parse a sample size from a table cell string.

    Strips commas, whitespace, and footnote brackets, then converts to int.

    Args:
        raw: Raw text of a table cell.

    Returns:
        Integer sample size, or ``None`` if not numeric.
    """
    cleaned = re.sub(r"\[[^\]]+\]", "", raw)
    cleaned = cleaned.replace(",", "").strip()
    if cleaned.isdigit():
        return int(cleaned)
    return None


# ── Main parser ───────────────────────────────────────────────────────────────


BALLOT_CONSTITUENCY = "constituency"
BALLOT_LIST = "list"

# Heading keyword used to locate each table on the Wikipedia page
_BALLOT_HEADING: dict[str, str] = {
    BALLOT_CONSTITUENCY: "constituency",
    BALLOT_LIST: "regional",
}


def parse_polls(html: str, ballot: str = BALLOT_CONSTITUENCY) -> list[ParsedScottishPoll]:
    """Parse VI polls from the Wikipedia Scottish Parliament polling page.

    Finds the wikitable under the "Constituency vote" or "Regional vote" heading
    depending on ``ballot``, then extracts one :class:`ParsedScottishPoll` per
    data row with a parseable date range.

    Args:
        html: Full HTML content of the Wikipedia opinion polling page.
        ballot: ``"constituency"`` to parse the constituency VI table,
            ``"list"`` to parse the regional list VI table.

    Returns:
        List of :class:`ParsedScottishPoll` instances in table order (newest first).
    """
    heading_keyword = _BALLOT_HEADING.get(ballot, ballot)
    soup = BeautifulSoup(html, "lxml")
    table = _find_table_by_heading(soup, heading_keyword)
    if table is None:
        return []

    # --- Identify party columns from the header ---
    party_cols: dict[int, str] = {}
    header_rows_used = 0
    all_rows = table.find_all("tr")
    for hrow in all_rows[:3]:  # Check first three rows for the header
        cells = [_clean(th.get_text()) for th in hrow.find_all(["th", "td"])]
        party_cols = identify_party_columns([c.lower() for c in cells])
        header_rows_used += 1
        if party_cols:
            break

    if not party_cols:
        return []

    # --- Detect column layout: date, pollster, (client?), sample size ---
    # The header row that matched tells us column labels.
    # Typical layout: 0=dates, 1=pollster, 2=client (optional), n=sample size
    # We find sample_col by header label, fallback to None.
    header_cells = [_clean(th.get_text()) for th in all_rows[header_rows_used - 1].find_all(["th", "td"])]
    sample_col: int | None = None
    for idx, cell in enumerate(header_cells):
        if cell.lower() in ("n", "sample", "sample size"):
            sample_col = idx
            break

    date_col = 0
    pollster_col = 1

    # --- Parse data rows ---
    results: list[ParsedScottishPoll] = []
    for row in all_rows[header_rows_used:]:
        cells = [_clean(td.get_text()) for td in row.find_all(["td", "th"])]
        if not cells or len(cells) < 2:
            continue

        # Skip rows where the first cell contains a year-only heading or election label
        date_range = parse_date_range(cells[date_col]) if len(cells) > date_col else None
        if date_range is None:
            continue

        fieldwork_start, fieldwork_end = date_range

        pollster_name = _clean(re.sub(r"\[[^\]]+\]", "", cells[pollster_col])) if len(cells) > pollster_col else ""
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
            ParsedScottishPoll(
                fieldwork_start=fieldwork_start,
                fieldwork_end=fieldwork_end,
                pollster_name=pollster_name,
                sample_size=sample_size,
                party_percentages=party_percentages,
            )
        )

    return results


# ── DB import ─────────────────────────────────────────────────────────────────


def _pollster_identifier(pollster_name: str, ballot: str = BALLOT_CONSTITUENCY) -> str:
    """Derive a ``<slug>_holyrood`` or ``<slug>_holyrood_list`` identifier.

    Args:
        pollster_name: Raw pollster label as shown in the Wikipedia table.
        ballot: ``"constituency"`` → suffix ``_holyrood``;
                ``"list"`` → suffix ``_holyrood_list``.

    Returns:
        Identifier string, e.g. ``"survation_holyrood"`` or
        ``"survation_holyrood_list"``.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", pollster_name.lower()).strip("_")
    suffix = "_holyrood_list" if ballot == BALLOT_LIST else "_holyrood"
    return f"{slug}{suffix}"


def _ensure_pollster(db: Database, identifier: str, name: str) -> Pollster:
    """Return an existing Pollster or create one if absent."""
    existing = db.get_pollster_by_identifier(identifier)
    if existing is not None:
        return existing
    return db.add_pollster(name=name, identifier=identifier)


def commit_polls(
    db: Database,
    polls: list[ParsedScottishPoll],
    *,
    map_name: str = DEFAULT_MAP_NAME,
    ballot: str = BALLOT_CONSTITUENCY,
    source_url: str = WIKI_URL,
    dry_run: bool = False,
) -> dict[str, int]:
    """Insert Poll and PollRow records for each parsed poll.

    Each poll is linked to a per-pollster record derived from the pollster name
    in the Wikipedia table.  Constituency polls use identifier
    ``<slug>_holyrood``; list polls use ``<slug>_holyrood_list``.
    Polls that already exist (matched by pollster, map, and fieldwork dates)
    are skipped.

    Args:
        db: Active database connection.
        polls: Parsed polls to import.
        map_name: Name of the Holyrood constituency map to link polls to.
        source_url: URL of the data source (stored on each Poll row).
        dry_run: If ``True``, log planned actions without writing to the DB.

    Returns:
        Dict with keys ``"created"``, ``"skipped"``, ``"unknown_parties"`` counts.
    """
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    party_by_name = {p.name: p for p in db.get_all_parties()}
    pollster_cache: dict[str, Pollster] = {}

    created = skipped = unknown_parties = 0

    for parsed in polls:
        identifier = _pollster_identifier(parsed.pollster_name, ballot)

        if not dry_run:
            if identifier not in pollster_cache:
                label = "Holyrood list" if ballot == BALLOT_LIST else "Holyrood"
                pollster_cache[identifier] = _ensure_pollster(
                    db, identifier, f"{parsed.pollster_name} ({label})"
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
                f"{parsed.pollster_name!r} ({identifier}) "
                f"n={parsed.sample_size} "
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
            db.add_poll_row(poll.id, party_id, pct)
        created += 1

    return {"created": created, "skipped": skipped, "unknown_parties": unknown_parties}


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point: fetch Wikipedia page and import Scottish Parliament polls."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=WIKI_URL,
        help=f"Wikipedia opinion polling page URL (default: {WIKI_URL})",
    )
    parser.add_argument(
        "--map-name",
        default=DEFAULT_MAP_NAME,
        help=f"Holyrood constituency map name (default: {DEFAULT_MAP_NAME!r})",
    )
    parser.add_argument(
        "--ballot",
        choices=[BALLOT_CONSTITUENCY, BALLOT_LIST],
        default=BALLOT_CONSTITUENCY,
        help="Which polling table to import: 'constituency' (default) or 'list'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be imported without writing to the database",
    )
    args = parser.parse_args()

    print(f"Fetching: {args.url}")
    html = fetch_html(args.url)

    polls = parse_polls(html, ballot=args.ballot)
    print(f"Parsed {len(polls)} {args.ballot} VI polls from Wikipedia table")

    if not polls:
        print("No polls found — check the page structure or URL")
        return

    db = Database()
    counts = commit_polls(
        db,
        polls,
        map_name=args.map_name,
        ballot=args.ballot,
        source_url=args.url,
        dry_run=args.dry_run,
    )

    print(
        f"Done: created={counts['created']} skipped={counts['skipped']} "
        f"unknown_parties={counts['unknown_parties']}"
    )
    if args.dry_run:
        print("Dry-run: no data written")


if __name__ == "__main__":
    main()
