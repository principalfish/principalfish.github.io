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
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster
from polls.importers.bases import (
    ParsedBase,
    PlannedPollBase,
    make_base,
    plan_poll_bases,
    region_refs,
    replace_poll_bases,
)
from polls.importers.types import PollImportResult

DEFAULT_PDF_URL = "https://www.ipsos.com/sites/default/files/ct/news/documents/2026-01/politmkp_w1jan2026web1.pdf"
DEFAULT_MAP_NAME = "UK Constituencies post 2022"
DEFAULT_POLLSTER_IDENTIFIER = "ipsos"
# Longest window accepted when an omitted start month is inferred to be the
# month before the end month. Same-month ranges ("1st to 20th") are uncapped.
MAX_INFERRED_FIELDWORK_DAYS = 14

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
    """Parsed poll data extracted from source.

    ``bases`` comes from the aligned-table layout only: the national decided
    base (Table 1's "Total voting (sub-base)" row, unweighted from its UnWgt
    Total column) and the likely-to-vote table's "Unweighted Total"/"Weighted
    Total" rows for each region column. Older unaligned text gives none.
    """

    sample_size: int = Field(gt=0)
    fieldwork_start: date
    fieldwork_end: date
    party_percentages: dict[str, float]
    party_region_percentages: dict[str, dict[str, float]]
    bases: list[ParsedBase] = Field(default_factory=list)

    def national_shares(self) -> dict[str, float]:
        """Return canonical party name -> national share."""
        return dict(self.party_percentages)


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
    bases: list[PlannedPollBase] = Field(default_factory=list)


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
    that the response body is a valid PDF (starts with ``%PDF``), then normalizes
    page rotation and extracts visually aligned text from every page.

    Args:
        pdf_url: Fully-qualified URL of the PDF to download.

    Returns:
        Concatenated text of all pages, joined by newlines.

    Raises:
        ValueError: If the response body is not a valid PDF.
        urllib.error.URLError: If the HTTP request fails.
    """
    req = Request(pdf_url, headers={"User-Agent": "Mozilla/5.0 (compatible; poll-importer/1.0)"})
    with urlopen(req, timeout=60) as response:
        payload = response.read()

    if not payload.startswith(b"%PDF"):
        raise ValueError(f"Could not fetch PDF payload from URL: {pdf_url}")

    reader = PdfReader(BytesIO(payload))
    writer = PdfWriter(clone_from=reader)
    pages = []
    for page in writer.pages:
        page.transfer_rotation_to_content()
        pages.append(
            page.extract_text(
                extraction_mode="layout",
                layout_mode_strip_rotated=False,
            ) or "",
        )
    return "\n".join(pages)


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
    When the start month is omitted it is the end month, unless the start day
    is after the end day ("30th to 5th November"), when it is the month before
    (December of the previous year for a January end).

    Args:
        lines: Non-empty, stripped lines from the PDF text.

    Returns:
        A ``(fieldwork_start, fieldwork_end)`` tuple of :class:`datetime.date`
        objects.

    Raises:
        ValueError: If no fieldwork line is found, or the date pattern does
            not match, a month name cannot be parsed, a day does not exist
            in its month, the dates are reversed (an explicit typo such as
            "5th February to 3rd February"), or a start month inferred as the
            previous month
            gives a window longer than ``MAX_INFERRED_FIELDWORK_DAYS``.
    """
    fieldwork_line = next((line for line in lines if "fieldwork dates" in line.lower()), None)
    if fieldwork_line is None:
        raise ValueError("Fieldwork line not found in PDF")

    pattern = re.compile(
        r"Fieldwork\s+dates\s*[-:]\s*[A-Za-z]+\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?(?:\s+([A-Za-z.]+))?\s+to\s+"
        r"[A-Za-z]+\s+(\d{1,2})(?:st|nd|rd|th)?"
        r"(?:\s*\((?:am|pm)\))?\s+([A-Za-z.]+)"
        r"(?:\s*\([^)]*\))?\s+(20\d{2})",
        re.IGNORECASE,
    )
    match = pattern.search(fieldwork_line)
    if not match:
        raise ValueError(f"Could not parse fieldwork line: {fieldwork_line!r}")

    day_start = int(match.group(1))
    day_end = int(match.group(3))
    year_end = int(match.group(5))

    month_end = _month_number(match.group(4).strip())
    is_previous_month = False
    if match.group(2):
        month_start = _month_number(match.group(2).strip())
    elif day_start > day_end:
        # "Thursday 30th to Wednesday 5th November": the start month is omitted
        # because it is the month before the end month.
        month_start = None if month_end is None else (month_end - 2) % 12 + 1
        is_previous_month = True
    else:
        month_start = month_end
    if month_start is None or month_end is None:
        raise ValueError(f"Could not parse month names in fieldwork line: {fieldwork_line!r}")

    year_start = year_end - 1 if month_start > month_end else year_end
    try:
        start = date(year_start, month_start, day_start)
        end = date(year_end, month_end, day_end)
    except ValueError as err:
        raise ValueError(f"Invalid fieldwork date in line: {fieldwork_line!r}") from err
    if start > end:
        raise ValueError(f"Reversed fieldwork dates in line: {fieldwork_line!r}")
    if is_previous_month and (end - start).days > MAX_INFERRED_FIELDWORK_DAYS:
        # A long window from a guessed month is more likely a typo than a
        # real fieldwork period.
        raise ValueError(f"Ambiguous fieldwork dates in line: {fieldwork_line!r}")
    return start, end


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


