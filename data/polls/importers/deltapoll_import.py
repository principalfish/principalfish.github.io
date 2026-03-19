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
    """Convert a month name or abbreviation to its calendar number.

    Args:
        month_text: Month name or abbreviation, e.g. "Jan", "January", "feb.".
            Case-insensitive; trailing periods are stripped.

    Returns:
        Integer month number (1–12), or None if the text is not recognised.
    """
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
    """Fetch the raw byte content of a URL with a browser-like User-Agent.

    Args:
        url: Absolute URL to fetch.

    Returns:
        Raw response body as bytes.

    Raises:
        urllib.error.URLError: If the request fails or times out (60 s limit).
    """
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; poll-importer/1.0)"})
    with urlopen(req, timeout=60) as response:
        data: bytes = response.read()
        return data


def _resolve_pdf_url(source_url: str) -> str:
    """Resolve a direct PDF URL or scrape the first PDF link from an HTML page.

    If ``source_url`` already ends with ``.pdf`` it is returned unchanged.
    Otherwise the page is fetched and all ``<a href>`` anchors are inspected;
    the first absolute URL ending in ``.pdf`` is returned.

    Args:
        source_url: Either a direct ``.pdf`` URL or an HTML page that links to
            a Deltapoll PDF.

    Returns:
        Absolute URL of the resolved PDF file.

    Raises:
        ValueError: If ``source_url`` is an HTML page but no PDF link is found.
    """
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
    """Download a PDF and return its full extracted text.

    If ``pdf_url`` points to a Wayback Machine snapshot without the ``if_``
    flag, an alternative URL with the flag is tried first so that the raw PDF
    bytes (not an HTML wrapper) are returned.

    Args:
        pdf_url: URL of the PDF to fetch.  May be a direct host URL or a
            Wayback Machine URL (``web.archive.org/web/...``).

    Returns:
        Concatenated text extracted from every page of the PDF, pages joined
        by newlines.

    Raises:
        ValueError: If no candidate URL returns a valid PDF payload
            (i.e. bytes starting with ``%PDF``).
    """
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
    """Split extracted PDF text into non-empty, stripped lines.

    Args:
        pdf_text: Raw text returned by :func:`_extract_pdf_text`.

    Returns:
        List of non-empty strings with leading/trailing whitespace removed.
    """
    return [line.strip() for line in pdf_text.splitlines() if line.strip()]


def _parse_fieldwork(lines: list[str]) -> tuple[date, date]:
    """Parse the fieldwork date range from PDF lines.

    Locates the line beginning with ``Fieldwork:`` and tries four date-range
    formats in order:

    1. Cross-month with explicit year on both sides:
       ``Fieldwork: 30 December 2025 to 2 January 2026``
    2. Cross-month without year on the start side:
       ``Fieldwork: 30 December to 2 January 2026``
    3. Same-month hyphen range:
       ``Fieldwork: 3-5 January 2026``
    4. Same-month ``to`` range:
       ``Fieldwork: 3 to 5 January 2026``

    Ordinal suffixes (``st``, ``nd``, ``rd``, ``th``) and em/en dashes are
    normalised before matching.  When the start month is later in the year than
    the end month the start year is set to ``year_end - 1``.

    Args:
        lines: Non-empty lines extracted from the Deltapoll PDF.

    Returns:
        A ``(fieldwork_start, fieldwork_end)`` tuple of :class:`datetime.date`
        objects.

    Raises:
        ValueError: If no ``Fieldwork:`` line is found, if month names cannot
            be resolved, or if none of the four patterns match.
    """
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
    """Parse the poll sample size from PDF lines.

    Locates the line beginning with ``Sample Size:`` and extracts the first
    run of digits, stripping any thousands separators or surrounding text.

    Args:
        lines: Non-empty lines extracted from the Deltapoll PDF.

    Returns:
        Sample size as a positive integer.

    Raises:
        ValueError: If no ``Sample Size:`` line is found or if no digits can
            be extracted from it.
    """
    sample_line = next((line for line in lines if line.lower().startswith("sample size:")), None)
    if sample_line is None:
        raise ValueError("Sample Size line not found in Deltapoll PDF")
    digits = re.sub(r"[^0-9]", "", sample_line)
    if not digits:
        raise ValueError(f"Could not parse sample size from line: {sample_line!r}")
    return int(digits)


