#!/usr/bin/env python3
"""Import a Techne poll directly from a PDF URL."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from db import Database
from models import Poll, PollRow, Pollster
from polls.importers.bases import (
    ParsedBase,
    PlannedPollBase,
    make_base,
    plan_poll_bases,
    replace_poll_bases,
)
from polls.importers.types import PollImportResult

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

# The rest of the line after the label; the figures are parsed from it.
_UNWEIGHTED_SAMPLE_ROW = re.compile(r"Unweighted Sample(?P<figures>[^\n]*)")
# The weighted row's previous- and current-wave figures, as separate numbers.
_WEIGHTED_SAMPLE_ROW = re.compile(
    r"^\s*Weighted Sample\s+(?P<previous>\d{1,5})\s+(?P<current>\d{1,5})(?!\d)", re.M
)
# Techne's samples are ~1,600; a longer digit run is figures glued together.
_MAX_SAMPLE_FIGURE_DIGITS = 5
_LEADING_DIGITS = re.compile(r"[0-9]+")
_MIN_SAMPLE_SIZE = 100


class UnweightedSample(BaseModel):
    """The leading figures of a Techne PDF's ``Unweighted Sample`` row.

    Attributes:
        previous_wave: The previous poll's sample (first column).
        current_wave: This poll's sample (second column);
            :func:`parse_unweighted_sample` enforces ``_MIN_SAMPLE_SIZE``.
        male: The Male column, or None when the row has fewer than four
            figures.
        female: The Female column, or None when the row has fewer than four
            figures.
    """

    previous_wave: int
    current_wave: int
    male: int | None = None
    female: int | None = None


class ParsedPoll(BaseModel):
    """Parsed poll data extracted from source.

    ``bases`` holds the national "Unweighted Sample"/"Weighted Sample"
    current-wave figures. Techne prints no base for its decided-only VI table,
    so this is the all-adult base; there are no regional figures.
    """

    sample_size: int = Field(gt=0)
    fieldwork_start: date
    fieldwork_end: date
    party_percentages: dict[str, float]
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
    """Convert a month name or abbreviation to its integer (1–12).

    Args:
        month_text: Month name or abbreviation, e.g. ``"January"``, ``"jan"``,
            ``"Feb."``. Case-insensitive; trailing periods are stripped.

    Returns:
        Integer month number (1–12), or ``None`` if the text is not recognised.
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


def _infer_year(pdf_url: str, fallback: int | None = None) -> int:
    """Infer the poll year from a PDF URL.

    Looks for a four-digit year of the form ``20xx`` first as a path segment
    (``/2026/``) and then anywhere in the URL string.  If no year is found and
    a ``fallback`` is provided that value is returned; otherwise a
    :class:`ValueError` is raised.

    Args:
        pdf_url: Full URL string of the Techne PDF.
        fallback: Optional year to return when no year can be found in the URL.

    Returns:
        Four-digit calendar year as an integer.

    Raises:
        ValueError: If no year pattern is found in the URL and no fallback is
            provided.
    """
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
    """Fetch a PDF from a URL and extract its full text content.

    Attempts the given URL directly; if it is a Wayback Machine URL without
    the ``if_`` raw-content flag the flag variant is also tried as a fallback.
    The raw PDF bytes are written to a temporary file and parsed with
    :class:`pypdf.PdfReader`.

    Args:
        pdf_url: HTTP(S) URL pointing to the PDF file.

    Returns:
        Concatenated text extracted from every page of the PDF, joined by
        newlines.

    Raises:
        ValueError: If no candidate URL returns a valid PDF payload.
    """
    candidate_urls = [pdf_url]
    if "web.archive.org/web/" in pdf_url and "if_/" not in pdf_url:
        candidate_urls.append(pdf_url.replace("/web/", "/web/").replace("/https://", "if_/https://", 1))

    payload = None
    for candidate in candidate_urls:
        req = Request(candidate, headers={"User-Agent": "Mozilla/5.0 (compatible; poll-importer/1.0)"})
        try:
            with urlopen(req, timeout=60) as response:
                data = response.read()
            if data.startswith(b"%PDF"):
                payload = data
                break
        except Exception:
            continue

    if payload is None:
        raise ValueError(f"Could not fetch PDF payload from URL: {pdf_url}")

    with NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(payload)
        tmp.flush()
        reader = PdfReader(tmp.name)
        return "\n".join((page.extract_text() or "") for page in reader.pages)


