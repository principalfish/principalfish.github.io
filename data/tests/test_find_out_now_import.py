"""Tests for the Find Out Now XLSX poll importer.

Covers the pure parsing helpers, ``extract_workbook``, ``build_import_plan``,
``_cli_preview`` and ``main``. ``commit_import_plan`` and ``_find_existing_poll``
are covered for every Westminster importer, this one included, by
``test_westminster_importers_commit.py``.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType

import pytest
from openpyxl import Workbook

from db import Database
from polls.importers.westminster import find_out_now_import as fon
from tests.uk_fixtures import (
    FakeUrlResponse,
    WestminsterWorld,
    build_workbook,
    workbook_bytes,
)

# ── Synthetic workbook builders ────────────────────────────────────────────────
#
# Cover page: either "Fieldwork date"/"Sample size" label rows (read via
# _find_label_value), or, with no labels at all, raw text at C5/C6 (the
# fallback cells parse_poll reads directly).
#
# "Headline VI" sheet: a header row ("Party" plus REGION_HEADER_TO_INTERNAL's
# eleven keys, prefixed by "All"), one row per party, then a "Filtered n" row
# that stops the scan.
#
# "Q2" sheet: a two-column fallback (Party, All) used when no Headline VI-like
# sheet exists; it carries national figures only.

_FIELDWORK_TEXT = "28th January - 3rd February 2026"
_SAMPLE_TEXT = "n=1,032"


def _labelled_cover_sheet(
    fieldwork_text: str | None = _FIELDWORK_TEXT,
    sample_text: str | None = _SAMPLE_TEXT,
) -> list[list[object]]:
    """A cover sheet with "Fieldwork date"/"Sample size" label rows."""
    rows: list[list[object]] = []
    if fieldwork_text is not None:
        rows.append(["Fieldwork date", fieldwork_text])
    if sample_text is not None:
        rows.append(["Sample size", sample_text])
    return rows


def _fallback_cover_sheet(fieldwork_text: str, sample_text: str) -> list[list[object]]:
    """A cover sheet with no labels: values sit at C5/C6 (the fallback cells)."""
    return [
        [None],
        [None],
        [None],
        [None],
        [None, None, fieldwork_text],
        [None, None, sample_text],
    ]


# Region headers exactly as REGION_HEADER_TO_INTERNAL's keys, "All" first.
_HEADLINE_COLUMNS: tuple[str, ...] = (
    "All",
    "East Midlands",
    "East of England",
    "London",
    "North East",
    "North West",
    "Scotland",
    "South East",
    "South West",
    "Wales",
    "West Midlands",
    "Yorkshire and the Humber",
)

# Raw workbook party label -> one percentage per _HEADLINE_COLUMNS entry.
# Values are unique across the whole table ((party_index - 1) * 12 +
# column_index + 1, 1-based), so a swapped row or column can't coincidentally
# reproduce another cell's expected value. They start at 2, not 1, because
# _to_percentage treats any value in [0.0, 1.0] as a fraction (multiplying by
# 100), and 1.0 sits exactly on that boundary.
_HEADLINE_VALUES: Mapping[str, tuple[float, ...]] = MappingProxyType(
    {
        "Conservative": (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0),
        "Labour": (
            14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0,
        ),
        "Liberal Democrat": (
            26.0, 27.0, 28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0, 36.0, 37.0,
        ),
        "Reform UK": (
            38.0, 39.0, 40.0, 41.0, 42.0, 43.0, 44.0, 45.0, 46.0, 47.0, 48.0, 49.0,
        ),
        "Green": (50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 57.0, 58.0, 59.0, 60.0, 61.0),
        "SNP": (62.0, 63.0, 64.0, 65.0, 66.0, 67.0, 68.0, 69.0, 70.0, 71.0, 72.0, 73.0),
        "Plaid Cymru": (
            74.0, 75.0, 76.0, 77.0, 78.0, 79.0, 80.0, 81.0, 82.0, 83.0, 84.0, 85.0,
        ),
        "Other": (86.0, 87.0, 88.0, 89.0, 90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0),
    }
)


def _headline_vi_rows(
    values: Mapping[str, tuple[float, ...]] = _HEADLINE_VALUES,
    *,
    columns: Sequence[str] = _HEADLINE_COLUMNS,
    omit_parties: Sequence[str] = (),
    include_blank_row: bool = False,
    include_unmapped_party: bool = False,
    sentinel_before: str | None = None,
    include_sentinel: bool = True,
) -> list[list[object]]:
    """A "Headline VI" sheet: a header row, one row per party, a stop row.

    ``sentinel_before`` inserts the "Filtered n" stop row before that party,
    but keeps writing every later party's row after it too. A correct
    implementation ``break``s at the sentinel and never reads those later
    rows; one that merely treated an unrecognised label as skippable (no
    ``break``) would carry on past "Filtered n" and wrongly pick them back
    up. ``include_sentinel=False`` omits the trailing "Filtered n" row
    entirely, so the row scan exhausts its range instead of breaking out of
    it.
    """
    header: list[object] = ["Party", *columns]
    rows: list[list[object]] = [header]
    if include_blank_row:
        rows.append([None] * (len(columns) + 1))
    if include_unmapped_party:
        rows.append(["Don't know", *([1.0] * len(columns))])
    sentinel_written = False
    for party, figures in values.items():
        if party in omit_parties:
            continue
        if party == sentinel_before and not sentinel_written:
            rows.append(["Filtered n", 1000])
            sentinel_written = True
            continue
        rows.append([party, *figures])
    if include_sentinel and not sentinel_written:
        rows.append(["Filtered n", 1000])
    return rows


_Q2_NATIONAL_VALUES: Mapping[str, float] = MappingProxyType(
    {
        "Conservative": 24.0,
        "Labour": 32.0,
        "Liberal Democrat": 11.0,
        "Reform UK": 21.0,
        "Green": 6.0,
        "SNP": 3.0,
        "Plaid Cymru": 1.0,
        "Other": 2.0,
    }
)


def _q2_rows(
    values: Mapping[str, float] = _Q2_NATIONAL_VALUES,
    *,
    include_blank_row: bool = False,
    include_unmapped_party: bool = False,
    omit_parties: Sequence[str] = (),
    sentinel_before: str | None = None,
    include_sentinel: bool = True,
) -> list[list[object]]:
    """A "Q2" sheet: "Party"/spacer/"All" header, one national-only row per party.

    "All" sits at column 3, not column 2, so a parser that hard-codes column 2
    instead of locating the "All" header by name fails these tests.
    ``sentinel_before`` behaves as in :func:`_headline_vi_rows`.
    """
    rows: list[list[object]] = [["Party", "Unused", "All"]]
    if include_blank_row:
        rows.append([None, None, None])
    if include_unmapped_party:
        rows.append(["Don't know", None, 1.0])
    sentinel_written = False
    for party, pct in values.items():
        if party in omit_parties:
            continue
        if party == sentinel_before and not sentinel_written:
            rows.append(["Filtered n", None, 1000])
            sentinel_written = True
            continue
        rows.append([party, None, pct])
    if include_sentinel and not sentinel_written:
        rows.append(["Filtered n", None, 1000])
    return rows


def _full_workbook(
    *,
    cover_sheet_name: str = "Cover page",
    cover_rows: list[list[object]] | None = None,
    include_headline: bool = True,
    headline_sheet_name: str = "Headline VI",
    headline_rows: list[list[object]] | None = None,
    q2_sheet_name: str | None = None,
    q2_rows_override: list[list[object]] | None = None,
) -> Workbook:
    """A cover sheet plus a Headline VI and/or Q2 sheet."""
    sheets: dict[str, list[list[object]]] = {
        cover_sheet_name: (
            cover_rows if cover_rows is not None else _labelled_cover_sheet()
        )
    }
    if include_headline:
        sheets[headline_sheet_name] = (
            headline_rows if headline_rows is not None else _headline_vi_rows()
        )
    if q2_sheet_name is not None:
        sheets[q2_sheet_name] = (
            q2_rows_override if q2_rows_override is not None else _q2_rows()
        )
    return build_workbook(sheets)


# ── _maybe_adjust_fieldwork_year_from_url ──────────────────────────────────────


def _parsed(
    *,
    fieldwork_start: date,
    fieldwork_end: date,
    sample_size: int = 1000,
) -> fon.ParsedPoll:
    return fon.ParsedPoll(
        sample_size=sample_size,
        fieldwork_start=fieldwork_start,
        fieldwork_end=fieldwork_end,
        party_region_percentages={"Labour": {fon.NATIONAL_KEY: 30.0}},
    )


class TestMaybeAdjustFieldworkYearFromUrl:
    """Tests for _maybe_adjust_fieldwork_year_from_url."""

    def test_no_url_year_returns_the_same_object(self) -> None:
        parsed = _parsed(
            fieldwork_start=date(2025, 1, 28), fieldwork_end=date(2025, 2, 3)
        )
        result = fon._maybe_adjust_fieldwork_year_from_url(parsed, None)
        assert result is parsed

    def test_start_and_end_year_differ_returns_the_same_object(self) -> None:
        parsed = _parsed(
            fieldwork_start=date(2025, 12, 30), fieldwork_end=date(2026, 1, 2)
        )
        result = fon._maybe_adjust_fieldwork_year_from_url(parsed, 2027)
        assert result is parsed

    def test_end_year_not_one_less_than_url_year_returns_the_same_object(
        self,
    ) -> None:
        parsed = _parsed(
            fieldwork_start=date(2025, 2, 5), fieldwork_end=date(2025, 2, 5)
        )
        result = fon._maybe_adjust_fieldwork_year_from_url(parsed, 2030)
        assert result is parsed

    def test_end_month_after_march_returns_the_same_object(self) -> None:
        parsed = _parsed(
            fieldwork_start=date(2025, 4, 5), fieldwork_end=date(2025, 4, 5)
        )
        result = fon._maybe_adjust_fieldwork_year_from_url(parsed, 2026)
        assert result is parsed

    def test_end_month_equal_to_march_still_adjusts(self) -> None:
        # The guard is "month > 3", so March (3) is still inside the adjusted
        # range — pins the boundary against a ">= 3" mutant, which would
        # wrongly treat March as too late and leave the dates unchanged.
        parsed = _parsed(
            fieldwork_start=date(2025, 2, 27), fieldwork_end=date(2025, 3, 3)
        )
        result = fon._maybe_adjust_fieldwork_year_from_url(parsed, 2026)
        assert result.fieldwork_start == date(2026, 2, 27)
        assert result.fieldwork_end == date(2026, 3, 3)

    def test_valid_adjustment_shifts_both_dates_forward_a_year(self) -> None:
        parsed = _parsed(
            fieldwork_start=date(2025, 1, 28),
            fieldwork_end=date(2025, 2, 3),
            sample_size=1032,
        )
        result = fon._maybe_adjust_fieldwork_year_from_url(parsed, 2026)
        assert result is not parsed
        assert result.fieldwork_start == date(2026, 1, 28)
        assert result.fieldwork_end == date(2026, 2, 3)
        assert result.sample_size == 1032
        assert result.party_region_percentages == parsed.party_region_percentages

    def test_replace_value_error_returns_the_same_object(self) -> None:
        # 29 Feb 2024 is valid (a leap year); shifting to 2025 is not, so the
        # internal date.replace(year=...) raises and the function must fall
        # back to returning the original, unmodified.
        parsed = _parsed(
            fieldwork_start=date(2024, 2, 29), fieldwork_end=date(2024, 2, 29)
        )
        result = fon._maybe_adjust_fieldwork_year_from_url(parsed, 2025)
        assert result is parsed


# ── normalize_name ──────────────────────────────────────────────────────────────


class TestNormalizeName:
    """Tests for normalize_name — whitespace-collapsing lowercase normalisation."""

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert fon.normalize_name("  London  ") == "london"

    def test_collapses_internal_whitespace(self) -> None:
        assert fon.normalize_name("North   West") == "north west"

    def test_lowercases(self) -> None:
        assert fon.normalize_name("LONDON") == "london"

    def test_tabs_treated_as_whitespace(self) -> None:
        assert fon.normalize_name("North\tWest") == "north west"

    def test_already_normalised_unchanged(self) -> None:
        assert fon.normalize_name("wales") == "wales"

    def test_empty_string(self) -> None:
        assert fon.normalize_name("") == ""


# ── _month_number ────────────────────────────────────────────────────────────


class TestMonthNumber:
    """Tests for _month_number — full month name -> integer, no abbreviations."""

    def test_full_names(self) -> None:
        assert fon._month_number("January") == 1
        assert fon._month_number("February") == 2
        assert fon._month_number("March") == 3
        assert fon._month_number("April") == 4
        assert fon._month_number("May") == 5
        assert fon._month_number("June") == 6
        assert fon._month_number("July") == 7
        assert fon._month_number("August") == 8
        assert fon._month_number("September") == 9
        assert fon._month_number("October") == 10
        assert fon._month_number("November") == 11
        assert fon._month_number("December") == 12

    def test_case_insensitive(self) -> None:
        assert fon._month_number("JANUARY") == 1
        assert fon._month_number("january") == 1
        assert fon._month_number("jAnUaRy") == 1

    def test_abbreviation_and_unknown_return_none(self) -> None:
        # Unlike some sibling importers, this module's month map only holds
        # full names, so an abbreviation is unrecognised too.
        assert fon._month_number("Jan") is None
        assert fon._month_number("Octember") is None
        assert fon._month_number("") is None


# ── parse_fieldwork ───────────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for parse_fieldwork — with/without year_hint, cross-month/year."""

    def test_same_month_with_year(self) -> None:
        start, end = fon.parse_fieldwork("1-5 February 2026")
        assert start == date(2026, 2, 1)
        assert end == date(2026, 2, 5)

    def test_cross_month_with_year(self) -> None:
        start, end = fon.parse_fieldwork("28th January - 3rd February 2026")
        assert start == date(2026, 1, 28)
        assert end == date(2026, 2, 3)

    def test_cross_month_year_boundary_infers_start_year_one_earlier(self) -> None:
        start, end = fon.parse_fieldwork("30th December - 2nd January 2026")
        assert start == date(2025, 12, 30)
        assert end == date(2026, 1, 2)

    def test_single_day_with_year(self) -> None:
        start, end = fon.parse_fieldwork("5th February 2026")
        assert start == date(2026, 2, 5)
        assert end == date(2026, 2, 5)

    def test_same_month_no_year_uses_year_hint(self) -> None:
        start, end = fon.parse_fieldwork("1-5 February", year_hint=2026)
        assert start == date(2026, 2, 1)
        assert end == date(2026, 2, 5)

    def test_single_day_no_year_uses_year_hint(self) -> None:
        start, end = fon.parse_fieldwork("5th February", year_hint=2026)
        assert start == date(2026, 2, 5)
        assert end == date(2026, 2, 5)

    def test_en_dash_normalised(self) -> None:
        start, end = fon.parse_fieldwork("28th January – 3rd February 2026")
        assert start == date(2026, 1, 28)
        assert end == date(2026, 2, 3)

    def test_no_pattern_matches_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            fon.parse_fieldwork("not a date at all")

    @pytest.mark.parametrize(
        "text",
        ["1-5 February", "5th February"],
        ids=["same_month_range", "single_day"],
    )
    def test_no_year_pattern_without_year_hint_raises(self, text: str) -> None:
        with pytest.raises(
            ValueError, match="Could not parse fieldwork year from string"
        ):
            fon.parse_fieldwork(text)

    @pytest.mark.parametrize(
        ("text", "kwargs"),
        [
            ("28th Blorpuary - 3rd February 2026", {}),
            ("28th January - 3rd Blorpuary 2026", {}),
            ("1-5 Blorpuary 2026", {}),
            ("5th Blorpuary 2026", {}),
            ("1-5 Blorpuary", {"year_hint": 2026}),
            ("5th Blorpuary", {"year_hint": 2026}),
        ],
        ids=[
            "cross_month_start",
            "cross_month_end",
            "same_month_with_year",
            "single_day_with_year",
            "same_month_no_year",
            "single_day_no_year",
        ],
    )
    def test_unknown_month_name_raises(
        self, text: str, kwargs: dict[str, int]
    ) -> None:
        with pytest.raises(ValueError, match="Could not parse fieldwork string"):
            fon.parse_fieldwork(text, **kwargs)


