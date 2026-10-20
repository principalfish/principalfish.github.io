"""Tests for export_elections manifest-building helpers."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from db import Database
from model_support.persistence import (
    OutputVote,
    StoredUncertainty,
    load_uncertainty,
    replace_output,
    westminster_uns_scope,
)
from model_support.public_uncertainty import (
    PublicUncertaintyError,
    public_document,
    seat_bands,
)
from models import Election, ElectionType, Party, Seat
import export_elections
from export_elections import (
    DEM_PARTY_ID,
    REP_PARTY_ID,
    _export_page,
    assign_comparison_elections,
    build_map_modes_with_regions,
    manifest_name_for_election,
    refresh_manifest_modes,
)
from scripts.export.ordering import float_model_entries_first, reorder_manifest_entries
from tests.export_golden import (
    GOLDEN_PATH,
    SHELL,
    read_files,
    run_export,
    seed_model_runs,
)
from tests.test_westminster_uncertainty_integration import DAY, _cfg, _poll
from tests.uk_fixtures import WestminsterWorld
from tests.us_ev_fixtures import seed_canonical_allocations
import run_uns_model as runner


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

    def test_senate_never_auto_compares_staggered_cycles(self) -> None:
        # The Senate's staggered classes contest different states each cycle, so no Senate
        # election chains to the previous one — even though they share parliament/mapId/type.
        entries = [
            self._entry("2024-us-senate", "us_senate", 23, ElectionType.us_senate.value),
            self._entry("2022-us-senate", "us_senate", 23, ElectionType.us_senate.value),
            self._entry("2020-us-senate", "us_senate", 23, ElectionType.us_senate.value),
        ]
        assign_comparison_elections(entries)
        assert all("comparisonElectionId" not in e for e in entries)

    def test_house_still_chains_alongside_senate(self) -> None:
        # The Senate exclusion must not affect other US contests: House still chains newest→prev.
        entries = [
            self._entry("2024-us-house", "us_house", 21, ElectionType.us_house.value),
            self._entry("2022-us-house", "us_house", 21, ElectionType.us_house.value),
            self._entry("2024-us-senate", "us_senate", 23, ElectionType.us_senate.value),
        ]
        assign_comparison_elections(entries)
        by_id = {e["id"]: e for e in entries}
        assert by_id["2024-us-house"]["comparisonElectionId"] == "2022-us-house"
        assert "comparisonElectionId" not in by_id["2022-us-house"]
        assert "comparisonElectionId" not in by_id["2024-us-senate"]

    def test_us_forecasts_compare_against_their_model_baselines(self) -> None:
        # Each forecast reads gains against the baseline its model swings from —
        # the Senate forecast against the 2020 Class-2 result, not 2024.
        entries = [
            self._entry("current-us-house", "us_house", 21, ElectionType.us_house_model.value),
            self._entry("current-us-president", "us_presidential", 22, ElectionType.us_presidential_model.value),
            self._entry("current-us-senate", "us_senate", 23, ElectionType.us_senate_model.value),
            self._entry("2024-us-house", "us_house", 21, ElectionType.us_house.value),
            self._entry("2024-us-president", "us_presidential", 22, ElectionType.us_presidential.value),
            self._entry("2024-us-senate", "us_senate", 23, ElectionType.us_senate.value),
            self._entry("2020-us-senate", "us_senate", 23, ElectionType.us_senate.value),
        ]
        assign_comparison_elections(entries)
        by_id = {e["id"]: e for e in entries}
        assert by_id["current-us-house"]["comparisonElectionId"] == "2024-us-house"
        assert by_id["current-us-president"]["comparisonElectionId"] == "2024-us-president"
        assert by_id["current-us-senate"]["comparisonElectionId"] == "2020-us-senate"

    def test_us_forecast_comparison_skipped_when_baseline_absent(self) -> None:
        # No dangling comparisonElectionId when the baseline isn't exported.
        entries = [
            self._entry("current-us-senate", "us_senate", 23, ElectionType.us_senate_model.value),
            self._entry("2024-us-senate", "us_senate", 23, ElectionType.us_senate.value),
        ]
        assign_comparison_elections(entries)
        assert "comparisonElectionId" not in entries[0]


class TestFloatModelEntriesFirst:
    """Forecast entries lead their parliament's block; everything else keeps its order."""

    @staticmethod
    def _entry(eid: str, parliament: str, model: bool = False) -> dict[str, Any]:
        entry: dict[str, Any] = {"id": eid, "parliament": parliament}
        if model:
            entry["model"] = True
        return entry

    def test_moves_trailing_forecasts_to_block_front(self) -> None:
        # Mirrors the real US page: forecasts first shipped appended at the end.
        entries = [
            self._entry("2024-us-house", "us_house"),
            self._entry("2022-us-house", "us_house"),
            self._entry("2024-us-president", "us_presidential"),
            self._entry("current-senate", "us_senate"),
            self._entry("2024-us-senate", "us_senate"),
            self._entry("current-us-house", "us_house", model=True),
            self._entry("current-us-president", "us_presidential", model=True),
            self._entry("current-us-senate", "us_senate", model=True),
        ]
        float_model_entries_first(entries)
        assert [e["id"] for e in entries] == [
            "current-us-house",
            "2024-us-house",
            "2022-us-house",
            "current-us-president",
            "2024-us-president",
            "current-us-senate",
            "current-senate",
            "2024-us-senate",
        ]

    def test_noop_when_forecasts_already_lead(self) -> None:
        # The UK pages already lead with their prediction entries — order untouched.
        entries = [
            self._entry("current-prediction", "westminster", model=True),
            self._entry("current-parliament", "westminster"),
            self._entry("2024-general", "westminster"),
            self._entry("current-holyrood-prediction", "holyrood", model=True),
            self._entry("2026-holyrood", "holyrood"),
        ]
        before = [e["id"] for e in entries]
        float_model_entries_first(entries)
        assert [e["id"] for e in entries] == before

    def test_noop_without_model_entries(self) -> None:
        entries = [
            self._entry("2024-us-house", "us_house"),
            self._entry("2024-us-president", "us_presidential"),
        ]
        before = [e["id"] for e in entries]
        float_model_entries_first(entries)
        assert [e["id"] for e in entries] == before


