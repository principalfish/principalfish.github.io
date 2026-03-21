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
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen

from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster
from polls.importers.types import PollImportResult

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


class ParsedPoll(BaseModel):
    """Parsed poll data extracted from a YouGov PDF."""

    sample_size: int = Field(gt=0)
    fieldwork_start: date
    fieldwork_end: date
    party_macro_percentages: dict[str, dict[str, float]]


class PlannedPollRow(BaseModel):
    """A single poll row planned for DB insertion, keyed by macro region."""

    party_id: int
    party_name: str
    macro_region: str
    region_id: int
    region_name: str
    percentage: float = Field(ge=0, le=100)


class ImportPlan(BaseModel):
    """Full import plan for a YouGov poll: pollster, poll, and rows."""

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
    """Normalise a region or party name for case-insensitive lookup.

    Strips leading/trailing whitespace, collapses internal whitespace to single
    spaces, and lowercases the result.

    Args:
        value: Raw name string to normalise.

    Returns:
        Lowercased, whitespace-normalised version of the input.
    """
    return " ".join(value.strip().split()).lower()


def parse_fieldwork(value: str) -> tuple[date, date]:
    """Parse a fieldwork date-range string into a start and end date.

    Handles two common YouGov formats:
    - Same-month range: ``"3-7 February 2025"``
    - Cross-month range: ``"28 January - 3 February 2025"``

    En-dashes (``–``) are normalised to hyphens before matching.  The start
    year is derived from the end year; if the start month is later than the end
    month the start year is decremented by one to handle year boundaries.

    Args:
        value: Raw fieldwork string as extracted from the PDF, e.g.
            ``"Fieldwork: 3–7 February 2025"``.

    Returns:
        A ``(fieldwork_start, fieldwork_end)`` tuple of :class:`datetime.date`
        objects.

    Raises:
        ValueError: If the string does not match either supported pattern, or
            if a month name cannot be resolved.
    """
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
    """Download a PDF from ``pdf_url`` and return its full extracted text.

    The PDF is written to a temporary file so that :class:`pypdf.PdfReader`
    can read it by path.  Text from all pages is joined with newlines.

    Args:
        pdf_url: Fully-qualified HTTP/HTTPS URL of the YouGov PDF document.

    Returns:
        Concatenated text content of all pages in the PDF.

    Raises:
        urllib.error.URLError: If the PDF cannot be fetched.
        pypdf.errors.PdfReadError: If the file is not a valid PDF.
    """
    with urlopen(pdf_url) as response:
        payload = response.read()

    with NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(payload)
        tmp.flush()
        reader = PdfReader(tmp.name)
        return "\n".join((page.extract_text() or "") for page in reader.pages)


_OLD_FORMAT_REGIONAL_INDICES = {
    "Wales": -6,
    "Scotland": -5,
    "North": -4,
    "Midlands": -3,
    "London": -2,
    "Rest of South": -1,
}

_ENGLAND_REGION_ORDER = ["North", "Midlands", "London", "Rest of South"]


def _parse_old_format_rows(
    lines: list[str],
    row_labels: list[str],
) -> dict[str, dict[str, float]]:
    """Parse regional vote shares from the older YouGov PDF cross-tab layout.

    In the old format each party row contains a series of integer percentages;
    the last six values correspond to the six macro regions in the order defined
    by ``_OLD_FORMAT_REGIONAL_INDICES`` (Wales, Scotland, North, Midlands,
    London, Rest of South).

    Args:
        lines: Stripped, non-empty lines from the Westminster VI section.
        row_labels: Party label strings to match against line prefixes, sorted
            longest-first to avoid partial matches.

    Returns:
        Mapping of canonical party name to a dict of macro-region name →
        vote-share percentage (as a float).  Parties whose row contains fewer
        than six numbers are silently skipped.
    """
    result: dict[str, dict[str, float]] = {}
    for line in lines:
        matched_label = None
        for label in row_labels:
            if line.startswith(f"{label} "):
                matched_label = label
                break
        if matched_label is None:
            continue
        values = [int(v) for v in re.findall(r"-?\d+", line)]
        if len(values) < 6:
            continue
        party_name = PARTY_NAME_MAP[matched_label]
        result[party_name] = {
            region: float(values[index])
            for region, index in _OLD_FORMAT_REGIONAL_INDICES.items()
        }
    return result