# ── _infer_year_hint_from_url ─────────────────────────────────────────────────


class TestInferYearHintFromUrl:
    """Tests for _infer_year_hint_from_url."""

    def test_year_found_in_url(self) -> None:
        url = "https://cms.findoutnow.co.uk/app/uploads/2026/02/tables.xlsx"
        assert fon._infer_year_hint_from_url(url) == 2026

    def test_no_year_in_url_returns_none(self) -> None:
        assert fon._infer_year_hint_from_url("https://example.test/tables.xlsx") is None

    def test_first_occurrence_wins(self) -> None:
        url = "https://example.test/2020/archive/2026/file.xlsx"
        assert fon._infer_year_hint_from_url(url) == 2020


# ── extract_workbook ────────────────────────────────────────────────────────


class TestExtractWorkbook:
    """Tests for extract_workbook — fetch and load an XLSX from a URL."""

    def test_downloads_and_loads_the_workbook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = workbook_bytes(build_workbook({"Sheet1": [["ok"]]}))
        calls: list[tuple[str, float | None]] = []

        def fake_urlopen(url: str, timeout: float | None = None) -> FakeUrlResponse:
            calls.append((url, timeout))
            return FakeUrlResponse(payload)

        monkeypatch.setattr(fon, "urlopen", fake_urlopen)

        workbook = fon.extract_workbook("https://example.test/tables.xlsx")

        assert workbook.sheetnames == ["Sheet1"]
        assert calls == [("https://example.test/tables.xlsx", 60)]


