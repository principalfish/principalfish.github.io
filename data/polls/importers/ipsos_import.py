#!/usr/bin/env python3
"""Import an Ipsos poll directly from a PDF URL."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster
from polls.import_types import PollImportResult

DEFAULT_PDF_URL = "https://www.ipsos.com/sites/default/files/ct/news/documents/2026-01/politmkp_w1jan2026web1.pdf"
DEFAULT_MAP_NAME = "UK Constituencies post 2022"
DEFAULT_POLLSTER_IDENTIFIER = "ipsos"

PARTY_LINE_MAP = {
    "Conservative": "Conservative",
    "Labour": "Labour",
    "Reform UK": "Reform UK",
    "Liberal Democrats": "Liberal Democrats",
    "Green Party": "Green",
    "Scottish National Party": "Scottish National Party",
    "Plaid Cymru": "Plaid Cymru",
    "Other": "Other",
}

MACRO_REGION_TO_INTERNAL = {
    "Wales": ["Wales"],
    "Scotland": ["Scotland"],
    "London": ["London"],
    "South excl London": ["South East England", "South West England"],
    "Midlands incl East of England": ["East Midlands", "West Midlands", "East of England"],
    "North excl Scotland": ["North East England", "North West England", "Yorkshire and The Humber"],
}

REGION_COLUMN_ORDER = [
    "Wales",
    "Scotland",
    "London",
    "South excl London",
    "Midlands incl East of England",
    "North excl Scotland",
]


class ParsedPoll(BaseModel):
    """Parsed poll data extracted from source."""

    sample_size: int = Field(gt=0)
    fieldwork_start: date
    fieldwork_end: date
    party_percentages: dict[str, float]
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
    """Convert a month name or abbreviation to its calendar number.

    Args:
        month_text: Month name or abbreviation (e.g. "Jan", "January", "Sept.").
            Case-insensitive; trailing periods are stripped.

    Returns:
        Integer month number 1–12, or None if the text is not recognised.
    """
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


def extract_pdf_text(pdf_url: str) -> str:
    """Fetch a PDF from a URL and extract its full text content.

    Sends an HTTP GET request with a browser-like User-Agent header, validates
    that the response body is a valid PDF (starts with ``%PDF``), then extracts
    and concatenates the text from every page.

    Args:
        pdf_url: Fully-qualified URL of the PDF to download.

    Returns:
        Concatenated text of all pages, joined by newlines.

    Raises:
        ValueError: If the response body is not a valid PDF.
        urllib.error.URLError: If the HTTP request fails.
    """
    req = Request(pdf_url, headers={"User-Agent": "Mozilla/5.0 (compatible; poll-importer/1.0)"})
    with urlopen(req) as response:
        payload = response.read()

    if not payload.startswith(b"%PDF"):
        raise ValueError(f"Could not fetch PDF payload from URL: {pdf_url}")

    reader = PdfReader(BytesIO(payload))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_lines(pdf_text: str) -> list[str]:
    """Split raw PDF text into non-empty, stripped lines.

    Args:
        pdf_text: Full text extracted from a PDF document.

    Returns:
        List of non-blank lines with leading/trailing whitespace removed.
    """
    return [line.strip() for line in pdf_text.splitlines() if line.strip()]


def _parse_fieldwork(lines: list[str]) -> tuple[date, date]:
    """Parse fieldwork start and end dates from PDF lines.

    Searches for a line containing "Fieldwork dates" and extracts the date
    range using a regex. Handles cross-month ranges (e.g. "31 January to
    3 February 2026") by inferring that the start year is one less than the
    end year when the start month is later in the calendar than the end month.

    Args:
        lines: Non-empty, stripped lines from the PDF text.

    Returns:
        A ``(fieldwork_start, fieldwork_end)`` tuple of :class:`datetime.date`
        objects.

    Raises:
        ValueError: If no fieldwork line is found, or the date pattern does
            not match, or a month name cannot be parsed.
    """
    fieldwork_line = next((line for line in lines if "fieldwork dates" in line.lower()), None)
    if fieldwork_line is None:
        raise ValueError("Fieldwork line not found in PDF")

    pattern = re.compile(
        r"Fieldwork\s+dates\s*[-:]\s*[A-Za-z]+\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+([A-Za-z.]+))?\s+to\s+[A-Za-z]+\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z.]+)(?:\s*\([^)]*\))?\s+(20\d{2})",
        re.IGNORECASE,
    )
    match = pattern.search(fieldwork_line)
    if not match:
        raise ValueError(f"Could not parse fieldwork line: {fieldwork_line!r}")

    day_start = int(match.group(1))
    month_start_text = (match.group(2) or match.group(4)).strip()
    day_end = int(match.group(3))
    month_end_text = match.group(4).strip()
    year_end = int(match.group(5))

    month_start = _month_number(month_start_text)
    month_end = _month_number(month_end_text)
    if month_start is None or month_end is None:
        raise ValueError(f"Could not parse month names in fieldwork line: {fieldwork_line!r}")

    year_start = year_end - 1 if month_start > month_end else year_end
    return date(year_start, month_start, day_start), date(year_end, month_end, day_end)


def _parse_sample_size(lines: list[str]) -> int:
    """Parse the unweighted sample size from PDF lines.

    Searches for a line containing "unweighted total" or "unweighted sample"
    and extracts the first 3-to-6-digit number found on that line.

    Args:
        lines: Non-empty, stripped lines from the PDF text.

    Returns:
        The unweighted sample size as a positive integer.

    Raises:
        ValueError: If no matching line is found or no number can be extracted.
    """
    sample_line = next(
        (
            line
            for line in lines
            if "unweighted total" in line.lower() or "unweighted sample" in line.lower()
        ),
        None,
    )
    if sample_line is None:
        raise ValueError("Could not find unweighted sample line in PDF")

    values = [int(value) for value in re.findall(r"\d{3,6}", sample_line)]
    if not values:
        raise ValueError(f"Could not parse sample size from line: {sample_line!r}")
    return values[0]


def _line_percentage(line: str) -> float | None:
    """Extract the first percentage value from a line of text.

    Matches patterns of the form ``<digits>%`` (with optional whitespace
    between the digits and the percent sign) and returns the first match as
    a float.

    Args:
        line: A single line of text, typically from a PDF page.

    Returns:
        The first percentage value found as a float, or None if no match.
    """
    matches = re.findall(r"(\d{1,2})\s*%", line)
    if not matches:
        return None
    return float(int(matches[0]))


def _parse_party_percentages(lines: list[str]) -> dict[str, float]:
    """Parse national voting intention percentages for each party.

    Scans from the "combined voting intention - all" section header and
    matches lines against ``PARTY_LINE_MAP``. If a percentage is not found
    on the party name line itself, the immediately following line is also
    checked.

    Args:
        lines: Non-empty, stripped lines from the PDF text.

    Returns:
        Dict mapping canonical party name to its national percentage (0–100).

    Raises:
        ValueError: If the section header is not found or any party percentage
            is missing.
    """
    start_idx = next(
        (
            index
            for index, line in enumerate(lines)
            if "combined voting intention - all" in line.lower()
        ),
        None,
    )
    if start_idx is None:
        raise ValueError("Could not find Ipsos combined voting intention (ALL) section")

    parsed: dict[str, float] = {}
    for index in range(start_idx, len(lines)):
        line = lines[index]
        line_lower = line.lower()

        for marker, canonical_party in PARTY_LINE_MAP.items():
            if marker.lower() not in line_lower:
                continue

            pct = _line_percentage(line)
            if pct is None and index + 1 < len(lines):
                pct = _line_percentage(lines[index + 1])
            if pct is not None:
                parsed[canonical_party] = pct

        if len(parsed) == len(PARTY_LINE_MAP):
            break

    missing = sorted(set(PARTY_LINE_MAP.values()) - set(parsed.keys()))
    if missing:
        raise ValueError(f"Missing party percentages in Ipsos PDF: {missing}")

    return parsed


def _parse_percentage_tokens(raw_line: str) -> list[float | None]:
    """Tokenise a compacted percentage row from a regional cross-tab table.

    Removes all whitespace from the line and then greedily scans for two
    token types:

    - ``<digits>%`` → parsed as a float percentage value.
    - ``-`` or ``-s`` → treated as a suppressed/missing cell (``None``).

    Any other characters are skipped.

    Args:
        raw_line: A single line of text containing percentage values and
            suppression markers, as extracted from a PDF cross-tab row.

    Returns:
        Ordered list of tokens; each element is either a float percentage
        or ``None`` for a suppressed cell.
    """
    compact = "".join(raw_line.split())
    tokens: list[float | None] = []
    index = 0
    while index < len(compact):
        number_match = re.match(r"(\d{1,3})%", compact[index:])
        if number_match:
            tokens.append(float(int(number_match.group(1))))
            index += len(number_match.group(0))
            continue

        missing_match = re.match(r"-(?:s)?", compact[index:])
        if missing_match:
            tokens.append(None)
            index += len(missing_match.group(0))
            continue

        index += 1

    return tokens


def _has_eight_consecutive_nones(tokens: list[float | None]) -> bool:
    """Return True if the token list contains 8 consecutive None values.

    This is the signature of the March 2026+ extended column layout, where the
    ONS area supergroup columns are suppressed (small bases) and appear as a
    contiguous block of Nones in the middle of each party percentage row.
    """
    count = 0
    for token in tokens:
        if token is None:
            count += 1
            if count >= 8:
                return True
        else:
            count = 0
    return False


def _extract_region_values(tokens: list[float | None]) -> list[float] | None:
    """Extract 6 regional percentage values from a parsed token list.

    Handles two column layouts that appear in different Ipsos PDF editions:
    - Standard layout: regional columns at tokens[-14:-8] = [Wales, Scotland,
      London, South excl London, Midlands incl E of England, North excl Scotland]
    - Extended layout (from March 2026): 8 consecutive suppressed ONS area
      supergroup columns appear in the middle of the row; a new Greater England
      column was also inserted. Regional columns are at positions [-9], [-8],
      skip [-7] (Greater England), then [-6], [-5], [-4], [-3]. Suppressed
      regions (None) are treated as 0%.
    """
    if _has_eight_consecutive_nones(tokens):
        extended = [tokens[i] for i in (-9, -8, -6, -5, -4, -3)]
        return [float(v) if v is not None else 0.0 for v in extended]

    standard = tokens[-14:-8]
    if len(standard) == 6 and not any(v is None for v in standard):
        return [float(v) for v in standard]  # type: ignore[arg-type]

    return None


def _parse_party_region_percentages(lines: list[str]) -> dict[str, dict[str, float]]:
    """Parse regional voting intention percentages for each party.

    Scans from the "combined voting intention - likely to vote" section header
    (up to 220 lines ahead) looking for party name lines whose immediately
    following line contains ``%`` values. Delegates token extraction and region
    column layout detection to ``_parse_percentage_tokens`` and
    ``_extract_region_values``.

    Only the first match per party is recorded to avoid picking up duplicate
    cross-tabs from the same PDF with incompatible column layouts.

    Optional parties (SNP, Plaid Cymru, Other) that are absent from the PDF
    are defaulted to 0% in all regions. If fewer than 5 parties are found the
    section is treated as absent and an empty dict is returned.

    Args:
        lines: Non-empty, stripped lines from the PDF text.

    Returns:
        Dict mapping canonical party name to a dict of
        ``{macro_region_name: percentage}`` for each region in
        ``REGION_COLUMN_ORDER``. Returns an empty dict if the section is not
        present or is too sparse.
    """
    start_idx = next(
        (
            index
            for index, line in enumerate(lines)
            if "combined voting intention - likely to vote" in line.lower()
        ),
        None,
    )
    if start_idx is None:
        return {}

    parsed: dict[str, dict[str, float]] = {}
    window_end = min(len(lines), start_idx + 220)

    for index in range(start_idx, window_end - 1):
        line = lines[index]
        next_line = lines[index + 1]
        if "%" not in next_line:
            continue

        canonical_party = None
        for marker, party_name in PARTY_LINE_MAP.items():
            if marker.lower() in line.lower():
                canonical_party = party_name
                break

        if canonical_party is None:
            continue

        # Take the first valid match per party to avoid cross-tab contamination:
        # PDFs with multiple cross-tabs under "likely to vote" can produce multiple
        # matches, and subsequent tables use different column layouts.
        if canonical_party in parsed:
            continue

        tokens = _parse_percentage_tokens(next_line)
        if len(tokens) < 14:
            continue

        region_values = _extract_region_values(tokens)
        if region_values is None:
            continue

        parsed[canonical_party] = {
            REGION_COLUMN_ORDER[position]: region_values[position]
            for position in range(len(REGION_COLUMN_ORDER))
        }

    if len(parsed) < 5:
        return {}

    for optional_party in ["Scottish National Party", "Plaid Cymru", "Other"]:
        parsed.setdefault(
            optional_party,
            {region_name: 0.0 for region_name in REGION_COLUMN_ORDER},
        )

    return parsed


def parse_poll(pdf_text: str) -> ParsedPoll:
    """Parse all structured poll data from raw Ipsos PDF text.

    Orchestrates the individual parse helpers to extract sample size, fieldwork
    dates, national party percentages, and regional party percentages.

    Args:
        pdf_text: Full text content of an Ipsos PDF, as returned by
            :func:`extract_pdf_text`.

    Returns:
        A :class:`ParsedPoll` instance populated with all extracted data.

    Raises:
        ValueError: If any required section or value cannot be parsed from the
            text (propagated from the individual parse helpers).
    """
    lines = _extract_lines(pdf_text)
    sample_size = _parse_sample_size(lines)
    fieldwork_start, fieldwork_end = _parse_fieldwork(lines)
    party_percentages = _parse_party_percentages(lines)
    party_region_percentages = _parse_party_region_percentages(lines)
    return ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        party_percentages=party_percentages,
        party_region_percentages=party_region_percentages,
    )


def _find_existing_poll(db: Database, pollster_id: int, map_id: int, parsed: ParsedPoll) -> Poll | None:
    """Look up an existing poll row matching the parsed poll's key fields.

    Matches on pollster, map, fieldwork start/end dates, and sample size.

    Args:
        db: Active :class:`Database` instance.
        pollster_id: Primary key of the pollster in the database.
        map_id: Primary key of the constituency map in the database.
        parsed: Parsed poll data containing the fieldwork dates and sample size
            to match against.

    Returns:
        The matching :class:`Poll` ORM instance, or ``None`` if not found.
    """
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
    """Fetch an Ipsos PDF and build a complete import plan without writing to the DB.

    Downloads the PDF, parses it, resolves pollster/map/party/region records
    from the database, and assembles the full set of :class:`PlannedPollRow`
    objects (national + regional) that would be inserted on commit.

    All required parties must already exist in the database; call the party
    importer first if they are missing.

    Args:
        db: Active :class:`Database` instance.
        pdf_url: URL of the Ipsos PDF to import. Defaults to
            ``DEFAULT_PDF_URL``.
        map_name: Name of the constituency map to associate with the poll.
            Defaults to ``DEFAULT_MAP_NAME``.
        pollster_identifier: Identifier string for the Ipsos pollster record.
            Defaults to ``DEFAULT_POLLSTER_IDENTIFIER``.

    Returns:
        A fully-populated :class:`ImportPlan` describing what would be created
        or updated on commit, including whether the pollster and poll already
        exist.

    Raises:
        ValueError: If the map is not found, any party is missing from the
            database, or any internal region referenced by
            ``MACRO_REGION_TO_INTERNAL`` is absent.
    """
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    pdf_text = extract_pdf_text(pdf_url)
    parsed = parse_poll(pdf_text)

    pollster = db.get_pollster_by_identifier(pollster_identifier)
    pollster_exists = pollster is not None

    party_by_name = {party.name: party for party in db.get_all_parties()}
    missing_parties = [
        party_name
        for party_name in sorted(set(PARTY_LINE_MAP.values()))
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

    if parsed.party_region_percentages:
        for party_name, macro_percentages in parsed.party_region_percentages.items():
            for macro_region_name, macro_percentage in macro_percentages.items():
                for internal_region_name in MACRO_REGION_TO_INTERNAL[macro_region_name]:
                    region = region_by_name.get(internal_region_name)
                    if region is None:
                        raise ValueError(f"Missing region in database: {internal_region_name!r}")

                    rows.append(
                        PlannedPollRow(
                            party_id=party_by_name[party_name].id,
                            party_name=party_name,
                            region_id=region.id,
                            region_name=region.name,
                            percentage=macro_percentage,
                        )
                    )

    existing_poll = (
        _find_existing_poll(db, pollster.id, poll_map.id, parsed)
        if pollster is not None
        else None
    )

    return ImportPlan(
        pollster_identifier=pollster_identifier,
        pollster_name=(pollster.name if pollster else "Ipsos"),
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
) -> PollImportResult:
    """Write an import plan to the database.

    Creates the pollster if it does not exist, creates the poll record if it
    does not exist (or updates ``source_url`` if it does), and inserts the
    planned poll rows. If rows already exist for the poll:

    - With ``replace_rows=False`` (default): skips insertion and sets
      ``skipped_existing_rows=True`` in the result.
    - With ``replace_rows=True``: deletes all existing rows before inserting
      the new ones.

    Args:
        db: Active :class:`Database` instance.
        plan: Import plan produced by :func:`build_import_plan`.
        replace_rows: If ``True``, delete existing poll rows before inserting.
            Defaults to ``False``.

    Returns:
        A :class:`PollImportResult` summarising what was created, inserted, or
        skipped.

    Raises:
        ValueError: If the pollster lookup fails during commit (should not
            occur under normal conditions).
    """
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
    """Print a dry-run preview of an import plan to stdout.

    Displays the parsed poll metadata, whether the pollster and poll records
    already exist, and the full list of rows that would be inserted.

    Args:
        plan: Import plan produced by :func:`build_import_plan`.
    """
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
    """CLI entry point for the Ipsos poll importer.

    Parses command-line arguments, fetches and parses the Ipsos PDF, and
    either prints a dry-run preview or commits the import plan to the database.

    Command-line arguments:

    - ``--pdf-url`` (str, optional): URL of the Ipsos PDF to import.
      Defaults to ``DEFAULT_PDF_URL``.
    - ``--map-name`` (str, optional): Name of the constituency map.
      Defaults to ``DEFAULT_MAP_NAME``.
    - ``--pollster-identifier`` (str, optional): Pollster identifier string.
      Defaults to ``DEFAULT_POLLSTER_IDENTIFIER``.
    - ``--replace-rows`` (flag): Delete existing poll rows before inserting.
    - ``--dry-run`` (flag): Print a preview without writing to the database.
    """
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
