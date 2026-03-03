#!/usr/bin/env python3
"""Import a Lord Ashcroft poll from HTML or XLS source URLs."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import xlrd
from bs4 import BeautifulSoup
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster

DEFAULT_SOURCE_URL = "https://lordashcroftpolls.com/2026/01/kemis-been-tough-and-jenricks-a-bit-of-a-bounder-say-tory-voters-but-plenty-agree-with-him-that-britain-is-broken/"
DEFAULT_MAP_NAME = "UK Constituencies post 2022"
DEFAULT_POLLSTER_IDENTIFIER = "lord_ashcroft"

PARTY_NAME_MAP = {
    "Conservative": "Conservative",
    "Labour": "Labour",
    "Lib Dem": "Liberal Democrats",
    "Liberal Democrat": "Liberal Democrats",
    "Liberal Democrats": "Liberal Democrats",
    "Reform UK": "Reform UK",
    "Green": "Green",
    "Green Party": "Green",
    "SNP": "Scottish National Party",
    "Scottish National Party": "Scottish National Party",
    "Plaid": "Plaid Cymru",
    "Plaid Cymru": "Plaid Cymru",
    "Other": "Other",
    "Another party": "Other",
}

SOURCE_REGION_TO_INTERNAL = {
    "North East": "North East England",
    "North West": "North West England",
    "Yorkshire and the Humber": "Yorkshire and The Humber",
    "East Midlands": "East Midlands",
    "West Midlands": "West Midlands",
    "East of England": "East of England",
    "London": "London",
    "South East": "South East England",
    "South West": "South West England",
    "Wales": "Wales",
    "Scotland": "Scotland",
}


@dataclass
class ParsedPoll:
    sample_size: int
    fieldwork_start: date
    fieldwork_end: date
    party_percentages: dict[str, float]
    party_region_percentages: dict[str, dict[str, float]]


@dataclass
class PlannedPollRow:
    party_id: int
    party_name: str
    region_id: int | None
    region_name: str
    percentage: float


@dataclass
class ImportPlan:
    pollster_identifier: str
    pollster_name: str
    pollster_id: int | None
    pollster_exists: bool
    regions_mapping: str
    map_id: int
    map_name: str
    source_url: str
    parsed: ParsedPoll
    poll_id: int | None
    poll_exists: bool
    rows: list[PlannedPollRow]


def _month_number(month_text: str) -> int | None:
    cleaned = month_text.strip().lower().rstrip(".")
    month_map = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    return month_map.get(cleaned)


def _fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; poll-importer/1.0)"})
    with urlopen(request) as response:
        return response.read()


def _resolve_xls_url(source_url: str) -> str:
    lower = source_url.lower()
    if lower.endswith(".xls") or lower.endswith(".xlsx"):
        return source_url

    html = _fetch_bytes(source_url).decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    candidates: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href:
            continue
        absolute = urljoin(source_url, href)
        absolute_lower = absolute.lower()
        if absolute_lower.endswith(".xls") or absolute_lower.endswith(".xlsx"):
            candidates.append(absolute)

    if not candidates:
        raise ValueError(f"No XLS/XLSX link found in Lord Ashcroft HTML page: {source_url}")

    return candidates[0]


def _as_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _as_int(value: object) -> int:
    digits = re.sub(r"[^0-9]", "", _as_text(value))
    if not digits:
        raise ValueError(f"Could not parse integer value from {value!r}")
    return int(digits)


def _find_fieldwork(sheet: xlrd.sheet.Sheet) -> tuple[date, date]:
    pattern = re.compile(
        r"Fieldwork:\s*(\d{1,2})(?:st|nd|rd|th)?\s*-\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(20\d{2})",
        re.IGNORECASE,
    )
    for row_idx in range(min(30, sheet.nrows)):
        value = _as_text(sheet.cell_value(row_idx, 0))
        if not value:
            continue
        match = pattern.search(value)
        if not match:
            continue

        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month = _month_number(match.group(3))
        year = int(match.group(4))
        if month is None:
            raise ValueError(f"Could not parse month in fieldwork line: {value!r}")
        return date(year, month, day_start), date(year, month, day_end)

    raise ValueError("Fieldwork line not found in Lord Ashcroft workbook")


def _find_sample_size(sheet: xlrd.sheet.Sheet) -> int:
    for row_idx in range(min(30, sheet.nrows)):
        label = _as_text(sheet.cell_value(row_idx, 0)).lower()
        if "sample size" in label:
            return _as_int(sheet.cell_value(row_idx, 0))
        if "weighted sample" in label:
            return _as_int(sheet.cell_value(row_idx, 1))
    raise ValueError("Sample size not found in Lord Ashcroft workbook")


def _find_vi_block_start(sheet: xlrd.sheet.Sheet) -> int:
    for row_idx in range(sheet.nrows):
        label = _as_text(sheet.cell_value(row_idx, 0)).lower()
        if "current westminster voting intention" in label:
            return row_idx
    raise ValueError("CURRENT WESTMINSTER VOTING INTENTION block not found")


def _canonical_party(label: str) -> str | None:
    normalized = " ".join(label.strip().split()).lower()
    for marker, canonical in PARTY_NAME_MAP.items():
        if marker.lower() in normalized:
            return canonical
    return None


def _parse_party_percentages(sheet: xlrd.sheet.Sheet) -> dict[str, float]:
    start_idx = _find_vi_block_start(sheet)
    parsed: dict[str, float] = {}

    for row_idx in range(start_idx + 1, min(start_idx + 40, sheet.nrows)):
        label = _as_text(sheet.cell_value(row_idx, 0))
        if not label:
            continue
        canonical_party = _canonical_party(label)
        if canonical_party is None:
            continue

        value = sheet.cell_value(row_idx, 1)
        if value in ("", None):
            continue

        percentage = float(value)
        parsed[canonical_party] = float(int(round(percentage)))

        if len(parsed) == 8:
            break

    required = {
        "Conservative",
        "Labour",
        "Liberal Democrats",
        "Reform UK",
        "Green",
        "Scottish National Party",
        "Plaid Cymru",
        "Other",
    }
    missing = sorted(required - set(parsed.keys()))
    if missing:
        raise ValueError(f"Missing party rows in Lord Ashcroft workbook: {missing}")

    return parsed


def _find_region_columns(sheet: xlrd.sheet.Sheet) -> dict[int, str]:
    for row_idx in range(min(40, sheet.nrows)):
        columns: dict[int, str] = {}
        for col_idx in range(sheet.ncols):
            header = _as_text(sheet.cell_value(row_idx, col_idx))
            if not header:
                continue
            internal = SOURCE_REGION_TO_INTERNAL.get(header)
            if internal is not None:
                columns[col_idx] = internal
        if len(columns) >= 8:
            return columns
    return {}


def _find_weighted_sample_row(sheet: xlrd.sheet.Sheet) -> int:
    for row_idx in range(min(40, sheet.nrows)):
        label = _as_text(sheet.cell_value(row_idx, 0)).lower()
        if label == "weighted sample":
            return row_idx
    raise ValueError("Weighted Sample row not found in Lord Ashcroft workbook")


def _to_float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = _as_text(value)
    if not text:
        return 0.0
    return float(text)


def _parse_party_region_percentages(values_sheet: xlrd.sheet.Sheet) -> dict[str, dict[str, float]]:
    region_columns = _find_region_columns(values_sheet)
    if not region_columns:
        return {}

    weighted_row = _find_weighted_sample_row(values_sheet)
    denominators: dict[int, float] = {}
    for col_idx in region_columns:
        denom = _to_float(values_sheet.cell_value(weighted_row, col_idx))
        if denom > 0:
            denominators[col_idx] = denom

    if not denominators:
        return {}

    start_idx = _find_vi_block_start(values_sheet)
    parsed: dict[str, dict[str, float]] = {}

    for row_idx in range(start_idx + 1, min(start_idx + 40, values_sheet.nrows)):
        label = _as_text(values_sheet.cell_value(row_idx, 0))
        if not label:
            continue

        canonical_party = _canonical_party(label)
        if canonical_party is None:
            continue

        region_values: dict[str, float] = {}
        for col_idx, internal_name in region_columns.items():
            denominator = denominators.get(col_idx)
            if not denominator:
                continue

            numerator = _to_float(values_sheet.cell_value(row_idx, col_idx))
            percentage = float(int(round((numerator / denominator) * 100.0)))
            region_values[internal_name] = percentage

        if region_values:
            parsed[canonical_party] = region_values

    return parsed


def parse_poll_from_xls_url(xls_url: str) -> ParsedPoll:
    payload = _fetch_bytes(xls_url)
    workbook = xlrd.open_workbook(file_contents=payload)
    if workbook.nsheets == 0:
        raise ValueError("Lord Ashcroft workbook has no sheets")

    sheet = workbook.sheet_by_index(0)
    values_sheet = None
    for idx in range(workbook.nsheets):
        candidate = workbook.sheet_by_index(idx)
        if "values" in candidate.name.lower():
            values_sheet = candidate
            break
    if values_sheet is None:
        values_sheet = sheet

    sample_size = _find_sample_size(sheet)
    fieldwork_start, fieldwork_end = _find_fieldwork(sheet)
    party_percentages = _parse_party_percentages(sheet)
    party_region_percentages = _parse_party_region_percentages(values_sheet)

    return ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        party_percentages=party_percentages,
        party_region_percentages=party_region_percentages,
    )


def _find_existing_poll(db: Database, pollster_id: int, map_id: int, parsed: ParsedPoll) -> Poll | None:
    with db.session() as session:
        return session.execute(
            select(Poll).where(
                Poll.pollster_id == pollster_id,
                Poll.map_id == map_id,
                Poll.fieldwork_start == parsed.fieldwork_start,
                Poll.fieldwork_end == parsed.fieldwork_end,
                Poll.sample_size == parsed.sample_size,
            )
        ).scalar_one_or_none()


def build_import_plan(
    db: Database,
    *,
    source_url: str = DEFAULT_SOURCE_URL,
    map_name: str = DEFAULT_MAP_NAME,
    pollster_identifier: str = DEFAULT_POLLSTER_IDENTIFIER,
) -> ImportPlan:
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    resolved_xls_url = _resolve_xls_url(source_url)
    parsed = parse_poll_from_xls_url(resolved_xls_url)

    pollster = db.get_pollster_by_identifier(pollster_identifier)
    pollster_exists = pollster is not None

    party_by_name = {party.name: party for party in db.get_all_parties()}
    missing_parties = [
        party_name
        for party_name in sorted(set(PARTY_NAME_MAP.values()))
        if party_name not in party_by_name
    ]
    if missing_parties:
        raise ValueError(
            "Missing parties in database (run party importer first): "
            f"{missing_parties}"
        )

    rows: list[PlannedPollRow] = []
    region_by_name = {region.name: region for region in db.get_regions_for_map(poll_map.id)}
    for party_name, percentage in parsed.party_percentages.items():
        rows.append(
            PlannedPollRow(
                party_id=party_by_name[party_name].id,
                party_name=party_name,
                region_id=None,
                region_name="National",
                percentage=percentage,
            )
        )

    for party_name, region_percentages in parsed.party_region_percentages.items():
        for region_name, percentage in region_percentages.items():
            region = region_by_name.get(region_name)
            if region is None:
                raise ValueError(f"Missing region in database: {region_name!r}")

            rows.append(
                PlannedPollRow(
                    party_id=party_by_name[party_name].id,
                    party_name=party_name,
                    region_id=region.id,
                    region_name=region.name,
                    percentage=percentage,
                )
            )

    existing_poll = (
        _find_existing_poll(db, pollster.id, poll_map.id, parsed)
        if pollster is not None
        else None
    )

    return ImportPlan(
        pollster_identifier=pollster_identifier,
        pollster_name=(pollster.name if pollster else "Lord Ashcroft Polls"),
        pollster_id=(pollster.id if pollster else None),
        pollster_exists=pollster_exists,
        regions_mapping="",
        map_id=poll_map.id,
        map_name=poll_map.name,
        source_url=source_url,
        parsed=parsed,
        poll_id=(existing_poll.id if existing_poll else None),
        poll_exists=existing_poll is not None,
        rows=rows,
    )


def commit_import_plan(
    db: Database,
    plan: ImportPlan,
    *,
    replace_rows: bool = False,
) -> dict[str, int | bool]:
    if plan.pollster_exists:
        pollster = db.get_pollster_by_identifier(plan.pollster_identifier)
        if pollster is None:
            raise ValueError("Pollster lookup failed during commit")
        pollster_id = pollster.id
        created_pollster = False
    else:
        created = db.add_pollster(
            name=plan.pollster_name,
            identifier=plan.pollster_identifier,
            weight=1.0,
            regions_mapping=plan.regions_mapping,
        )
        pollster_id = created.id
        created_pollster = True

    existing_poll = _find_existing_poll(db, pollster_id, plan.map_id, plan.parsed)
    if existing_poll is None:
        created_poll = db.add_poll(
            pollster_id=pollster_id,
            map_id=plan.map_id,
            fieldwork_start=plan.parsed.fieldwork_start,
            fieldwork_end=plan.parsed.fieldwork_end,
            sample_size=plan.parsed.sample_size,
            source_url=plan.source_url,
        )
        poll_id = created_poll.id
        created_poll_row = True
    else:
        with db.session() as session:
            db_poll = session.get(Poll, existing_poll.id)
            if db_poll is not None and db_poll.source_url != plan.source_url:
                db_poll.source_url = plan.source_url
        poll_id = existing_poll.id
        created_poll_row = False

    with db.session() as session:
        existing_rows = session.execute(
            select(PollRow).where(PollRow.poll_id == poll_id)
        ).scalars().all()

        if existing_rows and not replace_rows:
            return {
                "created_pollster": created_pollster,
                "created_poll": created_poll_row,
                "poll_id": poll_id,
                "inserted_rows": 0,
                "replaced_rows": 0,
                "skipped_existing_rows": True,
            }

        replaced_rows = 0
        if existing_rows and replace_rows:
            for row in existing_rows:
                session.delete(row)
                replaced_rows += 1

        for row in plan.rows:
            session.add(
                PollRow(
                    poll_id=poll_id,
                    region_id=row.region_id,
                    party_id=row.party_id,
                    percentage=row.percentage,
                )
            )

    return {
        "created_pollster": created_pollster,
        "created_poll": created_poll_row,
        "poll_id": poll_id,
        "inserted_rows": len(plan.rows),
        "replaced_rows": replaced_rows,
        "skipped_existing_rows": False,
    }


def _cli_preview(plan: ImportPlan) -> None:
    print(
        "Parsed poll: "
        f"fieldwork={plan.parsed.fieldwork_start} to {plan.parsed.fieldwork_end}, "
        f"sample={plan.parsed.sample_size}"
    )
    if plan.pollster_exists:
        print(f"pollster exists: {plan.pollster_identifier}")
    else:
        print(f"[dry-run] would create pollster: {plan.pollster_identifier}")

    if plan.poll_exists and plan.poll_id is not None:
        print(f"poll exists: {plan.poll_id}")
    else:
        print("[dry-run] would create poll")

    for row in plan.rows:
        print(
            "[dry-run] would insert row: "
            f"party={row.party_name}, region={row.region_name}, "
            f"region_id={row.region_id}, pct={row.percentage:.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--map-name", default=DEFAULT_MAP_NAME)
    parser.add_argument("--pollster-identifier", default=DEFAULT_POLLSTER_IDENTIFIER)
    parser.add_argument(
        "--replace-rows",
        action="store_true",
        help="Delete existing rows for the poll before inserting new ones",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = Database()
    print(f"Fetching source: {args.source_url}")

    plan = build_import_plan(
        db,
        source_url=args.source_url,
        map_name=args.map_name,
        pollster_identifier=args.pollster_identifier,
    )

    if args.dry_run:
        _cli_preview(plan)
        return

    result = commit_import_plan(db, plan, replace_rows=args.replace_rows)
    if result["created_pollster"]:
        print(f"created pollster: {args.pollster_identifier}")
    else:
        print(f"pollster exists: {args.pollster_identifier}")

    if result["created_poll"]:
        print(f"created poll: {result['poll_id']}")
    else:
        print(f"poll exists: {result['poll_id']}")

    if result["skipped_existing_rows"]:
        print(
            f"poll {result['poll_id']} already has rows; "
            "use --replace-rows to overwrite"
        )
    else:
        if result["replaced_rows"]:
            print(f"deleted existing rows: {result['replaced_rows']}")
        print(f"inserted poll rows: {result['inserted_rows']}")


if __name__ == "__main__":
    main()
