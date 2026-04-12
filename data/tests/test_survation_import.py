"""Tests for the Survation XLSX poll importer.

Covers pure parsing helpers that require no network access or database.
"""

from __future__ import annotations

from datetime import date

import pytest

from polls.importers.westminster.survation_import import (
    _cell_text,
    _infer_year,
    _month_number,
    _parse_fieldwork,
    _to_percentage,
    _to_percentage_or_zero,
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
    """Tests for _parse_fieldwork — date-range string parsing."""

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