def _layout_party_rows(lines: list[str], width: int) -> dict[str, list[float]]:
    """Read a bounded table, keeping suppressed cells in their columns."""
    parsed = {}
    for index, line in enumerate(lines[:-1]):
        party = next(
            (
                canonical
                for marker, canonical in PARTY_LINE_MAP.items()
                if line.strip().lower().startswith(marker.lower())
            ),
            None,
        )
        if party is None:
            continue
        if re.search(r"\s(?:\d+|\*|-)(?:\s|$)", line) is None:
            continue
        tokens = re.findall(
            r"(?<!\S)(\d{1,3}%|\*|-)(?:s)?(?=\s|$)", lines[index + 1],
        )
        if len(tokens) != width:
            raise ValueError(f"Incomplete aligned Ipsos percentage row: {party}")
        if party in parsed:
            raise ValueError(f"Duplicate aligned Ipsos party row: {party}")
        values = [
            0.0 if token in {"*", "-"} else float(token[:-1]) for token in tokens
        ]
        if any(value > 100 for value in values):
            raise ValueError(f"Invalid aligned Ipsos percentage: {party}")
        parsed[party] = values
    return parsed


_LAYOUT_CELL = re.compile(r"(?<!\S)(\d+|-|\*)(?=\s|$)")


def _layout_row_cells(lines: list[str], label: str) -> list[str] | None:
    """Return the cells of the first row starting with *label*, or None."""
    row = next((line for line in lines if line.strip().startswith(label)), None)
    if row is None:
        return None
    return _LAYOUT_CELL.findall(row.strip()[len(label):])


def _layout_national_base(table: str, lines: list[str]) -> ParsedBase | None:
    """Read the national decided base from the voting-intention (all) table.

    The shares are percentages of the "Total voting (sub-base)" row, which
    prints the weighted count in the Total column and the unweighted count in
    the final "UnWgt Total" column. That column is recognised by its header
    and by holding the unweighted total in both base rows.

    Args:
        table: The table's text.
        lines: The table's lines.

    Returns:
        The decided base, or None when the table does not print it this way.
    """
    if "UnWgt" not in table:
        return None
    unweighted = _layout_row_cells(lines, "Unweighted Total")
    weighted = _layout_row_cells(lines, "Weighted Total")
    sub_base = _layout_row_cells(lines, "Total voting (sub-base)")
    if not unweighted or not weighted or not sub_base:
        return None
    if not len(unweighted) == len(weighted) == len(sub_base):
        return None
    if not (unweighted[0] == unweighted[-1] == weighted[-1]):
        return None
    return make_base((), sub_base[-1], sub_base[0], "decided")