class TestRefreshManifestModes:
    """``--metadata-only`` refreshes the derived mapMode keys and nothing else."""

    @staticmethod
    def _manifest() -> dict[str, Any]:
        return {
            "mapModes": {
                "23": {
                    "name": "us-senate-2024",
                    "projection": "albersUsa",
                    "regions": [{"id": 1, "name": "Stale"}],
                    "senateClassNextElection": {"2": 2020},
                    "senateSpecialElections": [{"seat": "Ohio", "class": 3, "year": 2020}],
                },
            },
        }

    def test_replaces_regions_and_derived_keys_but_keeps_config(self) -> None:
        manifest = self._manifest()
        specials = [{"seat": "Ohio", "class": 3, "year": 2026, "baselineElectionId": "2022-us-senate"}]
        rebuilt = {
            "23": {
                "regions": [{"id": 1, "name": "East North Central"}],
                "senateClassNextElection": {"2": 2026},
                "senateSpecialElections": specials,
            },
        }
        refresh_manifest_modes(manifest, rebuilt, {"23": [{"id": 1, "name": "East North Central"}]})
        mode = manifest["mapModes"]["23"]
        assert mode["regions"] == [{"id": 1, "name": "East North Central"}]
        assert mode["senateClassNextElection"] == {"2": 2026}
        assert mode["senateSpecialElections"] == specials
        # Hand-authored config is left alone.
        assert mode["projection"] == "albersUsa"

    def test_drops_a_derived_key_the_rebuild_no_longer_produces(self) -> None:
        # The shell dropped the specials (or they expired) — the manifest must follow, or the
        # front end keeps projecting a contest that is over.
        manifest = self._manifest()
        rebuilt = {"23": {"regions": [], "senateClassNextElection": {"2": 2026}}}
        refresh_manifest_modes(manifest, rebuilt, {})
        assert "senateSpecialElections" not in manifest["mapModes"]["23"]
        assert manifest["mapModes"]["23"]["senateClassNextElection"] == {"2": 2026}

    def test_refreshes_specials_through_the_same_live_rule_as_a_full_export(self) -> None:
        # The metadata-only path rebuilds its modes from the shell exactly as a full export
        # does, so a later-cycle special and a malformed entry drop out here too.
        valid = [
            {"seat": "Florida", "class": 3, "year": 2026, "baselineElectionId": "2022-us-senate"},
            {"seat": "Ohio", "class": 3, "year": 2026, "baselineElectionId": "2022-us-senate"},
        ]
        shell = {
            "parliamentFeatures": {"us_senate": {"nextElectionYear": 2026}},
            "mapModes": {
                "23": {
                    "senateSpecialElections": [
                        valid[0],
                        {"seat": "Texas", "class": 1, "year": 2028, "baselineElectionId": "x"},
                        {"seat": "Maine", "class": 2, "year": "2026", "baselineElectionId": "x"},
                        valid[1],
                    ],
                },
            },
        }
        rebuilt = build_map_modes_with_regions(
            shell["mapModes"],
            {"23": []},
            current_year=2026,
            parliament_features=shell["parliamentFeatures"],
        )
        manifest = self._manifest()
        refresh_manifest_modes(manifest, rebuilt, {"23": []})
        assert manifest["mapModes"]["23"]["senateSpecialElections"] == valid

    def test_falls_back_to_raw_db_regions_without_a_rebuilt_mode(self) -> None:
        manifest = self._manifest()
        refresh_manifest_modes(manifest, {}, {"23": [{"id": 9, "name": "Pacific"}]})
        mode = manifest["mapModes"]["23"]
        assert mode["regions"] == [{"id": 9, "name": "Pacific"}]
        # With no shell to rebuild from, the derived keys are left as they are rather than wiped.
        assert mode["senateSpecialElections"] == [{"seat": "Ohio", "class": 3, "year": 2020}]