# ── _cell_text ────────────────────────────────────────────────────────────────


class TestCellText:
    """Tests for _cell_text — openpyxl cell value -> stripped string."""

    def test_none_returns_empty_string(self) -> None:
        assert fon._cell_text(None) == ""

    def test_string_stripped(self) -> None:
        assert fon._cell_text("  hello  ") == "hello"

    def test_integer_converted(self) -> None:
        assert fon._cell_text(42) == "42"

    def test_float_converted(self) -> None:
        assert fon._cell_text(3.5) == "3.5"

    def test_empty_string(self) -> None:
        assert fon._cell_text("") == ""


# ── _find_label_value ────────────────────────────────────────────────────────


class TestFindLabelValue:
    """Tests for _find_label_value — label search plus a 3-cell lookahead."""

    def test_value_immediately_to_the_right(self) -> None:
        workbook = build_workbook({"Cover": [["Fieldwork date", "1-5 Feb 2026"]]})
        assert fon._find_label_value(workbook["Cover"], "Fieldwork date") == (
            "1-5 Feb 2026"
        )

    def test_value_two_cells_to_the_right_when_the_adjacent_cell_is_empty(
        self,
    ) -> None:
        workbook = build_workbook(
            {"Cover": [["Fieldwork date", None, "1-5 Feb 2026"]]}
        )
        assert fon._find_label_value(workbook["Cover"], "Fieldwork date") == (
            "1-5 Feb 2026"
        )

    def test_case_insensitive_substring_match(self) -> None:
        workbook = build_workbook(
            {"Cover": [["FIELDWORK DATE (UK adults)", "1-5 Feb 2026"]]}
        )
        assert fon._find_label_value(workbook["Cover"], "fieldwork date") == (
            "1-5 Feb 2026"
        )

    def test_label_found_but_no_value_returns_none(self) -> None:
        workbook = build_workbook(
            {"Cover": [["Fieldwork date", None, None, None]]}
        )
        assert fon._find_label_value(workbook["Cover"], "Fieldwork date") is None

    def test_label_not_present_returns_none(self) -> None:
        workbook = build_workbook({"Cover": [["Nothing", "here"]]})
        assert fon._find_label_value(workbook["Cover"], "Fieldwork date") is None


