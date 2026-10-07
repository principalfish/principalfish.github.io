"""Tests for the Survation XLSX poll importer.

Covers pure parsing helpers that require no network access or database.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from types import MappingProxyType
from urllib.request import Request

import pytest
from openpyxl import Workbook

from db import Database
from polls.importers.westminster import survation_import
from polls.importers.westminster.survation_import import (
    NATIONAL_KEY,
    ImportPlan,
    ParsedPoll,
    PlannedPollRow,
    _cell_text,
    _cli_preview,
    _cover_sheet,
    _find_vi_table_start,
    _infer_year,
    _month_number,
    _parse_cover_metadata,
    _parse_fieldwork,
    _parse_party_region_percentages,
    _tables_sheet,
    _to_percentage,
    _to_percentage_or_zero,
    build_import_plan,
    extract_workbook,
    main,
    parse_poll,
)
from tests.uk_fixtures import (
    FakeUrlResponse,
    WestminsterWorld,
    add_poll_with_rows,
    build_workbook,
    workbook_bytes,
)


# ── _month_number ─────────────────────────────────────────────────────────────


class TestMonthNumber:
    """Tests for _month_number — month name → integer conversion."""

    def test_full_names(self) -> None:
        assert _month_number("January") == 1
        assert _month_number("February") == 2
        assert _month_number("March") == 3
        assert _month_number("April") == 4
        assert _month_number("May") == 5
        assert _month_number("June") == 6
        assert _month_number("July") == 7
        assert _month_number("August") == 8
        assert _month_number("September") == 9
        assert _month_number("October") == 10
        assert _month_number("November") == 11
        assert _month_number("December") == 12

    def test_abbreviated_names(self) -> None:
        assert _month_number("Jan") == 1
        assert _month_number("Feb") == 2
        assert _month_number("Sep") == 9
        assert _month_number("Sept") == 9
        assert _month_number("Dec") == 12

    def test_case_insensitive(self) -> None:
        assert _month_number("JANUARY") == 1
        assert _month_number("january") == 1
        assert _month_number("jAnUaRy") == 1

    def test_trailing_period_stripped(self) -> None:
        assert _month_number("Jan.") == 1

    def test_unknown_returns_none(self) -> None:
        assert _month_number("Octember") is None
        assert _month_number("") is None
        assert _month_number("13") is None


# ── _infer_year ───────────────────────────────────────────────────────────────


class TestInferYear:
    """Tests for _infer_year — four-digit year extraction from URLs."""

    def test_year_as_path_segment(self) -> None:
        url = "https://cdn.survation.com/wp-content/uploads/2026/01/survey.xlsx"
        assert _infer_year(url) == 2026

    def test_year_as_bare_occurrence(self) -> None:
        url = "https://cdn.example.com/survey_2025_v2.xlsx"
        assert _infer_year(url) == 2025

    def test_path_segment_preferred_over_bare(self) -> None:
        # URL contains /2026/ (path segment) and also 2024 (bare) — path takes priority
        url = "https://cdn.example.com/2024archive/2026/survey.xlsx"
        assert _infer_year(url) == 2026

    def test_fallback_when_no_year_in_url(self) -> None:
        url = "https://cdn.example.com/survey.xlsx"
        assert _infer_year(url, fallback=2025) == 2025

    def test_no_year_no_fallback_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not infer year"):
            _infer_year("https://cdn.example.com/survey.xlsx")


# ── _parse_fieldwork ──────────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for _parse_fieldwork — single-day and date-range parsing."""

    def test_reported_single_day(self) -> None:
        start, end = _parse_fieldwork("29th September 2026")
        assert start == end == date(2026, 9, 29)

    def test_explicit_cross_month_year_overrides_default(self) -> None:
        start, end = _parse_fieldwork("30 Jan - 1 Feb 2025", default_year=2026)
        assert start == date(2025, 1, 30)
        assert end == date(2025, 2, 1)

    @pytest.mark.parametrize(
        "fieldwork_text",
        ["29 September 2026", "29 sep 2026", " 29TH\t SEPTEMBER\n 2026 "],
    )
    def test_single_day_normalisation(self, fieldwork_text: str) -> None:
        start, end = _parse_fieldwork(fieldwork_text)
        assert start == end == date(2026, 9, 29)

    def test_single_day_no_year_uses_default(self) -> None:
        start, end = _parse_fieldwork("29th September", default_year=2026)
        assert start == end == date(2026, 9, 29)

    def test_single_day_no_year_or_default_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            _parse_fieldwork("29 September")

    def test_single_day_explicit_year_overrides_default(self) -> None:
        start, end = _parse_fieldwork("29 September 2025", default_year=2026)
        assert start == end == date(2025, 9, 29)

    def test_single_day_unknown_month_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse month"):
            _parse_fieldwork("29 Notamonth 2026")

    @pytest.mark.parametrize(
        "fieldwork_text",
        ["31 September 2026", "29 February 2026", "0 September 2026"],
    )
    def test_single_day_invalid_calendar_date_raises(self, fieldwork_text: str) -> None:
        with pytest.raises(ValueError):
            _parse_fieldwork(fieldwork_text)

    def test_single_day_valid_leap_day(self) -> None:
        start, end = _parse_fieldwork("29 February 2024", default_year=2026)
        assert start == end == date(2024, 2, 29)

    @pytest.mark.parametrize(
        "fieldwork_text",
        [
            "29 September to 2 October 2026",
            "Fieldwork: 29 September 2026",
            "29 September 2026 extra",
        ],
    )
    def test_single_day_must_match_complete_value(self, fieldwork_text: str) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            _parse_fieldwork(fieldwork_text, default_year=2026)

    def test_explicit_same_month_year_overrides_default(self) -> None:
        start, end = _parse_fieldwork("3-5 January 2025", default_year=2026)
        assert start == date(2025, 1, 3)
        assert end == date(2025, 1, 5)

    def test_explicit_cross_year_overrides_default_and_decrements_start(self) -> None:
        start, end = _parse_fieldwork("31 Dec - 2 Jan 2025", default_year=2026)
        assert start == date(2024, 12, 31)
        assert end == date(2025, 1, 2)

    def test_range_search_keeps_surrounding_text(self) -> None:
        start, end = _parse_fieldwork(
            "Fieldwork: 30 Jan - 1 Feb 2025 inclusive", default_year=2026
        )
        assert start == date(2025, 1, 30)
        assert end == date(2025, 2, 1)

    def test_invalid_explicit_range_does_not_fall_back_to_default(self) -> None:
        with pytest.raises(ValueError):
            _parse_fieldwork("29 Feb - 1 Mar 2025", default_year=2024)

    def test_same_month_with_year(self) -> None:
        start, end = _parse_fieldwork("3-5 January 2026")
        assert start == date(2026, 1, 3)
        assert end == date(2026, 1, 5)

    def test_same_month_no_year_uses_default(self) -> None:
        start, end = _parse_fieldwork("3-5 January", default_year=2026)
        assert start == date(2026, 1, 3)
        assert end == date(2026, 1, 5)

    def test_cross_month_with_year(self) -> None:
        start, end = _parse_fieldwork("30 Jan - 1 Feb 2026")
        assert start == date(2026, 1, 30)
        assert end == date(2026, 2, 1)

    def test_cross_month_no_year_uses_default(self) -> None:
        start, end = _parse_fieldwork("30 Jan - 1 Feb", default_year=2026)
        assert start == date(2026, 1, 30)
        assert end == date(2026, 2, 1)

    def test_cross_year_decrements_start_year(self) -> None:
        start, end = _parse_fieldwork("31 Dec - 2 Jan", default_year=2026)
        assert start == date(2025, 12, 31)
        assert end == date(2026, 1, 2)

    def test_ordinal_suffixes_stripped(self) -> None:
        start, end = _parse_fieldwork("3rd-5th January 2026")
        assert start == date(2026, 1, 3)
        assert end == date(2026, 1, 5)

    def test_em_dash_normalised(self) -> None:
        start, end = _parse_fieldwork("3–5 January 2026")
        assert start == date(2026, 1, 3)
        assert end == date(2026, 1, 5)

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _parse_fieldwork("not a date")

    def test_unparseable_month_same_month_with_year_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse month"):
            _parse_fieldwork("3-5 Notamonth 2026")

    def test_unparseable_month_same_month_no_year_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse month"):
            _parse_fieldwork("3-5 Notamonth", default_year=2026)

    def test_unparseable_month_cross_month_no_year_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse months"):
            _parse_fieldwork("30 Notamonth - 1 Feb", default_year=2026)

    def test_unparseable_month_cross_month_with_year_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse months"):
            _parse_fieldwork("30 Notamonth - 1 Feb 2026")