class TestMissingPrebuiltMapFails:
    """A full export must fail, not warn, when a referenced pre-built map is absent.

    Non-Westminster maps (Holyrood, US) ship a pre-built TopoJSON produced by a separate
    build step; exporting without it used to emit a manifest pointing at a 404 map.
    """

    def test_export_raises_when_prebuilt_map_missing(self, db: Database, tmp_path: Path) -> None:
        us_map = db.add_map("us-house-districts", parliament="us_house")
        region = db.add_region(us_map.id, "South Atlantic")
        db.add_seat(us_map.id, "GA-01", region_id=region.id)
        db.add_election(us_map.id, 2024, "2024 US House Election", ElectionType.us_house)

        output_root = tmp_path / "uselectionmaps" / "data"
        args = argparse.Namespace(dry_run=False, output_file=None, legacy_files_dir=tmp_path)
        with db.session() as session:
            election = session.execute(
                select(Election).options(joinedload(Election.map))
            ).scalars().one()
            with pytest.raises(FileNotFoundError, match=r"map-\d+\.topo\.json"):
                _export_page(
                    session=session,
                    elections=[election],
                    output_root=output_root,
                    parliaments={"us_house"},
                    args=args,
                    manifest_parties=[],
                    manifest_regions_by_map_id={},
                    has_electorate=True,
                    single_election_mode=False,
                )
        # The failure must happen before any results are written for the broken map.
        assert not (output_root / "results").exists()