# ── _as_int ───────────────────────────────────────────────────────────────────


class TestAsInt:
    """Tests for _as_int — digit extraction from a formatted string."""

    def test_plain_digits(self) -> None:
        assert fon._as_int("1032") == 1032

    def test_comma_formatted(self) -> None:
        assert fon._as_int("1,032") == 1032

    def test_prefixed_text(self) -> None:
        assert fon._as_int("n=1206") == 1206

    def test_no_digits_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not parse sample size"):
            fon._as_int("undisclosed")


# ── _to_percentage ────────────────────────────────────────────────────────────


class TestToPercentage:
    """Tests for _to_percentage — raw cell value -> rounded percentage float."""

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="Encountered empty percentage cell"):
            fon._to_percentage(None)

    def test_decimal_fraction_multiplied(self) -> None:
        assert fon._to_percentage(0.42) == pytest.approx(42.0)

    def test_one_boundary_treated_as_100_percent(self) -> None:
        assert fon._to_percentage(1.0) == pytest.approx(100.0)

    def test_zero_boundary(self) -> None:
        assert fon._to_percentage(0.0) == pytest.approx(0.0)

    def test_value_above_one_left_unchanged(self) -> None:
        assert fon._to_percentage(42) == pytest.approx(42.0)

    def test_rounds_to_nearest_integer(self) -> None:
        assert fon._to_percentage(34.6) == pytest.approx(35.0)
        assert fon._to_percentage(34.4) == pytest.approx(34.0)

    def test_returns_a_float(self) -> None:
        assert type(fon._to_percentage(42)) is float


# ── parse_poll ────────────────────────────────────────────────────────────────


