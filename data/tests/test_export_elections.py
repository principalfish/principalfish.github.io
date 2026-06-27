"""Tests for export_elections manifest-building helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from models import ElectionType
from export_elections import (
    assign_comparison_elections,
    manifest_name_for_election,
    reorder_manifest_entries,
)


class _Election:
    """Minimal stand-in for an Election ORM row (only the fields the helper reads)."""

    def __init__(self, name: str, type: ElectionType) -> None:
        self.name = name
        self.type = type


class TestManifestNameForElection:
    """The verbose DB names are shortened to the curated front-end display forms."""

    @pytest.mark.parametrize(
        "name, election_type, expected",
        [
            ("2024 General Election", ElectionType.uk_general, "2024 Election"),
            ("2019 General Election", ElectionType.uk_general, "2019 Election"),
            ("2021 Scottish Parliament Election", ElectionType.holyrood_general, "2021 Election"),
            ("2016 Scottish Parliament Election", ElectionType.holyrood_general, "2016 Election"),
            ("2026 Scottish Parliament Election", ElectionType.holyrood_general, "2026 Election"),
            (
                "2021 Scottish Parliament Election (2026 Boundaries)",
                ElectionType.holyrood_general,
                "2021 Election (2026 boundaries)",
            ),
        ],
    )
    def test_shortens_standard_names(
        self, name: str, election_type: ElectionType, expected: str
    ) -> None:
        assert manifest_name_for_election(_Election(name, election_type)) == expected

    def test_model_uns_is_current_prediction(self) -> None:
        assert (
            manifest_name_for_election(_Election("anything", ElectionType.model_uns))
            == "Current prediction"
        )

    def test_general_pattern_only_applies_to_uk_general(self) -> None:
        """A by-election whose name happens to match the general pattern is left verbatim."""
        assert (
            manifest_name_for_election(_Election("2021 General Election", ElectionType.by_election))
            == "2021 General Election"
        )

    def test_unmatched_name_passes_through_verbatim(self) -> None:
        assert (
            manifest_name_for_election(_Election("Some Odd Election", ElectionType.holyrood_general))
            == "Some Odd Election"
        )


class TestReorderManifestEntries:
    """Entries follow the curated manifest order; new ones slot next to their neighbour."""

    @staticmethod
    def _ids(entries: list[dict[str, Any]]) -> list[str]:
        return [e["id"] for e in entries]

    def test_empty_existing_order_keeps_built_order(self) -> None:
        built = [{"id": "a"}, {"id": "b"}]
        assert reorder_manifest_entries(built, []) == built

    def test_reorders_to_match_existing(self) -> None:
        built = [{"id": "c"}, {"id": "a"}, {"id": "b"}]
        out = reorder_manifest_entries(built, ["a", "b", "c"])
        assert self._ids(out) == ["a", "b", "c"]

    def test_new_entry_slots_after_its_comparer(self) -> None:
        # current-holyrood-prediction compares against the new 2026-holyrood, so 2026
        # slots immediately after the prediction (mirrors the real 2026 restoration).
        built = [
            {"id": "current-holyrood-prediction", "comparisonElectionId": "2026-holyrood"},
            {"id": "2021-holyrood-2026"},
            {"id": "2026-holyrood", "comparisonElectionId": "2021-holyrood"},
            {"id": "2021-holyrood"},
        ]
        existing = ["current-holyrood-prediction", "2021-holyrood-2026", "2021-holyrood"]
        out = reorder_manifest_entries(built, existing)
        assert self._ids(out) == [
            "current-holyrood-prediction",
            "2026-holyrood",
            "2021-holyrood-2026",
            "2021-holyrood",
        ]

    def test_new_entry_without_inbound_slots_before_its_comparison(self) -> None:
        built = [{"id": "old"}, {"id": "new", "comparisonElectionId": "old"}]
        out = reorder_manifest_entries(built, ["old"])
        assert self._ids(out) == ["new", "old"]

    def test_unanchored_new_entry_appended_at_end(self) -> None:
        built = [{"id": "a"}, {"id": "floating"}, {"id": "b"}]
        out = reorder_manifest_entries(built, ["a", "b"])
        assert self._ids(out) == ["a", "b", "floating"]


class TestAssignComparisonElections:
    """Comparisons follow same parliament + same boundaries (mapId), newest-first."""

    @staticmethod
    def _entry(
        eid: str,
        parliament: str,
        map_id: int,
        type: str = ElectionType.holyrood_general.value,
    ) -> dict[str, Any]:
        return {"id": eid, "type": type, "parliament": parliament, "mapId": map_id}

    def test_boundary_changed_election_compares_within_same_map(self) -> None:
        # 2026 (map 12) must compare against the map-12 baseline, not the map-11 2021.
        entries = [
            self._entry("2026-holyrood", "holyrood", 12),
            self._entry("2021-holyrood-2026", "holyrood", 12),
            self._entry("2021-holyrood", "holyrood", 11),
            self._entry("2016-holyrood", "holyrood", 11),
        ]
        assign_comparison_elections(entries)
        by_id = {e["id"]: e for e in entries}
        assert by_id["2026-holyrood"]["comparisonElectionId"] == "2021-holyrood-2026"
        assert by_id["2021-holyrood"]["comparisonElectionId"] == "2016-holyrood"
        # baseline / oldest on each map get no comparison
        assert "comparisonElectionId" not in by_id["2021-holyrood-2026"]
        assert "comparisonElectionId" not in by_id["2016-holyrood"]

    def test_general_does_not_compare_against_same_map_referendum(self) -> None:
        # The oldest general (map 1) must not grab the EU referendum (map 1, other type).
        entries = [
            self._entry("2015-general", "westminster", 1, ElectionType.uk_general.value),
            self._entry("2010-general", "westminster", 1, ElectionType.uk_general.value),
            {"id": "eu-referendum-2016", "type": "eu_referendum", "parliament": "westminster", "mapId": 1},
        ]
        assign_comparison_elections(entries)
        by_id = {e["id"]: e for e in entries}
        assert by_id["2015-general"]["comparisonElectionId"] == "2010-general"
        assert "comparisonElectionId" not in by_id["2010-general"]
        assert "comparisonElectionId" not in by_id["eu-referendum-2016"]

    def test_never_compares_across_parliaments(self) -> None:
        entries = [
            self._entry("2024-general", "westminster", 2, ElectionType.uk_general.value),
            self._entry("2021-holyrood", "holyrood", 11),
        ]
        assign_comparison_elections(entries)
        # different parliament AND map → no comparison either way
        assert "comparisonElectionId" not in entries[0]
        assert "comparisonElectionId" not in entries[1]

    def test_model_uns_compares_against_latest_general(self) -> None:
        entries = [
            self._entry("current-prediction", "westminster", 2, ElectionType.model_uns.value),
            self._entry("2024-general", "westminster", 2, ElectionType.uk_general.value),
            self._entry("2019-general", "westminster", 1, ElectionType.uk_general.value),
        ]
        assign_comparison_elections(entries)
        assert entries[0]["comparisonElectionId"] == "2024-general"

    def test_existing_comparison_is_not_overwritten(self) -> None:
        entries = [
            self._entry("2026-holyrood", "holyrood", 12),
            self._entry("2021-holyrood-2026", "holyrood", 12),
        ]
        entries[0]["comparisonElectionId"] = "already-set"
        assign_comparison_elections(entries)
        assert entries[0]["comparisonElectionId"] == "already-set"

    def test_second_pass_resolves_late_added_baseline(self) -> None:
        # Mimics the real flow: first pass runs without the map-12 baseline present,
        # so 2026 is left unresolved; a later pass with the baseline present fills it.
        entries = [self._entry("2026-holyrood", "holyrood", 12)]
        assign_comparison_elections(entries)
        assert "comparisonElectionId" not in entries[0]
        entries.append(self._entry("2021-holyrood-2026", "holyrood", 12))
        assign_comparison_elections(entries)
        assert entries[0]["comparisonElectionId"] == "2021-holyrood-2026"
