"""Tests for the YouGov PDF poll importer.

Covers pure parsing helpers that require no network access or database.
"""

from __future__ import annotations

from datetime import date

import pytest

from polls.importers.westminster.yougov_import import (
    _normalize_percentage,
    normalize_name,
    parse_fieldwork,
)


# ── normalize_name ────────────────────────────────────────────────────────────


class TestNormalizeName:
    """Tests for normalize_name — region/party name normalisation."""

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert normalize_name("  Scotland  ") == "scotland"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_name("North  West  England") == "north west england"

    def test_lowercases(self) -> None:
        assert normalize_name("SCOTLAND") == "scotland"

    def test_tabs_treated_as_whitespace(self) -> None:
        assert normalize_name("North\tWest") == "north west"

    def test_already_normalised_unchanged(self) -> None:
        assert normalize_name("london") == "london"

    def test_empty_string(self) -> None:
        assert normalize_name("") == ""


# ── parse_fieldwork ───────────────────────────────────────────────────────────


class TestParseFieldwork:
    """Tests for parse_fieldwork — YouGov PDF fieldwork date string parsing."""

    def test_same_month(self) -> None:
        start, end = parse_fieldwork("3-7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_cross_month(self) -> None:
        start, end = parse_fieldwork("28 January - 3 February 2025")
        assert start == date(2025, 1, 28)
        assert end == date(2025, 2, 3)

    def test_cross_year(self) -> None:
        start, end = parse_fieldwork("30 December - 2 January 2026")
        assert start == date(2025, 12, 30)
        assert end == date(2026, 1, 2)

    def test_en_dash_normalised(self) -> None:
        start, end = parse_fieldwork("3–7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_fieldwork_prefix_present(self) -> None:
        # As extracted from PDF: "Fieldwork: 3-7 February 2025"
        start, end = parse_fieldwork("Fieldwork: 3-7 February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_ordinal_suffix_present(self) -> None:
        start, end = parse_fieldwork("3rd-7th February 2025")
        assert start == date(2025, 2, 3)
        assert end == date(2025, 2, 7)

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            parse_fieldwork("not a date at all")


# ── _normalize_percentage ─────────────────────────────────────────────────────


class TestNormalizePercentage:
    """Tests for _normalize_percentage — round to nearest integer as float."""

    def test_rounds_up(self) -> None:
        assert _normalize_percentage(34.6) == pytest.approx(35.0)

    def test_rounds_down(self) -> None:
        assert _normalize_percentage(34.4) == pytest.approx(34.0)

    def test_exact_integer(self) -> None:
        assert _normalize_percentage(42.0) == pytest.approx(42.0)

    def test_returns_float(self) -> None:
        result = _normalize_percentage(30.0)
        assert isinstance(result, float)

    def test_rounds_half_to_even(self) -> None:
        # Python's built-in round() uses banker's rounding: 34.5 → 34 (nearest even)
        assert _normalize_percentage(34.5) == pytest.approx(34.0)