def _parse_fieldwork(pdf_text: str, year: int) -> tuple[date, date]:
    """Parse the fieldwork date window from PDF text.

    Looks for a pattern of the form ``FIELDWORK: Month DD - Month DD`` (ordinal
    suffixes such as ``st``/``nd``/``rd``/``th`` are accepted).  When the start
    month is numerically later than the end month the start year is assumed to
    be ``year - 1`` (i.e. the window crosses a year boundary).

    Args:
        pdf_text: Full text extracted from the Techne PDF.
        year: The calendar year inferred from the PDF URL, used as the end year
            of the fieldwork window.

    Returns:
        A two-tuple ``(fieldwork_start, fieldwork_end)`` of :class:`datetime.date`
        objects.

    Raises:
        ValueError: If the fieldwork pattern is not found in the text, or if a
            month name cannot be recognised.
    """
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


def parse_unweighted_sample(pdf_text: str) -> UnweightedSample:
    """Parse the first ``Unweighted Sample`` row of a Techne PDF.

    The row's figures are whitespace-separated: the previous wave's sample,
    the current wave's, then one column per demographic, starting with Male
    and Female (the header reads ``UK <prev date> | UK <current date> | Male |
    Female | 18-34 years | ...``). Only the leading run of plain integers is
    read; it ends at the first other token (some later columns print as
    ``1.140``).

    pypdf 6.6.0 to 6.16.1 dropped the spaces between the figures, gluing the
    row into one long digit run. A figure longer than
    ``_MAX_SAMPLE_FIGURE_DIGITS`` digits is that symptom, so it raises rather
    than guessing where one number ends.

    Args:
        pdf_text: Full text extracted from the Techne PDF.

    Returns:
        The row's previous- and current-wave figures, plus Male and Female
        when the row has at least four figures.

    Raises:
        ValueError: If the row is missing, its figures look concatenated, it
            has fewer than two figures, the current-wave figure is below
            ``_MIN_SAMPLE_SIZE``, or Male + Female does not equal the
            current-wave figure.
    """
    match = _UNWEIGHTED_SAMPLE_ROW.search(pdf_text)
    if not match:
        raise ValueError("Unweighted sample size not found in PDF")

    figures: list[int] = []
    for token in match.group("figures").split():
        # Checked before the non-integer stop: a glued real row runs on into
        # a "1.140"-style column, so the whole row is one token with a dot.
        leading_digits = _LEADING_DIGITS.match(token)
        if leading_digits and len(leading_digits.group()) > _MAX_SAMPLE_FIGURE_DIGITS:
            raise ValueError(
                "Unweighted Sample figures look concatenated (no spaces between "
                f"numbers): {token[:20]!r}"
            )
        if leading_digits is None or leading_digits.group() != token:
            break
        figures.append(int(token))

    if len(figures) < 2:
        raise ValueError(
            "Unweighted Sample row needs previous- and current-wave figures, "
            f"found {figures}"
        )
    previous_wave, current_wave = figures[0], figures[1]
    if current_wave < _MIN_SAMPLE_SIZE:
        raise ValueError(
            f"Unweighted Sample current-wave figure {current_wave} is below "
            f"{_MIN_SAMPLE_SIZE}"
        )
    if len(figures) < 4:
        return UnweightedSample(previous_wave=previous_wave, current_wave=current_wave)

    male, female = figures[2], figures[3]
    if male + female != current_wave:
        raise ValueError(
            f"Unweighted Sample Male + Female ({male} + {female} = {male + female}) "
            f"does not match the current-wave figure {current_wave}"
        )
    return UnweightedSample(
        previous_wave=previous_wave,
        current_wave=current_wave,
        male=male,
        female=female,
    )


def _parse_sample_size(pdf_text: str) -> int:
    """Extract the current wave's unweighted sample size from PDF text.

    Args:
        pdf_text: Full text extracted from the Techne PDF.

    Returns:
        The current-wave figure of the ``Unweighted Sample`` row (its second
        figure; the first is the previous wave's).

    Raises:
        ValueError: Propagated from :func:`parse_unweighted_sample`.
    """
    return parse_unweighted_sample(pdf_text).current_wave


