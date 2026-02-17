#!/usr/bin/env python3
"""Import a Techne poll directly from a PDF URL."""

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

DEFAULT_PDF_URL = "https://www.techneuk.com/wp-content/uploads/2026/02/R162-UK-2026-2-13-DATA.pdf"
DEFAULT_MAP_NAME = "UK Constituencies post 2022"
DEFAULT_POLLSTER_IDENTIFIER = "techne"

PARTY_NAME_MAP = {
    "Reform UK": "Reform UK",
    "Conservative and Unionist Party": "Conservative",
    "Conservative": "Conservative",
    "Labour Party": "Labour",
    "Labour": "Labour",
    "Liberal Democrats": "Liberal Democrats",
    "Liberal Democrat": "Liberal Democrats",
    "Green Party": "Green",
    "Scottish National Party": "Scottish National Party",
    "Plaid Cymru": "Plaid Cymru",
    "Other party": "Other",
    "Other": "Other",
}


@dataclass
class ParsedPoll:
    sample_size: int
    fieldwork_start: date
    fieldwork_end: date
    party_percentages: dict[str, float]


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
    return month_map.get(month_text.lower().rstrip("."))


def _infer_year(pdf_url: str, fallback: int | None = None) -> int:
    match = re.search(r"/(20\d{2})/", pdf_url)
    if match:
        return int(match.group(1))
    matches = re.findall(r"(20\d{2})", pdf_url)
    if matches:
        return int(matches[0])
    if fallback is not None:
        return fallback
    raise ValueError("Could not infer year from PDF URL")


def extract_pdf_text(pdf_url: str) -> str:
    with urlopen(pdf_url) as response:
        payload = response.read()

    with NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(payload)
        tmp.flush()
        reader = PdfReader(tmp.name)
        return "\n".join((page.extract_text() or "") for page in reader.pages)


def _parse_fieldwork(pdf_text: str, year: int) -> tuple[date, date]:
    pattern = re.compile(
        r"FIELDWORK:\s*([A-Za-z]+)\s*(\d{1,2})(?:st|nd|rd|th)?\s*-\s*([A-Za-z]+)\s*(\d{1,2})(?:st|nd|rd|th)?",
        re.IGNORECASE,
    )
    match = pattern.search(pdf_text)
    if not match:
        raise ValueError("Fieldwork window not found in PDF")

    month_start = _month_number(match.group(1))
    day_start = int(match.group(2))
    month_end = _month_number(match.group(3))
    day_end = int(match.group(4))
    if month_start is None or month_end is None:
        raise ValueError("Unrecognized month in fieldwork window")

    year_start = year - 1 if month_start > month_end else year
    return date(year_start, month_start, day_start), date(year, month_end, day_end)


def _parse_sample_size(pdf_text: str) -> int:
    match = re.search(r"Unweighted Sample\s*(\d{3,5})", pdf_text)
    if not match:
        raise ValueError("Unweighted sample size not found in PDF")
    return int(match.group(1))


def _party_percentage_from_line(line: str) -> float:
    values = [int(v) for v in re.findall(r"(\d{1,2})%", line)]
    if len(values) < 2:
        raise ValueError(f"Could not parse headline percentages from line: {line!r}")
    return float(values[1])


def _parse_party_percentages(pdf_text: str) -> dict[str, float]:
    def _extract(text: str) -> dict[str, float]:
        extracted: dict[str, float] = {}
        for raw_party, canonical_party in PARTY_NAME_MAP.items():
            party_pattern = re.compile(
                re.escape(raw_party) + r"\s*(\d{1,2})%\s*(\d{1,2})%",
                re.IGNORECASE,
            )
            matches = list(party_pattern.finditer(text))
            if not matches:
                continue
            second_value = int(matches[-1].group(2))
            extracted[canonical_party] = float(second_value)
        return extracted

    block_pattern = re.compile(
        r"Which political party would you vote for\?\s*\[all cases\](.*?)(?:Which political party would you vote for\?\s*\[only who indicates a pol party\]|$)",
        re.IGNORECASE | re.DOTALL,
    )
    block_match = block_pattern.search(pdf_text)
    block_text = block_match.group(1) if block_match else ""

    result = _extract(block_text) if block_text else {}
    if len(result) < 5:
        result = _extract(pdf_text)

    for optional_party in ["Scottish National Party", "Plaid Cymru", "Other"]:
        result.setdefault(optional_party, 0.0)

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
    missing = sorted(required_parties - set(result.keys()))
    if missing:
        raise ValueError(f"Missing expected party rows in PDF: {missing}")

    return result


def parse_poll(pdf_text: str, *, inferred_year: int) -> ParsedPoll:
    sample_size = _parse_sample_size(pdf_text)
    fieldwork_start, fieldwork_end = _parse_fieldwork(pdf_text, inferred_year)
    party_percentages = _parse_party_percentages(pdf_text)
    return ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        party_percentages=party_percentages,
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
    pdf_url: str = DEFAULT_PDF_URL,
    map_name: str = DEFAULT_MAP_NAME,
    pollster_identifier: str = DEFAULT_POLLSTER_IDENTIFIER,
    year_hint: int | None = None,
) -> ImportPlan:
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    inferred_year = _infer_year(pdf_url, fallback=year_hint)
    pdf_text = extract_pdf_text(pdf_url)
    parsed = parse_poll(pdf_text, inferred_year=inferred_year)

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

    existing_poll = (
        _find_existing_poll(db, pollster.id, poll_map.id, parsed)
        if pollster is not None
        else None
    )

    return ImportPlan(
        pollster_identifier=pollster_identifier,
        pollster_name=(pollster.name if pollster else "Techne"),
        pollster_id=(pollster.id if pollster else None),
        pollster_exists=pollster_exists,
        regions_mapping="",
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
    parser.add_argument("--pdf-url", default=DEFAULT_PDF_URL)
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
    print(f"Fetching PDF: {args.pdf_url}")

    plan = build_import_plan(
        db,
        pdf_url=args.pdf_url,
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
