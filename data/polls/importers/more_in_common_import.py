#!/usr/bin/env python3
"""Import a More in Common poll directly from an XLSX URL."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

from openpyxl import load_workbook
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster

DEFAULT_XLSX_URL = "https://www.moreincommon.org.uk/media/dshdwjt0/voting-intention-and-trackers-10-feb.xlsx"
DEFAULT_MAP_NAME = "UK Constituencies post 2022"
DEFAULT_POLLSTER_IDENTIFIER = "more_in_common"

NATIONAL_KEY = "__national__"

PARTY_NAME_MAP = {
    "Conservatives": "Conservative",
    "Conservative": "Conservative",
    "Labour": "Labour",
    "Liberal Democrat": "Liberal Democrats",
    "Liberal Democrats": "Liberal Democrats",
    "Reform UK": "Reform UK",
    "The Green Party": "Green",
    "Green Party": "Green",
    "Green": "Green",
    "Scottish National Party": "Scottish National Party",
    "Scottish National Party (SNP)": "Scottish National Party",
    "The SNP": "Scottish National Party",
    "SNP": "Scottish National Party",
    "Plaid Cymru": "Plaid Cymru",
    "Another Party/ Independent": "Other",
    "Another party/ Independent": "Other",
    "Another Party/Independent": "Other",
    "Another party/Independent": "Other",
    "Another party/Independent candidate": "Other",
    "Another party/Independent Candidate": "Other",
    "Another party": "Other",
    "Other": "Other",
}

REGION_HEADER_TO_INTERNAL = {
    "East Midlands": "East Midlands",
    "East of England": "East of England",
    "Greater London": "London",
    "London": "London",
    "North East England": "North East England",
    "North West England": "North West England",
    "Scotland": "Scotland",
    "South East England": "South East England",
    "South West England": "South West England",
    "Wales": "Wales",
    "West Midlands": "West Midlands",
    "Yorkshire and the Humber": "Yorkshire and The Humber",
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


def normalize_name(value: str) -> str:
    return " ".join(value.strip().split()).lower()


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


def parse_fieldwork(value: str, *, default_year: int | None = None) -> tuple[date, date]:
    normalized = re.sub(r"\s+", " ", value.strip().replace("–", "-").replace("—", "-"))

    cross_month_pattern = re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z.]+)\s*-\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z.]+)\s+(\d{4})"
    )
    range_same_month_pattern = re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s*-\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z.]+)\s+(\d{4})"
    )
    range_same_month_no_year_pattern = re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s*-\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z.]+)"
    )
    single_day_pattern = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z.]+)\s+(\d{4})")
    single_day_no_year_pattern = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z.]+)$")

    match = cross_month_pattern.search(normalized)
    if match:
        day_start = int(match.group(1))
        month_start = _month_number(match.group(2))
        day_end = int(match.group(3))
        month_end = _month_number(match.group(4))
        year_end = int(match.group(5))
        if month_start is None or month_end is None:
            raise ValueError(f"Could not parse fieldwork string: {value!r}")
        year_start = year_end - 1 if month_start > month_end else year_end
        return date(year_start, month_start, day_start), date(year_end, month_end, day_end)

    match = range_same_month_pattern.search(normalized)
    if match:
        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month = _month_number(match.group(3))
        year = int(match.group(4))
        if month is None:
            raise ValueError(f"Could not parse fieldwork string: {value!r}")
        return date(year, month, day_start), date(year, month, day_end)

    match = range_same_month_no_year_pattern.search(normalized)
    if match and default_year is not None:
        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month = _month_number(match.group(3))
        if month is None:
            raise ValueError(f"Could not parse fieldwork string: {value!r}")
        return date(default_year, month, day_start), date(default_year, month, day_end)

    match = single_day_pattern.search(normalized)
    if match:
        day = int(match.group(1))
        month = _month_number(match.group(2))
        year = int(match.group(3))
        if month is None:
            raise ValueError(f"Could not parse fieldwork string: {value!r}")
        parsed_day = date(year, month, day)
        return parsed_day, parsed_day

    match = single_day_no_year_pattern.search(normalized)
    if match and default_year is not None:
        day = int(match.group(1))
        month = _month_number(match.group(2))
        if month is None:
            raise ValueError(f"Could not parse fieldwork string: {value!r}")
        parsed_day = date(default_year, month, day)
        return parsed_day, parsed_day

    raise ValueError(f"Could not parse fieldwork string: {value!r}")


def extract_workbook(xlsx_url: str):
    payload = urlopen(xlsx_url).read()
    return load_workbook(filename=BytesIO(payload), data_only=True)


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _find_label_value(ws, label_fragment: str) -> str | None:
    needle = label_fragment.lower()
    for row in range(1, 45):
        for col in range(1, 8):
            label = _cell_text(ws.cell(row, col).value)
            if not label:
                continue
            if needle in label.lower():
                for offset in range(1, 4):
                    candidate = _cell_text(ws.cell(row, col + offset).value)
                    if candidate:
                        return candidate
    return None


def _as_int(value: str) -> int:
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        raise ValueError(f"Could not parse sample size from value: {value!r}")
    return int(digits)


def _to_percentage(value: object) -> float:
    if value is None:
        raise ValueError("Encountered empty percentage cell")
    number = float(value)
    pct = number * 100.0 if 0.0 <= number <= 1.0 else number
    return float(int(round(pct)))


def _infer_year_from_url(xlsx_url: str) -> int | None:
    matches = re.findall(r"(20\d{2})", xlsx_url)
    if not matches:
        return None
    return int(matches[0])


def _infer_fieldwork_from_source_url(
    source_url: str,
    *,
    default_year: int | None,
) -> tuple[date, date] | None:
    if default_year is None:
        return None

    lower = source_url.lower()
    month_pattern = "|".join(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )

    patterns = [
        re.compile(rf"voting[-_ ]intention[-_ ]({month_pattern})[-_ ](\d{{1,2}})"),
        re.compile(rf"voting[-_ ]intention[-_ ]({month_pattern})(\d{{1,2}})"),
    ]

    for pattern in patterns:
        match = pattern.search(lower)
        if not match:
            continue
        month = _month_number(match.group(1))
        day = int(match.group(2))
        if month is None:
            continue
        parsed_day = date(default_year, month, day)
        return parsed_day, parsed_day

    return None


def _find_headline_sheet(workbook):
    def _sheet_priority(sheet_name: str) -> tuple[int, str]:
        lowered = sheet_name.lower()
        if "votingintention (headline)" in lowered:
            return (0, lowered)
        if "headline" in lowered and "votingintention" in lowered:
            return (1, lowered)
        if "votingintention" in lowered:
            return (2, lowered)
        if "corbyn" in lowered:
            return (3, lowered)
        return (9, lowered)

    for sheet_name in sorted(workbook.sheetnames, key=_sheet_priority):
        ws = workbook[sheet_name]
        for row in range(1, 35):
            values = [_cell_text(ws.cell(row, col).value) for col in range(1, 60)]
            if "All" in values and "East Midlands" in values:
                return ws, row

    for sheet_name in sorted(workbook.sheetnames, key=_sheet_priority):
        ws = workbook[sheet_name]
        for row in range(1, 35):
            values = [_cell_text(ws.cell(row, col).value) for col in range(1, 60)]
            if "All" not in values:
                continue
            if any(label in values for label in ("Conservative", "Labour", "Reform UK", "The Green Party")):
                return ws, row

    raise ValueError("Could not locate headline voting intention table")


def parse_poll(
    workbook,
    *,
    source_url: str,
    fieldwork_year_hint: int | None = None,
) -> ParsedPoll:
    default_year = fieldwork_year_hint if fieldwork_year_hint is not None else _infer_year_from_url(source_url)

    sheet, header_row = _find_headline_sheet(workbook)

    fieldwork_raw = None
    sample_raw = None
    preferred_sheet_names = ["Cover page", workbook.sheetnames[0]]
    for sheet_name in preferred_sheet_names + workbook.sheetnames:
        if sheet_name not in workbook.sheetnames:
            continue
        ws = workbook[sheet_name]
        if fieldwork_raw is None:
            fieldwork_raw = _find_label_value(ws, "Fieldwork")
        if sample_raw is None:
            sample_raw = _find_label_value(ws, "Sample size")
        if fieldwork_raw and sample_raw:
            break

    if not fieldwork_raw:
        inferred = _infer_fieldwork_from_source_url(source_url, default_year=default_year)
        if inferred is None:
            raise ValueError("Fieldwork date not found in workbook")
        fieldwork_start, fieldwork_end = inferred
    else:
        fieldwork_start, fieldwork_end = parse_fieldwork(fieldwork_raw, default_year=default_year)

    if not sample_raw:
        for row in range(header_row + 1, header_row + 80):
            row_label = normalize_name(_cell_text(sheet.cell(row, 1).value))
            if row_label in {"unweighted n", "weighted n"}:
                sample_candidate = _cell_text(sheet.cell(row, 2).value)
                if sample_candidate:
                    sample_raw = sample_candidate
                    break
    if not sample_raw:
        raise ValueError("Sample size not found in workbook")
    sample_size = _as_int(sample_raw)

    population_label = ""
    for sheet_name in ["Cover page", workbook.sheetnames[0]] + workbook.sheetnames:
        if sheet_name not in workbook.sheetnames:
            continue
        ws = workbook[sheet_name]
        value = _find_label_value(ws, "Population effectively represented")
        if value:
            population_label = value
            break

    region_columns: dict[int, str] = {}
    national_column: int | None = None
    for col in range(1, 120):
        header = _cell_text(sheet.cell(header_row, col).value)
        if header == "All":
            national_column = col
        mapped_region = REGION_HEADER_TO_INTERNAL.get(header)
        if mapped_region:
            region_columns[col] = mapped_region

    if national_column is None:
        raise ValueError("Could not locate 'All' (national) column in headline table")
    is_special_population = "16-17" in population_label.replace(" ", "")
    if len(region_columns) < 6 and not is_special_population:
        raise ValueError("Could not resolve sufficient region columns in headline table")

    party_region_percentages: dict[str, dict[str, float]] = {}
    for row in range(header_row + 1, header_row + 60):
        party_label = _cell_text(sheet.cell(row, 1).value)
        if not party_label:
            continue

        normalized_label = normalize_name(party_label)
        if normalized_label in {"weighted n", "unweighted n", "weight"}:
            break

        canonical_party = PARTY_NAME_MAP.get(party_label)
        if canonical_party is None:
            continue

        region_values: dict[str, float] = {NATIONAL_KEY: _to_percentage(sheet.cell(row, national_column).value)}
        for col, internal_region in region_columns.items():
            region_values[internal_region] = _to_percentage(sheet.cell(row, col).value)

        party_region_percentages[canonical_party] = region_values

    required_parties = {
        "Conservative",
        "Labour",
        "Liberal Democrats",
        "Reform UK",
        "Green",
        "Scottish National Party",
        "Plaid Cymru",
        "Other",
    }
    missing = sorted(required_parties - set(party_region_percentages.keys()))
    if missing:
        raise ValueError(f"Missing expected party rows in workbook: {missing}")

    return ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        party_region_percentages=party_region_percentages,
    )


def _find_existing_poll(
    db: Database,
    pollster_id: int,
    map_id: int,
    parsed: ParsedPoll,
) -> Poll | None:
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
    fieldwork_year_hint: int | None = None,
) -> ImportPlan:
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    regions = db.get_regions_for_map(poll_map.id)

    region_ids_by_name: dict[str, int] = {}
    for region in regions:
        region_ids_by_name[region.name] = region.id

    regions_mapping = "\n".join(
        f"{region_name}:{region_ids_by_name[region_name]}"
        for region_name in sorted(region_ids_by_name.keys())
    )

    workbook = extract_workbook(xlsx_url)
    parsed = parse_poll(
        workbook,
        source_url=xlsx_url,
        fieldwork_year_hint=fieldwork_year_hint,
    )

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
    for party_name, region_values in parsed.party_region_percentages.items():
        party_id = party_by_name[party_name].id

        national_percentage = region_values.get(NATIONAL_KEY)
        if national_percentage is not None:
            rows.append(
                PlannedPollRow(
                    party_id=party_id,
                    party_name=party_name,
                    region_id=None,
                    region_name="National",
                    percentage=national_percentage,
                )
            )

        for region_name, region_id in region_ids_by_name.items():
            percentage = region_values.get(region_name, 0.0)
            rows.append(
                PlannedPollRow(
                    party_id=party_id,
                    party_name=party_name,
                    region_id=region_id,
                    region_name=region_name,
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
        pollster_name=(pollster.name if pollster else "More in Common"),
        pollster_id=(pollster.id if pollster else None),
        pollster_exists=pollster_exists,
        regions_mapping=regions_mapping,
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
        with db.session() as session:
            db_pollster = session.get(Pollster, pollster.id)
            if db_pollster is not None and db_pollster.regions_mapping != plan.regions_mapping:
                db_pollster.regions_mapping = plan.regions_mapping
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
    parser.add_argument("--fieldwork-year-hint", type=int, default=None)
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
        fieldwork_year_hint=args.fieldwork_year_hint,
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