def _parse_new_format(
    section: str,
    lines: list[str],
    row_labels: list[str],
) -> dict[str, dict[str, float]]:
    """Parse regional vote shares from the newer YouGov PDF layout.

    The newer format splits regional data across two sub-tables:

    1. **Wales and Scotland** — taken from the penultimate and final integer
       values of each party's MRP headline cross-tab row.
    2. **England regions** (North, Midlands, London, Rest of South) — taken
       from an unlabelled four-column table that appears after a
       ``"% % % %"`` header within the ``"Region in England"`` block.

    Parties are matched in the order they first appear in the cross-tab rows,
    and the same order is used to consume rows from the England table.

    Args:
        section: Raw text of the Westminster VI section of the PDF.
        lines: Stripped, non-empty lines from ``section``.
        row_labels: Party label strings to match against line prefixes, sorted
            longest-first to avoid partial matches.

    Returns:
        Mapping of canonical party name to a dict of macro-region name →
        vote-share percentage (as a float).

    Raises:
        ValueError: If the England regions header or ``"% % % %"`` marker
            cannot be located, or if the England table contains fewer party
            rows than were found in the cross-tab.
    """
    # Step 1: Wales and Scotland — first occurrence of each party (MRP headline rows)
    wales_scotland: dict[str, dict[str, float]] = {}
    party_order: list[str] = []
    for line in lines:
        matched_label = None
        for label in row_labels:
            if line.startswith(f"{label} "):
                matched_label = label
                break
        if matched_label is None:
            continue
        values = [int(v) for v in re.findall(r"-?\d+", line)]
        if len(values) < 3:
            continue
        party_name = PARTY_NAME_MAP[matched_label]
        if party_name not in wales_scotland:
            wales_scotland[party_name] = {
                "Wales": float(values[-2]),
                "Scotland": float(values[-1]),
            }
            party_order.append(party_name)

    # Step 2: England regions — unlabelled table after "% % % %" in the region block
    block_match = re.search(r"North\s+Midlands\s+London\s+Rest of\s*\n?\s*South", section)
    if block_match is None:
        raise ValueError("Could not find England regions table in new-format PDF")
    pct_match = re.compile(r"%\s+%\s+%\s+%").search(section, block_match.end())
    if pct_match is None:
        raise ValueError("Could not find '% % % %' header in England regions table")

    england_regions: dict[str, dict[str, float]] = {}
    party_idx = 0
    for line in section[pct_match.end():].splitlines():
        if party_idx >= len(party_order):
            break
        line = line.strip()
        if not line:
            continue
        values = re.findall(r"\d+", line)
        if len(values) == 4:
            party_name = party_order[party_idx]
            england_regions[party_name] = {
                region: float(values[i])
                for i, region in enumerate(_ENGLAND_REGION_ORDER)
            }
            party_idx += 1

    if len(england_regions) != len(party_order):
        raise ValueError(
            f"England regions table incomplete: expected {len(party_order)} parties, "
            f"got {len(england_regions)}"
        )

    return {
        party_name: {**wales_scotland[party_name], **england_regions[party_name]}
        for party_name in party_order
    }


def parse_headline_vi_table(full_text: str) -> dict[str, dict[str, float]]:
    """Extract per-region vote shares from the Westminster VI section of the PDF.

    Isolates the relevant section between the ``"Westminster Voting Intention"``
    and ``"Now, thinking specifically"`` markers, then delegates to either
    :func:`_parse_new_format` (when a ``"Region in England"`` sub-table is
    present) or :func:`_parse_old_format_rows`.

    Args:
        full_text: Complete text content extracted from the YouGov PDF.

    Returns:
        Mapping of canonical party name to a dict of macro-region name →
        vote-share percentage (as a float).  All parties in
        ``PARTY_NAME_MAP`` must be present.

    Raises:
        ValueError: If the Westminster VI section cannot be isolated, or if
            any party defined in ``PARTY_NAME_MAP`` is absent from the parsed
            result.
    """
    start_marker = "Westminster Voting Intention"
    end_marker = "Now, thinking specifically"

    start_index = full_text.find(start_marker)
    end_index = full_text.find(end_marker)
    if start_index == -1 or end_index == -1 or end_index <= start_index:
        raise ValueError("Could not isolate Westminster headline voting intention section")

    section = full_text[start_index:end_index]
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    row_labels = sorted(PARTY_NAME_MAP.keys(), key=len, reverse=True)

    if "Region in England" in section:
        result = _parse_new_format(section, lines, row_labels)
    else:
        result = _parse_old_format_rows(lines, row_labels)

    missing_parties = [p for p in PARTY_NAME_MAP.values() if p not in result]
    if missing_parties:
        raise ValueError(f"Missing party rows in headline table: {missing_parties}")

    return result