class TestParsePollCoverSheet:
    """parse_poll's cover-sheet handling: labels, C5/C6 fallback, sheet choice."""

    def test_labelled_fieldwork_and_sample(self) -> None:
        workbook = _full_workbook()
        parsed = fon.parse_poll(workbook)
        assert parsed.sample_size == 1032
        assert parsed.fieldwork_start == date(2026, 1, 28)
        assert parsed.fieldwork_end == date(2026, 2, 3)

    def test_c5_c6_fallback_used_when_no_labels_present(self) -> None:
        cover = _fallback_cover_sheet(_FIELDWORK_TEXT, _SAMPLE_TEXT)
        workbook = _full_workbook(cover_rows=cover)
        parsed = fon.parse_poll(workbook)
        assert parsed.sample_size == 1032
        assert parsed.fieldwork_start == date(2026, 1, 28)

    def test_first_sheet_used_when_no_cover_page_title(self) -> None:
        workbook = build_workbook(
            {"Data": _labelled_cover_sheet(), "Headline VI": _headline_vi_rows()}
        )
        parsed = fon.parse_poll(workbook)
        assert parsed.sample_size == 1032
        assert parsed.fieldwork_start == date(2026, 1, 28)

    def test_cover_page_used_even_when_not_the_first_sheet(self) -> None:
        # "Cover page" is second here, not first — a mutant that always uses
        # workbook.sheetnames[0] instead of matching the name would read the
        # Headline VI sheet as the cover and fail to find fieldwork/sample
        # labels there.
        workbook = build_workbook(
            {"Headline VI": _headline_vi_rows(), "Cover page": _labelled_cover_sheet()}
        )
        parsed = fon.parse_poll(workbook)
        assert parsed.sample_size == 1032
        assert parsed.fieldwork_start == date(2026, 1, 28)

    def test_missing_fieldwork_raises(self) -> None:
        cover = _labelled_cover_sheet(fieldwork_text=None)
        workbook = _full_workbook(cover_rows=cover)
        with pytest.raises(ValueError, match="Fieldwork date not found in workbook"):
            fon.parse_poll(workbook)

    def test_missing_sample_size_raises(self) -> None:
        cover = _labelled_cover_sheet(sample_text=None)
        workbook = _full_workbook(cover_rows=cover)
        with pytest.raises(ValueError, match="Sample size not found in workbook"):
            fon.parse_poll(workbook)


class TestParsePollHeadlineVi:
    """parse_poll's "Headline VI" branch: full extraction and its raises."""

    def test_full_extraction_maps_every_header_to_the_internal_region_name(
        self,
    ) -> None:
        workbook = _full_workbook()
        parsed = fon.parse_poll(workbook)

        assert len(parsed.party_region_percentages) == 8
        assert parsed.party_region_percentages["Conservative"] == {
            fon.NATIONAL_KEY: 2.0,
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
        assert parsed.party_region_percentages["Liberal Democrats"][
            fon.NATIONAL_KEY
        ] == 26.0
        assert parsed.party_region_percentages["Scottish National Party"][
            fon.NATIONAL_KEY
        ] == 62.0
        assert parsed.party_region_percentages["Other"][fon.NATIONAL_KEY] == 86.0

    def test_sheet_matched_by_headline_substring(self) -> None:
        workbook = _full_workbook(headline_sheet_name="Headline Results")
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages) == 8

    def test_blank_row_does_not_crash_or_add_a_spurious_entry(self) -> None:
        # Coverage of the "if not party_label: continue" guard: a blank
        # label also falls through to the unmapped-party continue just
        # below it (PARTY_NAME_MAP has no "" key), so removing this guard
        # would not change the outcome — this shows the blank row is
        # harmless, not that the guard specifically matters.
        rows = _headline_vi_rows(include_blank_row=True)
        workbook = _full_workbook(headline_rows=rows)
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages) == 8

    def test_unmapped_party_row_is_skipped(self) -> None:
        rows = _headline_vi_rows(include_unmapped_party=True)
        workbook = _full_workbook(headline_rows=rows)
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages) == 8
        assert "Don't know" not in parsed.party_region_percentages

    def test_missing_regional_header_row_raises(self) -> None:
        rows: list[list[object]] = [["Nothing", "here"]]
        workbook = _full_workbook(headline_rows=rows)
        with pytest.raises(
            ValueError, match="Could not locate regional header row"
        ):
            fon.parse_poll(workbook)

    def test_fewer_than_six_region_columns_raises(self) -> None:
        # Exactly 5 region columns: pins the "< 6" boundary precisely (a
        # mutant comparing "< 4" would let this workbook through).
        rows: list[list[object]] = [
            [
                "Party",
                "All",
                "East Midlands",
                "London",
                "Wales",
                "Scotland",
                "South East",
            ]
        ]
        workbook = _full_workbook(headline_rows=rows)
        with pytest.raises(
            ValueError, match="Could not resolve sufficient region columns"
        ):
            fon.parse_poll(workbook)

    def test_missing_required_party_raises(self) -> None:
        rows = _headline_vi_rows(omit_parties=("Green",))
        workbook = _full_workbook(headline_rows=rows)
        expected = "Missing expected party rows in workbook: ['Green']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            fon.parse_poll(workbook)

    def test_sentinel_row_stops_the_scan_early(self) -> None:
        rows = _headline_vi_rows(sentinel_before="Plaid Cymru")
        workbook = _full_workbook(headline_rows=rows)
        expected = "Missing expected party rows in workbook: ['Other', 'Plaid Cymru']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            fon.parse_poll(workbook)

    def test_row_scan_exhausts_its_range_without_a_sentinel_row(self) -> None:
        # Every required party is present but there is no "Filtered n" row at
        # all, so the scan runs to the end of its range instead of breaking
        # out of it early.
        rows = _headline_vi_rows(include_sentinel=False)
        workbook = _full_workbook(headline_rows=rows)
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages) == 8


