"""Tests for pure console units: trend-date parsing, preview cache, and form validation."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from pydantic import ValidationError

from console.forms import ModelRunForm
from console.services.preview import get_preview, pop_preview, store_preview
from console.services.trends import trend_entry_as_of_date


class TestTrendEntryAsOfDate:
    """The as-of date prefers the UNS-name date, then falls back to as_of_date."""

    def test_prefers_date_in_election_name(self) -> None:
        entry: dict[str, Any] = {"election_name": "UNS 2025-05-01", "as_of_date": "2020-01-01"}
        assert trend_entry_as_of_date(entry) == date(2025, 5, 1)

    def test_falls_back_to_as_of_date(self) -> None:
        entry: dict[str, Any] = {"election_name": "no date here", "as_of_date": "2025-06-15"}
        assert trend_entry_as_of_date(entry) == date(2025, 6, 15)

    def test_invalid_name_date_falls_back_to_as_of_date(self) -> None:
        entry: dict[str, Any] = {"election_name": "UNS 2025-13-99", "as_of_date": "2025-06-15"}
        assert trend_entry_as_of_date(entry) == date(2025, 6, 15)

    def test_returns_none_when_nothing_parseable(self) -> None:
        assert trend_entry_as_of_date({"election_name": "", "as_of_date": ""}) is None

    def test_returns_none_for_invalid_as_of_date(self) -> None:
        assert trend_entry_as_of_date({"as_of_date": "not-a-date"}) is None


class TestPreviewCache:
    """store -> get round-trips; pop removes; unknown tokens are safe."""

    def test_round_trip(self) -> None:
        payload: dict[str, Any] = {"hello": "world"}
        token = store_preview(payload)
        assert get_preview(token) == payload

    def test_pop_discards(self) -> None:
        token = store_preview({"a": 1})
        pop_preview(token)
        assert get_preview(token) is None

    def test_get_unknown_token_is_none(self) -> None:
        assert get_preview("does-not-exist") is None

    def test_pop_unknown_token_is_noop(self) -> None:
        pop_preview("does-not-exist")  # must not raise


class TestModelRunForm:
    """Validation bounds and the since >= as_of cross-field rule."""

    @staticmethod
    def _valid() -> dict[str, Any]:
        return {
            "map_name": "UK Constituencies post 2022",
            "baseline_election_name": "2024 General Election",
            "as_of_days_back": "0",
            "since_days_back": "30",
            "half_life_days": "30.0",
            "dry_run": "true",
        }

    def test_valid_form_parses_and_coerces(self) -> None:
        form = ModelRunForm.model_validate(self._valid())
        assert form.as_of_days_back == 0
        assert form.since_days_back == 30
        assert form.half_life_days == 30.0
        assert form.dry_run is True
        assert form.output_csv == ""  # default

    def test_since_less_than_as_of_rejected(self) -> None:
        data = self._valid()
        data["as_of_days_back"] = "30"
        data["since_days_back"] = "7"
        with pytest.raises(ValidationError):
            ModelRunForm.model_validate(data)

    def test_negative_as_of_rejected(self) -> None:
        data = self._valid()
        data["as_of_days_back"] = "-1"
        with pytest.raises(ValidationError):
            ModelRunForm.model_validate(data)

    def test_zero_half_life_rejected(self) -> None:
        data = self._valid()
        data["half_life_days"] = "0"
        with pytest.raises(ValidationError):
            ModelRunForm.model_validate(data)