def _normalize_percentage(value: float) -> float:
    """Round a percentage to the nearest integer and return it as a float.

    Args:
        value: Raw percentage value, e.g. ``34.6``.

    Returns:
        The value rounded to the nearest whole number, returned as a
        :class:`float` to satisfy the ``PlannedPollRow.percentage`` field type.
    """
    return float(int(round(value)))


def parse_poll(pdf_text: str) -> ParsedPoll:
    """Parse a complete :class:`ParsedPoll` from raw YouGov PDF text.

    Extracts the sample size, fieldwork date range, and per-region party
    vote shares by delegating to :func:`parse_fieldwork` and
    :func:`parse_headline_vi_table`.

    Args:
        pdf_text: Full text content of the YouGov PDF as returned by
            :func:`extract_pdf_text`.

    Returns:
        A :class:`ParsedPoll` containing the sample size, fieldwork dates, and
        party macro-region percentages.

    Raises:
        ValueError: If the sample size or fieldwork window cannot be found in
            the text, or if the headline VI table cannot be parsed.
    """
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
    """Look up an existing Poll row matching the given pollster, map, and dates.

    Matches on pollster ID, map ID, fieldwork start and end dates, and sample
    size.  Returns the first match or ``None`` if no match exists.

    Args:
        db: Active :class:`Database` instance.
        pollster_id: Primary key of the pollster to match.
        map_id: Primary key of the constituency map to match.
        parsed: Parsed poll data supplying fieldwork dates and sample size.

    Returns:
        The matching :class:`Poll` ORM object, or ``None`` if not found.
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
    """Build a full :class:`ImportPlan` from a YouGov PDF without writing to the DB.

    Downloads and parses the PDF, resolves all DB references (map, regions,
    pollster, parties), and assembles the complete set of :class:`PlannedPollRow`
    objects.  No data is written; call :func:`commit_import_plan` to persist.

    Args:
        db: Active :class:`Database` instance.
        pdf_url: URL of the YouGov PDF to import.  Defaults to
            ``DEFAULT_PDF_URL``.
        map_name: Name of the constituency map to associate the poll with.
            Defaults to ``DEFAULT_MAP_NAME``.
        pollster_identifier: Slug identifier for the pollster record.
            Defaults to ``DEFAULT_POLLSTER_IDENTIFIER``.

    Returns:
        A fully-populated :class:`ImportPlan` describing what will be created
        or updated when :func:`commit_import_plan` is called.

    Raises:
        ValueError: If the map is not found, any required region is missing
            from the map, any party in ``PARTY_NAME_MAP`` is absent from the
            database, or the PDF cannot be parsed.
    """
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
) -> PollImportResult:
    """Persist an :class:`ImportPlan` to the database.

    Creates the pollster and/or poll records if they do not yet exist, then
    inserts the planned poll rows.  If rows already exist for the poll:

    - By default (``replace_rows=False``) the existing rows are left untouched
      and the result reports ``skipped_existing_rows=True``.
    - When ``replace_rows=True`` the existing rows are deleted before new ones
      are inserted.

    The pollster's ``regions_mapping`` is updated in place if it has changed.
    The poll's ``source_url`` is updated in place if it has changed.

    Args:
        db: Active :class:`Database` instance.
        plan: Import plan produced by :func:`build_import_plan`.
        replace_rows: If ``True``, delete existing :class:`PollRow` records for
            the poll before inserting new ones.  Defaults to ``False``.

    Returns:
        A :class:`PollImportResult` summarising what was created, replaced, or
        skipped.

    Raises:
        ValueError: If the pollster lookup fails unexpectedly during commit.
    """
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
    """Print a dry-run summary of an :class:`ImportPlan` to stdout.

    Displays the parsed fieldwork dates and sample size, whether the pollster
    and poll already exist, and each row that would be inserted.  Nothing is
    written to the database.

    Args:
        plan: Import plan to preview, as returned by :func:`build_import_plan`.
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
            f"party={row.party_name}, macro={row.macro_region}, "
            f"region_id={row.region_id}, pct={row.percentage}"
        )


def main() -> None:
    """CLI entry point for the YouGov poll importer.

    Parses command-line arguments, fetches and parses the specified PDF, and
    either prints a dry-run preview or commits the import to the database.

    Command-line arguments:
        --pdf-url (str): URL of the YouGov PDF to import.
            Defaults to ``DEFAULT_PDF_URL``.
        --map-name (str): Name of the constituency map to use.
            Defaults to ``DEFAULT_MAP_NAME``.
        --pollster-identifier (str): Slug identifier for the pollster.
            Defaults to ``DEFAULT_POLLSTER_IDENTIFIER``.
        --replace-rows (flag): Delete existing poll rows before inserting new
            ones.  Off by default.
        --dry-run (flag): Print the import plan without writing to the database.
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