class TestParsePollQ2Fallback:
    """parse_poll's "Q2" branch: used only when no Headline VI-like sheet exists."""

    def test_national_only_figures_from_q2_sheet(self) -> None:
        workbook = _full_workbook(include_headline=False, q2_sheet_name="Q2")
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages) == 8
        assert parsed.party_region_percentages["Labour"] == {fon.NATIONAL_KEY: 32.0}
        assert parsed.party_region_percentages["Conservative"] == {
            fon.NATIONAL_KEY: 24.0
        }

    def test_sheet_matched_by_q2_prefix(self) -> None:
        workbook = _full_workbook(
            include_headline=False, q2_sheet_name="Q2 Regional Breakdown"
        )
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages) == 8

    def test_blank_and_unmapped_rows_are_both_harmless(self) -> None:
        # As in the Headline VI branch, the blank-row guard is coverage-only
        # here: a blank label falls through to the same unmapped-party
        # continue that skips "Don't know", so this shows both rows are
        # harmless rather than pinning the blank-row guard specifically.
        rows = _q2_rows(include_blank_row=True, include_unmapped_party=True)
        workbook = _full_workbook(
            include_headline=False, q2_sheet_name="Q2", q2_rows_override=rows
        )
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages) == 8
        assert "Don't know" not in parsed.party_region_percentages

    def test_missing_all_column_raises(self) -> None:
        rows: list[list[object]] = [["Party", "Share"]]
        workbook = _full_workbook(
            include_headline=False, q2_sheet_name="Q2", q2_rows_override=rows
        )
        with pytest.raises(
            ValueError, match="Could not locate 'All' \\(national\\) column in Q2"
        ):
            fon.parse_poll(workbook)

    def test_sentinel_row_stops_the_scan_early(self) -> None:
        rows = _q2_rows(sentinel_before="Plaid Cymru")
        workbook = _full_workbook(
            include_headline=False, q2_sheet_name="Q2", q2_rows_override=rows
        )
        expected = "Missing expected party rows in workbook: ['Other', 'Plaid Cymru']"
        with pytest.raises(ValueError, match=re.escape(expected)):
            fon.parse_poll(workbook)

    def test_neither_headline_nor_q2_sheet_raises(self) -> None:
        workbook = _full_workbook(include_headline=False)
        expected = "Could not find 'Headline VI' or 'Q2' sheet in workbook"
        with pytest.raises(ValueError, match=re.escape(expected)):
            fon.parse_poll(workbook)

    def test_row_scan_exhausts_its_range_without_a_sentinel_row(self) -> None:
        rows = _q2_rows(include_sentinel=False)
        workbook = _full_workbook(
            include_headline=False, q2_sheet_name="Q2", q2_rows_override=rows
        )
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages) == 8

    def test_headline_vi_preferred_when_both_sheets_present(self) -> None:
        # Both "Headline VI" and "Q2" exist; the Headline VI branch must win.
        # The Q2 branch would only ever produce a 1-entry (national-only)
        # dict per party, so a full 12-entry dict proves which branch ran.
        workbook = _full_workbook(include_headline=True, q2_sheet_name="Q2")
        parsed = fon.parse_poll(workbook)
        assert len(parsed.party_region_percentages["Conservative"]) == 12


# ── build_import_plan ──────────────────────────────────────────────────────────


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

        monkeypatch.setattr(fon, "extract_workbook", fail_extract_workbook)

        with pytest.raises(ValueError, match=re.escape("Map not found: 'Nope'")):
            fon.build_import_plan(db, map_name="Nope")

    def test_missing_parties_raises(
        self, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        poll_map = db.add_map("Empty Parties Map", parliament="westminster")

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)

        expected = (
            "Missing parties in database (run party importer first): "
            "['Conservative', 'Green', 'Labour', 'Liberal Democrats', 'Other', "
            "'Plaid Cymru', 'Reform UK', 'Scottish National Party']"
        )
        with pytest.raises(ValueError, match=re.escape(expected)):
            fon.build_import_plan(db, map_name=poll_map.name)


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

        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)

        plan = fon.build_import_plan(
            db, xlsx_url=fon.DEFAULT_XLSX_URL, map_name=world.map_name
        )

        assert plan.map_id == world.map_id
        assert plan.map_name == world.map_name
        assert plan.regions_mapping == _expected_regions_mapping(world.region_ids)
        assert plan.pollster_identifier == fon.DEFAULT_POLLSTER_IDENTIFIER
        assert plan.pollster_exists is False
        assert plan.pollster_name == "Find Out Now"
        assert plan.pollster_id is None
        assert plan.poll_exists is False
        assert plan.poll_id is None
        assert plan.source_url == fon.DEFAULT_XLSX_URL

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
        # 0.0 for every party — a second, distinct party proves the default
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
        pollster = db.add_pollster(
            "Find Out Now Ltd", "find_out_now", weight=1.0
        )
        poll = db.add_poll(
            pollster.id,
            world.map_id,
            date(2026, 1, 28),
            date(2026, 2, 3),
            sample_size=1032,
        )

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)

        plan = fon.build_import_plan(db, map_name=world.map_name)

        assert plan.pollster_exists is True
        assert plan.pollster_id == pollster.id
        assert plan.pollster_name == "Find Out Now Ltd"
        assert plan.poll_exists is True
        assert plan.poll_id == poll.id

    def test_pollster_existing_without_a_matching_poll_leaves_poll_absent(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        db.add_pollster("Find Out Now Ltd", "find_out_now", weight=1.0)

        def fake_extract_workbook(*_a: object, **_k: object) -> Workbook:
            return _full_workbook()

        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)

        plan = fon.build_import_plan(db, map_name=world.map_name)

        assert plan.pollster_exists is True
        assert plan.poll_exists is False
        assert plan.poll_id is None


