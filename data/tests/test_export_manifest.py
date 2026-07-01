"""Tests for scripts.export.manifest — mapMode region/override/Senate-cycle building.

Pure functions; no DB needed. Covers the region attachment, ``regionNameOverride``
application (and stripping), and the durable ``senateClassCycle`` → concrete
``senateClassNextElection`` resolution that the full export and the ``--metadata-only``
refresh both rely on.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
        result = build_map_modes_with_regions({"21": {"projection": "albersUsa"}}, regions)
        assert result["21"]["projection"] == "albersUsa"
        assert result["21"]["regions"] == [{"id": 1, "name": "Pacific"}, {"id": 2, "name": "Mountain"}]

    def test_applies_region_name_override_and_strips_key(self) -> None:
        regions = {"12": [
            {"id": 67, "name": "Central Scotland and Lothians West"},
            {"id": 68, "name": "Glasgow"},
        ]}
        modes = {"12": {"regionNameOverride": {"Central Scotland and Lothians West": "Central and Lothian W"}}}
        result = build_map_modes_with_regions(modes, regions)
        assert result["12"]["regions"] == [
            {"id": 67, "name": "Central and Lothian W"},
            {"id": 68, "name": "Glasgow"},
        ]
        assert "regionNameOverride" not in result["12"]

    def test_resolves_senate_cycle_and_strips_it(self) -> None:
        modes = {"23": {"senateClassCycle": {"base": {"1": 2018, "2": 2020, "3": 2022}, "period": 6}}}
        result = build_map_modes_with_regions(modes, {"23": []}, current_year=2025)
        assert result["23"]["senateClassNextElection"] == {"1": 2030, "2": 2026, "3": 2028}
        assert "senateClassCycle" not in result["23"]

    def test_shell_listed_regions_are_kept_verbatim(self) -> None:
        # Back-compat: a mapMode that still lists regions keeps them; DB regions are not attached.
        modes = {"5": {"regions": [{"id": 9, "name": "Custom"}]}}
        result = build_map_modes_with_regions(modes, {"5": [{"id": 1, "name": "DB Region"}]})
        assert result["5"]["regions"] == [{"id": 9, "name": "Custom"}]

    def test_does_not_mutate_input(self) -> None:
        modes = {"23": {"senateClassCycle": {"base": {"2": 2020}, "period": 6}}}
        build_map_modes_with_regions(modes, {"23": []}, current_year=2025)
        # The original mapMode dict is untouched (a fresh dict is built per map).
        assert "senateClassCycle" in modes["23"]
        assert "senateClassNextElection" not in modes["23"]
