#!/usr/bin/env python3
"""Import a BMG Research poll directly from an XLSX URL."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from openpyxl import load_workbook
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster

DEFAULT_XLSX_URL = "https://bmgresearch.com/wp-content/uploads/2026/02/january-2026-omnibus-tables-for-the-i.xlsx"
DEFAULT_MAP_NAME = "UK Constituencies post 2022"
DEFAULT_POLLSTER_IDENTIFIER = "bmg_research"
NATIONAL_KEY = "__national__"

SOURCE_REGION_TO_INTERNAL = {
    "East Midlands": "East Midlands",
    "East of England": "East of England",
    "London": "London",
    "North East": "North East England",
    "North West": "North West England",
    "South East": "South East England",
    "South West": "South West England",
    "West Midlands": "West Midlands",
    "Yorkshire and The Humber": "Yorkshire and The Humber",
    "Scotland": "Scotland",
    "Wales": "Wales",
}

PARTY_NAME_MAP = {
    "Conservative": "Conservative",
    "Conservatives": "Conservative",
    "Labour": "Labour",
    "Liberal Democrats": "Liberal Democrats",
    "Liberal Democrat": "Liberal Democrats",
    "Reform UK": "Reform UK",
    "Green": "Green",
    "Green Party": "Green",
    "Scottish National Party": "Scottish National Party",
    "Scottish National Party (SNP)": "Scottish National Party",
    "SNP": "Scottish National Party",
    "Plaid Cymru": "Plaid Cymru",
    "Another Party": "Other",
    "Another Party / An independent candidate": "Other",
    "Another Party / An Independent Candidate": "Other",
    "Another Party/An independent candidate": "Other",
    "Other": "Other",
}


@dataclass
class ParsedPoll:
    sample_size: int
    fieldwork_start: date
    fieldwork_end: date
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
    return month_map.get(month_text.strip().lower().rstrip("."))


def _infer_year(xlsx_url: str, fallback: int | None = None) -> int:
    match = re.search(r"/(20\d{2})/", xlsx_url)
    if match:
        return int(match.group(1))
    matches = re.findall(r"(20\d{2})", xlsx_url)
    if matches:
        return int(matches[0])
    if fallback is not None:
        return fallback
    raise ValueError("Could not infer year from URL")


def _parse_fieldwork(fieldwork_text: str, default_year: int | None = None) -> tuple[date, date]:
    normalized = re.sub(r"\s+", " ", fieldwork_text.strip().replace("–", "-").replace("—", "-"))
    normalized = re.sub(r"(\d)(st|nd|rd|th)", r"\1", normalized, flags=re.IGNORECASE)

    pattern_same_month_amp = re.compile(r"(\d{1,2})\s*&\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
    match = pattern_same_month_amp.search(normalized)
    if match:
        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month = _month_number(match.group(3))
        year = int(match.group(4))
        if month is None:
            raise ValueError(f"Could not parse fieldwork month: {fieldwork_text!r}")
        return date(year, month, day_start), date(year, month, day_end)

    pattern_same_month = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
    match = pattern_same_month.search(normalized)
    if match:
        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month = _month_number(match.group(3))
        year = int(match.group(4))
        if month is None:
            raise ValueError(f"Could not parse fieldwork month: {fieldwork_text!r}")
        return date(year, month, day_start), date(year, month, day_end)

    pattern_cross_month = re.compile(
        r"(\d{1,2})\s+([A-Za-z]+)\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"
    )
    match = pattern_cross_month.search(normalized)
    if match:
        day_start = int(match.group(1))
        month_start = _month_number(match.group(2))
        day_end = int(match.group(3))
        month_end = _month_number(match.group(4))
        year_end = int(match.group(5))
        if month_start is None or month_end is None:
            raise ValueError(f"Could not parse fieldwork months: {fieldwork_text!r}")
        year_start = year_end - 1 if month_start > month_end else year_end
        return date(year_start, month_start, day_start), date(year_end, month_end, day_end)

    pattern_no_year = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-z]+)")
    match = pattern_no_year.search(normalized)
    if match and default_year is not None:
        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month = _month_number(match.group(3))
        if month is None:
            raise ValueError(f"Could not parse fieldwork month: {fieldwork_text!r}")
        return date(default_year, month, day_start), date(default_year, month, day_end)

    pattern_single_day = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
    match = pattern_single_day.search(normalized)
    if match:
        day = int(match.group(1))
        month = _month_number(match.group(2))
        year = int(match.group(3))
        if month is None:
            raise ValueError(f"Could not parse fieldwork month: {fieldwork_text!r}")
        parsed_day = date(year, month, day)
        return parsed_day, parsed_day

    pattern_single_day_no_year = re.compile(r"(\d{1,2})\s+([A-Za-z]+)$")
    match = pattern_single_day_no_year.search(normalized)
    if match and default_year is not None:
        day = int(match.group(1))
        month = _month_number(match.group(2))
        if month is None:
            raise ValueError(f"Could not parse fieldwork month: {fieldwork_text!r}")
        parsed_day = date(default_year, month, day)
        return parsed_day, parsed_day

    raise ValueError(f"Could not parse fieldwork string: {fieldwork_text!r}")


def extract_workbook(xlsx_url: str):
    candidate_urls = [xlsx_url]
    if "bmgresearch.co.uk" in xlsx_url:
        candidate_urls.append(xlsx_url.replace("bmgresearch.co.uk", "bmgresearch.com"))
        candidate_urls.append(xlsx_url.replace("www.bmgresearch.co.uk", "bmgresearch.com"))

    payload = None
    errors: list[str] = []
    for candidate in dict.fromkeys(candidate_urls):
        req = Request(candidate, headers={"User-Agent": "Mozilla/5.0 (compatible; poll-importer/1.0)"})
        try:
            with urlopen(req, timeout=45) as response:
                data = response.read()
            if data.startswith(b"PK"):
                payload = data
                break
            errors.append(f"non-xlsx payload at {candidate}")
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")

    if payload is None:
        raise ValueError("Could not fetch XLSX payload: " + " | ".join(errors))

    return load_workbook(filename=BytesIO(payload), data_only=True)


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_percentage(value: object) -> float:
    if value is None:
        raise ValueError("Encountered empty percentage cell")
    number = float(value)
    pct = number * 100.0 if 0.0 <= number <= 1.0 else number
    return float(int(round(pct)))


def _to_percentage_or_zero(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in {"", "-", "–", "—"}:
            return 0.0
    return _to_percentage(value)


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _find_methodology_sheet(workbook):
    for name in workbook.sheetnames:
        if "method" in name.lower():
            return workbook[name]
    return workbook[workbook.sheetnames[0]]


def _parse_fieldwork_and_sample(workbook, default_year: int) -> tuple[date, date, int]:
    sheet = _find_methodology_sheet(workbook)

    fieldwork_raw = None
    sample_raw = None

    for row in range(1, 80):
        for col in range(1, 6):
            text = _cell_text(sheet.cell(row, col).value)
            if not text:
                continue
            lowered = text.lower()
            if "fieldwork date" in lowered or "fieldwork dates" in lowered:
                match = re.search(r"fieldwork dates?\s*:\s*(.+)$", text, flags=re.IGNORECASE)
                fieldwork_raw = match.group(1).strip() if match else text
            if lowered.startswith("sample:"):
                sample_raw = text

    if not fieldwork_raw:
        raise ValueError("Fieldwork dates not found in methodology sheet")

    fieldwork_start, fieldwork_end = _parse_fieldwork(fieldwork_raw, default_year=default_year)

    if not sample_raw:
        raise ValueError("Sample line not found in methodology sheet")
    digits = re.sub(r"[^0-9]", "", sample_raw)
    if not digits:
        raise ValueError("Could not parse sample size from methodology sheet")

    return fieldwork_start, fieldwork_end, int(digits)


def _find_tables_sheet(workbook):
    for name in workbook.sheetnames:
        if name.lower() == "tables" or "table" in name.lower():
            return workbook[name]
    raise ValueError("Could not find tables sheet")


def _find_vi_table_starts(sheet) -> list[int]:
    starts: list[int] = []
    for row in range(1, sheet.max_row + 1):
        value = _cell_text(sheet.cell(row, 2).value)
        if "wouldvotetodayrevised" in value.lower():
            starts.append(row)
    if not starts:
        raise ValueError("Could not find WouldVoteTodayRevised table")
    return starts


def _parse_party_region_percentages(workbook) -> dict[str, dict[str, float]]:
    sheet = _find_tables_sheet(workbook)
    starts = _find_vi_table_starts(sheet)
    national_start = starts[0]
    regional_start = starts[-1]

    national_values: dict[str, float] = {}
    for row in range(national_start + 1, min(sheet.max_row, national_start + 180)):
        label = _cell_text(sheet.cell(row, 2).value)
        if label.lower().startswith("table ") and row > national_start + 4:
            break
        canonical = PARTY_NAME_MAP.get(label)
        if canonical is None:
            continue

        next_total = sheet.cell(row + 1, 3).value
        current_total = sheet.cell(row, 3).value

        if isinstance(next_total, (int, float)) and 0.0 <= float(next_total) <= 1.2:
            percentage = _to_percentage(next_total)
        elif isinstance(current_total, (int, float)) and 0.0 <= float(current_total) <= 1.2:
            percentage = _to_percentage(current_total)
        else:
            continue

        national_values[canonical] = percentage

    region_header_row = regional_start + 3
    region_columns: dict[str, int] = {}
    for col in range(34, 90):
        header = _normalize_header(_cell_text(sheet.cell(region_header_row, col).value))
        if not header:
            continue
        internal = SOURCE_REGION_TO_INTERNAL.get(header)
        if internal is not None:
            region_columns[internal] = col

    if not region_columns:
        raise ValueError("Could not locate BMG regional columns in tables sheet")

    regional_values_by_party: dict[str, dict[str, float]] = {}
    for row in range(regional_start + 1, min(sheet.max_row, regional_start + 220)):
        label = _cell_text(sheet.cell(row, 2).value)
        if label.lower().startswith("table ") and row > regional_start + 4:
            break
        canonical = PARTY_NAME_MAP.get(label)
        if canonical is None:
            continue

        region_percentages: dict[str, float] = {}
        for region_name, col in region_columns.items():
            pct_cell = sheet.cell(row + 1, col).value
            region_percentages[region_name] = _to_percentage_or_zero(pct_cell)

        regional_values_by_party[canonical] = region_percentages

    parsed: dict[str, dict[str, float]] = {}
    all_region_names = set(region_columns.keys())
    for canonical, national_pct in national_values.items():
        merged = {NATIONAL_KEY: national_pct}
        source_regions = regional_values_by_party.get(canonical, {})
        for region_name in all_region_names:
            merged[region_name] = source_regions.get(region_name, 0.0)
        parsed[canonical] = merged

    for optional_party in ["Scottish National Party", "Plaid Cymru", "Other"]:
        if optional_party not in parsed:
            parsed[optional_party] = {NATIONAL_KEY: 0.0}
        for region_name in all_region_names:
            parsed[optional_party].setdefault(region_name, 0.0)

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
    missing = sorted(required - set(national_values.keys()))
    if missing:
        raise ValueError(f"Missing expected party rows in workbook: {missing}")

    return parsed


def parse_poll(workbook, *, source_url: str, year_hint: int | None = None) -> ParsedPoll:
    inferred_year = _infer_year(source_url, fallback=year_hint)
    fieldwork_start, fieldwork_end, sample_size = _parse_fieldwork_and_sample(workbook, inferred_year)
    party_region_percentages = _parse_party_region_percentages(workbook)
    return ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
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
    xlsx_url: str = DEFAULT_XLSX_URL,
    map_name: str = DEFAULT_MAP_NAME,
    pollster_identifier: str = DEFAULT_POLLSTER_IDENTIFIER,
    year_hint: int | None = None,
) -> ImportPlan:
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    workbook = extract_workbook(xlsx_url)
    parsed = parse_poll(workbook, source_url=xlsx_url, year_hint=year_hint)

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
    regions = db.get_regions_for_map(poll_map.id)
    region_ids_by_name: dict[str, int] = {region.name: region.id for region in regions}

    for party_name, region_values in parsed.party_region_percentages.items():
        national_percentage = region_values.get(NATIONAL_KEY, 0.0)
        rows.append(
            PlannedPollRow(
                party_id=party_by_name[party_name].id,
                party_name=party_name,
                region_id=None,
                region_name="National",
                percentage=national_percentage,
            )
        )

        for region_name, region_id in region_ids_by_name.items():
            rows.append(
                PlannedPollRow(
                    party_id=party_by_name[party_name].id,
                    party_name=party_name,
                    region_id=region_id,
                    region_name=region_name,
                    percentage=region_values.get(region_name, 0.0),
                )
            )

    existing_poll = (
        _find_existing_poll(db, pollster.id, poll_map.id, parsed)
        if pollster is not None
        else None
    )

    return ImportPlan(
        pollster_identifier=pollster_identifier,
        pollster_name=(pollster.name if pollster else "BMG Research"),
        pollster_id=(pollster.id if pollster else None),
        pollster_exists=pollster_exists,
        regions_mapping="",
        map_id=poll_map.id,
        map_name=poll_map.name,
        source_url=xlsx_url,
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
    parser.add_argument("--xlsx-url", default=DEFAULT_XLSX_URL)
    parser.add_argument("--map-name", default=DEFAULT_MAP_NAME)
    parser.add_argument("--pollster-identifier", default=DEFAULT_POLLSTER_IDENTIFIER)
    parser.add_argument("--year-hint", type=int, default=None)
    parser.add_argument(
        "--replace-rows",
        action="store_true",
        help="Delete existing rows for the poll before inserting new ones",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = Database()
    print(f"Fetching XLSX: {args.xlsx_url}")

    plan = build_import_plan(
        db,
        xlsx_url=args.xlsx_url,
        map_name=args.map_name,
        pollster_identifier=args.pollster_identifier,
        year_hint=args.year_hint,
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
