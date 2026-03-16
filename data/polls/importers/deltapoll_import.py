#!/usr/bin/env python3
"""Import a Deltapoll poll from PDF or HTML source URLs."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster
from polls.import_types import PollImportResult

DEFAULT_SOURCE_URL = "https://deltapoll.co.uk/wp-content/uploads/2026/01/260105_Deltapoll-Mirror-pdf.pdf"
DEFAULT_MAP_NAME = "UK Constituencies post 2022"
DEFAULT_POLLSTER_IDENTIFIER = "deltapoll"

NATIONAL_KEY = "__national__"

PARTY_LABEL_TO_CANONICAL = {
    "Conservative": "Conservative",
    "Labour": "Labour",
    "Liberal Democrat": "Liberal Democrats",
    "Liberal Democrats": "Liberal Democrats",
    "Reform UK": "Reform UK",
    "Scottish National Party": "Scottish National Party",
    "Scottish National Party (SNP)": "Scottish National Party",
    "SNP": "Scottish National Party",
    "Plaid Cymru": "Plaid Cymru",
    "Plaid Cymru (PC)": "Plaid Cymru",
    "Green": "Green",
    "Other": "Other",
}

MACRO_TO_INTERNAL_REGIONS = {
    "London": ["London"],
    "Rest of South": ["East of England", "South East England", "South West England"],
    "Midlands": ["East Midlands", "West Midlands"],
    "North": ["North East England", "North West England", "Yorkshire and The Humber"],
    "Wales": ["Wales"],
    "Scotland": ["Scotland"],
}

MACRO_ORDER = ["London", "Rest of South", "Midlands", "North", "Wales", "Scotland"]


class ParsedPoll(BaseModel):
    """Parsed poll data extracted from source."""

    sample_size: int = Field(gt=0)
    fieldwork_start: date
    fieldwork_end: date
    party_region_percentages: dict[str, dict[str, float]]


class PlannedPollRow(BaseModel):
    """A single poll row planned for DB insertion."""

    party_id: int
    party_name: str
    region_id: int | None
    region_name: str
    percentage: float = Field(ge=0, le=100)


class ImportPlan(BaseModel):
    """Full import plan: pollster, poll metadata, and rows."""

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
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; poll-importer/1.0)"})
    with urlopen(req, timeout=60) as response:
        data: bytes = response.read()
        return data


def _resolve_pdf_url(source_url: str) -> str:
    lower = source_url.lower()
    if lower.endswith(".pdf"):
        return source_url

    html = _fetch_bytes(source_url).decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    candidates: list[str] = []
    for anchor in soup.select("a[href]"):
        raw_href = anchor.get("href", "")
        href = raw_href.strip() if isinstance(raw_href, str) else ""
        if not href:
            continue
        absolute = urljoin(source_url, href)
        if absolute.lower().endswith(".pdf"):
            candidates.append(absolute)

    if not candidates:
        raise ValueError(f"No PDF link found in Deltapoll HTML page: {source_url}")

    return candidates[0]


def _extract_pdf_text(pdf_url: str) -> str:
    candidate_urls = [pdf_url]
    if "web.archive.org/web/" in pdf_url and "if_/" not in pdf_url:
        candidate_urls.append(
            re.sub(r"/web/(\d+)/https://", r"/web/\1if_/https://", pdf_url, count=1)
        )

    payload = None
    for candidate in candidate_urls:
        try:
            data = _fetch_bytes(candidate)
        except Exception:
            continue
        if data.startswith(b"%PDF"):
            payload = data
            break

    if payload is None:
        raise ValueError(f"Could not fetch PDF payload from URL: {pdf_url}")

    reader = PdfReader(BytesIO(payload))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_lines(pdf_text: str) -> list[str]:
    return [line.strip() for line in pdf_text.splitlines() if line.strip()]


def _parse_fieldwork(lines: list[str]) -> tuple[date, date]:
    fieldwork_line = next((line for line in lines if line.lower().startswith("fieldwork:")), None)
    if fieldwork_line is None:
        raise ValueError("Fieldwork line not found in Deltapoll PDF")

    normalized = fieldwork_line.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"(\d)(st|nd|rd|th)", r"\1", normalized, flags=re.IGNORECASE)

    pattern_cross = re.compile(r"Fieldwork:\s*(\d{1,2})\s+([A-Za-z]+)\s+\d{4}\s+to\s+(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", re.IGNORECASE)
    match = pattern_cross.search(normalized)
    if match:
        day_start = int(match.group(1))
        month_start = _month_number(match.group(2))
        day_end = int(match.group(3))
        month_end = _month_number(match.group(4))
        year_end = int(match.group(5))
        if month_start is None or month_end is None:
            raise ValueError(f"Could not parse fieldwork months from: {fieldwork_line!r}")
        year_start = year_end - 1 if month_start > month_end else year_end
        return date(year_start, month_start, day_start), date(year_end, month_end, day_end)

    pattern_cross_alt = re.compile(r"Fieldwork:\s*(\d{1,2})\s+([A-Za-z]+)\s+to\s+(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", re.IGNORECASE)
    match = pattern_cross_alt.search(normalized)
    if match:
        day_start = int(match.group(1))
        month_start = _month_number(match.group(2))
        day_end = int(match.group(3))
        month_end = _month_number(match.group(4))
        year_end = int(match.group(5))
        if month_start is None or month_end is None:
            raise ValueError(f"Could not parse fieldwork months from: {fieldwork_line!r}")
        year_start = year_end - 1 if month_start > month_end else year_end
        return date(year_start, month_start, day_start), date(year_end, month_end, day_end)

    pattern_same_hyphen = re.compile(r"Fieldwork:\s*(\d{1,2})\s*-\s*(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", re.IGNORECASE)
    match = pattern_same_hyphen.search(normalized)
    if match:
        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month = _month_number(match.group(3))
        year = int(match.group(4))
        if month is None:
            raise ValueError(f"Could not parse fieldwork month from: {fieldwork_line!r}")
        return date(year, month, day_start), date(year, month, day_end)

    pattern_same_to = re.compile(r"Fieldwork:\s*(\d{1,2})\s+to\s*(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", re.IGNORECASE)
    match = pattern_same_to.search(normalized)
    if match:
        day_start = int(match.group(1))
        day_end = int(match.group(2))
        month = _month_number(match.group(3))
        year = int(match.group(4))
        if month is None:
            raise ValueError(f"Could not parse fieldwork month from: {fieldwork_line!r}")
        return date(year, month, day_start), date(year, month, day_end)

    raise ValueError(f"Could not parse fieldwork line: {fieldwork_line!r}")


def _parse_sample_size(lines: list[str]) -> int:
    sample_line = next((line for line in lines if line.lower().startswith("sample size:")), None)
    if sample_line is None:
        raise ValueError("Sample Size line not found in Deltapoll PDF")
    digits = re.sub(r"[^0-9]", "", sample_line)
    if not digits:
        raise ValueError(f"Could not parse sample size from line: {sample_line!r}")
    return int(digits)


def _canonical_party_from_line(line: str) -> str | None:
    normalized = " ".join(line.strip().split())
    for raw, canonical in PARTY_LABEL_TO_CANONICAL.items():
        if normalized.startswith(raw):
            return canonical
    return None


def _extract_party_order_and_national(lines: list[str]) -> tuple[list[str], dict[str, float]]:
    start_idx = next(
        (
            i
            for i, line in enumerate(lines)
            if "voting intention" in line.lower()
        ),
        None,
    )
    if start_idx is None:
        raise ValueError("Could not find Q1 voting intention section in Deltapoll PDF")

    party_order: list[str] = []
    national: dict[str, float] = {}

    for line in lines[start_idx:start_idx + 60]:
        party_name = _canonical_party_from_line(line)
        if party_name is None:
            continue
        values = [int(v) for v in re.findall(r"-?\d+", line)]
        if not values:
            continue
        party_order.append(party_name)
        national[party_name] = float(values[0])
        if len(party_order) >= 8:
            break

    required_core = {
        "Conservative",
        "Labour",
        "Liberal Democrats",
        "Reform UK",
        "Green",
        "Scottish National Party",
        "Plaid Cymru",
    }
    missing = sorted(required_core - set(national.keys()))
    if missing:
        raise ValueError(f"Missing expected party rows in Deltapoll Q1 table: {missing}")

    national.setdefault("Other", 0.0)
    if "Other" not in party_order:
        party_order.append("Other")

    return party_order, national


def _parse_regional_from_block(lines: list[str], party_order: list[str]) -> dict[str, dict[str, float]]:
    header_idx = next(
        (
            idx
            for idx, line in enumerate(lines)
            if "total london rest of south midlands north wales scotland" in line.lower()
        ),
        None,
    )
    if header_idx is None:
        raise ValueError("Could not find regional header block in Deltapoll PDF")

    rows: list[list[int]] = []
    for line in lines[header_idx + 1:header_idx + 35]:
        values = [int(v) for v in re.findall(r"-?\d+", line)]
        if len(values) == 8 and all(0 <= value <= 100 for value in values):
            rows.append(values)
            if len(rows) >= len(party_order):
                break

    if len(rows) < len(party_order) - 1:
        raise ValueError("Could not parse complete regional rows from Deltapoll block")

    parsed: dict[str, dict[str, float]] = {}
    for idx, party_name in enumerate(party_order):
        if idx >= len(rows):
            parsed[party_name] = {macro: 0.0 for macro in MACRO_ORDER}
            continue
        values = rows[idx]
        parsed[party_name] = {
            "London": float(values[0]),
            "Rest of South": float(values[1]),
            "Midlands": float(values[2]),
            "North": float(values[3]),
            "Wales": float(values[4]),
            "Scotland": float(values[5]),
        }

    return parsed


def parse_poll(pdf_text: str) -> ParsedPoll:
    lines = _extract_lines(pdf_text)
    sample_size = _parse_sample_size(lines)
    fieldwork_start, fieldwork_end = _parse_fieldwork(lines)

    party_order, national = _extract_party_order_and_national(lines)
    try:
        regional = _parse_regional_from_block(lines, party_order)
    except Exception:
        regional = {party_name: {} for party_name in national.keys()}

    party_region_percentages: dict[str, dict[str, float]] = {}
    for party_name, national_pct in national.items():
        party_region_percentages[party_name] = {NATIONAL_KEY: national_pct}
        party_region_percentages[party_name].update(regional.get(party_name, {}))

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
    source_url: str = DEFAULT_SOURCE_URL,
    map_name: str = DEFAULT_MAP_NAME,
    pollster_identifier: str = DEFAULT_POLLSTER_IDENTIFIER,
) -> ImportPlan:
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    regions = db.get_regions_for_map(poll_map.id)
    regions_by_name = {region.name: region for region in regions}

    resolved_pdf_url = _resolve_pdf_url(source_url)
    parsed = parse_poll(_extract_pdf_text(resolved_pdf_url))

    pollster = db.get_pollster_by_identifier(pollster_identifier)
    pollster_exists = pollster is not None

    party_by_name = {party.name: party for party in db.get_all_parties()}
    required_parties = sorted(set(PARTY_LABEL_TO_CANONICAL.values()))
    missing_parties = [party_name for party_name in required_parties if party_name not in party_by_name]
    if missing_parties:
        raise ValueError(
            "Missing parties in database (run party importer first): "
            f"{missing_parties}"
        )

    rows: list[PlannedPollRow] = []
    for party_name, region_values in parsed.party_region_percentages.items():
        party_id = party_by_name[party_name].id

        national_pct = region_values.get(NATIONAL_KEY)
        if national_pct is not None:
            rows.append(
                PlannedPollRow(
                    party_id=party_id,
                    party_name=party_name,
                    region_id=None,
                    region_name="National",
                    percentage=national_pct,
                )
            )

        for macro in MACRO_ORDER:
            macro_pct = region_values.get(macro)
            if macro_pct is None:
                continue
            for internal_region_name in MACRO_TO_INTERNAL_REGIONS[macro]:
                region = regions_by_name.get(internal_region_name)
                if region is None:
                    raise ValueError(f"Region {internal_region_name!r} not found in map {map_name!r}")
                rows.append(
                    PlannedPollRow(
                        party_id=party_id,
                        party_name=party_name,
                        region_id=region.id,
                        region_name=region.name,
                        percentage=macro_pct,
                    )
                )

    existing_poll = (
        _find_existing_poll(db, pollster.id, poll_map.id, parsed)
        if pollster is not None
        else None
    )

    return ImportPlan(
        pollster_identifier=pollster_identifier,
        pollster_name=(pollster.name if pollster else "Deltapoll"),
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
) -> PollImportResult:
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
            return PollImportResult(
                created_pollster=created_pollster,
                created_poll=created_poll_row,
                poll_id=poll_id,
                inserted_rows=0,
                replaced_rows=0,
                skipped_existing_rows=True,
            )

        replaced_rows = 0
        if existing_rows and replace_rows:
            for existing_row in existing_rows:
                session.delete(existing_row)
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

    return PollImportResult(
        created_pollster=created_pollster,
        created_poll=created_poll_row,
        poll_id=poll_id,
        inserted_rows=len(plan.rows),
        replaced_rows=replaced_rows,
        skipped_existing_rows=False,
    )


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

    for row in plan.rows[:30]:
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
    if result.created_pollster:
        print(f"created pollster: {args.pollster_identifier}")
    else:
        print(f"pollster exists: {args.pollster_identifier}")

    if result.created_poll:
        print(f"created poll: {result.poll_id}")
    else:
        print(f"poll exists: {result.poll_id}")

    if result.skipped_existing_rows:
        print(
            f"poll {result.poll_id} already has rows; "
            "use --replace-rows to overwrite"
        )
    else:
        if result.replaced_rows:
            print(f"deleted existing rows: {result.replaced_rows}")
        print(f"inserted poll rows: {result.inserted_rows}")


if __name__ == "__main__":
    main()