class TestBuildImportPlanFieldworkYearHandling:
    """build_import_plan actually wires URL-year inference and the fix-up.

    Both are otherwise only unit-tested in isolation (TestInferYearHintFromUrl
    and TestMaybeAdjustFieldworkYearFromUrl); neither of those proves
    build_import_plan calls them at all.
    """

    def test_no_year_in_text_infers_the_year_from_the_url(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        world = westminster_world
        cover = _labelled_cover_sheet(fieldwork_text="1-5 February")
        workbook = _full_workbook(cover_rows=cover)

        def fake_extract_workbook(*_a: object, **_k: object) -> object:
            return workbook

        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)

        plan = fon.build_import_plan(
            db,
            xlsx_url="https://example.test/2024/tables.xlsx",
            map_name=world.map_name,
        )

        assert plan.parsed.fieldwork_start == date(2024, 2, 1)
        assert plan.parsed.fieldwork_end == date(2024, 2, 5)

    def test_year_boundary_fix_up_applied_from_the_url(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The cover text's own year (2025) is one less than the URL's (2026)
        # and the range falls in Jan-Feb, so build_import_plan must apply
        # _maybe_adjust_fieldwork_year_from_url and shift both dates forward.
        world = westminster_world
        cover = _labelled_cover_sheet(
            fieldwork_text="28th January - 3rd February 2025"
        )
        workbook = _full_workbook(cover_rows=cover)

        def fake_extract_workbook(*_a: object, **_k: object) -> object:
            return workbook

        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)

        plan = fon.build_import_plan(
            db,
            xlsx_url="https://example.test/2026/tables.xlsx",
            map_name=world.map_name,
        )

        assert plan.parsed.fieldwork_start == date(2026, 1, 28)
        assert plan.parsed.fieldwork_end == date(2026, 2, 3)


class TestBuildImportPlanNationalNoneHandling:
    """A party whose parsed data has no NATIONAL_KEY skips the National row."""

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
            _workbook: object, *, fieldwork_year_hint: int | None = None
        ) -> fon.ParsedPoll:
            return fon.ParsedPoll(
                sample_size=1000,
                fieldwork_start=date(2026, 1, 1),
                fieldwork_end=date(2026, 1, 2),
                party_region_percentages={"Labour": {"London": 55.0}},
            )

        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)
        monkeypatch.setattr(fon, "parse_poll", fake_parse_poll)

        plan = fon.build_import_plan(db, map_name=world.map_name)

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


class TestBuildImportPlanRegionMatchingPinnedBug:
    """``regions_by_name`` (build_import_plan) is computed but never read again.

    Region rows are actually matched by exact ``region.name`` against
    ``parsed.party_region_percentages``' region keys (which come from
    ``REGION_HEADER_TO_INTERNAL``'s values verbatim) — not by
    ``normalize_name``, despite the normalised dict being built for exactly
    that purpose. Confirmed by reading the source: ``regions_by_name`` has
    exactly one reference in the file, its own definition. A DB region name
    differing only in case or whitespace from the internal name therefore
    never matches and silently falls back to the 0.0 default rather than
    raising or matching case-insensitively. Latent — the live map's region
    names match the internal names exactly, so this has not been observed to
    bite in production.
    """

    def test_odd_cased_region_name_falls_back_to_zero_pins_current_behaviour(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        custom_map = db.add_map("Odd Case Map", parliament="westminster")
        odd_region = db.add_region(custom_map.id, "  LONDON  ")

        def fake_extract_workbook(*_a: object, **_k: object) -> object:
            return object()

        def fake_parse_poll(
            _workbook: object, *, fieldwork_year_hint: int | None = None
        ) -> fon.ParsedPoll:
            return fon.ParsedPoll(
                sample_size=1000,
                fieldwork_start=date(2026, 1, 1),
                fieldwork_end=date(2026, 1, 2),
                party_region_percentages={
                    "Labour": {fon.NATIONAL_KEY: 30.0, "London": 55.0}
                },
            )

        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)
        monkeypatch.setattr(fon, "parse_poll", fake_parse_poll)

        plan = fon.build_import_plan(db, map_name=custom_map.name)

        odd_row = next(
            row for row in plan.rows if row.region_id == odd_region.id
        )
        # If the parsed "London" figure had matched via normalize_name, this
        # would be 55.0. It is 0.0, because the match is an exact string
        # comparison against "  LONDON  ", which never equals "London".
        assert odd_row.percentage == 0.0
        assert plan.regions_mapping == f"  LONDON  :{odd_region.id}"


# ── _cli_preview ─────────────────────────────────────────────────────────────


def _preview_plan(**overrides: object) -> fon.ImportPlan:
    """Build an ImportPlan for _cli_preview tests: one Labour/Wales row."""
    parsed = fon.ParsedPoll(
        sample_size=1032,
        fieldwork_start=date(2026, 1, 28),
        fieldwork_end=date(2026, 2, 3),
        party_region_percentages={
            "Labour": {fon.NATIONAL_KEY: 32.0, "Wales": 22.0}
        },
    )
    row = fon.PlannedPollRow(
        party_id=2,
        party_name="Labour",
        region_id=10,
        region_name="Wales",
        percentage=22.0,
    )
    defaults: dict[str, object] = {
        "pollster_identifier": "find_out_now",
        "pollster_name": "Find Out Now",
        "pollster_id": None,
        "pollster_exists": False,
        "regions_mapping": "Wales:10",
        "map_id": 1,
        "map_name": "UK Constituencies post 2022",
        "source_url": fon.DEFAULT_XLSX_URL,
        "parsed": parsed,
        "poll_id": None,
        "poll_exists": False,
        "rows": [row],
    }
    defaults.update(overrides)
    return fon.ImportPlan.model_validate(defaults)