# ── _cell_text ────────────────────────────────────────────────────────────────


class TestCellText:
    """Tests for _cell_text — openpyxl cell value → stripped string."""

    def test_none_returns_empty_string(self) -> None:
        assert _cell_text(None) == ""

    def test_string_stripped(self) -> None:
        assert _cell_text("  hello  ") == "hello"

    def test_integer_converted(self) -> None:
        assert _cell_text(42) == "42"

    def test_float_converted(self) -> None:
        assert _cell_text(3.14) == "3.14"

    def test_empty_string(self) -> None:
        assert _cell_text("") == ""


# ── _to_percentage ────────────────────────────────────────────────────────────


class TestToPercentage:
    """Tests for _to_percentage — raw cell value → percentage float."""

    def test_integer_in_0_100_range(self) -> None:
        assert _to_percentage(42) == pytest.approx(42.0)

    def test_decimal_in_0_1_range_multiplied(self) -> None:
        assert _to_percentage(0.42) == pytest.approx(42.0)

    def test_rounds_to_nearest_integer(self) -> None:
        assert _to_percentage(34.6) == pytest.approx(35.0)
        assert _to_percentage(34.4) == pytest.approx(34.0)

    def test_zero(self) -> None:
        assert _to_percentage(0) == pytest.approx(0.0)

    def test_one_boundary_treated_as_100_percent(self) -> None:
        # 1.0 is in the 0–1 range, so multiplied to 100
        assert _to_percentage(1.0) == pytest.approx(100.0)

    def test_none_raises(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            _to_percentage(None)


# ── _to_percentage_or_zero ────────────────────────────────────────────────────


class TestToPercentageOrZero:
    """Tests for _to_percentage_or_zero — blank-tolerant percentage conversion."""

    def test_none_returns_zero(self) -> None:
        assert _to_percentage_or_zero(None) == pytest.approx(0.0)

    def test_hyphen_returns_zero(self) -> None:
        assert _to_percentage_or_zero("-") == pytest.approx(0.0)

    def test_en_dash_returns_zero(self) -> None:
        assert _to_percentage_or_zero("–") == pytest.approx(0.0)

    def test_empty_string_returns_zero(self) -> None:
        assert _to_percentage_or_zero("") == pytest.approx(0.0)

    def test_numeric_delegates_to_to_percentage(self) -> None:
        assert _to_percentage_or_zero(0.35) == pytest.approx(35.0)

    def test_integer_in_0_100_range(self) -> None:
        assert _to_percentage_or_zero(28) == pytest.approx(28.0)

    def test_non_dash_string_delegates_to_to_percentage(self) -> None:
        # A numeric string that isn't blank or a dash falls through to
        # _to_percentage rather than being treated as a blank cell.
        assert _to_percentage_or_zero("42") == pytest.approx(42.0)


# ── Synthetic workbook builders ─────────────────────────────────────────────

# A .test URL (never resolves) that carries a /2026/ path segment, so
# _infer_year resolves without a year_hint.
_XLSX_URL = "https://polls.example.test/2026/01/x.xlsx"

# Every canonical party name _parse_party_region_percentages requires, spelled
# out rather than derived from PARTY_NAME_MAP (the module under test).
_ALL_PARTY_NAMES: tuple[str, ...] = (
    "Conservative",
    "Labour",
    "Liberal Democrats",
    "Reform UK",
    "Green",
    "Scottish National Party",
    "Plaid Cymru",
    "Other",
)

# Column (1-based) each source region header occupies in the VI table, matching
# SOURCE_REGION_TO_INTERNAL's keys exactly (the text a real Survation tables
# sheet prints in the header row).
_REGION_COLUMNS: Mapping[str, int] = MappingProxyType(
    {
        "East Midlands": 11,
        "East of England": 12,
        "London": 13,
        "North East": 14,
        "North West": 15,
        "South East": 16,
        "South West": 17,
        "West Midlands": 18,
        "Yorkshire and The Humber": 19,
        "Scotland": 20,
        "Wales": 21,
        "Northern Ireland": 22,
    }
)

# One national figure and a handful of distinct regional figures per party,
# covering every canonical party _parse_party_region_percentages requires.
# Conservative's Northern Ireland cell is a dash, to prove dash-to-zero
# conversion inside the parser itself (as opposed to the DB-region default
# build_import_plan applies for a region missing from the header entirely).
# National figures avoid exactly 1 (or any 0-1 value), which _to_percentage's
# fraction branch would silently scale up to 100.
_DEFAULT_PARTY_FIGURES: tuple[tuple[str, float, Mapping[str, object]], ...] = (
    ("Conservative", 32, MappingProxyType({"London": 25.0, "Northern Ireland": "-"})),
    ("Labour", 40, MappingProxyType({"London": 45.0, "Scotland": 20.0})),
    ("Liberal Democrats", 10, MappingProxyType({})),
    ("Reform UK", 9, MappingProxyType({"North East": 18.0})),
    ("Green", 4, MappingProxyType({"South West": 6.0})),
    ("Scottish National Party", 3, MappingProxyType({"Scotland": 35.0})),
    ("Plaid Cymru", 2, MappingProxyType({"Wales": 22.0})),
    ("Other", 2, MappingProxyType({})),
)

_TITLE_ROW: tuple[object, ...] = (
    "Table_1. If there was a UK Parliament General Election tomorrow, for "
    "which party would you vote?",
)
_PREFERRED_BASE_ROW: tuple[object, ...] = (
    "Base: all respondents, excluding undecided voters and those who would "
    "remove their preference",
)


def _sparse_row(cells: Mapping[int, object], width: int = 23) -> list[object]:
    """Return a ``width``-long row with 1-based ``cells`` overrides, else None."""
    row: list[object] = [None] * width
    for column, value in cells.items():
        row[column - 1] = value
    return row


def _region_header_row(*, omit: Sequence[str] = ()) -> list[object]:
    """The region-column header row, with any names in ``omit`` left out."""
    cells = {col: name for name, col in _REGION_COLUMNS.items() if name not in omit}
    return _sparse_row(cells)


def _party_rows(
    party: str, national: object, regional: Mapping[str, object] | None = None
) -> tuple[list[object], list[object]]:
    """A (label row, value row) pair for one party in the VI table."""
    cells: dict[int, object] = {2: national}
    for region_name, value in (regional or {}).items():
        cells[_REGION_COLUMNS[region_name]] = value
    return [party], _sparse_row(cells)


def _tables_rows(
    *,
    include_region_header: bool = True,
    omit_region_from_header: Sequence[str] = (),
    omit_party: str | None = None,
    figures: Sequence[
        tuple[str, float, Mapping[str, object]]
    ] = _DEFAULT_PARTY_FIGURES,
) -> list[Sequence[object]]:
    """Build a full Survation-style voting-intention table's rows."""
    rows: list[Sequence[object]] = [_TITLE_ROW, _PREFERRED_BASE_ROW]
    if include_region_header:
        rows.append(_region_header_row(omit=omit_region_from_header))
    for party, national, regional in figures:
        if party == omit_party:
            continue
        label_row, value_row = _party_rows(party, national, regional)
        rows.append(label_row)
        rows.append(value_row)
    return rows


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    """Monkeypatch survation_import.urlopen to serve ``payload``.

    Returns the list of URLs each request actually carried (via
    ``Request.full_url``), so a test can prove the URL it passed all the way
    through rather than the fake having silently ignored it.
    """
    requested: list[str] = []

    def _fake(req: Request, *_a: object, **_k: object) -> FakeUrlResponse:
        requested.append(req.full_url)
        return FakeUrlResponse(payload)

    monkeypatch.setattr(survation_import, "urlopen", _fake)
    return requested


def _raising_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch survation_import.urlopen to fail any call.

    Used to prove a code path returns before ever reaching the network,
    rather than merely happening not to be exercised by the test's fixture.
    """

    def _fail(*_a: object, **_k: object) -> FakeUrlResponse:
        raise AssertionError("urlopen should not have been called")

    monkeypatch.setattr(survation_import, "urlopen", _fail)


def _cover_rows(
    *,
    fieldwork_text: object = "3-5 January 2026",
    sample_cell: object = 1511,
    include_fieldwork: bool = True,
    include_sample: bool = True,
) -> list[list[object]]:
    """Build a Survation-style cover/methodology sheet's rows."""
    rows: list[list[object]] = [["Survation Omnibus"]]
    if include_fieldwork:
        rows.append(["Fieldwork Dates"])
        rows.append([fieldwork_text])
    if include_sample:
        rows.append(["Sample Size"])
        rows.append([sample_cell])
    return rows


def _full_workbook(
    *,
    cover_rows: Sequence[Sequence[object]] | None = None,
    tables_rows: Sequence[Sequence[object]] | None = None,
) -> Workbook:
    """A workbook with a cover sheet and a tables sheet, both fully populated."""
    return build_workbook(
        {
            "Cover and Methodology": (
                cover_rows if cover_rows is not None else _cover_rows()
            ),
            "Tables": tables_rows if tables_rows is not None else _tables_rows(),
        }
    )


# ── _cover_sheet ─────────────────────────────────────────────────────────────


class TestCoverSheet:
    """Tests for _cover_sheet — locating the cover/methodology sheet."""

    def test_matches_cover_and_method_in_name(self) -> None:
        workbook = build_workbook(
            {"Summary": [["x"]], "Cover and Methodology": [["y"]], "Tables": [["z"]]}
        )
        assert _cover_sheet(workbook).title == "Cover and Methodology"

    def test_falls_back_to_first_sheet(self) -> None:
        workbook = build_workbook({"Intro": [["x"]], "Tables": [["y"]]})
        assert _cover_sheet(workbook).title == "Intro"


# ── _tables_sheet ────────────────────────────────────────────────────────────


class TestTablesSheet:
    """Tests for _tables_sheet — locating the data tables sheet by priority."""

    def test_exact_name_match(self) -> None:
        workbook = build_workbook({"Intro": [["x"]], "Tables": [["y"]]})
        assert _tables_sheet(workbook).title == "Tables"

    def test_starts_with_tables_when_no_exact_match(self) -> None:
        workbook = build_workbook({"Intro": [["x"]], "TablesAppendix": [["y"]]})
        assert _tables_sheet(workbook).title == "TablesAppendix"

    def test_contains_table_excluding_contents(self) -> None:
        workbook = build_workbook({"Intro": [["x"]], "Full Tables Data": [["y"]]})
        assert _tables_sheet(workbook).title == "Full Tables Data"

    def test_exact_match_wins_over_startswith_candidate(self) -> None:
        # Both sheets would match on their own tier; the exact "Tables" name
        # must win over "TablesAppendix" (a startswith-only candidate).
        workbook = build_workbook(
            {"TablesAppendix": [["x"]], "Tables": [["y"]]}
        )
        assert _tables_sheet(workbook).title == "Tables"

    def test_startswith_wins_over_contains_candidate(self) -> None:
        # Neither sheet matches exactly; "Tables Extra" (starts with
        # "tables") must win over "Full Table Data" (contains-only).
        workbook = build_workbook(
            {"Full Table Data": [["x"]], "Tables Extra": [["y"]]}
        )
        assert _tables_sheet(workbook).title == "Tables Extra"

    def test_table_of_contents_sheet_excluded_raises(self) -> None:
        workbook = build_workbook({"Intro": [["x"]], "Table of Contents": [["y"]]})
        with pytest.raises(ValueError, match="Could not find tables sheet"):
            _tables_sheet(workbook)

    def test_no_matching_sheet_raises(self) -> None:
        workbook = build_workbook({"Intro": [["x"]], "Summary": [["y"]]})
        with pytest.raises(ValueError, match="Could not find tables sheet"):
            _tables_sheet(workbook)


# ── _find_vi_table_start ─────────────────────────────────────────────────────


class TestFindViTableStart:
    """Tests for _find_vi_table_start — locating the VI table header row."""

    def test_prefers_the_last_preferred_row(self) -> None:
        # Four header rows at 1/3/5/7 (bases at 2/4/6/8): rows 3 and 5 are
        # preferred (their base mentions "undecided" and "remove"), rows 1
        # and 7 are plain. The expected winner (5) is neither the first nor
        # the last matching row, nor the first preferred row — so a mutant
        # that ignores preference (returns 7), returns the first preferred
        # row (3), or returns the first matching row outright (1) all
        # disagree with it.
        workbook = build_workbook(
            {
                "Tables": [
                    ["Table_1. General Election for which party would you vote?"],
                    ["Base: all respondents"],
                    ["Table_2. General Election for which party would you vote?"],
                    ["Base: excluding undecided voters, don't knows removed"],
                    ["Table_3. General Election for which party would you vote?"],
                    ["Base: excluding undecided voters, don't knows removed"],
                    ["Table_4. General Election for which party would you vote?"],
                    ["Base: all respondents"],
                ]
            }
        )
        assert _find_vi_table_start(workbook["Tables"]) == 5

    def test_falls_back_to_last_plain_match_when_none_preferred(self) -> None:
        workbook = build_workbook(
            {
                "Tables": [
                    ["Table_1. General Election for which party would you vote?"],
                    ["Base: all respondents"],
                    [
                        "Table_2. Westminster Election for which party would "
                        "you vote?"
                    ],
                    ["Base: all likely voters"],
                ]
            }
        )
        assert _find_vi_table_start(workbook["Tables"]) == 3

    def test_no_match_raises(self) -> None:
        workbook = build_workbook({"Tables": [["Nothing relevant here"]]})
        with pytest.raises(
            ValueError, match="Could not locate voting intention table"
        ):
            _find_vi_table_start(workbook["Tables"])

    def test_partial_header_matches_are_skipped(self) -> None:
        # Neither row 1 (no "general election"/"westminster election") nor
        # row 2 (no "for which party"/"vote") is a real table header; only
        # row 3 is, and it must still be the one returned.
        workbook = build_workbook(
            {
                "Tables": [
                    ["Table_9. Scottish Parliament regional list question"],
                    ["Table_8. General Election awareness question"],
                    ["Table_1. General Election for which party would you vote?"],
                ]
            }
        )
        assert _find_vi_table_start(workbook["Tables"]) == 3

    def test_single_missing_phrase_disqualifies_a_row(self) -> None:
        # The real header (row 1, "westminster election" only — proving
        # general/westminster is an OR) comes *first*; rows 2 and 3 each
        # miss exactly one required phrase ("vote" / "for which party") and
        # must be excluded. They're placed *after* row 1 deliberately: if
        # the "for which party"/"vote" OR were collapsed to an AND, rows 2
        # and 3 would wrongly match too and, being later, would become the
        # returned row — so a same-answer coincidence can't hide the bug.
        workbook = build_workbook(
            {
                "Tables": [
                    ["Table_1. Westminster election for which party would you vote?"],
                    ["Table_2. General election for which party (percentage share)"],
                    [
                        "Table_3. General election voting intention: who "
                        "would you vote for?"
                    ],
                ]
            }
        )
        assert _find_vi_table_start(workbook["Tables"]) == 1


# ── _parse_cover_metadata ────────────────────────────────────────────────────


class TestParseCoverMetadata:
    """Tests for _parse_cover_metadata — fieldwork dates and sample size."""

    def test_parses_dates_and_numeric_sample_size(self) -> None:
        workbook = build_workbook({"Cover": _cover_rows()})
        start, end, sample_size = _parse_cover_metadata(workbook, 2026)
        assert start == date(2026, 1, 3)
        assert end == date(2026, 1, 5)
        assert sample_size == 1511

    def test_sample_size_as_text_extracts_digits(self) -> None:
        workbook = build_workbook(
            {"Cover": _cover_rows(sample_cell="1,511 GB adults")}
        )
        _, _, sample_size = _parse_cover_metadata(workbook, 2026)
        assert sample_size == 1511

    def test_missing_fieldwork_raises(self) -> None:
        workbook = build_workbook({"Cover": _cover_rows(include_fieldwork=False)})
        with pytest.raises(ValueError, match="Fieldwork dates not found"):
            _parse_cover_metadata(workbook, 2026)

    def test_missing_sample_size_label_raises(self) -> None:
        workbook = build_workbook({"Cover": _cover_rows(include_sample=False)})
        with pytest.raises(ValueError, match="Sample size not found"):
            _parse_cover_metadata(workbook, 2026)

    def test_sample_size_label_with_no_digits_raises(self) -> None:
        workbook = build_workbook({"Cover": _cover_rows(sample_cell="n/a")})
        with pytest.raises(ValueError, match="Sample size not found"):
            _parse_cover_metadata(workbook, 2026)


# ── _parse_party_region_percentages ─────────────────────────────────────────


class TestParsePartyRegionPercentages:
    """Tests for _parse_party_region_percentages — party x region VI extraction."""

    def test_national_and_every_region(self) -> None:
        workbook = build_workbook({"Tables": _tables_rows()})
        parsed = _parse_party_region_percentages(workbook)

        assert parsed["Conservative"][NATIONAL_KEY] == 32.0
        assert parsed["Conservative"]["London"] == 25.0
        assert parsed["Labour"][NATIONAL_KEY] == 40.0
        assert parsed["Labour"]["Scotland"] == 20.0
        assert parsed["Scottish National Party"]["Scotland"] == 35.0
        assert parsed["Plaid Cymru"]["Wales"] == 22.0
        # Witnesses for source→internal region renaming (SOURCE_REGION_TO_
        # INTERNAL): the parsed dict is keyed by the *internal* name, not the
        # source header text ("North East" / "South West").
        assert parsed["Reform UK"]["North East England"] == 18.0
        assert parsed["Green"]["South West England"] == 6.0
        assert "North East" not in parsed["Reform UK"]
        assert "South West" not in parsed["Green"]

    def test_dash_cell_becomes_zero(self) -> None:
        workbook = build_workbook({"Tables": _tables_rows()})
        parsed = _parse_party_region_percentages(workbook)
        assert parsed["Conservative"]["Northern Ireland"] == 0.0

    def test_optional_party_present_keeps_its_own_value(self) -> None:
        workbook = build_workbook({"Tables": _tables_rows()})
        parsed = _parse_party_region_percentages(workbook)
        # SNP is one of the optional-defaulted parties but is present in the
        # table, so the trailing setdefault pass must not clobber its figure.
        assert parsed["Scottish National Party"][NATIONAL_KEY] == 3.0

    def test_optional_party_absent_defaults_every_region_to_zero(self) -> None:
        workbook = build_workbook({"Tables": _tables_rows(omit_party="Plaid Cymru")})
        parsed = _parse_party_region_percentages(workbook)
        assert parsed["Plaid Cymru"][NATIONAL_KEY] == 0.0
        assert parsed["Plaid Cymru"]["Wales"] == 0.0
        assert parsed["Plaid Cymru"]["Scotland"] == 0.0

    def test_required_party_absent_raises(self) -> None:
        workbook = build_workbook({"Tables": _tables_rows(omit_party="Green")})
        with pytest.raises(ValueError, match=r"Missing expected party rows.*Green"):
            _parse_party_region_percentages(workbook)

    def test_non_numeric_percentage_row_is_skipped(self) -> None:
        # The stray non-numeric "Reform UK" row sits *between* two real
        # party rows (Reform UK's own row and Green's), not at the end of
        # the table. A `continue`-to-`break` mutation on the non-numeric
        # guard would truncate parsing there and lose every party after
        # it — so the test would fail on the required-party check, not
        # pass by accident because there was nothing left to lose.
        figures_before = _DEFAULT_PARTY_FIGURES[:4]  # ...through Reform UK
        figures_after = _DEFAULT_PARTY_FIGURES[4:]  # Green onward
        rows: list[Sequence[object]] = [
            _TITLE_ROW,
            _PREFERRED_BASE_ROW,
            _region_header_row(),
        ]
        for party, national, regional in figures_before:
            label_row, value_row = _party_rows(party, national, regional)
            rows.append(label_row)
            rows.append(value_row)
        rows.append(["Reform UK"])
        rows.append([None, "not numeric"])
        for party, national, regional in figures_after:
            label_row, value_row = _party_rows(party, national, regional)
            rows.append(label_row)
            rows.append(value_row)

        workbook = build_workbook({"Tables": rows})
        parsed = _parse_party_region_percentages(workbook)
        assert parsed["Reform UK"][NATIONAL_KEY] == 9.0
        assert parsed["Green"][NATIONAL_KEY] == 4.0
        assert parsed["Other"][NATIONAL_KEY] == 2.0

    def test_contents_row_within_six_rows_does_not_stop_parsing(self) -> None:
        # "Contents" only breaks parsing when it appears more than 6 rows
        # after the table header (start_row); here it's row 2 (start_row=1),
        # well inside that window, so parsing must continue past it.
        rows: list[Sequence[object]] = [
            _TITLE_ROW,
            ["Contents (see page 2)"],
            _PREFERRED_BASE_ROW,
            _region_header_row(),
        ]
        for party, national, regional in _DEFAULT_PARTY_FIGURES:
            label_row, value_row = _party_rows(party, national, regional)
            rows.append(label_row)
            rows.append(value_row)

        workbook = build_workbook({"Tables": rows})
        parsed = _parse_party_region_percentages(workbook)
        assert parsed["Conservative"][NATIONAL_KEY] == 32.0
        assert parsed["Other"][NATIONAL_KEY] == 2.0

    def test_no_region_header_leaves_only_national(self) -> None:
        workbook = build_workbook(
            {"Tables": _tables_rows(include_region_header=False)}
        )
        parsed = _parse_party_region_percentages(workbook)
        assert parsed["Conservative"] == {NATIONAL_KEY: 32.0}
        assert parsed["Labour"] == {NATIONAL_KEY: 40.0}

    def test_total_row_stops_parsing(self) -> None:
        rows: list[Sequence[object]] = [
            *_tables_rows(),
            ["Total"],
            ["Conservative"],
            [None, 999],
        ]
        workbook = build_workbook({"Tables": rows})
        parsed = _parse_party_region_percentages(workbook)
        assert parsed["Conservative"][NATIONAL_KEY] == 32.0

    def test_contents_row_after_start_stops_parsing(self) -> None:
        rows: list[Sequence[object]] = [
            *_tables_rows(),
            ["Contents (continued)"],
            ["Labour"],
            [None, 999],
        ]
        workbook = build_workbook({"Tables": rows})
        parsed = _parse_party_region_percentages(workbook)
        assert parsed["Labour"][NATIONAL_KEY] == 40.0


# ── parse_poll ───────────────────────────────────────────────────────────────


class TestParsePoll:
    """Tests for parse_poll — combining cover metadata with VI percentages."""

    def test_reported_single_day_workbook(self) -> None:
        workbook = _full_workbook(
            cover_rows=_cover_rows(
                fieldwork_text="29th September 2026", sample_cell=1548
            )
        )
        source_url = (
            "https://cdn.survation.com/wp-content/uploads/2026/09/30073002/"
            "Mandate_Burnham_Speech_2026-09-29_Tables.xlsx"
        )
        parsed = parse_poll(workbook, source_url=source_url)
        assert parsed.fieldwork_start == parsed.fieldwork_end == date(2026, 9, 29)
        assert parsed.sample_size == 1548
        assert parsed.party_region_percentages["Labour"][NATIONAL_KEY] == 40.0
        assert parsed.party_region_percentages["Conservative"][NATIONAL_KEY] == 32.0
        assert parsed.party_region_percentages["Labour"]["London"] == 45.0
        assert parsed.party_region_percentages["Reform UK"]["North East England"] == 18.0

    def test_yearless_single_day_uses_url_year(self) -> None:
        workbook = _full_workbook(
            cover_rows=_cover_rows(fieldwork_text="29 September")
        )
        parsed = parse_poll(workbook, source_url=_XLSX_URL)
        assert parsed.fieldwork_start == parsed.fieldwork_end == date(2026, 9, 29)
        assert parsed.sample_size == 1511
        assert parsed.party_region_percentages["Labour"][NATIONAL_KEY] == 40.0

    def test_explicit_cross_month_year_overrides_url_year(self) -> None:
        workbook = _full_workbook(
            cover_rows=_cover_rows(fieldwork_text="30 Jan - 1 Feb 2025")
        )
        parsed = parse_poll(workbook, source_url=_XLSX_URL)
        assert parsed.fieldwork_start == date(2025, 1, 30)
        assert parsed.fieldwork_end == date(2025, 2, 1)
        assert parsed.sample_size == 1511
        assert parsed.party_region_percentages["Labour"][NATIONAL_KEY] == 40.0

    def test_full_workbook(self) -> None:
        workbook = _full_workbook()
        parsed = parse_poll(workbook, source_url=_XLSX_URL)
        assert parsed.sample_size == 1511
        assert parsed.fieldwork_start == date(2026, 1, 3)
        assert parsed.fieldwork_end == date(2026, 1, 5)
        assert parsed.party_region_percentages["Labour"][NATIONAL_KEY] == 40.0

    def test_year_hint_used_when_url_has_no_year(self) -> None:
        workbook = _full_workbook(
            cover_rows=_cover_rows(fieldwork_text="3-5 January")
        )
        parsed = parse_poll(
            workbook,
            source_url="https://cdn.example.test/survation.xlsx",
            year_hint=2026,
        )
        assert parsed.fieldwork_start == date(2026, 1, 3)


# ── extract_workbook ─────────────────────────────────────────────────────────


class TestExtractWorkbook:
    """Tests for extract_workbook — fetching and loading the XLSX payload."""

    def test_downloads_and_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = workbook_bytes(_full_workbook())
        requested = _patch_urlopen(monkeypatch, payload)

        loaded = extract_workbook(_XLSX_URL)

        assert set(loaded.sheetnames) == {"Cover and Methodology", "Tables"}
        # Proves the URL argument reached urlopen rather than the fake just
        # ignoring whatever it was called with.
        assert requested == [_XLSX_URL]

    def test_non_xlsx_payload_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            survation_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(b"not an xlsx file"),
        )
        with pytest.raises(ValueError, match="Could not fetch XLSX payload"):
            extract_workbook(_XLSX_URL)


