"""Tests for scripts.export.manifest — mapMode region/override/Senate-cycle building.

Pure functions; no DB needed. Covers the region attachment, ``regionNameOverride``
application (and stripping), and the durable ``senateClassCycle`` → concrete
``senateClassNextElection`` resolution that the full export and the ``--metadata-only``
refresh both rely on.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.export.manifest import (
    _senate_class_next_election,
    build_map_modes_with_regions,
)


class TestSenateClassNextElection:
    """Resolve each class's next election year from a durable cycle definition."""

    CYCLE = {"base": {"1": 2018, "2": 2020, "3": 2022}, "period": 6}

    def test_between_cycles_rolls_forward(self) -> None:
        # 2025: class 1 next in 2030, class 2 in 2026, class 3 in 2028.
        assert _senate_class_next_election(self.CYCLE, 2025) == {"1": 2030, "2": 2026, "3": 2028}

    def test_on_a_base_year_returns_that_year(self) -> None:
        assert _senate_class_next_election(self.CYCLE, 2018)["1"] == 2018

    def test_on_an_election_year_returns_current_year(self) -> None:
        # Documented behaviour: during a class's election year it reads as "up this year"
        # (year-granular; it rolls to the next cycle only once the year turns over).
        assert _senate_class_next_election(self.CYCLE, 2026)["2"] == 2026

    def test_current_year_before_base_returns_base(self) -> None:
        assert _senate_class_next_election(self.CYCLE, 2019)["2"] == 2020

    def test_missing_period_defaults_to_six(self) -> None:
        assert _senate_class_next_election({"base": {"2": 2020}}, 2025)["2"] == 2026


class TestBuildMapModesWithRegions:
    """Attach DB regions, apply overrides, and resolve the Senate cycle."""

    def test_attaches_db_regions_and_preserves_config(self) -> None:
        regions = {"21": [{"id": 1, "name": "Pacific"}, {"id": 2, "name": "Mountain"}]}
        result = build_map_modes_with_regions(
            {"21": {"projection": "albersUsa"}}, regions, parliament_features={}
        )
        assert result["21"]["projection"] == "albersUsa"
        assert result["21"]["regions"] == [{"id": 1, "name": "Pacific"}, {"id": 2, "name": "Mountain"}]

    def test_applies_region_name_override_and_strips_key(self) -> None:
        regions = {"12": [
            {"id": 67, "name": "Central Scotland and Lothians West"},
            {"id": 68, "name": "Glasgow"},
        ]}
        modes = {"12": {"regionNameOverride": {"Central Scotland and Lothians West": "Central and Lothian W"}}}
        result = build_map_modes_with_regions(modes, regions, parliament_features={})
        assert result["12"]["regions"] == [
            {"id": 67, "name": "Central and Lothian W"},
            {"id": 68, "name": "Glasgow"},
        ]
        assert "regionNameOverride" not in result["12"]

    def test_resolves_senate_cycle_and_strips_it(self) -> None:
        modes = {"23": {"senateClassCycle": {"base": {"1": 2018, "2": 2020, "3": 2022}, "period": 6}}}
        result = build_map_modes_with_regions(
            modes, {"23": []}, current_year=2025, parliament_features={}
        )
        assert result["23"]["senateClassNextElection"] == {"1": 2030, "2": 2026, "3": 2028}
        assert "senateClassCycle" not in result["23"]

    def test_shell_listed_regions_are_kept_verbatim(self) -> None:
        # Back-compat: a mapMode that still lists regions keeps them; DB regions are not attached.
        modes = {"5": {"regions": [{"id": 9, "name": "Custom"}]}}
        result = build_map_modes_with_regions(
            modes, {"5": [{"id": 1, "name": "DB Region"}]}, parliament_features={}
        )
        assert result["5"]["regions"] == [{"id": 9, "name": "Custom"}]

    def test_does_not_mutate_input(self) -> None:
        modes = {"23": {"senateClassCycle": {"base": {"2": 2020}, "period": 6}}}
        build_map_modes_with_regions(modes, {"23": []}, current_year=2025, parliament_features={})
        # The original mapMode dict is untouched (a fresh dict is built per map).
        assert "senateClassCycle" in modes["23"]
        assert "senateClassNextElection" not in modes["23"]