class TestCliPreview:
    """_cli_preview's dry-run summary, all four pollster/poll existence combos."""

    def test_new_pollster_and_new_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fon._cli_preview(_preview_plan())

        lines = capsys.readouterr().out.splitlines()
        assert "Parsed poll: fieldwork=2026-01-28 to 2026-02-03, sample=1032" in lines
        assert "[dry-run] would create pollster: find_out_now" in lines
        assert "[dry-run] would create poll" in lines
        assert (
            "[dry-run] would insert row: party=Labour, region=Wales, "
            "region_id=10, pct=22.00"
        ) in lines

    def test_existing_pollster_and_existing_poll(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fon._cli_preview(_preview_plan(pollster_exists=True, poll_exists=True, poll_id=7))

        lines = capsys.readouterr().out.splitlines()
        assert "pollster exists: find_out_now" in lines
        assert "poll exists: 7" in lines
        assert "[dry-run] would create pollster: find_out_now" not in lines
        assert "[dry-run] would create poll" not in lines

    def test_poll_exists_true_with_no_id_still_previews_creation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fon._cli_preview(_preview_plan(poll_exists=True, poll_id=None))

        lines = capsys.readouterr().out.splitlines()
        assert "poll exists: None" not in lines
        assert "[dry-run] would create poll" in lines

    def test_poll_id_set_but_poll_exists_false_still_previews_creation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fon._cli_preview(_preview_plan(poll_exists=False, poll_id=42))

        lines = capsys.readouterr().out.splitlines()
        assert "poll exists: 42" not in lines
        assert "[dry-run] would create poll" in lines

    def test_no_rows_prints_no_row_lines(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fon._cli_preview(_preview_plan(rows=[]))

        out = capsys.readouterr().out
        assert "[dry-run] would insert row" not in out


# ── main ──────────────────────────────────────────────────────────────────────


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

        monkeypatch.setattr(fon, "Database", fake_database)
        monkeypatch.setattr(fon, "extract_workbook", fake_extract_workbook)
        monkeypatch.setattr(sys, "argv", ["find_out_now_import.py", *argv])
        fon.main()

    def test_cli_defaults_pin(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        assert fon.DEFAULT_MAP_NAME == world.map_name
        assert fon.DEFAULT_POLLSTER_IDENTIFIER == "find_out_now"

        self._run(db, monkeypatch, _full_workbook(), "--dry-run")

        out = capsys.readouterr().out
        assert f"Fetching XLSX: {fon.DEFAULT_XLSX_URL}" in out
        assert "[dry-run] would create pollster: find_out_now" in out

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
        assert "[dry-run] would create pollster: find_out_now" in out
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
        second_map = db.add_map("Second Find Out Now Map", parliament="westminster")
        for name in fon.REGION_HEADER_TO_INTERNAL.values():
            db.add_region(second_map.id, name)
        custom_url = "https://example.test/custom-find-out-now.xlsx"
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
            "find_out_now_custom",
            fetched_urls=fetched_urls,
        )

        assert fetched_urls == [custom_url]
        assert db.get_pollster_by_identifier(fon.DEFAULT_POLLSTER_IDENTIFIER) is None
        pollster = db.get_pollster_by_identifier("find_out_now_custom")
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
        # --xlsx-url deliberately carries a *different* year (2024) than the
        # hint (2026): DEFAULT_XLSX_URL itself contains "2026", so a hint of
        # 2026 against the default URL can't tell "the hint was forwarded"
        # from "the hint was dropped and the URL's own year was inferred
        # instead". Using a mismatched URL year makes the two paths diverge.
        world = westminster_world
        cover = _labelled_cover_sheet(fieldwork_text="1-5 February")
        workbook = _full_workbook(cover_rows=cover)

        self._run(
            db,
            monkeypatch,
            workbook,
            "--map-name",
            world.map_name,
            "--xlsx-url",
            "https://example.test/2024/tables.xlsx",
            "--fieldwork-year-hint",
            "2026",
            "--dry-run",
        )

        out = capsys.readouterr().out
        assert "Parsed poll: fieldwork=2026-02-01 to 2026-02-05" in out

    def test_commit_creates_pollster_poll_and_rows(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        world = westminster_world
        self._run(db, monkeypatch, _full_workbook(), "--map-name", world.map_name)

        out = capsys.readouterr().out
        assert "created pollster: find_out_now" in out
        assert "created poll:" in out
        assert "inserted poll rows: 104" in out
        assert "deleted existing rows" not in out
        pollster = db.get_pollster_by_identifier("find_out_now")
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
        pollster = db.get_pollster_by_identifier("find_out_now")
        assert pollster is not None
        poll_id = db.get_polls_by_pollster(pollster.id)[0].id
        assert len(db.get_rows_for_poll(poll_id)) == 104

        self._run(db, monkeypatch, _full_workbook(), *argv)
        out = capsys.readouterr().out
        assert "poll exists" in out
        assert f"poll {poll_id} already has rows; use --replace-rows" in out
        assert len(db.get_rows_for_poll(poll_id)) == 104

        self._run(db, monkeypatch, _full_workbook(), *argv, "--replace-rows")
        out = capsys.readouterr().out
        assert "deleted existing rows: 104" in out
        assert "inserted poll rows: 104" in out
        assert len(db.get_rows_for_poll(poll_id)) == 104