def _party_percentage_from_line(line: str) -> float:
    """Extract the headline (weighted) percentage from a party result line.

    Techne PDFs typically present two percentage values per party per line —
    the first is unweighted and the second is weighted.  This function returns
    the second (weighted) value.

    Args:
        line: A single text line containing at least two ``XX%`` tokens.

    Returns:
        The second percentage value on the line as a float.

    Raises:
        ValueError: If fewer than two percentage tokens are found on the line.
    """
    values = [int(v) for v in re.findall(r"(\d{1,2})%", line)]
    if len(values) < 2:
        raise ValueError(f"Could not parse headline percentages from line: {line!r}")
    return float(values[1])


def _parse_party_percentages(pdf_text: str) -> dict[str, float]:
    """Parse party vote-share percentages from the full PDF text.

    First attempts to isolate the ``[all cases]`` voting-intention block and
    extract values from it.  If fewer than five parties are found there the
    entire PDF text is searched as a fallback.  Optional regional parties
    (SNP, Plaid Cymru, Other) default to ``0.0`` when absent.

    Args:
        pdf_text: Full text extracted from the Techne PDF.

    Returns:
        Dict mapping canonical party name (as defined in ``PARTY_NAME_MAP``)
        to its weighted vote-share percentage as a float.

    Raises:
        ValueError: If any required party (Conservative, Labour, Liberal
            Democrats, Reform UK, Green, Scottish National Party, Plaid Cymru,
            Other) is missing after parsing.
    """
    def _extract(text: str) -> dict[str, float]:
        """Extract canonical party percentages from a block of text.

        Scans ``text`` for each raw party name in ``PARTY_NAME_MAP`` and
        captures the second ``XX%`` token on the matching line as the weighted
        figure.  Later occurrences of the same raw party name take precedence.

        Args:
            text: Arbitrary text block to search (full PDF text or a sub-block).

        Returns:
            Dict mapping canonical party name to weighted percentage float.
            Parties not found in ``text`` are omitted.
        """
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
    """Orchestrate parsing of all fields from PDF text into a :class:`ParsedPoll`.

    Args:
        pdf_text: Full text extracted from the Techne PDF.
        inferred_year: Calendar year inferred from the PDF URL, used to
            resolve fieldwork dates that span a year boundary.

    Returns:
        A :class:`ParsedPoll` instance populated with sample size, fieldwork
        dates, and party percentages.

    Raises:
        ValueError: Propagated from the underlying parse helpers if any
            required field cannot be extracted.
    """
    sample_size = _parse_sample_size(pdf_text)
    fieldwork_start, fieldwork_end = _parse_fieldwork(pdf_text, inferred_year)
    party_percentages = _parse_party_percentages(pdf_text)
    weighted = _WEIGHTED_SAMPLE_ROW.search(pdf_text)
    national = make_base(
        (), sample_size, weighted.group("current") if weighted else None, "all_adults"
    )
    return ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        party_percentages=party_percentages,
        bases=[national] if national is not None else [],
    )