class TestStateTrendsExport:
    """A full presidential export accumulates a per-state two-party trend artifact.

    Each us_presidential seat's Dem/Rep totals become a per-year two-party share, and a
    full (non-dry-run) export writes results/us-president-trends-by-state.json with the
    per-unit series sorted oldest-first regardless of the input election order.
    """

    # Two-party splits per unit (dem, rep) — chosen so shares are exact 2dp values.
    _UNITS: dict[str, tuple[int, int]] = {
        "California": (60, 40),
        "Texas": (45, 55),
        "Maine CD-1": (64, 36),
    }

    @classmethod
    def _seed_presidential_page(cls, db: Database, tmp_path: Path) -> Path:
        """Seed a us_presidential map (3 units, 2020 + 2024 elections) and the pre-built map.

        Returns the page output root; the dummy TopoJSON is pre-created so the export does
        not raise FileNotFoundError on the pre-built-map check.
        """
        seed_canonical_allocations(db)
        us_map = db.add_map("us-president", parliament="us_presidential")
        with db.session() as s:
            s.add(Party(id=DEM_PARTY_ID, name="Democratic", colour="#1375E1"))
            s.add(Party(id=REP_PARTY_ID, name="Republican", colour="#E81B23"))

        region = db.add_region(us_map.id, "United States")
        seat_ids: dict[str, int] = {}
        for name in cls._UNITS:
            seat = db.add_seat(us_map.id, name, region_id=region.id, electoral_votes=3)
            seat_ids[name] = seat.id

        # Add 2020 and 2024; the export loads them 2024-first (descending) to prove the sort.
        for year in (2020, 2024):
            election = db.add_election(
                us_map.id, year, f"{year} US Presidential Election", ElectionType.us_presidential
            )
            for name, (dem, rep) in cls._UNITS.items():
                db.add_vote(election.id, seat_ids[name], party_id=DEM_PARTY_ID, vote_total=dem)
                db.add_vote(election.id, seat_ids[name], party_id=REP_PARTY_ID, vote_total=rep)

        output_root = tmp_path / "uselectionmaps" / "data"
        (output_root / "maps").mkdir(parents=True)
        (output_root / "maps" / f"map-{us_map.id}.topo.json").write_text("{}", encoding="utf-8")
        return output_root

    def test_full_export_writes_sorted_trend_artifact(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed_presidential_page(db, tmp_path)
        args = argparse.Namespace(dry_run=False, output_file=None, legacy_files_dir=tmp_path)
        with db.session() as session:
            # DESCENDING year order (2024 before 2020) — the artifact must still sort ascending.
            elections = session.execute(
                select(Election)
                .where(Election.type == ElectionType.us_presidential)
                .options(joinedload(Election.map))
                .order_by(Election.year.desc())
            ).scalars().all()
            _export_page(
                session=session,
                elections=list(elections),
                output_root=output_root,
                parliaments={"us_presidential"},
                args=args,
                manifest_parties=[],
                manifest_regions_by_map_id={},
                has_electorate=True,
                single_election_mode=False,
            )

        trends_path = output_root / "results" / "us-president-trends-by-state.json"
        assert trends_path.exists()
        data = json.loads(trends_path.read_text(encoding="utf-8"))
        assert data["schema"] == "pf-state-trends-v1"

        units = data["units"]
        assert set(units.keys()) == set(self._UNITS.keys())
        for series in units.values():
            assert [entry["year"] for entry in series] == [2020, 2024]  # ascending despite reversed input
            for entry in series:
                assert isinstance(entry["year"], int)  # JSON 2020, not 2020.0
                assert entry["dem"] + entry["rep"] == pytest.approx(100.0)

        california = units["California"]
        assert california[0]["dem"] == 60.0
        assert california[0]["rep"] == 40.0

    def test_dry_run_writes_no_trend_artifact(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed_presidential_page(db, tmp_path)
        args = argparse.Namespace(dry_run=True, output_file=None, legacy_files_dir=tmp_path)
        with db.session() as session:
            elections = session.execute(
                select(Election)
                .where(Election.type == ElectionType.us_presidential)
                .options(joinedload(Election.map))
                .order_by(Election.year.desc())
            ).scalars().all()
            _export_page(
                session=session,
                elections=list(elections),
                output_root=output_root,
                parliaments={"us_presidential"},
                args=args,
                manifest_parties=[],
                manifest_regions_by_map_id={},
                has_electorate=True,
                single_election_mode=False,
            )

        assert not (output_root / "results" / "us-president-trends-by-state.json").exists()


class TestStateTrendsThirdParty:
    """The two-party trend normalises over Dem+Rep only, even where a third party wins.

    Covers the 1964–1996 additions: a state a third party carried (Wallace 1968) still yields
    a valid Dem-vs-Rep point, and a unit with no Dem/Rep vote is dropped by the guard.
    """

    _IND_PARTY_ID = 22  # any non-Dem/Rep id; the trend counts only DEM/REP totals.

    @classmethod
    def _seed(cls, db: Database, tmp_path: Path) -> Path:
        seed_canonical_allocations(db)
        us_map = db.add_map("us-president", parliament="us_presidential")
        with db.session() as s:
            s.add(Party(id=DEM_PARTY_ID, name="Democratic", colour="#1375E1"))
            s.add(Party(id=REP_PARTY_ID, name="Republican", colour="#E81B23"))
            s.add(Party(id=cls._IND_PARTY_ID, name="Independent", colour="#9B59B6"))
        region = db.add_region(us_map.id, "United States")
        seats = {name: db.add_seat(us_map.id, name, region_id=region.id, electoral_votes=7).id
                 for name in ("Mississippi", "Alabama")}
        election = db.add_election(
            us_map.id, 1968, "1968 US Presidential Election", ElectionType.us_presidential
        )
        # Mississippi: Wallace wins on votes (45) but Dem 30 / Rep 25 give a real two-party split.
        db.add_vote(election.id, seats["Mississippi"], party_id=DEM_PARTY_ID, vote_total=30)
        db.add_vote(election.id, seats["Mississippi"], party_id=REP_PARTY_ID, vote_total=25)
        db.add_vote(election.id, seats["Mississippi"], party_id=cls._IND_PARTY_ID, vote_total=45)
        # Alabama: only an Independent vote — no Dem/Rep, so two_party == 0 (synthetic edge).
        db.add_vote(election.id, seats["Alabama"], party_id=cls._IND_PARTY_ID, vote_total=100)

        output_root = tmp_path / "uselectionmaps" / "data"
        (output_root / "maps").mkdir(parents=True)
        (output_root / "maps" / f"map-{us_map.id}.topo.json").write_text("{}", encoding="utf-8")
        return output_root

    def _run_export(self, db: Database, output_root: Path, tmp_path: Path) -> dict[str, list[dict[str, Any]]]:
        args = argparse.Namespace(dry_run=False, output_file=None, legacy_files_dir=tmp_path)
        with db.session() as session:
            elections = session.execute(
                select(Election)
                .where(Election.type == ElectionType.us_presidential)
                .options(joinedload(Election.map))
            ).scalars().all()
            _export_page(
                session=session,
                elections=list(elections),
                output_root=output_root,
                parliaments={"us_presidential"},
                args=args,
                manifest_parties=[],
                manifest_regions_by_map_id={},
                has_electorate=True,
                single_election_mode=False,
            )
        trends_path = output_root / "results" / "us-president-trends-by-state.json"
        units: dict[str, list[dict[str, Any]]] = json.loads(trends_path.read_text(encoding="utf-8"))["units"]
        return units

    def test_third_party_state_normalises_over_two_party(self, db: Database, tmp_path: Path) -> None:
        units = self._run_export(db, self._seed(db, tmp_path), tmp_path)
        entry = units["Mississippi"][0]
        # Wallace's 45 is excluded; the point is Dem/Rep share of Dem+Rep only (30+25=55).
        assert entry == {"year": 1968, "dem": pytest.approx(54.55, abs=0.01),
                         "rep": pytest.approx(45.45, abs=0.01)}
        assert entry["dem"] + entry["rep"] == pytest.approx(100.0)
        assert "independent" not in entry

    def test_zero_two_party_unit_is_skipped(self, db: Database, tmp_path: Path) -> None:
        units = self._run_export(db, self._seed(db, tmp_path), tmp_path)
        assert "Mississippi" in units  # export ran and produced a series...
        assert "Alabama" not in units  # ...but the no-Dem/Rep unit is dropped by the guard


# ── Model-run uncertainty ────────────────────────────────────────────────────


@pytest.fixture()
def model_run(
    db: Database,
    westminster_world: WestminsterWorld,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> StoredUncertainty:
    """A real 50-simulation UNS run on the Westminster world, stored in full."""
    draws = tmp_path / "draws.npz"
    monkeypatch.setenv("SIMULATIONS_PATH", str(draws))
    monkeypatch.setattr(db.config, "simulations_path", str(draws))
    monkeypatch.setattr(runner, "TREND_CACHE_JSON", tmp_path / "trend.json")
    monkeypatch.setattr(runner, "TREND_CACHE_META_JSON", tmp_path / "meta.json")
    _poll(db, westminster_world)
    cfg = _cfg(westminster_world)
    cfg = replace(
        cfg, uncertainty=replace(cfg.uncertainty, simulations=50, chunk_size=7)
    )
    runner.run_simulation(db, cfg)
    election = db.get_election_by_name(f"UNS {DAY}")
    assert election is not None
    stored = load_uncertainty(Path(db.config.database_path), election.id)
    assert stored is not None
    assert stored.seats is not None and stored.regions is not None
    return stored


SHELL_WITH_FLAG = {
    **SHELL,
    "parliamentFeatures": {
        "westminster": {
            **SHELL["parliamentFeatures"]["westminster"],
            # Not the committed value, so the test sees it copied, not made up.
            "uncertaintyDataPath": "results/flag-under-test.json",
        }
    },
}

PRIVATE_KEYS = ('"budget"', '"input_hash"', '"config_hash"', '"seed"')


class TestModelRunUncertaintyExport:
    """A model run with full detail publishes ``u`` rows and the uncertainty file."""

    def test_full_export_publishes_the_public_subset(
        self,
        db: Database,
        model_run: StoredUncertainty,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = run_export(db, tmp_path, [], monkeypatch, shell=SHELL_WITH_FLAG)
        files = result["files"]
        document = json.loads(files["results/prediction-uncertainty.json"])
        assert document == public_document(
            model_run.national,
            model_run.regions or {},
            election_name=f"UNS {DAY}",
            as_of_date=DAY.isoformat(),
        )
        assert document["schema"] == "pf-uncertainty-v1"
        assert document["simulations"] == 50
        assert "50 simulations" in document["basis"]
        stored_parties: Any = model_run.national["parties"]
        assert set(document["national"]["parties"]) == set(stored_parties)
        for text in (files["results/prediction-uncertainty.json"],
                     files["results/prediction-simulation.json"]):
            for private in PRIVATE_KEYS:
                assert private not in text
        manifest = json.loads(files["map-modes.json"])
        assert manifest["parliamentFeatures"]["westminster"][
            "uncertaintyDataPath"
        ] == "results/flag-under-test.json"
        assert result["stdout"].splitlines() == [
            "Wrote uncertainty: <root>/out/results/prediction-uncertainty.json "
            f"({len(document['regions'])} regions)",
            "Wrote manifest: <root>/out/map-modes.json (3 elections)",
        ]

    def test_seat_rows_align_to_p(
        self,
        db: Database,
        model_run: StoredUncertainty,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        files = run_export(db, tmp_path, [], monkeypatch)["files"]
        seats = json.loads(files["results/prediction-simulation.json"])["seats"]
        bands = seat_bands(model_run.seats or {})
        ids = _seat_ids(db)
        assert len(seats) == 4
        for seat in seats:
            stored = bands[ids[seat["n"]]]
            expected = [
                [row[0], stored[row[0]].lo, stored[row[0]].hi, stored[row[0]].win]
                for row in seat["p"]
                if row[0] in stored
            ]
            assert expected and seat["u"] == expected
        # The baseline general election gets none.
        general = json.loads(files["results/uk-general-2024.json"])["seats"]
        assert all("u" not in seat for seat in general)

    def test_dry_run_reports_and_writes_nothing(
        self,
        db: Database,
        model_run: StoredUncertainty,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = run_export(db, tmp_path, ["--dry-run"], monkeypatch)
        assert (
            "Would write uncertainty: <root>/out/results/prediction-uncertainty.json "
            f"({len(model_run.regions or {})} regions)"
        ) in result["stdout"].splitlines()
        assert list(result["files"]) == ["map-modes-shell.json"]

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_stale_file_removed_when_the_latest_run_has_no_detail(
        self,
        db: Database,
        model_run: StoredUncertainty,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dry_run: bool,
    ) -> None:
        # A later run without a summary: the export now ships it, and the
        # earlier run's detail is pruned.
        later = DAY + timedelta(days=1)
        seat_id = next(iter(westminster_world.seat_ids.values()))
        replace_output(
            Path(db.config.database_path),
            westminster_uns_scope(westminster_world.map_id),
            later,
            f"UNS {later}",
            [OutputVote(seat_id, westminster_world.party_ids["Labour"], "", 9.0, True)],
        )
        stale = tmp_path / "out" / "results" / "prediction-uncertainty.json"
        stale.parent.mkdir(parents=True)
        stale.write_text('{"stale": true}', encoding="utf-8")

        result = run_export(
            db, tmp_path, ["--dry-run"] if dry_run else [], monkeypatch
        )
        lines = result["stdout"].splitlines()
        if dry_run:
            assert (
                "Would remove stale uncertainty file: "
                "<root>/out/results/prediction-uncertainty.json"
            ) in lines
            assert stale.read_text(encoding="utf-8") == '{"stale": true}'
        else:
            assert (
                "Removed stale uncertainty file: "
                "<root>/out/results/prediction-uncertainty.json"
            ) in lines
            assert not stale.exists()
            seats = json.loads(result["files"]["results/prediction-simulation.json"])
            assert all("u" not in seat for seat in seats["seats"])

    def test_current_simulation_writes_both_files(
        self,
        db: Database,
        model_run: StoredUncertainty,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out = tmp_path / "out"
        files = run_export(
            db, tmp_path, ["--current-simulation", "--output-root", str(out)],
            monkeypatch,
        )["files"]
        assert json.loads(files["results/prediction-uncertainty.json"])[
            "election_name"
        ] == f"UNS {DAY}"
        seats = json.loads(files["results/prediction-simulation.json"])["seats"]
        assert all("u" in seat for seat in seats)

    def test_output_file_carries_u_but_writes_no_uncertainty_file(
        self,
        db: Database,
        model_run: StoredUncertainty,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "single.json"
        result = run_export(
            db, tmp_path, ["--current-simulation", "--output-file", str(target)],
            monkeypatch,
        )
        assert all("u" in seat for seat in json.loads(target.read_text())["seats"])
        assert "results/prediction-uncertainty.json" not in result["files"]

    def test_another_runs_summary_is_refused(
        self,
        db: Database,
        model_run: StoredUncertainty,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stranger = replace(model_run, election_id=model_run.election_id + 1)
        monkeypatch.setattr(export_elections, "load_uncertainty", lambda *_: stranger)
        with pytest.raises(ValueError, match="given for"):
            run_export(db, tmp_path, [], monkeypatch)
        assert not (tmp_path / "out" / "results").exists()

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_rerunning_an_older_date_keeps_the_latest_as_the_prediction(
        self,
        db: Database,
        model_run: StoredUncertainty,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dry_run: bool,
    ) -> None:
        older_id = _rerun_older_date(db, westminster_world)
        assert older_id > model_run.election_id
        published = tmp_path / "out" / "results" / "prediction-uncertainty.json"
        published.parent.mkdir(parents=True)
        published.write_text('{"earlier": true}', encoding="utf-8")

        result = run_export(
            db, tmp_path, ["--dry-run"] if dry_run else [], monkeypatch
        )
        assert "stale" not in result["stdout"]
        if dry_run:
            assert any(
                line.startswith("Would write uncertainty:")
                for line in result["stdout"].splitlines()
            )
            return
        document = json.loads(result["files"]["results/prediction-uncertainty.json"])
        assert document["election_name"] == f"UNS {DAY}"
        seats = json.loads(result["files"]["results/prediction-simulation.json"])
        assert all("u" in seat for seat in seats["seats"])

    def test_current_simulation_ships_the_latest_date_after_an_older_rerun(
        self,
        db: Database,
        model_run: StoredUncertainty,
        westminster_world: WestminsterWorld,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _rerun_older_date(db, westminster_world)
        out = tmp_path / "out"
        result = run_export(
            db, tmp_path, ["--current-simulation", "--output-root", str(out)],
            monkeypatch,
        )
        assert f"from election 'UNS {DAY}'" in result["stdout"]
        assert "stale" not in result["stdout"]
        document = json.loads(result["files"]["results/prediction-uncertainty.json"])
        assert document["election_name"] == f"UNS {DAY}"
        seats = json.loads(result["files"]["results/prediction-simulation.json"])
        assert all("u" in seat for seat in seats["seats"])

    def test_malformed_summary_fails_before_any_result_is_written(
        self,
        db: Database,
        model_run: StoredUncertainty,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        national = dict(model_run.national)
        national["hung"] = 2.0
        with closing(sqlite3.connect(db.config.database_path)) as conn:
            conn.execute(
                "UPDATE model_uncertainty SET national_json = ? WHERE election_id = ?",
                (json.dumps(national), model_run.election_id),
            )
            conn.commit()
        with pytest.raises(PublicUncertaintyError, match="national.hung"):
            run_export(db, tmp_path, [], monkeypatch)
        assert not (tmp_path / "out" / "results").exists()


def _rerun_older_date(db: Database, world: WestminsterWorld) -> int:
    """Save a run one day before ``DAY`` after it: a higher id, an earlier date."""
    older = DAY - timedelta(days=1)
    seat_id = next(iter(world.seat_ids.values()))
    _, election_id = replace_output(
        Path(db.config.database_path),
        westminster_uns_scope(world.map_id),
        older,
        f"UNS {older}",
        [OutputVote(seat_id, world.party_ids["Labour"], "", 9.0, True)],
    )
    return election_id


def _seat_ids(db: Database) -> dict[str, int]:
    with db.session() as session:
        return {
            seat.seat_name: seat.id for seat in session.execute(select(Seat)).scalars()
        }


# ── Scratch exports: --output-base / --output-root ───────────────────────────


@pytest.fixture()
def golden_world(db: Database, westminster_world: WestminsterWorld) -> Database:
    seed_model_runs(db, westminster_world)
    return db


def _golden() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return loaded


class TestOutputBase:
    """``--output-base`` writes every page under the base and never the repo."""

    def test_full_export_writes_only_under_the_base(
        self, golden_world: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "base"
        result = run_export(
            golden_world, tmp_path, ["--output-base", str(base)], monkeypatch
        )
        # The "repository" page root (run_export's root/out) holds only its shell.
        assert list(result["files"]) == ["map-modes-shell.json"]
        assert json.loads(result["files"]["map-modes-shell.json"]) == SHELL
        # The base holds exactly what a normal export writes there.
        page = base / "electionmaps" / "data"
        assert read_files(page) == _golden()["full"]["files"]
        lines = result["stdout"].splitlines()
        assert lines[0] == (
            "Seeded scratch page: <root>/out -> <root>/base/electionmaps/data"
        )
        assert "\n".join(lines[1:]) + "\n" == _golden()["full"]["stdout"].replace(
            "<root>/out", "<root>/base/electionmaps/data"
        )

    def test_existing_base_is_reused_not_reseeded(
        self, golden_world: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "base"
        run_export(golden_world, tmp_path, ["--output-base", str(base)], monkeypatch)
        marker = base / "electionmaps" / "data" / "results" / "kept.json"
        marker.write_text("{}", encoding="utf-8")
        again = run_export(
            golden_world, tmp_path, ["--output-base", str(base)], monkeypatch
        )
        assert "Seeded" not in again["stdout"]
        assert marker.read_text(encoding="utf-8") == "{}"

    def test_dry_run_seeds_the_base_and_writes_no_outputs(
        self, golden_world: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "base"
        result = run_export(
            golden_world, tmp_path, ["--output-base", str(base), "--dry-run"],
            monkeypatch,
        )
        assert list(result["files"]) == ["map-modes-shell.json"]
        assert read_files(base / "electionmaps" / "data") == {
            "map-modes-shell.json": json.dumps(SHELL)
        }
        assert "Would write manifest: <root>/base/electionmaps/data/map-modes.json" in (
            result["stdout"]
        )

    def test_metadata_only_honours_the_base(
        self, golden_world: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "base"
        run_export(golden_world, tmp_path, ["--output-base", str(base)], monkeypatch)
        result = run_export(
            golden_world, tmp_path, ["--output-base", str(base), "--metadata-only"],
            monkeypatch,
        )
        assert result["stdout"].splitlines() == [
            "Wrote manifest metadata: <root>/base/electionmaps/data/map-modes.json"
        ]
        assert list(result["files"]) == ["map-modes-shell.json"]

    def test_current_simulation_honours_the_base(
        self, golden_world: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "base"
        result = run_export(
            golden_world,
            tmp_path,
            ["--current-simulation", "--output-base", str(base)],
            monkeypatch,
        )
        assert list(result["files"]) == ["map-modes-shell.json"]
        written = (
            base / "electionmaps" / "data" / "results" / "prediction-simulation.json"
        )
        assert written.read_text(encoding="utf-8") == _golden()["current_simulation"][
            "files"
        ]["results/prediction-simulation.json"]

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (
                ["--output-root", "x"],
                "--output-root applies only to --current-simulation",
            ),
            (["--metadata-only", "--output-root", "x"], "use --output-base"),
            (
                ["--current-simulation", "--output-root", "x", "--output-base", "y"],
                "not allowed with argument",
            ),
        ],
    )
    def test_misused_output_flags_are_refused(
        self,
        golden_world: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        argv: list[str],
        message: str,
    ) -> None:
        # The placeholders become scratch paths, so nothing could land in data/.
        argv = [str(tmp_path / arg) if arg in ("x", "y") else arg for arg in argv]
        with pytest.raises(SystemExit):
            run_export(golden_world, tmp_path, argv, monkeypatch)
        assert message in capsys.readouterr().err
        assert not (tmp_path / "x").exists() and not (tmp_path / "y").exists()
        assert not (tmp_path / "out" / "results").exists()