# ── build_import_plan ────────────────────────────────────────────────────────


class TestBuildImportPlan:
    """Tests for build_import_plan — plan construction against the DB."""

    def test_map_missing_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The map check must raise before any fetch — proven by making the
        # fetch itself blow up if it's ever reached, rather than merely
        # relying on incidental check order and never calling urlopen.
        _raising_urlopen(monkeypatch)
        with pytest.raises(ValueError, match="Map not found"):
            build_import_plan(db, xlsx_url=_XLSX_URL)

    def test_missing_parties_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db.add_map(survation_import.DEFAULT_MAP_NAME, parliament="westminster")
        for name in _ALL_PARTY_NAMES:
            if name != "Green":
                db.add_party(name)
        payload = workbook_bytes(_full_workbook())
        requested = _patch_urlopen(monkeypatch, payload)

        with pytest.raises(ValueError, match=r"Missing parties.*Green"):
            build_import_plan(db, xlsx_url=_XLSX_URL)

        assert requested == [_XLSX_URL]

    def test_one_row_per_db_region_defaulting_a_header_gap_to_zero(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        # A *real, non-zero* Northern Ireland figure (41.0, not the usual
        # dash) sits in the workbook at its normal column, but the header
        # omits that column's label — so if the default-to-zero logic
        # didn't actually run (e.g. the omission were ignored and the value
        # read anyway), this would surface as 41.0, not 0.0.
        figures = (
            ("Conservative", 32, {"London": 25.0, "Northern Ireland": 41.0}),
            *_DEFAULT_PARTY_FIGURES[1:],
        )
        rows = _tables_rows(
            omit_region_from_header=("Northern Ireland",), figures=figures
        )
        payload = workbook_bytes(
            build_workbook({"Cover": _cover_rows(), "Tables": rows})
        )
        requested = _patch_urlopen(monkeypatch, payload)

        plan = build_import_plan(db, xlsx_url=_XLSX_URL, map_name=world.map_name)

        assert requested == [_XLSX_URL]
        con_rows = [row for row in plan.rows if row.party_name == "Conservative"]
        assert len(con_rows) == 1 + len(world.region_ids)
        by_region = {row.region_name: row for row in con_rows}
        assert by_region["National"].percentage == 32.0
        assert by_region["National"].region_id is None
        assert by_region["National"].party_id == world.party_ids["Conservative"]
        assert by_region["London"].percentage == 25.0
        assert by_region["London"].region_id == world.region_ids["London"]
        assert by_region["London"].party_id == world.party_ids["Conservative"]
        assert by_region["Northern Ireland"].percentage == 0.0
        assert (
            by_region["Northern Ireland"].region_id
            == world.region_ids["Northern Ireland"]
        )
        # A second party (whose id != Conservative's) proves party_id is read
        # per row, not a value that happens to coincide with Conservative's.
        lab_london = next(
            row
            for row in plan.rows
            if row.party_name == "Labour" and row.region_name == "London"
        )
        assert lab_london.party_id == world.party_ids["Labour"]
        assert lab_london.percentage == 45.0

    def test_poll_exists_true_when_seeded(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        payload = workbook_bytes(_full_workbook())
        _patch_urlopen(monkeypatch, payload)
        seeded = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation",
            fieldwork_start=date(2026, 1, 3),
            fieldwork_end=date(2026, 1, 5),
            sample_size=1511,
            national={world.party_ids["Labour"]: 40.0},
        )
        pollster = db.get_pollster_by_identifier("survation")
        assert pollster is not None

        plan = build_import_plan(db, xlsx_url=_XLSX_URL, map_name=world.map_name)

        assert plan.pollster_exists is True
        # The seeded pollster's real name/id (add_poll_with_rows defaults the
        # name to the identifier, "survation") — not the "Survation" literal
        # build_import_plan falls back to when no pollster exists.
        assert plan.pollster_name == "survation"
        assert plan.pollster_id == pollster.id
        assert plan.poll_exists is True
        assert plan.poll_id == seeded.id

    def test_pollster_name_defaults_to_survation(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        payload = workbook_bytes(_full_workbook())
        _patch_urlopen(monkeypatch, payload)

        plan = build_import_plan(db, xlsx_url=_XLSX_URL, map_name=world.map_name)

        assert plan.pollster_exists is False
        assert plan.pollster_id is None
        assert plan.pollster_name == "Survation"


# ── _cli_preview ─────────────────────────────────────────────────────────────


class TestCliPreview:
    """Tests for _cli_preview — the --dry-run stdout preview."""

    def _plan(
        self, *, pollster_exists: bool, poll_exists: bool, poll_id: int | None
    ) -> ImportPlan:
        parsed = ParsedPoll(
            sample_size=1511,
            fieldwork_start=date(2026, 1, 3),
            fieldwork_end=date(2026, 1, 5),
            party_region_percentages={"Labour": {NATIONAL_KEY: 40.0}},
        )
        return ImportPlan(
            pollster_identifier="survation",
            pollster_name="Survation",
            pollster_id=(7 if pollster_exists else None),
            pollster_exists=pollster_exists,
            regions_mapping="",
            map_id=1,
            map_name="UK Constituencies post 2022",
            source_url=_XLSX_URL,
            parsed=parsed,
            poll_id=poll_id,
            poll_exists=poll_exists,
            rows=[
                PlannedPollRow(
                    party_id=2,
                    party_name="Labour",
                    region_id=None,
                    region_name="National",
                    percentage=40.0,
                ),
                PlannedPollRow(
                    party_id=2,
                    party_name="Labour",
                    region_id=13,
                    region_name="London",
                    percentage=45.0,
                ),
            ],
        )

    def test_new_pollster_and_poll(self, capsys: pytest.CaptureFixture[str]) -> None:
        plan = self._plan(pollster_exists=False, poll_exists=False, poll_id=None)
        _cli_preview(plan)
        out = capsys.readouterr().out.splitlines()
        assert out[0] == "Parsed poll: fieldwork=2026-01-03 to 2026-01-05, sample=1511"
        assert "[dry-run] would create pollster: survation" in out
        assert "[dry-run] would create poll" in out
        assert (
            "[dry-run] would insert row: party=Labour, region=National, "
            "region_id=None, pct=40.00" in out
        )
        assert (
            "[dry-run] would insert row: party=Labour, region=London, "
            "region_id=13, pct=45.00" in out
        )

    def test_existing_pollster_and_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        plan = self._plan(pollster_exists=True, poll_exists=True, poll_id=42)
        _cli_preview(plan)
        out = capsys.readouterr().out.splitlines()
        assert "pollster exists: survation" in out
        assert "poll exists: 42" in out


# ── main ─────────────────────────────────────────────────────────────────────


class TestMain:
    """Tests for main — the CLI entry point (dry-run and commit)."""

    def _argv(self, world: WestminsterWorld, *extra: str) -> list[str]:
        return [
            "survation_import.py",
            "--xlsx-url",
            _XLSX_URL,
            "--map-name",
            world.map_name,
            *extra,
        ]

    def test_dry_run_writes_nothing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        payload = workbook_bytes(_full_workbook())
        _patch_urlopen(monkeypatch, payload)
        monkeypatch.setattr(sys, "argv", self._argv(world, "--dry-run"))

        main()

        out = capsys.readouterr().out
        assert "[dry-run] would create pollster: survation" in out
        assert db.get_pollster_by_identifier("survation") is None

    def test_commit_creates_pollster_poll_and_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        payload = workbook_bytes(_full_workbook())
        requested = _patch_urlopen(monkeypatch, payload)
        monkeypatch.setattr(sys, "argv", self._argv(world))

        main()

        out = capsys.readouterr().out
        assert "created pollster: survation" in out
        assert "created poll:" in out
        assert "inserted poll rows:" in out
        # --xlsx-url actually reached urlopen, rather than main() silently
        # falling back to DEFAULT_XLSX_URL.
        assert requested == [_XLSX_URL]
        pollster = db.get_pollster_by_identifier("survation")
        assert pollster is not None
        polls = db.get_polls_by_pollster(pollster.id)
        assert len(polls) == 1
        poll = polls[0]
        assert poll.source_url == _XLSX_URL
        rows = db.get_rows_for_poll(poll.id)
        assert len(rows) == 8 * (1 + len(world.region_ids))
        london_conservative = next(
            row
            for row in rows
            if row.region_id == world.region_ids["London"]
            and row.party_id == world.party_ids["Conservative"]
        )
        assert london_conservative.percentage == 25.0

    def test_commit_existing_poll_skips_without_replace_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        seeded = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation",
            fieldwork_start=date(2026, 1, 3),
            fieldwork_end=date(2026, 1, 5),
            sample_size=1511,
            national={world.party_ids["Labour"]: 1.0},
        )
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        payload = workbook_bytes(_full_workbook())
        _patch_urlopen(monkeypatch, payload)
        monkeypatch.setattr(sys, "argv", self._argv(world))

        main()

        out = capsys.readouterr().out
        assert "pollster exists: survation" in out
        assert f"poll exists: {seeded.id}" in out
        assert "already has rows; use --replace-rows to overwrite" in out
        assert len(db.get_rows_for_poll(seeded.id)) == 1

    def test_commit_with_replace_rows_deletes_and_reinserts(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        seeded = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="survation",
            fieldwork_start=date(2026, 1, 3),
            fieldwork_end=date(2026, 1, 5),
            sample_size=1511,
            national={world.party_ids["Labour"]: 1.0},
        )
        monkeypatch.setenv("DATABASE_PATH", str(only_the_test_database))
        payload = workbook_bytes(_full_workbook())
        _patch_urlopen(monkeypatch, payload)
        monkeypatch.setattr(sys, "argv", self._argv(world, "--replace-rows"))

        main()

        out = capsys.readouterr().out
        assert "deleted existing rows: 1" in out
        assert "inserted poll rows:" in out
        assert len(db.get_rows_for_poll(seeded.id)) == 8 * (1 + len(world.region_ids))