def _canonical_party_from_line(line: str) -> str | None:
    """Return the canonical party name if a line starts with a known party label.

    Collapses internal whitespace before checking against
    :data:`PARTY_LABEL_TO_CANONICAL` keys.

    Args:
        line: A single line of text from the Deltapoll PDF.

    Returns:
        Canonical party name string (e.g. ``"Liberal Democrats"``), or
        ``None`` if the line does not start with any recognised label.
    """
    normalized = " ".join(line.strip().split())
    for raw, canonical in PARTY_LABEL_TO_CANONICAL.items():
        if normalized.startswith(raw):
            return canonical
    return None


def _extract_party_order_and_national(lines: list[str]) -> tuple[list[str], dict[str, float]]:
    """Extract the national vote-share table from the Q1 voting-intention section.

    Scans for the first line containing ``voting intention`` (case-insensitive)
    and reads up to 60 subsequent lines, collecting party rows in document
    order.  Stops after 8 parties.  ``"Other"`` is always appended with a
    default of ``0.0`` if not found in the table.

    Args:
        lines: Non-empty lines extracted from the Deltapoll PDF.

    Returns:
        A two-element tuple ``(party_order, national)`` where:

        - ``party_order`` is a list of canonical party name strings in the
          order they appear in the PDF.
        - ``national`` is a dict mapping each canonical party name to its
          national vote-share percentage as a float.

    Raises:
        ValueError: If the voting-intention section cannot be located, or if
            any of the seven required core parties are absent from the table.
    """
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
    """Parse the macro-regional vote-share block from the Deltapoll PDF.

    Looks for a header line containing the full region sequence
    ``"total london rest of south midlands north wales scotland"``
    (case-insensitive), then reads up to 35 subsequent lines and collects
    rows of exactly 8 integers whose values are all in ``[0, 100]``.
    Columns 0–5 map to Total, London, Rest of South, Midlands, North, Wales,
    Scotland; the Total column is discarded.

    If a party appears in ``party_order`` but no corresponding data row was
    found, all its macro-region values default to ``0.0``.

    Args:
        lines: Non-empty lines extracted from the Deltapoll PDF.
        party_order: Canonical party names in the order they appear in the
            national table, as returned by
            :func:`_extract_party_order_and_national`.

    Returns:
        Nested dict ``{party_name: {macro_region: percentage}}`` where
        macro-region keys are the six strings in :data:`MACRO_ORDER`.

    Raises:
        ValueError: If the regional header line cannot be found, or if fewer
            rows are parsed than ``len(party_order) - 1``.
    """
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
    """Parse a Deltapoll PDF text into a structured :class:`ParsedPoll`.

    Extracts sample size, fieldwork dates, national vote shares, and
    macro-regional vote shares.  If regional parsing fails for any reason the
    regional data for all parties is silently set to empty dicts (national-only
    import continues uninterrupted).

    Args:
        pdf_text: Full text extracted from the Deltapoll PDF, as returned by
            :func:`_extract_pdf_text`.

    Returns:
        A :class:`ParsedPoll` containing sample size, fieldwork start/end
        dates, and a nested ``party_region_percentages`` dict of the form
        ``{party_name: {region_key: percentage}}``.  The special key
        :data:`NATIONAL_KEY` (``"__national__"``) holds the national figure;
        remaining keys are macro-region names from :data:`MACRO_ORDER`.

    Raises:
        ValueError: Propagated from the underlying parse helpers if mandatory
            fields (fieldwork, sample size, national table) cannot be found.
    """
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
    """Look up a poll that matches the parsed metadata exactly.

    Matches on pollster, map, fieldwork start/end, and sample size.

    Args:
        db: Open :class:`Database` instance.
        pollster_id: Primary key of the pollster row.
        map_id: Primary key of the constituency map row.
        parsed: Parsed poll data whose dates and sample size are used as
            filter criteria.

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
    source_url: str = DEFAULT_SOURCE_URL,
    map_name: str = DEFAULT_MAP_NAME,
    pollster_identifier: str = DEFAULT_POLLSTER_IDENTIFIER,
) -> ImportPlan:
    """Fetch, parse, and plan a Deltapoll import without writing to the DB.

    Resolves the PDF URL from ``source_url``, parses the poll, looks up all
    required DB entities (map, regions, pollster, parties), and builds the
    complete set of :class:`PlannedPollRow` objects — one national row and one
    row per internal region for each party.  Nothing is written to the
    database.

    Args:
        db: Open :class:`Database` instance.
        source_url: Direct PDF URL or HTML page URL from which a PDF link can
            be scraped.  Defaults to :data:`DEFAULT_SOURCE_URL`.
        map_name: Name of the constituency map used to resolve region IDs.
            Defaults to :data:`DEFAULT_MAP_NAME`.
        pollster_identifier: Identifier string for the Deltapoll pollster row.
            Defaults to :data:`DEFAULT_POLLSTER_IDENTIFIER`.

    Returns:
        A fully populated :class:`ImportPlan` describing what would be created
        or updated if :func:`commit_import_plan` is called.

    Raises:
        ValueError: If the map, any required region, or any required party is
            not found in the database, or if PDF parsing fails.
    """
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
    """Write a previously built :class:`ImportPlan` to the database.

    Creates the pollster and/or poll records if they do not already exist.
    If the poll already exists its ``source_url`` is updated if it has
    changed.  Poll rows are inserted unless they already exist; if
    ``replace_rows`` is ``True`` all existing rows are deleted first.

    Args:
        db: Open :class:`Database` instance.
        plan: Import plan produced by :func:`build_import_plan`.
        replace_rows: If ``True``, delete any existing :class:`PollRow` records
            for the poll before inserting the new ones.  If ``False`` and rows
            already exist, insertion is skipped entirely.  Defaults to
            ``False``.

    Returns:
        A :class:`PollImportResult` summarising what was created, inserted,
        replaced, or skipped.

    Raises:
        ValueError: If the pollster lookup fails unexpectedly after
            ``plan.pollster_exists`` is ``True``.
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
    """Print a human-readable dry-run summary of an import plan to stdout.

    Shows the parsed fieldwork dates and sample size, whether the pollster and
    poll already exist, and up to 30 of the planned poll rows.

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

    for row in plan.rows[:30]:
        print(
            "[dry-run] would insert row: "
            f"party={row.party_name}, region={row.region_name}, "
            f"region_id={row.region_id}, pct={row.percentage:.2f}"
        )


def main() -> None:
    """CLI entry point for the Deltapoll importer.

    Parses command-line arguments, fetches and parses the source PDF, and
    either prints a dry-run preview or commits the import to the database.

    Command-line arguments:

    - ``--source-url`` (str, optional): PDF or HTML page URL to import from.
      Defaults to :data:`DEFAULT_SOURCE_URL`.
    - ``--map-name`` (str, optional): Constituency map name used to resolve
      region IDs.  Defaults to :data:`DEFAULT_MAP_NAME`.
    - ``--pollster-identifier`` (str, optional): Pollster identifier string.
      Defaults to :data:`DEFAULT_POLLSTER_IDENTIFIER`.
    - ``--replace-rows`` (flag): Delete existing poll rows before inserting
      new ones.  Omitting this flag skips insertion if rows already exist.
    - ``--dry-run`` (flag): Print the import plan without writing to the DB.
    """
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