class TestSenateSpecialElections:
    """Specials are filtered once, at export, to the ones the next Senate election holds."""

    SPECIALS = [
        {"seat": "Florida", "class": 3, "year": 2026, "baselineElectionId": "2022-us-senate"},
        {"seat": "Ohio", "class": 3, "year": 2026, "baselineElectionId": "2022-us-senate"},
    ]
    FEATURES_2026 = {"us_senate": {"nextElectionYear": 2026}}

    @staticmethod
    def _live(
        specials: object, *, current_year: int, features: dict[str, Any] | None = None
    ) -> Any:
        modes = {"23": {"senateSpecialElections": specials}}
        result = build_map_modes_with_regions(
            modes,
            {"23": []},
            current_year=current_year,
            parliament_features=(
                TestSenateSpecialElections.FEATURES_2026 if features is None else features
            ),
        )
        return result["23"]["senateSpecialElections"]

    def test_next_cycle_specials_are_carried_through_unchanged(self) -> None:
        assert self._live(self.SPECIALS, current_year=2025) == self.SPECIALS

    def test_election_year_still_counts_as_upcoming(self) -> None:
        # Year-granular like senateClassNextElection: the special reads as "up" all year.
        assert len(self._live(self.SPECIALS, current_year=2026)) == 2

    def test_a_later_cycle_special_is_not_exported(self) -> None:
        # The model projects only nextElectionYear's specials; a 2028 entry added during the
        # 2026 cycle must not reach front-end Predict either.
        later = {"seat": "Texas", "class": 1, "year": 2028, "baselineElectionId": "2022-us-senate"}
        assert self._live([*self.SPECIALS, later], current_year=2026) == self.SPECIALS

    def test_past_specials_are_dropped(self) -> None:
        past = {"seat": "Georgia", "class": 3, "year": 2020, "baselineElectionId": "2016-us-senate"}
        assert self._live([past, *self.SPECIALS], current_year=2026) == self.SPECIALS

    def test_a_next_election_year_already_passed_keeps_nothing(self) -> None:
        # A stale shell (nextElectionYear not rolled on) must not resurrect a finished cycle.
        assert self._live(self.SPECIALS, current_year=2027) == []

    def test_no_next_election_year_keeps_nothing(self) -> None:
        # Same as the model: with no Senate nextElectionYear there is no live cycle.
        assert self._live(self.SPECIALS, current_year=2026, features={}) == []
        bad = {"us_senate": {"nextElectionYear": "2026"}}
        assert self._live(self.SPECIALS, current_year=2026, features=bad) == []

    def test_malformed_entries_are_skipped_not_fatal(self) -> None:
        malformed = [
            {"seat": "Maine", "class": 2, "baselineElectionId": "2022-us-senate"},  # no year
            {"seat": "Maine", "class": 2, "year": "soon", "baselineElectionId": "x"},
            {"seat": "Maine", "class": 2, "year": "2026", "baselineElectionId": "x"},
            {"seat": "Maine", "class": 2, "year": None, "baselineElectionId": "x"},
            {"seat": "Maine", "class": 2, "year": True, "baselineElectionId": "x"},
            {"class": 3, "year": 2026, "baselineElectionId": "2022-us-senate"},  # no seat
            {"seat": "  ", "class": 3, "year": 2026, "baselineElectionId": "2022-us-senate"},
            {"seat": "Maine", "year": 2026, "baselineElectionId": "2022-us-senate"},  # no class
            {"seat": "Maine", "class": "three", "year": 2026, "baselineElectionId": "x"},
            {"seat": "Maine", "class": 2, "year": 2026},  # no baseline
            "Maine",
            None,
        ]
        assert self._live([*malformed, *self.SPECIALS], current_year=2026) == self.SPECIALS

    def test_a_non_list_value_keeps_nothing(self) -> None:
        assert self._live({"seat": "Ohio"}, current_year=2026) == []

    def test_key_is_absent_when_the_shell_has_none(self) -> None:
        result = build_map_modes_with_regions(
            {"23": {}}, {"23": []}, current_year=2026, parliament_features=self.FEATURES_2026
        )
        assert "senateSpecialElections" not in result["23"]

    def test_does_not_mutate_the_shell_list(self) -> None:
        shell_entry = dict(self.SPECIALS[0])
        modes = {"23": {"senateSpecialElections": [shell_entry]}}
        result = build_map_modes_with_regions(
            modes, {"23": []}, current_year=2030, parliament_features=self.FEATURES_2026
        )
        assert len(modes["23"]["senateSpecialElections"]) == 1
        assert result["23"]["senateSpecialElections"] == []