def _find_existing_poll(db: Database, pollster_id: int, map_id: int, parsed: ParsedPoll) -> Poll | None:
    """Look up an existing poll that matches the given key fields.

    Matches on pollster, map, fieldwork start/end dates, and sample size.

    Args:
        db: Active :class:`Database` connection.
        pollster_id: Primary key of the pollster row.
        map_id: Primary key of the constituency map row.
        parsed: Parsed poll data containing the fieldwork dates and sample size
            used as lookup keys.

    Returns:
        The matching :class:`Poll` ORM object, or ``None`` if no match is found.
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


def fetch_parsed_poll(source_url: str, *, year_hint: int | None = None) -> ParsedPoll:
    """Download a Techne PDF and parse it.

    Args:
        source_url: URL of the PDF.
        year_hint: Fallback year when none can be inferred from the URL.

    Returns:
        The parsed poll.

    Raises:
        ValueError: If the year cannot be inferred or the PDF cannot be
            fetched or parsed.
    """
    inferred_year = _infer_year(source_url, fallback=year_hint)
    return parse_poll(extract_pdf_text(source_url), inferred_year=inferred_year)


def build_import_plan(
    db: Database,
    *,
    pdf_url: str = DEFAULT_PDF_URL,
    map_name: str = DEFAULT_MAP_NAME,
    pollster_identifier: str = DEFAULT_POLLSTER_IDENTIFIER,
    year_hint: int | None = None,
) -> ImportPlan:
    """Fetch, parse, and validate a Techne poll PDF, returning an :class:`ImportPlan`.

    Downloads the PDF from ``pdf_url``, extracts text, parses poll metadata and
    party percentages, resolves pollster/map/party references from the database,
    and constructs a dry-run-safe plan describing every write that would be
    performed.  No data is written to the database by this function.

    Args:
        db: Active :class:`Database` connection.
        pdf_url: HTTP(S) URL of the Techne PDF to import.  Defaults to
            ``DEFAULT_PDF_URL``.
        map_name: Name of the constituency map row to associate the poll with.
            Defaults to ``DEFAULT_MAP_NAME``.
        pollster_identifier: Short identifier string for the pollster (e.g.
            ``"techne"``).  Defaults to ``DEFAULT_POLLSTER_IDENTIFIER``.
        year_hint: Optional fallback year to use when the year cannot be
            inferred from ``pdf_url``.

    Returns:
        A fully populated :class:`ImportPlan` describing the pollster, poll
        metadata, and per-party :class:`PlannedPollRow` objects.

    Raises:
        ValueError: If the map name is not found in the database, the year
            cannot be inferred and no hint is supplied, any required party is
            absent from the database, or PDF parsing fails.
    """
    poll_map = db.get_map_by_name(map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {map_name!r}")

    parsed = fetch_parsed_poll(pdf_url, year_hint=year_hint)

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
        bases=plan_poll_bases(parsed.bases, {}),
    )


def commit_import_plan(
    db: Database,
    plan: ImportPlan,
    *,
    replace_rows: bool = False,
) -> PollImportResult:
    """Write an :class:`ImportPlan` to the database.

    Creates the pollster and poll rows if they do not already exist.  Inserts
    :class:`PollRow` records for every party in the plan.  If rows already
    exist for the poll they are skipped unless ``replace_rows`` is ``True``,
    in which case existing rows are deleted before insertion.

    Side effects:
        - May create a new :class:`Pollster` row.
        - May create a new :class:`Poll` row; updates ``source_url`` on an
          existing poll if it has changed.
        - Deletes existing :class:`PollRow` records when ``replace_rows`` is
          ``True``.
        - Inserts new :class:`PollRow` records.

    Args:
        db: Active :class:`Database` connection.
        plan: The :class:`ImportPlan` produced by :func:`build_import_plan`.
        replace_rows: When ``True``, delete any pre-existing rows for the poll
            before inserting the new ones.  Defaults to ``False``.

    Returns:
        A :class:`PollImportResult` summarising what was created, replaced, or
        skipped.

    Raises:
        ValueError: If the pollster lookup fails unexpectedly during commit
            (should not occur under normal operation).
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
    """Print a human-readable dry-run preview of an :class:`ImportPlan` to stdout.

    Displays parsed poll metadata (fieldwork window, sample size), whether the
    pollster and poll already exist, and the rows that would be inserted.

    Args:
        plan: The :class:`ImportPlan` to preview.
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
    """CLI entry point for importing a Techne poll from a PDF URL.

    Parses command-line arguments, fetches and parses the PDF, then either
    prints a dry-run preview (``--dry-run``) or commits the import to the
    database.

    CLI arguments:
        --pdf-url (str, optional): URL of the Techne PDF to import.
            Defaults to ``DEFAULT_PDF_URL``.
        --map-name (str, optional): Name of the constituency map to use.
            Defaults to ``DEFAULT_MAP_NAME``.
        --pollster-identifier (str, optional): Short pollster identifier.
            Defaults to ``DEFAULT_POLLSTER_IDENTIFIER``.
        --year-hint (int, optional): Fallback year when it cannot be inferred
            from the PDF URL.
        --replace-rows (flag): Delete existing poll rows before inserting new
            ones.
        --dry-run (flag): Print a preview of what would be imported without
            writing to the database.
    """
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
