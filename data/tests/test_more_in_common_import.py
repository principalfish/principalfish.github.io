"""Tests for the More in Common XLSX poll importer.

``commit_import_plan`` and ``_find_existing_poll`` are already covered, across
all eleven Westminster importers, by ``test_westminster_importers_commit.py``
(this module is one of the four "Variant B" importers that also update an
existing pollster's ``regions_mapping`` on commit; that update path is
exercised there, not here). This file covers everything else: the parsing
helpers, ``extract_workbook``, ``build_import_plan``'s region handling,
``_cli_preview`` and ``main``.

Every workbook below is built synthetically with ``build_workbook``: shaped to
fit the columns/labels the parsing functions match on, not sourced from a
real More in Common file, with one exception: ``TestParseFieldwork``'s inverted
same-month-range test uses the literal fieldwork string from the real source
XLSX behind poll 261 in the live DB (fetched via the Wayback Machine and
checked against the DB's stored values and against ``parse_poll`` run on the
real file). That poll's stored dates are inverted not because of a bug in
this module's parsing logic, but because More in Common's own source
spreadsheet has a typo ("20 - 13 February 2026") that ``parse_fieldwork``
passes through unvalidated -- see that test's docstring for detail.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from types import MappingProxyType

import pytest
from openpyxl import Workbook

from db import Database
from polls.importers.westminster import more_in_common_import as mic
from tests.uk_fixtures import (
    FakeUrlResponse,
    WestminsterWorld,
    build_workbook,
    workbook_bytes,
)

# ── Synthetic workbook builders ────────────────────────────────────────────
#
# "Cover page": "Fieldwork"/"Sample size"/"Population effectively represented"
# label rows, read via _find_label_value.
#
# Headline sheet: a header row ("Party" plus REGION_HEADER_TO_INTERNAL's
# distinct internal-region values, prefixed by "All"), one row per party,
# optionally a "Weighted n"/"Unweighted n" trailer row that stops the scan.

_FIELDWORK_TEXT = "10-13 February 2026"
_SAMPLE_TEXT = "n=2,015"
_SOURCE_URL = "https://example.test/tables.xlsx"


def _cover_sheet(
    fieldwork_text: str | None = _FIELDWORK_TEXT,
    sample_text: str | None = _SAMPLE_TEXT,
    population_text: str | None = None,
) -> list[list[object]]:
    """A cover sheet with "Fieldwork"/"Sample size"/population label rows."""
    rows: list[list[object]] = []
    if fieldwork_text is not None:
        rows.append(["Fieldwork", fieldwork_text])
    if sample_text is not None:
        rows.append(["Sample size", sample_text])
    if population_text is not None:
        rows.append(["Population effectively represented", population_text])
    return rows


# Region headers exactly as REGION_HEADER_TO_INTERNAL's keys, "All" first.
# "Greater London" is the spelling the real workbook uses (confirmed against
# poll 261's real source XLSX); REGION_HEADER_TO_INTERNAL also accepts the
# bare "London" form, which TestBuildImportPlanFullBuild's dedicated test
# below exercises separately, since a header-spelling mismatch here would
# otherwise silently zero that region via build_import_plan's 0.0 default
# with no test catching it.
_HEADLINE_COLUMNS: tuple[str, ...] = (
    "All",
    "East Midlands",
    "East of England",
    "Greater London",
    "North East England",
    "North West England",
    "Scotland",
    "South East England",
    "South West England",
    "Wales",
    "West Midlands",
    "Yorkshire and the Humber",
)

# Raw workbook party label -> one percentage per _HEADLINE_COLUMNS entry.
# Values are unique across the whole table ((party_index - 1) * 12 +
# column_index + 2), so a swapped row or column can't coincidentally
# reproduce another cell's expected value. They start at 2, not 1, because
# _to_percentage treats any value in [0.0, 1.0] as a fraction (multiplying by
# 100), and 1.0 sits exactly on that boundary.
_HEADLINE_VALUES: Mapping[str, tuple[float, ...]] = MappingProxyType(
    {
        "Conservative": (
            2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0,
        ),
        "Labour": (
            14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0,
        ),
        "Liberal Democrat": (
            26.0, 27.0, 28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0, 36.0, 37.0,
        ),
        "Reform UK": (
            38.0, 39.0, 40.0, 41.0, 42.0, 43.0, 44.0, 45.0, 46.0, 47.0, 48.0, 49.0,
        ),
        "Green Party": (
            50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 57.0, 58.0, 59.0, 60.0, 61.0,
        ),
        "SNP": (62.0, 63.0, 64.0, 65.0, 66.0, 67.0, 68.0, 69.0, 70.0, 71.0, 72.0, 73.0),
        "Plaid Cymru": (
            74.0, 75.0, 76.0, 77.0, 78.0, 79.0, 80.0, 81.0, 82.0, 83.0, 84.0, 85.0,
        ),
        "Other": (
            86.0, 87.0, 88.0, 89.0, 90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0,
        ),
    }
)


def _headline_sheet_rows(
    values: Mapping[str, tuple[float, ...]] = _HEADLINE_VALUES,
    *,
    omit_parties: Sequence[str] = (),
    include_blank_row: bool = False,
    include_unmapped_party: bool = False,
    sample_row: tuple[str, object] | None = None,
) -> list[list[object]]:
    """A headline VI sheet: a header row, one row per party, optional extras."""
    header: list[object] = ["Party", *_HEADLINE_COLUMNS]
    rows: list[list[object]] = [header]
    if include_blank_row:
        rows.append([None] * (len(_HEADLINE_COLUMNS) + 1))
    if include_unmapped_party:
        rows.append(["Don't Know", *([1.0] * len(_HEADLINE_COLUMNS))])
    for raw_label, figures in values.items():
        if raw_label in omit_parties:
            continue
        rows.append([raw_label, *figures])
    if sample_row is not None:
        label, value = sample_row
        rows.append([label, value])
    return rows


def _full_workbook(
    *,
    cover_sheet_name: str = "Cover page",
    cover_rows: list[list[object]] | None = None,
    headline_sheet_name: str = "VotingIntention (Headline)",
    headline_rows: list[list[object]] | None = None,
) -> Workbook:
    """A cover sheet plus a headline VI sheet."""
    sheets: dict[str, list[list[object]]] = {
        cover_sheet_name: cover_rows if cover_rows is not None else _cover_sheet(),
        headline_sheet_name: (
            headline_rows if headline_rows is not None else _headline_sheet_rows()
        ),
    }
    return build_workbook(sheets)


# ── normalize_name ───────────────────────────────────────────────────────


class TestNormalizeName:
    """Tests for normalize_name -- whitespace-collapsing lowercase normalisation."""

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert mic.normalize_name("  London  ") == "london"

    def test_collapses_internal_whitespace(self) -> None:
        assert mic.normalize_name("North   West") == "north west"

    def test_lowercases(self) -> None:
        assert mic.normalize_name("LONDON") == "london"

    def test_tabs_treated_as_whitespace(self) -> None:
        assert mic.normalize_name("North\tWest") == "north west"

    def test_already_normalised_unchanged(self) -> None:
        assert mic.normalize_name("wales") == "wales"

    def test_empty_string(self) -> None:
        assert mic.normalize_name("") == ""


# ── _month_number ─────────────────────────────────────────────────────────


class TestMonthNumber:
    """Tests for _month_number -- full names and abbreviations, case-insensitive."""

    def test_full_names(self) -> None:
        assert mic._month_number("January") == 1
        assert mic._month_number("February") == 2
        assert mic._month_number("March") == 3
        assert mic._month_number("April") == 4
        assert mic._month_number("May") == 5
        assert mic._month_number("June") == 6
        assert mic._month_number("July") == 7
        assert mic._month_number("August") == 8
        assert mic._month_number("September") == 9
        assert mic._month_number("October") == 10
        assert mic._month_number("November") == 11
        assert mic._month_number("December") == 12

    def test_abbreviations(self) -> None:
        assert mic._month_number("Jan") == 1
        assert mic._month_number("Feb") == 2
        assert mic._month_number("Mar") == 3
        assert mic._month_number("Apr") == 4
        assert mic._month_number("Jun") == 6
        assert mic._month_number("Jul") == 7
        assert mic._month_number("Aug") == 8
        assert mic._month_number("Sep") == 9
        assert mic._month_number("Sept") == 9
        assert mic._month_number("Oct") == 10
        assert mic._month_number("Nov") == 11
        assert mic._month_number("Dec") == 12

    def test_trailing_period_stripped(self) -> None:
        assert mic._month_number("Feb.") == 2
        assert mic._month_number("Sept.") == 9

    def test_case_insensitive(self) -> None:
        assert mic._month_number("JANUARY") == 1
        assert mic._month_number("january") == 1
        assert mic._month_number("jAnUaRy") == 1

    def test_unknown_returns_none(self) -> None:
        assert mic._month_number("Octember") is None
        assert mic._month_number("") is None


# ── parse_fieldwork ──────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for parse_fieldwork -- five date-shape patterns, with/without year."""

    def test_cross_month_with_year(self) -> None:
        start, end = mic.parse_fieldwork("28 Jan - 3 Feb 2025")
        assert start == date(2025, 1, 28)
        assert end == date(2025, 2, 3)

    def test_cross_month_same_month_named_twice(self) -> None:
        # Both months given, and equal -- month_start is not "> " month_end,
        # so the year does not roll back.
        start, end = mic.parse_fieldwork("3 Feb - 7 Feb 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_cross_month_year_boundary_infers_start_year_one_earlier(self) -> None:
        start, end = mic.parse_fieldwork("30 Dec - 2 Jan 2026")
        assert start == date(2025, 12, 30)
        assert end == date(2026, 1, 2)

    def test_cross_month_unknown_start_month_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("28 Blah - 3 Feb 2025")

    def test_cross_month_unknown_end_month_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("28 Jan - 3 Blah 2025")

    def test_same_month_with_year(self) -> None:
        start, end = mic.parse_fieldwork("3-7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_same_month_with_year_unknown_month_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("3-7 Blah 2025")

    def test_same_month_no_year_uses_default_year(self) -> None:
        start, end = mic.parse_fieldwork("3-7 February", default_year=2025)
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_same_month_no_year_without_default_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("3-7 February")

    def test_same_month_no_year_unknown_month_with_default_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("3-7 Blah", default_year=2025)

    def test_single_day_with_year(self) -> None:
        start, end = mic.parse_fieldwork("5 March 2025")
        assert start == date(2025, 3, 5)
        assert end == date(2025, 3, 5)

    def test_single_day_with_year_unknown_month_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("5 Blah 2025")

    def test_single_day_no_year_uses_default_year(self) -> None:
        start, end = mic.parse_fieldwork("5 March", default_year=2025)
        assert start == date(2025, 3, 5)
        assert end == date(2025, 3, 5)

    def test_single_day_no_year_without_default_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("5 March")

    def test_single_day_no_year_unknown_month_with_default_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("5 Blah", default_year=2025)

    def test_en_dash_normalised(self) -> None:
        start, end = mic.parse_fieldwork("28 Jan – 3 Feb 2025")
        assert start == date(2025, 1, 28)
        assert end == date(2025, 2, 3)

    def test_em_dash_normalised(self) -> None:
        start, end = mic.parse_fieldwork("28 Jan — 3 Feb 2025")
        assert start == date(2025, 1, 28)
        assert end == date(2025, 2, 3)

    def test_ordinal_suffixes_tolerated(self) -> None:
        start, end = mic.parse_fieldwork("3rd-7th February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_no_pattern_matches_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            mic.parse_fieldwork("not a date at all")

    def test_end_day_before_start_day_in_same_month_pins_current_behaviour(
        self,
    ) -> None:
        """Resolved: a source typo, passed through unvalidated by this function.

        ``range_same_month_pattern`` matches "<day>-<day> <Month> <year>"
        with no check that the first day is numerically before the second,
        so a string with the days the wrong way round is returned as an
        inverted ``fieldwork_start > fieldwork_end``, with no error raised
        anywhere in the function.

        This is not hypothetical: More in Common poll id 261 (pollster
        identifier "more_in_common") is stored live with
        ``fieldwork_start=2026-02-20`` and ``fieldwork_end=2026-02-13`` --
        exactly this inversion. Its real source XLSX (fetched via the
        Wayback Machine, cross-checked against the DB's stored dates, sample
        size and party shares, all of which match exactly when the real file
        is run through the real ``parse_poll``) has a "Fieldwork dates: "
        cover-page label whose value literally reads "20 - 13 February
        2026" -- a typo in More in Common's own spreadsheet (the DB's own
        context confirms it: the source URL is dated "february-25", the
        pollster's cadence is a weekly Fri-Mon window, and every other
        genuinely cross-month More in Common poll parses correctly). So the
        stored inversion traces to bad input, not a parser misread -- but
        this function still has no validation that would have caught it, and
        the same input recurs verbatim below.

        A practical consequence either way: poll 261's stored
        ``fieldwork_end`` is factually wrong (the real fieldwork ran to
        around 2026-02-23, not 2026-02-13), and re-importing after a manual
        DB correction would create a duplicate poll, since
        ``_find_existing_poll`` matches on the (currently wrong) stored
        dates. Compare ``ipsos_import._parse_fieldwork``, which pins an
        unrelated defect in the same area (a regex fallback that discards an
        omitted start month) with a similar live-DB signature but a
        different root cause.
        """
        start, end = mic.parse_fieldwork("20 - 13 February 2026")
        assert start == date(2026, 2, 20)
        assert end == date(2026, 2, 13)
        assert start > end


# ── _infer_year_from_url ──────────────────────────────────────────────────


class TestInferYearFromUrl:
    """Tests for _infer_year_from_url."""

    def test_year_found_in_url(self) -> None:
        url = "https://www.moreincommon.org.uk/media/x/voting-intention-2026.xlsx"
        assert mic._infer_year_from_url(url) == 2026

    def test_no_year_in_url_returns_none(self) -> None:
        assert mic._infer_year_from_url("https://example.test/tables.xlsx") is None

    def test_first_occurrence_wins(self) -> None:
        url = "https://example.test/2020/archive/2026/file.xlsx"
        assert mic._infer_year_from_url(url) == 2020


# ── _infer_fieldwork_from_source_url ──────────────────────────────────────


class TestInferFieldworkFromSourceUrl:
    """Tests for _infer_fieldwork_from_source_url."""

    def test_default_year_none_returns_none_immediately(self) -> None:
        url = "https://example.test/voting-intention-february-10.xlsx"
        assert mic._infer_fieldwork_from_source_url(url, default_year=None) is None

    def test_separator_pattern_matches(self) -> None:
        url = "https://example.test/voting-intention-february-10.xlsx"
        result = mic._infer_fieldwork_from_source_url(url, default_year=2026)
        assert result == (date(2026, 2, 10), date(2026, 2, 10))

    def test_no_separator_pattern_matches(self) -> None:
        # The first pattern (which requires a separator between month and
        # day) finds no match at all for this URL -- its "if not match:
        # continue" fires before it ever reaches its own month/day guard --
        # so it's the second pattern, not a fallback from a failed first
        # match, that resolves this case.
        url = "https://example.test/voting-intention-february10.xlsx"
        result = mic._infer_fieldwork_from_source_url(url, default_year=2026)
        assert result == (date(2026, 2, 10), date(2026, 2, 10))

    def test_case_insensitive(self) -> None:
        url = "https://example.test/Voting-Intention-February-10.xlsx"
        result = mic._infer_fieldwork_from_source_url(url, default_year=2026)
        assert result == (date(2026, 2, 10), date(2026, 2, 10))

    def test_no_match_returns_none(self) -> None:
        url = "https://example.test/tables.xlsx"
        assert mic._infer_fieldwork_from_source_url(url, default_year=2026) is None

    def test_unresolvable_month_name_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The month group is built only from names _month_number already
        # recognises, so this branch is otherwise unreachable through normal
        # use; forcing _month_number to fail here proves the "continue"
        # doesn't crash and the function still returns None once both
        # patterns have been tried without a usable month.
        monkeypatch.setattr(mic, "_month_number", lambda _text: None)
        url = "https://example.test/voting-intention-february-10.xlsx"
        assert mic._infer_fieldwork_from_source_url(url, default_year=2026) is None


# ── extract_workbook ────────────────────────────────────────────────────


class TestExtractWorkbook:
    """Tests for extract_workbook -- fetch and load an XLSX from a URL."""

    def test_downloads_and_loads_the_workbook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = workbook_bytes(build_workbook({"Sheet1": [["ok"]]}))
        calls: list[tuple[str, float | None]] = []

        def fake_urlopen(url: str, timeout: float | None = None) -> FakeUrlResponse:
            calls.append((url, timeout))
            return FakeUrlResponse(payload)

        monkeypatch.setattr(mic, "urlopen", fake_urlopen)

        workbook = mic.extract_workbook("https://example.test/tables.xlsx")

        assert workbook.sheetnames == ["Sheet1"]
        assert calls == [("https://example.test/tables.xlsx", 60)]


# ── _cell_text ─────────────────────────────────────────────────────────


class TestCellText:
    """Tests for _cell_text -- openpyxl cell value -> stripped string."""

    def test_none_returns_empty_string(self) -> None:
        assert mic._cell_text(None) == ""

    def test_string_stripped(self) -> None:
        assert mic._cell_text("  hello  ") == "hello"

    def test_integer_converted(self) -> None:
        assert mic._cell_text(42) == "42"

    def test_float_converted(self) -> None:
        assert mic._cell_text(3.5) == "3.5"

    def test_empty_string(self) -> None:
        assert mic._cell_text("") == ""


# ── _find_label_value ─────────────────────────────────────────────────


class TestFindLabelValue:
    """Tests for _find_label_value -- label search plus a 3-cell lookahead."""

    def test_value_immediately_to_the_right(self) -> None:
        workbook = build_workbook({"Cover": [["Fieldwork", "10-13 Feb 2026"]]})
        assert mic._find_label_value(workbook["Cover"], "Fieldwork") == (
            "10-13 Feb 2026"
        )

    def test_value_two_cells_to_the_right_when_the_adjacent_cell_is_empty(
        self,
    ) -> None:
        workbook = build_workbook({"Cover": [["Fieldwork", None, "10-13 Feb 2026"]]})
        assert mic._find_label_value(workbook["Cover"], "Fieldwork") == (
            "10-13 Feb 2026"
        )

    def test_value_four_cells_to_the_right_is_out_of_range(self) -> None:
        # Only offsets 1-3 are checked; a value one cell further out must
        # not be found, even though it's the only non-empty cell in the row.
        workbook = build_workbook(
            {"Cover": [["Fieldwork", None, None, None, "10-13 Feb 2026"]]}
        )
        assert mic._find_label_value(workbook["Cover"], "Fieldwork") is None

    def test_case_insensitive_substring_match(self) -> None:
        workbook = build_workbook(
            {"Cover": [["FIELDWORK DATES (UK adults)", "10-13 Feb 2026"]]}
        )
        assert mic._find_label_value(workbook["Cover"], "fieldwork") == (
            "10-13 Feb 2026"
        )

    def test_label_found_but_no_value_returns_none(self) -> None:
        workbook = build_workbook({"Cover": [["Fieldwork", None, None, None]]})
        assert mic._find_label_value(workbook["Cover"], "Fieldwork") is None

    def test_label_not_present_returns_none(self) -> None:
        workbook = build_workbook({"Cover": [["Nothing", "here"]]})
        assert mic._find_label_value(workbook["Cover"], "Fieldwork") is None


# ── _as_int ────────────────────────────────────────────────────────────


class TestAsInt:
    """Tests for _as_int -- digit extraction from a formatted string."""

    def test_plain_digits(self) -> None:
        assert mic._as_int("2015") == 2015

    def test_comma_formatted(self) -> None:
        assert mic._as_int("2,015") == 2015

    def test_prefixed_text(self) -> None:
        assert mic._as_int("n=2015") == 2015

    def test_no_digits_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse sample size"):
            mic._as_int("undisclosed")


# ── _to_percentage ─────────────────────────────────────────────────────


class TestToPercentage:
    """Tests for _to_percentage -- raw cell value -> rounded percentage float."""

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="Encountered empty percentage cell"):
            mic._to_percentage(None)

    def test_decimal_fraction_multiplied(self) -> None:
        assert mic._to_percentage(0.42) == pytest.approx(42.0)

    def test_one_boundary_treated_as_100_percent(self) -> None:
        assert mic._to_percentage(1.0) == pytest.approx(100.0)

    def test_zero_boundary(self) -> None:
        assert mic._to_percentage(0.0) == pytest.approx(0.0)

    def test_value_above_one_left_unchanged(self) -> None:
        assert mic._to_percentage(42) == pytest.approx(42.0)

    def test_rounds_to_nearest_integer(self) -> None:
        assert mic._to_percentage(34.6) == pytest.approx(35.0)
        assert mic._to_percentage(34.4) == pytest.approx(34.0)

    def test_returns_a_float(self) -> None:
        assert type(mic._to_percentage(42)) is float


# ── _find_headline_sheet ───────────────────────────────────────────────


class TestFindHeadlineSheet:
    """Tests for _find_headline_sheet -- sheet/header-row search and priority."""

    def test_first_pass_matches_all_and_east_midlands(self) -> None:
        workbook = build_workbook(
            {"Data": [["Party", "All", "East Midlands", "Scotland"]]}
        )
        ws, row = mic._find_headline_sheet(workbook)
        assert ws.title == "Data"
        assert row == 1

    def test_sheet_name_priority_prefers_votingintention_headline(self) -> None:
        # Both sheets have valid headers; only the sheet name decides.
        workbook = build_workbook(
            {
                "Random Tab": [["Party", "All", "East Midlands"]],
                "VotingIntention (Headline)": [["Party", "All", "East Midlands"]],
            }
        )
        ws, _row = mic._find_headline_sheet(workbook)
        assert ws.title == "VotingIntention (Headline)"

    def test_sheet_name_priority_exact_phrase_beats_the_looser_pair_match(self) -> None:
        # Both sheet names satisfy the (looser) "headline" + "votingintention"
        # rule, but only one matches the literal "votingintention (headline)"
        # phrase, which must win. Distinguishes the two highest-priority
        # buckets, which a name containing both substrings together (as in
        # the test above) cannot: "votingintention (headline)" also always
        # contains "headline" and "votingintention" separately.
        workbook = build_workbook(
            {
                "Headline VotingIntention Info": [["Party", "All", "East Midlands"]],
                "VotingIntention (Headline)": [["Party", "All", "East Midlands"]],
            }
        )
        ws, _row = mic._find_headline_sheet(workbook)
        assert ws.title == "VotingIntention (Headline)"

    def test_sheet_name_priority_headline_and_votingintention_pair_beats_solo_match(
        self,
    ) -> None:
        # "Alt VotingIntention" matches only the looser "votingintention"
        # rule (rank 2) and sorts alphabetically *before* "Headline
        # VotingIntention Table" (rank 1, matching "headline" and
        # "votingintention" both) -- so this isolates the rank-1 rule
        # actually mattering, rather than the two candidates merely tying
        # and falling back to alphabetical order.
        workbook = build_workbook(
            {
                "Alt VotingIntention": [["Party", "All", "East Midlands"]],
                "Headline VotingIntention Table": [["Party", "All", "East Midlands"]],
            }
        )
        ws, _row = mic._find_headline_sheet(workbook)
        assert ws.title == "Headline VotingIntention Table"

    def test_sheet_name_priority_corbyn_beats_no_match(self) -> None:
        # "A Random Tab" matches no naming rule (rank 9, the default) and
        # sorts alphabetically *before* "Corbyn Tracker" (rank 3) -- so this
        # isolates the corbyn rule actually mattering, rather than both
        # candidates tying at rank 9 and falling back to alphabetical order.
        workbook = build_workbook(
            {
                "A Random Tab": [["Party", "All", "East Midlands"]],
                "Corbyn Tracker": [["Party", "All", "East Midlands"]],
            }
        )
        ws, _row = mic._find_headline_sheet(workbook)
        assert ws.title == "Corbyn Tracker"

    def test_second_pass_matches_all_plus_a_known_party_label(self) -> None:
        # No "East Midlands" anywhere, so the first pass fails everywhere;
        # the second pass accepts "All" plus a recognised party name.
        workbook = build_workbook({"Data": [["Party", "All", "Conservative"]]})
        ws, row = mic._find_headline_sheet(workbook)
        assert ws.title == "Data"
        assert row == 1

    def test_no_all_column_anywhere_raises(self) -> None:
        workbook = build_workbook({"Data": [["Party", "East Midlands", "Scotland"]]})
        with pytest.raises(
            ValueError, match="Could not locate headline voting intention table"
        ):
            mic._find_headline_sheet(workbook)

    def test_all_present_but_no_recognised_party_or_region_raises(self) -> None:
        workbook = build_workbook({"Data": [["Party", "All", "Something Else"]]})
        with pytest.raises(
            ValueError, match="Could not locate headline voting intention table"
        ):
            mic._find_headline_sheet(workbook)


# ── parse_poll: fieldwork and sample size ─────────────────────────────


class TestParsePollFieldworkAndSample:
    """parse_poll's fieldwork/sample resolution: labels, URL fallback, raises."""

    def test_labelled_fieldwork_and_sample(self) -> None:
        workbook = _full_workbook()
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.fieldwork_start == date(2026, 2, 10)
        assert parsed.fieldwork_end == date(2026, 2, 13)
        assert parsed.sample_size == 2015

    def test_labels_found_on_a_non_cover_page_sheet(self) -> None:
        # No sheet is named "Cover page" here; the labels still get found
        # because parse_poll's scan loop falls through to every sheet in
        # workbook.sheetnames, not because of any special first-sheet
        # preference (see test_cover_page_used_even_when_not_the_first_sheet
        # below for a test that actually isolates the preference order).
        workbook = build_workbook(
            {"Data": _cover_sheet(), "VI Headline": _headline_sheet_rows()}
        )
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.sample_size == 2015

    def test_cover_page_used_even_when_not_the_first_sheet(self) -> None:
        # "Cover page" is second here, and the first sheet ("VI Headline")
        # carries its own, *wrong* Fieldwork/Sample size labels -- so a
        # mutant that checked workbook.sheetnames[0] before (or instead of)
        # "Cover page" would pick up those wrong values, not merely fail to
        # find any. Only genuinely preferring "Cover page" by name gets this
        # right (a decoy sheet with no labels at all, as in the test above,
        # can't tell the two apart).
        fieldwork_row: list[object] = ["Fieldwork", "1-2 January 2020"]
        sample_row: list[object] = ["Sample size", "n=1"]
        first_sheet_rows: list[list[object]] = [
            *_headline_sheet_rows(),
            fieldwork_row,
            sample_row,
        ]
        workbook = build_workbook(
            {"VI Headline": first_sheet_rows, "Cover page": _cover_sheet()}
        )
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.fieldwork_start == date(2026, 2, 10)
        assert parsed.fieldwork_end == date(2026, 2, 13)
        assert parsed.sample_size == 2015

    def test_missing_fieldwork_label_falls_back_to_url_pattern(self) -> None:
        cover = _cover_sheet(fieldwork_text=None)
        workbook = _full_workbook(cover_rows=cover)
        url = "https://example.test/2026/voting-intention-february-11.xlsx"
        parsed = mic.parse_poll(workbook, source_url=url)
        assert parsed.fieldwork_start == date(2026, 2, 11)
        assert parsed.fieldwork_end == date(2026, 2, 11)

    def test_missing_fieldwork_label_and_no_url_pattern_raises(self) -> None:
        cover = _cover_sheet(fieldwork_text=None)
        workbook = _full_workbook(cover_rows=cover)
        with pytest.raises(ValueError, match="Fieldwork date not found in workbook"):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_missing_sample_size_label_falls_back_to_weighted_n_row(self) -> None:
        cover = _cover_sheet(sample_text=None)
        headline = _headline_sheet_rows(sample_row=("Weighted n", "n=1,801"))
        workbook = _full_workbook(cover_rows=cover, headline_rows=headline)
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.sample_size == 1801

    def test_missing_sample_size_label_falls_back_to_unweighted_n_row(self) -> None:
        cover = _cover_sheet(sample_text=None)
        headline = _headline_sheet_rows(sample_row=("Unweighted n", "n=1,750"))
        workbook = _full_workbook(cover_rows=cover, headline_rows=headline)
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.sample_size == 1750

    def test_missing_sample_size_label_and_no_headline_row_raises(self) -> None:
        cover = _cover_sheet(sample_text=None)
        workbook = _full_workbook(cover_rows=cover)
        with pytest.raises(ValueError, match="Sample size not found in workbook"):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_blank_weighted_n_value_is_skipped_in_favour_of_a_later_row(self) -> None:
        # A "Weighted n" row whose value cell is empty must not stop the
        # scan -- it should carry on and pick up a later matching row.
        cover = _cover_sheet(sample_text=None)
        rows = _headline_sheet_rows()
        header, *party_rows = rows
        empty_row: list[object] = ["Weighted n", None]
        real_row: list[object] = ["Unweighted n", "n=1,900"]
        headline = [header, *party_rows, empty_row, real_row]
        workbook = _full_workbook(cover_rows=cover, headline_rows=headline)
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.sample_size == 1900


# ── parse_poll: national/region column resolution ─────────────────────


class TestParsePollRegionColumns:
    """parse_poll's national/region column resolution and its raises."""

    def test_missing_all_column_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # _find_headline_sheet can never itself return a header row lacking
        # "All" (both its passes require it), so this defensive check in
        # parse_poll is reached only by forcing the header-row lookup.
        workbook = _full_workbook(
            headline_rows=[["Party", "East Midlands", "Scotland"]]
        )
        headline_ws = workbook["VotingIntention (Headline)"]
        monkeypatch.setattr(mic, "_find_headline_sheet", lambda _wb: (headline_ws, 1))
        expected = "Could not locate 'All' (national) column in headline table"
        with pytest.raises(ValueError, match=re.escape(expected)):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_fewer_than_six_region_columns_raises(self) -> None:
        header: list[object] = ["Party", "All", "East Midlands", "Scotland"]
        workbook = _full_workbook(headline_rows=[header])
        with pytest.raises(
            ValueError, match="Could not resolve sufficient region columns"
        ):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_exactly_five_region_columns_raises(self) -> None:
        # Pins the "< 6" boundary precisely (a mutant comparing "< 4" would
        # let this workbook through).
        header: list[object] = [
            "Party", "All", "East Midlands", "East of England", "London",
            "Scotland", "Wales",
        ]
        workbook = _full_workbook(headline_rows=[header])
        with pytest.raises(
            ValueError, match="Could not resolve sufficient region columns"
        ):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_exactly_six_region_columns_does_not_raise(self) -> None:
        header: list[object] = [
            "Party", "All", "East Midlands", "East of England", "London",
            "North East England", "Scotland", "Wales",
        ]
        rows: list[list[object]] = [header]
        for raw_label in (
            "Conservative", "Labour", "Liberal Democrat", "Reform UK",
            "Green Party", "SNP", "Plaid Cymru", "Other",
        ):
            rows.append([raw_label, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0])
        workbook = _full_workbook(headline_rows=rows)
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert len(parsed.party_region_percentages) == 8
        assert parsed.party_region_percentages["Conservative"]["Scotland"] == 15.0
        # The bare "London" header (as opposed to the real workbook's
        # "Greater London", used by _HEADLINE_COLUMNS above) is also a
        # REGION_HEADER_TO_INTERNAL key and must resolve, not silently drop
        # out and leave that region defaulted to 0.0 downstream.
        assert parsed.party_region_percentages["Conservative"]["London"] == 13.0

    def test_special_population_relaxes_the_minimum_region_count(self) -> None:
        cover = _cover_sheet(population_text="16-17 year olds")
        header: list[object] = ["Party", "All", "East Midlands", "Scotland"]
        rows: list[list[object]] = [
            header,
            ["Conservative", 20.0, 21.0, 22.0],
            ["Labour", 30.0, 31.0, 32.0],
            ["Liberal Democrat", 8.0, 9.0, 10.0],
            ["Reform UK", 15.0, 16.0, 17.0],
            ["Green Party", 5.0, 6.0, 7.0],
            ["SNP", 4.0, 5.0, 6.0],
            ["Plaid Cymru", 3.0, 4.0, 5.0],
            ["Other", 2.0, 3.0, 4.0],
        ]
        workbook = _full_workbook(cover_rows=cover, headline_rows=rows)
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert len(parsed.party_region_percentages) == 8
        assert parsed.party_region_percentages["Conservative"] == {
            mic.NATIONAL_KEY: 20.0,
            "East Midlands": 21.0,
            "Scotland": 22.0,
        }


# ── parse_poll: party rows ─────────────────────────────────────────────


class TestParsePollPartyRows:
    """parse_poll's party-row scan: extraction, skips, the stop label, raises."""

    def test_full_extraction_maps_every_header_to_the_internal_region_name(
        self,
    ) -> None:
        workbook = _full_workbook()
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)

        assert len(parsed.party_region_percentages) == 8
        assert parsed.party_region_percentages["Conservative"] == {
            mic.NATIONAL_KEY: 2.0,
            "East Midlands": 3.0,
            "East of England": 4.0,
            "London": 5.0,
            "North East England": 6.0,
            "North West England": 7.0,
            "Scotland": 8.0,
            "South East England": 9.0,
            "South West England": 10.0,
            "Wales": 11.0,
            "West Midlands": 12.0,
            "Yorkshire and The Humber": 13.0,
        }
        assert (
            parsed.party_region_percentages["Liberal Democrats"][mic.NATIONAL_KEY]
            == 26.0
        )
        assert (
            parsed.party_region_percentages["Scottish National Party"][
                mic.NATIONAL_KEY
            ]
            == 62.0
        )
        assert parsed.party_region_percentages["Other"][mic.NATIONAL_KEY] == 86.0

    def test_blank_row_is_harmless(self) -> None:
        rows = _headline_sheet_rows(include_blank_row=True)
        workbook = _full_workbook(headline_rows=rows)
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert len(parsed.party_region_percentages) == 8

    def test_unmapped_party_row_is_skipped(self) -> None:
        rows = _headline_sheet_rows(include_unmapped_party=True)
        workbook = _full_workbook(headline_rows=rows)
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert len(parsed.party_region_percentages) == 8
        assert "Don't Know" not in parsed.party_region_percentages

    def test_stop_label_breaks_the_scan_before_later_parties(self) -> None:
        # "Weighted n" sits after Conservative/Labour but before the rest;
        # the loop must break there instead of skipping over it, so the
        # later parties are never read.
        rows = _headline_sheet_rows()
        header, *party_rows = rows
        stop_row: list[object] = ["Weighted n", 2000]
        rows = [header, party_rows[0], party_rows[1], stop_row, *party_rows[2:]]
        workbook = _full_workbook(headline_rows=rows)
        expected = (
            "Missing expected party rows in workbook: "
            "['Green', 'Liberal Democrats', 'Other', 'Plaid Cymru', "
            "'Reform UK', 'Scottish National Party']"
        )
        with pytest.raises(ValueError, match=re.escape(expected)):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_missing_required_party_raises(self) -> None:
        rows = _headline_sheet_rows(omit_parties=("Green Party",))
        workbook = _full_workbook(headline_rows=rows)
        expected = "Missing expected party rows in workbook: ['Green']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)


# ── build_import_plan ─────────────────────────────────────────────────


_EXPECTED_REGION_ORDER: tuple[str, ...] = (
    "East Midlands",
    "East of England",
    "London",
    "North East England",
    "North West England",
    "Northern Ireland",
    "Scotland",
    "South East England",
    "South West England",
    "Wales",
    "West Midlands",
    "Yorkshire and The Humber",
)


def _expected_regions_mapping(region_ids: Mapping[str, int]) -> str:
    """The alphabetically-sorted "name:id" mapping every DB region produces."""
    return "\n".join(f"{name}:{region_ids[name]}" for name in _EXPECTED_REGION_ORDER)


class TestBuildImportPlanRaises:
    """build_import_plan's raise for a missing map and for missing parties."""

    def test_missing_map_raises_before_fetching(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_extract_workbook(*_a: object, **_k: object) -> Workbook:
            raise AssertionError("must not fetch when the map lookup already failed")

        monkeypatch.setattr(mic, "extract_workbook", fail_extract_workbook)

        with pytest.raises(ValueError, match=re.escape("Map not found: 'Nope'")):
            mic.build_import_plan(db, map_name="Nope")

    def test_missing_parties_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        poll_map = db.add_map("Empty Parties Map", parliament="westminster")

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(mic, "extract_workbook", fake_extract_workbook)

        expected = (
            "Missing parties in database (run party importer first): "
            "['Conservative', 'Green', 'Labour', 'Liberal Democrats', 'Other', "
            "'Plaid Cymru', 'Reform UK', 'Scottish National Party']"
        )
        with pytest.raises(ValueError, match=re.escape(expected)):
            mic.build_import_plan(db, map_name=poll_map.name)


class TestBuildImportPlanFullBuild:
    """build_import_plan over the seeded Westminster world: mapping and rows."""

    def test_regions_mapping_and_row_count(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(mic, "extract_workbook", fake_extract_workbook)

        plan = mic.build_import_plan(
            db, xlsx_url=mic.DEFAULT_XLSX_URL, map_name=world.map_name
        )

        assert plan.map_id == world.map_id
        assert plan.map_name == world.map_name
        assert plan.regions_mapping == _expected_regions_mapping(world.region_ids)
        assert plan.pollster_identifier == mic.DEFAULT_POLLSTER_IDENTIFIER
        assert plan.pollster_exists is False
        assert plan.pollster_name == "More in Common"
        assert plan.pollster_id is None
        assert plan.poll_exists is False
        assert plan.poll_id is None
        assert plan.source_url == mic.DEFAULT_XLSX_URL

        # 8 required parties x (1 national + 12 DB regions).
        assert len(plan.rows) == 8 * 13
        national_rows = [row for row in plan.rows if row.region_id is None]
        assert len(national_rows) == 8

        conservative_national = next(
            row
            for row in plan.rows
            if row.party_name == "Conservative" and row.region_id is None
        )
        assert conservative_national.percentage == 2.0
        assert conservative_national.region_name == "National"
        assert conservative_national.party_id == world.party_ids["Conservative"]

        # Northern Ireland has no header in the workbook, so it defaults to
        # 0.0 for every party -- a second, distinct party proves the default
        # isn't accidentally Conservative's own figure.
        labour_ni = next(
            row
            for row in plan.rows
            if row.party_name == "Labour"
            and row.region_id == world.region_ids["Northern Ireland"]
        )
        assert labour_ni.percentage == 0.0
        assert labour_ni.party_id == world.party_ids["Labour"]

        conservative_london = next(
            row
            for row in plan.rows
            if row.party_name == "Conservative"
            and row.region_id == world.region_ids["London"]
        )
        assert conservative_london.percentage == 5.0

    def test_pollster_and_poll_existing_are_detected(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        pollster = db.add_pollster("More in Common Ltd", "more_in_common", weight=1.0)
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            date(2026, 2, 10),
            date(2026, 2, 13),
            sample_size=2015,
        )

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(mic, "extract_workbook", fake_extract_workbook)

        plan = mic.build_import_plan(db, map_name=world.map_name)

        assert plan.pollster_exists is True
        assert plan.pollster_id == pollster.id
        assert plan.pollster_name == "More in Common Ltd"
        assert plan.poll_exists is True
        assert plan.poll_id == poll.id

    def test_pollster_existing_without_a_matching_poll_leaves_poll_absent(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        db.add_pollster("More in Common Ltd", "more_in_common", weight=1.0)

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(mic, "extract_workbook", fake_extract_workbook)

        plan = mic.build_import_plan(db, map_name=world.map_name)

        assert plan.pollster_exists is True
        assert plan.poll_exists is False
        assert plan.poll_id is None


class TestBuildImportPlanNationalNoneHandling:
    """A party whose parsed data has no NATIONAL_KEY skips the National row.

    parse_poll always includes NATIONAL_KEY in real workbooks (the national
    column is mandatory and raises otherwise), so this monkeypatches
    parse_poll directly to exercise build_import_plan's own defensive check.
    """

    def test_party_without_a_national_figure_gets_no_national_row(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world

        def fake_extract_workbook(*_a: object, **_k: object) -> object:
            return object()

        def fake_parse_poll(
            _workbook: object,
            *,
            source_url: str,
            fieldwork_year_hint: int | None = None,
        ) -> mic.ParsedPoll:
            return mic.ParsedPoll(
                sample_size=1000,
                fieldwork_start=date(2026, 1, 1),
                fieldwork_end=date(2026, 1, 2),
                party_region_percentages={"Labour": {"London": 55.0}},
            )

        monkeypatch.setattr(mic, "extract_workbook", fake_extract_workbook)
        monkeypatch.setattr(mic, "parse_poll", fake_parse_poll)

        plan = mic.build_import_plan(db, map_name=world.map_name)

        # No National row for Labour, but every DB region row is still built.
        assert not any(
            row.party_name == "Labour" and row.region_id is None for row in plan.rows
        )
        labour_rows = [row for row in plan.rows if row.party_name == "Labour"]
        assert len(labour_rows) == len(world.region_ids)
        labour_london = next(
            row for row in labour_rows if row.region_id == world.region_ids["London"]
        )
        assert labour_london.percentage == 55.0


class TestBuildImportPlanFieldworkYearHint:
    """build_import_plan forwards fieldwork_year_hint through to parse_poll."""

    def test_year_hint_used_when_fieldwork_text_has_no_year(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        cover = _cover_sheet(fieldwork_text="10-13 February")
        workbook = _full_workbook(cover_rows=cover)

        def fake_extract_workbook(*_a: object, **_k: object) -> object:
            return workbook

        monkeypatch.setattr(mic, "extract_workbook", fake_extract_workbook)

        # The URL carries no year of its own, so a dropped hint would make
        # default_year None and raise, rather than silently falling back to
        # a URL-inferred year that happens to match.
        plan = mic.build_import_plan(
            db,
            xlsx_url="https://example.test/tables.xlsx",
            map_name=world.map_name,
            fieldwork_year_hint=2027,
        )

        assert plan.parsed.fieldwork_start == date(2027, 2, 10)
        assert plan.parsed.fieldwork_end == date(2027, 2, 13)


# ── _cli_preview ──────────────────────────────────────────────────────


def _preview_plan(**overrides: object) -> mic.ImportPlan:
    """Build an ImportPlan for _cli_preview tests: one Labour/Wales row."""
    parsed = mic.ParsedPoll(
        sample_size=2015,
        fieldwork_start=date(2026, 2, 10),
        fieldwork_end=date(2026, 2, 13),
        party_region_percentages={"Labour": {mic.NATIONAL_KEY: 32.0, "Wales": 22.0}},
    )
    row = mic.PlannedPollRow(
        party_id=2,
        party_name="Labour",
        region_id=10,
        region_name="Wales",
        percentage=22.0,
    )
    defaults: dict[str, object] = {
        "pollster_identifier": "more_in_common",
        "pollster_name": "More in Common",
        "pollster_id": None,
        "pollster_exists": False,
        "regions_mapping": "Wales:10",
        "map_id": 1,
        "map_name": "UK Constituencies post 2022",
        "source_url": mic.DEFAULT_XLSX_URL,
        "parsed": parsed,
        "poll_id": None,
        "poll_exists": False,
        "rows": [row],
    }
    defaults.update(overrides)
    return mic.ImportPlan.model_validate(defaults)


class TestCliPreview:
    """_cli_preview's dry-run summary, all four pollster/poll existence combos."""

    def test_new_pollster_and_new_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mic._cli_preview(_preview_plan())

        lines = capsys.readouterr().out.splitlines()
        assert "Parsed poll: fieldwork=2026-02-10 to 2026-02-13, sample=2015" in lines
        assert "[dry-run] would create pollster: more_in_common" in lines
        assert "[dry-run] would create poll" in lines
        assert (
            "[dry-run] would insert row: party=Labour, region=Wales, "
            "region_id=10, pct=22.00"
        ) in lines

    def test_existing_pollster_and_existing_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mic._cli_preview(
            _preview_plan(pollster_exists=True, poll_exists=True, poll_id=7)
        )

        lines = capsys.readouterr().out.splitlines()
        assert "pollster exists: more_in_common" in lines
        assert "poll exists: 7" in lines
        assert "[dry-run] would create pollster: more_in_common" not in lines
        assert "[dry-run] would create poll" not in lines

    def test_poll_exists_true_with_no_id_still_previews_creation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mic._cli_preview(_preview_plan(poll_exists=True, poll_id=None))

        lines = capsys.readouterr().out.splitlines()
        assert "poll exists: None" not in lines
        assert "[dry-run] would create poll" in lines

    def test_poll_id_set_but_poll_exists_false_still_previews_creation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mic._cli_preview(_preview_plan(poll_exists=False, poll_id=42))

        lines = capsys.readouterr().out.splitlines()
        assert "poll exists: 42" not in lines
        assert "[dry-run] would create poll" in lines

    def test_no_rows_prints_no_row_lines(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mic._cli_preview(_preview_plan(rows=[]))

        out = capsys.readouterr().out
        assert "[dry-run] would insert row" not in out


# ── main ──────────────────────────────────────────────────────────────


class TestMain:
    """main's dry-run/commit branches, sys.argv and Database patched to db."""

    def _run(
        self,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        workbook: Workbook,
        *argv: str,
        fetched_urls: list[str] | None = None,
    ) -> None:
        """Run main() with Database and extract_workbook faked, via sys.argv.

        If ``fetched_urls`` is given, every URL ``extract_workbook`` is called
        with is recorded, so a test can prove ``--xlsx-url`` actually reaches
        the fetch rather than the CLI default silently being used.
        """

        def fake_database(*_a: object, **_k: object) -> Database:
            return db

        def fake_extract_workbook(xlsx_url: str) -> Workbook:
            if fetched_urls is not None:
                fetched_urls.append(xlsx_url)
            return workbook

        monkeypatch.setattr(mic, "Database", fake_database)
        monkeypatch.setattr(mic, "extract_workbook", fake_extract_workbook)
        monkeypatch.setattr(sys, "argv", ["more_in_common_import.py", *argv])
        mic.main()

    def test_cli_defaults_pin(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        assert mic.DEFAULT_MAP_NAME == world.map_name
        assert mic.DEFAULT_POLLSTER_IDENTIFIER == "more_in_common"

        self._run(db, monkeypatch, _full_workbook(), "--dry-run")

        out = capsys.readouterr().out
        assert f"Fetching XLSX: {mic.DEFAULT_XLSX_URL}" in out
        assert "[dry-run] would create pollster: more_in_common" in out.splitlines()

    def test_dry_run_previews_without_writing(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        self._run(
            db, monkeypatch, _full_workbook(), "--map-name", world.map_name, "--dry-run"
        )

        out = capsys.readouterr().out
        assert "[dry-run] would create pollster: more_in_common" in out.splitlines()
        assert len(db.get_all_pollsters()) == 0

    def test_commit_with_non_default_arguments_forwards_them(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--xlsx-url, --map-name and --pollster-identifier are all forwarded.

        A second, non-default map proves ``--map-name`` was read rather than
        falling back to ``DEFAULT_MAP_NAME`` (which equals the seeded world's
        map name, so the other tests can't tell forwarding from a default).
        """
        second_map = db.add_map("Second More in Common Map", parliament="westminster")
        # A literal tuple, not mic.REGION_HEADER_TO_INTERNAL.values() (which
        # duplicates "London" via its "Greater London" alias, with nothing
        # to prove the DB's own region set is right if that dict were wrong).
        second_map_regions = (
            "East Midlands",
            "East of England",
            "London",
            "North East England",
            "North West England",
            "Scotland",
            "South East England",
            "South West England",
            "Wales",
            "West Midlands",
            "Yorkshire and The Humber",
        )
        for name in second_map_regions:
            db.add_region(second_map.id, name)
        custom_url = "https://example.test/custom-more-in-common.xlsx"
        fetched_urls: list[str] = []

        self._run(
            db,
            monkeypatch,
            _full_workbook(),
            "--map-name",
            second_map.name,
            "--xlsx-url",
            custom_url,
            "--pollster-identifier",
            "more_in_common_custom",
            fetched_urls=fetched_urls,
        )

        assert fetched_urls == [custom_url]
        assert db.get_pollster_by_identifier(mic.DEFAULT_POLLSTER_IDENTIFIER) is None
        pollster = db.get_pollster_by_identifier("more_in_common_custom")
        assert pollster is not None
        polls = db.get_polls_by_pollster(pollster.id)
        assert len(polls) == 1
        assert polls[0].map_id == second_map.id
        assert polls[0].source_url == custom_url

    def test_fieldwork_year_hint_forwarded(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        cover = _cover_sheet(fieldwork_text="10-13 February")
        workbook = _full_workbook(cover_rows=cover)

        self._run(
            db,
            monkeypatch,
            workbook,
            "--map-name",
            world.map_name,
            "--fieldwork-year-hint",
            "2027",
            "--dry-run",
        )

        out = capsys.readouterr().out
        assert "Parsed poll: fieldwork=2027-02-10 to 2027-02-13" in out

    def test_commit_creates_pollster_poll_and_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        self._run(db, monkeypatch, _full_workbook(), "--map-name", world.map_name)

        lines = capsys.readouterr().out.splitlines()
        assert "created pollster: more_in_common" in lines
        assert "inserted poll rows: 104" in lines
        assert "deleted existing rows" not in "\n".join(lines)
        pollster = db.get_pollster_by_identifier("more_in_common")
        assert pollster is not None
        assert pollster.weight == 1.0
        polls = db.get_polls_by_pollster(pollster.id)
        assert len(polls) == 1
        assert len(db.get_rows_for_poll(polls[0].id)) == 104

    def test_rerun_skips_then_replace_rows_overwrites(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        argv = ("--map-name", world.map_name)

        self._run(db, monkeypatch, _full_workbook(), *argv)
        capsys.readouterr()  # drain, so the next read can't see this run's output
        pollster = db.get_pollster_by_identifier("more_in_common")
        assert pollster is not None
        poll_id = db.get_polls_by_pollster(pollster.id)[0].id
        assert len(db.get_rows_for_poll(poll_id)) == 104

        self._run(db, monkeypatch, _full_workbook(), *argv)
        lines = capsys.readouterr().out.splitlines()
        assert f"poll exists: {poll_id}" in lines
        assert (
            f"poll {poll_id} already has rows; use --replace-rows to overwrite"
        ) in lines
        assert len(db.get_rows_for_poll(poll_id)) == 104

        self._run(db, monkeypatch, _full_workbook(), *argv, "--replace-rows")
        lines = capsys.readouterr().out.splitlines()
        assert "deleted existing rows: 104" in lines
        assert "inserted poll rows: 104" in lines
        assert len(db.get_rows_for_poll(poll_id)) == 104


_SPLIT_HEADER_FIXTURE = (
    Path(__file__).parent / "fixtures/more_in_common/headline-20260921.json"
)


def _split_header_workbook() -> Workbook:
    fixture = json.loads(_SPLIT_HEADER_FIXTURE.read_text())
    return build_workbook(fixture["sheets"])


class TestSplitRegionHeaders:
    """Percentage and count subcolumns under the September region headings."""

    def test_source_metadata_and_all_mapped_shares(self) -> None:
        workbook = _split_header_workbook()
        sheet = workbook["votingintention (headline)"]
        for col in range(22, 43, 2):
            sheet.cell(17, col, 0.99)
        sheet.cell(17, 1, "Conservative")
        sheet.cell(17, 2, 0.99)
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        expected = json.loads(_SPLIT_HEADER_FIXTURE.read_text())["expected"]
        assert parsed.model_dump(mode="json") == expected
        assert "Restore Britain" not in parsed.party_region_percentages

    def test_headline_preferred_to_competing_raw_table(self) -> None:
        workbook = _split_header_workbook()
        raw = workbook.copy_worksheet(workbook["votingintention (headline)"])
        raw.title = "votingintention (raw)"
        raw.cell(7, 2, 0.75)
        sheet, row = mic._find_headline_sheet(workbook)
        assert sheet.title == "votingintention (headline)"
        assert row == 6
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        assert parsed.party_region_percentages["Conservative"][mic.NATIONAL_KEY] == 23

    def test_reordered_geography_and_demographic_pairs(self) -> None:
        workbook = _split_header_workbook()
        sheet = workbook["votingintention (headline)"]
        # Move London into the first demographic pair, including its count.
        for row in range(1, 18):
            for left, right in ((4, 26), (5, 27)):
                first = sheet.cell(row, left).value
                second = sheet.cell(row, right).value
                sheet.cell(row, left).value = second
                sheet.cell(row, right).value = first
        parsed = mic.parse_poll(workbook, source_url=_SOURCE_URL)
        expected = json.loads(_SPLIT_HEADER_FIXTURE.read_text())["expected"]
        assert parsed.model_dump(mode="json") == expected

    @pytest.mark.parametrize(
        ("percentage_marker", "count_marker"),
        [("Unweighted N", "%"), ("", "Unweighted N"), ("%", ""), ("%", "All")],
    )
    def test_invalid_pairs_cannot_supply_region_headers(
        self, percentage_marker: str, count_marker: str,
    ) -> None:
        workbook = _split_header_workbook()
        sheet = workbook["votingintention (headline)"]
        for col in range(22, 43, 2):
            sheet.cell(6, col).value = percentage_marker
            sheet.cell(6, col + 1).value = count_marker
        with pytest.raises(ValueError, match="Could not locate headline"):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_nonadjacent_region_labels_are_not_borrowed(self) -> None:
        workbook = _split_header_workbook()
        sheet = workbook["votingintention (headline)"]
        sheet.insert_rows(6)
        with pytest.raises(ValueError, match="Could not locate headline"):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)

    def test_too_few_valid_region_pairs_still_fail(self) -> None:
        workbook = _split_header_workbook()
        sheet = workbook["votingintention (headline)"]
        for col in range(24, 43, 2):
            sheet.cell(6, col + 1).value = "Respondents"
        with pytest.raises(ValueError, match="sufficient region columns"):
            mic.parse_poll(workbook, source_url=_SOURCE_URL)
