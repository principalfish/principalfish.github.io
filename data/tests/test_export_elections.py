"""Tests for export_elections manifest-building helpers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from models import ElectionType
from export_elections import manifest_name_for_election, reorder_manifest_entries


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
    def _ids(entries: list[dict]) -> list[str]:
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