def _parse_layout_tables(
    pdf_text: str,
) -> tuple[dict[str, float], dict[str, dict[str, float]], list[ParsedBase]] | None:
    """Read visually aligned tables using their column and region anchors.

    Returns national shares, regional shares and the column bases (see
    :class:`ParsedPoll`), or None when the text has no aligned tables.
    """
    tables = re.split(r"(?m)^Table\s+\d+\s*$", pdf_text)[1:]
    national: dict[str, float] = {}
    regional: dict[str, dict[str, float]] = {}
    national_base: ParsedBase | None = None
    regional_bases: list[ParsedBase] = []
    found_layout = False
    region_header = re.compile(
        r"(?P<north>Scot)\s+(?P<midlands>(?:E\s+)?of England\))\s+"
        r"(?P<south>London)\s+(?P<london>London)\s+(?P<england>land)\s+"
        r"(?P<scotland>Scot)\s+(?P<wales>Wales)"
    )
    group_regions = {
        "north": "North excl Scotland",
        "midlands": "Midlands incl East of England",
        "south": "South excl London",
        "london": "London",
        "scotland": "Scotland",
        "wales": "Wales",
    }
    for table in tables:
        lowered = table.lower()
        is_all = "combined voting intention - all" in lowered
        is_regional = (
            "combined voting intention - likely to vote" in lowered
            and "ONS Regions" in table
        )
        if not (is_all or is_regional):
            continue
        lines = table.splitlines()
        count_row = next(
            (line for line in lines if line.strip().startswith("Unweighted Total")),
            None,
        )
        # Unstructured older text can put the label after its values.
        if count_row is None:
            if "ONS Regions" in table:
                raise ValueError("Missing aligned Ipsos column anchors")
            continue
        found_layout = True
        anchors = list(re.finditer(r"\d+", count_row))
        if not anchors:
            raise ValueError("Missing aligned Ipsos column anchors")
        rows = _layout_party_rows(lines, len(anchors))
        if is_all and not national:
            missing = set(PARTY_LINE_MAP.values()) - rows.keys()
            if missing:
                raise ValueError(
                    f"Missing aligned Ipsos national parties: {sorted(missing)}"
                )
            national = {party: values[0] for party, values in rows.items()}
            national_base = _layout_national_base(table, lines)
        if is_regional:
            header = next(
                (match for line in lines if (match := region_header.search(line))),
                None,
            )
            if header is None:
                raise ValueError("Could not resolve aligned Ipsos region headers")
            columns = {}
            for group, region in group_regions.items():
                start, end = header.span(group)
                centre = (start + end) / 2
                column = min(
                    range(len(anchors)),
                    key=lambda col: abs(
                        (anchors[col].start() + anchors[col].end()) / 2 - centre
                    ),
                )
                anchor = anchors[column]
                if abs((anchor.start() + anchor.end()) / 2 - centre) > 4:
                    raise ValueError("Misaligned Ipsos regional column anchors")
                columns[region] = column
            if len(set(columns.values())) != len(columns):
                raise ValueError("Overlapping Ipsos regional column anchors")
            required = {
                "Conservative", "Labour", "Liberal Democrats", "Reform UK", "Green",
            }
            if not required <= rows.keys():
                raise ValueError("Missing aligned Ipsos regional parties")
            current = {
                party: {region: values[col] for region, col in columns.items()}
                for party, values in rows.items()
            }
            for party in ("Scottish National Party", "Plaid Cymru", "Other"):
                current.setdefault(party, dict.fromkeys(REGION_COLUMN_ORDER, 0.0))
            if regional and regional != current:
                raise ValueError("Conflicting aligned Ipsos regional tables")
            regional = current
            if not regional_bases:
                regional_bases = _layout_regional_bases(lines, anchors, columns)
    if not found_layout:
        return None
    if not national:
        raise ValueError("Missing aligned Ipsos national table")
    bases = ([national_base] if national_base is not None else []) + regional_bases
    return national, regional, bases


def _layout_regional_bases(
    lines: list[str], anchors: list[re.Match[str]], columns: dict[str, int]
) -> list[ParsedBase]:
    """Read each region column's base from the likely-to-vote ONS table.

    The table's "Unweighted Total" row (whose numbers are the column anchors)
    counts everyone certain to vote, undecided included, so the bases are
    likely-voter bases. The "Weighted Total" row supplies the weighted base
    when it has one number per anchor.

    Args:
        lines: The table's lines.
        anchors: The "Unweighted Total" row's numbers.
        columns: Macro region -> anchor index.

    Returns:
        One base per macro region column.
    """
    weighted_row = next(
        (line for line in lines if line.strip().startswith("Weighted Total")), None
    )
    weighted = re.findall(r"\d+", weighted_row) if weighted_row else []
    aligned = len(weighted) == len(anchors)
    candidates = [
        make_base(
            MACRO_REGION_TO_INTERNAL[region],
            anchors[column].group(),
            weighted[column] if aligned else None,
            "likely_voters",
        )
        for region, column in columns.items()
    ]
    return [candidate for candidate in candidates if candidate is not None]


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
    layout = _parse_layout_tables(pdf_text)
    bases: list[ParsedBase] = []
    if layout is None:
        party_percentages = _parse_party_percentages(lines)
        party_region_percentages = _parse_party_region_percentages(lines)
    else:
        party_percentages, party_region_percentages, bases = layout
    return ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        party_percentages=party_percentages,
        party_region_percentages=party_region_percentages,
        bases=bases,
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


def fetch_parsed_poll(source_url: str) -> ParsedPoll:
    """Download an Ipsos PDF and parse it.

    Args:
        source_url: URL of the PDF.

    Returns:
        The parsed poll.

    Raises:
        ValueError: If the PDF cannot be fetched or parsed.
    """
    return parse_poll(extract_pdf_text(source_url))


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

    parsed = fetch_parsed_poll(pdf_url)

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
        bases=plan_poll_bases(parsed.bases, region_refs(region_by_name.values())),
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
        inserted_bases = replace_poll_bases(session, poll_id, plan.bases)

    return PollImportResult(
        created_pollster=created_pollster,
        created_poll=created_poll_row,
        poll_id=poll_id,
        inserted_rows=len(plan.rows),
        replaced_rows=replaced_rows,
        skipped_existing_rows=False,
        inserted_bases=inserted_bases,
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
        print(f"inserted bases: {result.inserted_bases}")


if __name__ == "__main__":
    main()
