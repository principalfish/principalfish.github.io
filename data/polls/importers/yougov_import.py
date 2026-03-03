#!/usr/bin/env python3
"""Import a YouGov poll directly from a PDF URL.

Default source:
https://d3nkl3psvxxpe9.cloudfront.net/documents/VotingIntention_MRP_Results_260209_w.pdf

This module can be used from CLI and from the local web server.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

from pypdf import PdfReader
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster

DEFAULT_PDF_URL = (
    "https://d3nkl3psvxxpe9.cloudfront.net/documents/"
    "VotingIntention_MRP_Results_260209_w.pdf"
)
DEFAULT_MAP_NAME = "UK Constituencies post 2022"
DEFAULT_POLLSTER_IDENTIFIER = "yougov"

PARTY_NAME_MAP = {
    "Con": "Conservative",
    "Conservative": "Conservative",
    "Lab": "Labour",
    "Labour": "Labour",
    "Lib Dem": "Liberal Democrats",
    "Liberal Democrat": "Liberal Democrats",
    "Liberal Democrats": "Liberal Democrats",
    "SNP": "Scottish National Party",
    "Scottish National Party": "Scottish National Party",
    "Scottish National Party (SNP)": "Scottish National Party",
    "Plaid Cymru": "Plaid Cymru",
    "Reform UK": "Reform UK",
    "Green": "Green",
    "Other": "Other",
}

MACRO_TO_INTERNAL_REGIONS = {
    "North": [
        "North East England",
        "North West England",
        "Yorkshire and The Humber",
    ],
    "Midlands": ["East Midlands", "West Midlands"],
    "London": ["London"],
    "Rest of South": ["East of England", "South East England", "South West England"],
    "Wales": ["Wales"],
    "Scotland": ["Scotland"],
}


@dataclass
class ParsedPoll:
    sample_size: int
    fieldwork_start: date
    fieldwork_end: date
    party_macro_percentages: dict[str, dict[str, float]]


@dataclass
class PlannedPollRow:
    party_id: int
    party_name: str
    macro_region: str
    region_id: int
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


def parse_fieldwork(value: str) -> tuple[date, date]:
    normalized_value = re.sub(r"\s+", " ", value.strip().replace("–", "-"))

    cross_month_pattern = re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s*-\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})"
    )
    single_month_pattern = re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s*-\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})"
    )

    match = cross_month_pattern.search(normalized_value)
    if match:
        day_start = int(match.group(1))
        month_start_name = match.group(2)
        day_end = int(match.group(3))
        month_end_name = match.group(4)
        year_end = int(match.group(5))
    else:
        match = single_month_pattern.search(normalized_value)
        if not match:
            raise ValueError(f"Could not parse fieldwork string: {value!r}")
        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month_start_name = match.group(3)
        month_end_name = match.group(3)
        year_end = int(match.group(4))

    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    month_end = month_map.get(month_end_name.lower())
    if month_end is None:
        raise ValueError(f"Unknown month name in fieldwork string: {month_end_name!r}")

    month_start = month_map.get(month_start_name.lower())
    if month_start is None:
        raise ValueError(f"Unknown month name in fieldwork string: {month_start_name!r}")

    year_start = year_end
    if month_start > month_end:
        year_start = year_end - 1

    return date(year_start, month_start, day_start), date(year_end, month_end, day_end)


def extract_pdf_text(pdf_url: str) -> str:
    with urlopen(pdf_url) as response:
        payload = response.read()

    with NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(payload)
        tmp.flush()
        reader = PdfReader(tmp.name)
        return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_headline_vi_table(full_text: str) -> dict[str, dict[str, float]]:
    start_marker = "Westminster Voting Intention"
    end_marker = "Now, thinking specifically"

    start_index = full_text.find(start_marker)
    end_index = full_text.find(end_marker)
    if start_index == -1 or end_index == -1 or end_index <= start_index:
        raise ValueError("Could not isolate Westminster headline voting intention section")

    section = full_text[start_index:end_index]
    lines = [line.strip() for line in section.splitlines() if line.strip()]

    regional_indices = {
        "Wales": -6,
        "Scotland": -5,
        "North": -4,
        "Midlands": -3,
        "London": -2,
        "Rest of South": -1,
    }

    row_labels = sorted(PARTY_NAME_MAP.keys(), key=len, reverse=True)
    result: dict[str, dict[str, float]] = {}

    for line in lines:
        matched_label = None
        for label in row_labels:
            if line.startswith(f"{label} "):
                matched_label = label
                break

        if matched_label is None:
            continue

        values = [int(value) for value in re.findall(r"-?\d+", line)]
        if len(values) < 6:
            continue

        party_name = PARTY_NAME_MAP[matched_label]
        result[party_name] = {
            region: float(values[index])
            for region, index in regional_indices.items()
        }

    missing_parties = [p for p in PARTY_NAME_MAP.values() if p not in result]
    if missing_parties:
        raise ValueError(f"Missing party rows in headline table: {missing_parties}")

    return result


def _normalize_percentage(value: float) -> float:
    return float(int(round(value)))


def parse_poll(pdf_text: str) -> ParsedPoll:
    sample_match = re.search(r"Sample Size:\s*([0-9,]+)", pdf_text)
    if not sample_match:
        raise ValueError("Sample size not found in PDF")

    fieldwork_match = re.search(r"Fieldwork:\s*([^\n]+)", pdf_text)
    if not fieldwork_match:
        raise ValueError("Fieldwork window not found in PDF")

    sample_size = int(sample_match.group(1).replace(",", ""))
    fieldwork_start, fieldwork_end = parse_fieldwork(fieldwork_match.group(1).strip())
    party_macro_percentages = parse_headline_vi_table(pdf_text)

    return ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        party_macro_percentages=party_macro_percentages,
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
    pdf_url: str = DEFAULT_PDF_URL,
    map_name: str = DEFAULT_MAP_NAME,
    pollster_identifier: str = DEFAULT_POLLSTER_IDENTIFIER,
) -> ImportPlan:
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    regions = db.get_regions_for_map(poll_map.id)
    regions_by_name = {normalize_name(region.name): region for region in regions}

    macro_to_region_ids: dict[str, list[int]] = {}
    for macro, region_names in MACRO_TO_INTERNAL_REGIONS.items():
        ids: list[int] = []
        for region_name in region_names:
            region = regions_by_name.get(normalize_name(region_name))
            if region is None:
                raise ValueError(f"Region {region_name!r} not found in map {map_name!r}")
            ids.append(region.id)
        macro_to_region_ids[macro] = ids

    regions_mapping = "\n".join(
        f"{macro}:{','.join(str(region_id) for region_id in region_ids)}"
        for macro, region_ids in macro_to_region_ids.items()
    )

    pdf_text = extract_pdf_text(pdf_url)
    parsed = parse_poll(pdf_text)

    pollster = db.get_pollster_by_identifier(pollster_identifier)
    pollster_exists = pollster is not None

    party_by_name = {party.name: party for party in db.get_all_parties()}
    missing_parties = [
        party_name
        for party_name in PARTY_NAME_MAP.values()
        if party_name not in party_by_name
    ]
    if missing_parties:
        raise ValueError(
            "Missing parties in database (run party importer first): "
            f"{missing_parties}"
        )

    rows: list[PlannedPollRow] = []
    for party_name, macro_values in parsed.party_macro_percentages.items():
        party_id = party_by_name[party_name].id
        for macro_region, percentage in macro_values.items():
            normalized_percentage = _normalize_percentage(percentage)
            for region_id in macro_to_region_ids[macro_region]:
                region_name = next(
                    region.name for region in regions if region.id == region_id
                )
                rows.append(
                    PlannedPollRow(
                        party_id=party_id,
                        party_name=party_name,
                        macro_region=macro_region,
                        region_id=region_id,
                        region_name=region_name,
                        percentage=normalized_percentage,
                    )
                )

    existing_poll = (
        _find_existing_poll(db, pollster.id, poll_map.id, parsed)
        if pollster is not None
        else None
    )

    return ImportPlan(
        pollster_identifier=pollster_identifier,
        pollster_name=(pollster.name if pollster else "YouGov"),
        pollster_id=(pollster.id if pollster else None),
        pollster_exists=pollster_exists,
        regions_mapping=regions_mapping,
        map_id=poll_map.id,
        map_name=poll_map.name,
        source_url=pdf_url,
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
            f"party={row.party_name}, macro={row.macro_region}, "
            f"region_id={row.region_id}, pct={row.percentage}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-url", default=DEFAULT_PDF_URL)
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
    print(f"Fetching PDF: {args.pdf_url}")

    plan = build_import_plan(
        db,
        pdf_url=args.pdf_url,
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
