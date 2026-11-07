"""Tests for scripts.export.manifest — mapMode region/override/Senate-cycle building.

Covers the region attachment, ``regionNameOverride`` application (and stripping), and
the durable ``senateClassCycle`` → concrete ``senateClassNextElection`` resolution that
the full export and the ``--metadata-only`` refresh both rely on. These are pure
functions. ``TestUsResultsExport`` runs a whole US page export on a temp DB to cover
the manifest entry's ``sourceNote`` and the folding of Senate specials.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload

import export_elections
from db import Database, SeatStatus
from export_elections import DEM_PARTY_ID, REP_PARTY_ID, export_pages, missing_export_inputs
from models import Election, ElectionType, Party
from scripts.export.manifest import (
    _senate_class_next_election,
    assign_comparison_elections,
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


class TestSenateClassFloor:
    """A class that has just voted must not read as "up this year"."""

    CYCLE = {"base": {"1": 2018, "2": 2020, "3": 2022}, "period": 6}

    @staticmethod
    def _next_up(features: dict[str, Any], *, current_year: int | None = None) -> Any:
        modes = {"23": {"senateClassCycle": TestSenateClassFloor.CYCLE}}
        return build_map_modes_with_regions(
            modes, {"23": []}, current_year, parliament_features=features
        )["23"]["senateClassNextElection"]

    def test_the_2026_export_rolls_class_2_on_to_2032(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.export.manifest as manifest_module

        class FakeDate:
            @staticmethod
            def today() -> Any:
                return SimpleNamespace(year=2026)

        monkeypatch.setattr(manifest_module, "date", FakeDate)
        # Wall-clock year 2026 with the Senate's next election in 2028: Class 2 voted in 2026.
        assert self._next_up({"us_senate": {"nextElectionYear": 2028}}) == {"1": 2030, "2": 2032, "3": 2028}

    def test_before_the_election_class_2_is_still_up_in_2026(self) -> None:
        assert self._next_up({"us_senate": {"nextElectionYear": 2026}}, current_year=2025) == {
            "1": 2030, "2": 2026, "3": 2028,
        }

    def test_a_stale_next_election_year_still_rolls_forward(self) -> None:
        assert self._next_up({"us_senate": {"nextElectionYear": 2026}}, current_year=2027) == {
            "1": 2030, "2": 2032, "3": 2028,
        }

    def test_no_next_election_year_uses_the_export_year(self) -> None:
        assert self._next_up({}, current_year=2026) == {"1": 2030, "2": 2026, "3": 2028}


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


class TestSenateBaselineOverrides:
    """Per-seat baseline overrides pass through the export, cleaned but never expired."""

    OVERRIDES = [
        {"seat": "Florida", "class": 3, "baselineElectionId": "2026-us-senate"},
        {"seat": "Ohio", "class": 3, "baselineElectionId": "2026-us-senate"},
    ]
    FEATURES_2028 = {"us_senate": {"nextElectionYear": 2028}}

    @staticmethod
    def _built(overrides: object, *, current_year: int = 2026, features: dict[str, Any] | None = None) -> Any:
        result = build_map_modes_with_regions(
            {"23": {"senateBaselineOverrides": overrides}},
            {"23": []},
            current_year=current_year,
            parliament_features=TestSenateBaselineOverrides.FEATURES_2028 if features is None else features,
        )
        return result["23"]["senateBaselineOverrides"]

    def test_valid_overrides_pass_through_unchanged(self) -> None:
        assert self._built(self.OVERRIDES) == self.OVERRIDES

    def test_they_are_not_year_gated(self) -> None:
        # Unlike specials: no nextElectionYear, a passed one, or a distant one drops them.
        assert self._built(self.OVERRIDES, features={}) == self.OVERRIDES
        assert self._built(self.OVERRIDES, current_year=2031) == self.OVERRIDES
        assert self._built(self.OVERRIDES, features={"us_senate": {"nextElectionYear": 2040}}) == self.OVERRIDES

    def test_malformed_records_are_dropped(self) -> None:
        malformed = [
            {"class": 3, "baselineElectionId": "2026-us-senate"},  # no seat
            {"seat": "  ", "class": 3, "baselineElectionId": "2026-us-senate"},
            {"seat": 7, "class": 3, "baselineElectionId": "2026-us-senate"},
            {"seat": "Texas", "class": 3},  # no baseline
            {"seat": "Texas", "class": 3, "baselineElectionId": "   "},
            {"seat": "Texas", "class": 3, "baselineElectionId": None},
            {"seat": "Texas", "baselineElectionId": "2026-us-senate"},  # no class
            {"seat": "Texas", "class": 0, "baselineElectionId": "2026-us-senate"},
            {"seat": "Texas", "class": 4, "baselineElectionId": "2026-us-senate"},
            {"seat": "Texas", "class": "3", "baselineElectionId": "2026-us-senate"},
            {"seat": "Texas", "class": 3.0, "baselineElectionId": "2026-us-senate"},
            {"seat": "Texas", "class": True, "baselineElectionId": "2026-us-senate"},
            "Texas",
            None,
        ]
        assert self._built([*malformed, *self.OVERRIDES]) == self.OVERRIDES

    def test_a_later_record_for_a_named_seat_and_class_is_dropped(self) -> None:
        again = {"seat": "Ohio", "class": 3, "baselineElectionId": "2022-us-senate"}
        assert self._built([*self.OVERRIDES, again]) == self.OVERRIDES

    def test_one_seat_may_be_overridden_for_two_classes(self) -> None:
        class_one = {"seat": "Ohio", "class": 1, "baselineElectionId": "2030-us-senate"}
        assert self._built([*self.OVERRIDES, class_one]) == [*self.OVERRIDES, class_one]

    def test_extra_fields_ride_along(self) -> None:
        entry = {"seat": "Ohio", "class": 3, "baselineElectionId": "2026-us-senate", "note": "special"}
        assert self._built([entry]) == [entry]

    def test_a_non_list_value_keeps_nothing(self) -> None:
        assert self._built({"seat": "Ohio"}) == []

    def test_key_is_absent_when_the_shell_has_none(self) -> None:
        result = build_map_modes_with_regions(
            {"23": {}}, {"23": []}, current_year=2026, parliament_features=self.FEATURES_2028
        )
        assert "senateBaselineOverrides" not in result["23"]

    def test_the_shell_list_is_not_mutated(self) -> None:
        shell = [dict(self.OVERRIDES[0]), {"seat": "", "class": 3, "baselineElectionId": "x"}]
        modes = {"23": {"senateBaselineOverrides": shell}}
        build_map_modes_with_regions(
            modes, {"23": []}, current_year=2026, parliament_features=self.FEATURES_2028
        )
        assert len(modes["23"]["senateBaselineOverrides"]) == 2


class TestShippedUsShell:
    """The repo's real US shell after the 2028 rollover, built the way the export builds it."""

    SHELL_PATH = Path(__file__).resolve().parents[2] / "uselectionmaps" / "data" / "map-modes-shell.json"

    @classmethod
    def _shell(cls) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(cls.SHELL_PATH.read_text(encoding="utf-8"))
        return loaded

    def _built(self, current_year: int = 2026) -> dict[str, Any]:
        shell = self._shell()
        return build_map_modes_with_regions(
            shell["mapModes"], {}, current_year, parliament_features=shell["parliamentFeatures"]
        )

    def test_the_2026_senate_specials_have_expired(self) -> None:
        senate = self._built()["23"]
        assert senate.get("senateSpecialElections", []) == []

    def test_florida_and_ohio_swing_from_the_2026_senate_result(self) -> None:
        senate = self._built()["23"]
        assert senate["senateBaselineOverrides"] == [
            {"seat": "Florida", "class": 3, "baselineElectionId": "2026-us-senate"},
            {"seat": "Ohio", "class": 3, "baselineElectionId": "2026-us-senate"},
        ]

    def test_class_3_is_next_up_in_2028(self) -> None:
        assert self._built()["23"]["senateClassNextElection"]["3"] == 2028

    def test_predict_rolls_over_to_2028_anchored_on_the_2026_results(self) -> None:
        features = self._shell()["parliamentFeatures"]
        house, senate = features["us_house"], features["us_senate"]
        assert (house["predictBaselineElectionId"], house["predictAnchorElectionId"]) == (
            "2026-us-house",
            "2026-us-house",
        )
        assert (senate["predictBaselineElectionId"], senate["predictAnchorElectionId"]) == (
            "2022-us-senate",
            "2026-us-senate",
        )
        for parliament in (house, senate):
            assert parliament["nextElectionYear"] == 2028
            assert parliament["nextElectionDate"] == "2028-11-07T00:00:00-05:00"
        assert {"key": "seatsup", "label": "Seats up (2028)"} in senate["predict"]["tabs"]

    def test_the_archived_2026_forecasts_still_compare_against_their_own_baselines(self) -> None:
        from scripts.export.manifest import US_MODEL_COMPARISON_IDS

        assert US_MODEL_COMPARISON_IDS["us_house_model"] == "2024-us-house"
        assert US_MODEL_COMPARISON_IDS["us_senate_model"] == "2020-us-senate"


class TestSenateClassUpIn:
    CYCLE = {"base": {"1": 2018, "2": 2020, "3": 2022}, "period": 6}

    def test_each_class_is_up_on_its_cycle_years(self) -> None:
        from scripts.export.manifest import senate_class_up_in

        assert [senate_class_up_in(self.CYCLE, year) for year in (2026, 2028, 2030, 2032, 2034, 2036)] == [
            2, 3, 1, 2, 3, 1,
        ]

    def test_a_year_no_class_lands_on_is_none(self) -> None:
        from scripts.export.manifest import senate_class_up_in

        assert senate_class_up_in(self.CYCLE, 2027) is None
        assert senate_class_up_in({"base": {"1": 2018, "2": 2018}, "period": 6}, 2024) is None

    def test_a_malformed_cycle_is_none(self) -> None:
        from scripts.export.manifest import senate_class_up_in

        bad_cycles: list[Any] = [None, [], {}, {"base": []}, {"base": {"1": "2018"}}, {"base": {"1": 2018}, "period": 0}]
        for bad in bad_cycles:
            assert senate_class_up_in(bad, 2024) is None


class TestUsResultsExport:
    """A full US Senate page export: status fields, ``sourceNote``, folded specials.

    Seeds a temp DB with a 2020 Senate election (credited, one uncalled seat) and its
    same-day special as a child election, an uncredited 2018 Senate election with no
    status rows, and a Senate forecast, then runs the page export into a temp root.
    """

    _NOTE = "Results: The New York Times / AP. Provisional until certified."

    @staticmethod
    def _seed(db: Database, tmp_path: Path) -> Path:
        senate_map = db.add_map("us-senate", parliament="us_senate")
        with db.session() as s:
            s.add(Party(id=DEM_PARTY_ID, name="Democratic", colour="#1375E1"))
            s.add(Party(id=REP_PARTY_ID, name="Republican", colour="#E81B23"))
        region = db.add_region(senate_map.id, "South")
        seat_ids = {
            name: db.add_seat(senate_map.id, name, region_id=region.id).id
            for name in ("Alabama", "Georgia", "Ohio", "Texas")
        }

        def votes(election_id: int, seat: str, dem: int, rep: int, *, winner: int | None) -> None:
            db.add_vote(
                election_id, seat_ids[seat], party_id=DEM_PARTY_ID, vote_total=dem,
                candidate_name=f"{seat} D", elected=winner == DEM_PARTY_ID,
            )
            db.add_vote(
                election_id, seat_ids[seat], party_id=REP_PARTY_ID, vote_total=rep,
                candidate_name=f"{seat} R", elected=winner == REP_PARTY_ID,
            )

        regular = db.add_election(senate_map.id, 2020, "2020 US Senate Election", ElectionType.us_senate)
        votes(regular.id, "Georgia", 480, 500, winner=None)
        votes(regular.id, "Texas", 400, 600, winner=REP_PARTY_ID)
        db.set_seat_statuses(regular.id, [
            SeatStatus(seat_id=seat_ids["Georgia"], called=False, pct_counted=97.26, senate_class=2),
            SeatStatus(seat_id=seat_ids["Texas"], called=True, pct_counted=99.0, senate_class=2),
        ])
        with db.session() as s:
            s.execute(update(Election).where(Election.id == regular.id).values(source_note=TestUsResultsExport._NOTE))

        special = db.add_election(
            senate_map.id, 2020, "2020 US Senate Special Election", ElectionType.us_senate,
            parent_election_id=regular.id,
        )
        votes(special.id, "Georgia", 700, 650, winner=DEM_PARTY_ID)
        db.set_seat_statuses(special.id, [
            SeatStatus(seat_id=seat_ids["Georgia"], called=True, senate_class=3, decisive_round="runoff"),
        ])

        older = db.add_election(senate_map.id, 2018, "2018 US Senate Election", ElectionType.us_senate)
        votes(older.id, "Ohio", 550, 450, winner=DEM_PARTY_ID)

        forecast = db.add_election(
            senate_map.id, 2026, "US Senate UNS 2026-11-01", ElectionType.us_senate_model
        )
        votes(forecast.id, "Georgia", 520, 480, winner=DEM_PARTY_ID)

        output_root = tmp_path / "uselectionmaps" / "data"
        (output_root / "maps").mkdir(parents=True)
        (output_root / "maps" / f"map-{senate_map.id}.topo.json").write_text("{}", encoding="utf-8")
        # The supplemental Current Senate entry needs its prebuilt result file.
        (output_root / "results").mkdir()
        (output_root / "results" / "senate-current.json").write_text("{}", encoding="utf-8")
        # A stale note on the uncredited 2018 entry: sourceNote is DB-derived, so it must
        # not survive regeneration.
        stale = {"elections": [{"id": "2018-us-senate", "sourceNote": "stale"}]}
        (output_root / "map-modes.json").write_text(json.dumps(stale), encoding="utf-8")
        shell = {"mapModes": {str(senate_map.id): {"projection": "albersUsa"}}}
        (output_root / "map-modes-shell.json").write_text(json.dumps(shell), encoding="utf-8")
        return output_root

    @staticmethod
    def _export(
        db: Database,
        output_root: Path,
        tmp_path: Path,
        *,
        dry_run: bool = False,
        names: tuple[str, ...] | None = None,
        output_file: Path | None = None,
        page_roots: dict[str, Path] | None = None,
    ) -> None:
        """Export the seeded elections (or just ``names``, as a single-election run)."""
        args = argparse.Namespace(dry_run=dry_run, output_file=output_file, legacy_files_dir=tmp_path)
        with db.session() as session:
            query = (
                select(Election)
                .options(joinedload(Election.map))
                .order_by(Election.year.desc(), Election.name.asc())
            )
            if names is not None:
                query = query.where(Election.name.in_(names))
            export_pages(
                session=session,
                elections=list(session.execute(query).scalars().all()),
                page_roots=page_roots or {"uselectionmaps": output_root},
                args=args,
                manifest_parties=[],
                manifest_regions_by_map_id={},
                has_electorate=True,
                single_election_mode=names is not None,
            )

    @staticmethod
    def _election_id(db: Database, name: str) -> int:
        election = db.get_election_by_name(name)
        assert election is not None
        return int(election.id)

    @staticmethod
    def _read(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_special_is_folded_into_its_parent(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed(db, tmp_path)
        self._export(db, output_root, tmp_path)

        results = output_root / "results"
        assert sorted(path.name for path in results.iterdir()) == [
            "senate-current.json",
            "us-president-trends-by-state.json",  # the whole US page runs, presidential included
            "us-senate-2018.json",
            "us-senate-2020.json",
        ]  # no us-senate-forecast.json: chamber forecasts are switched off by default
        manifest = self._read(output_root / "map-modes.json")
        ids = [entry["id"] for entry in manifest["elections"]]
        assert "2020-us-senate-special" not in ids
        assert "2020-us-senate-special" not in manifest["files"]["elections"]["electionsById"]

        payload = self._read(results / "us-senate-2020.json")
        assert payload["specials"] == [
            {"n": "Georgia", "cl": 3, "w": DEM_PARTY_ID,
             "p": [[DEM_PARTY_ID, 700, "Georgia D"], [REP_PARTY_ID, 650, "Georgia R"]], "rd": "runoff"},
        ]

    def test_regular_seats_carry_status_fields(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed(db, tmp_path)
        self._export(db, output_root, tmp_path)

        seats = {seat["n"]: seat for seat in self._read(output_root / "results" / "us-senate-2020.json")["seats"]}
        # Uncalled: coloured by its leader (Republican), flagged, with its count.
        assert seats["Georgia"]["w"] == REP_PARTY_ID
        assert seats["Georgia"]["c"] is False
        assert seats["Georgia"]["pc"] == 97.3
        # Called: no status fields at all.
        assert set(seats["Texas"]) == {"n", "r", "w", "p"}

    def test_election_without_status_rows_is_unchanged(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed(db, tmp_path)
        self._export(db, output_root, tmp_path)

        payload = self._read(output_root / "results" / "us-senate-2018.json")
        region_id = payload["seats"][0]["r"]
        assert payload == {
            "schema": "pf-results-v4",
            "seats": [{
                "n": "Ohio", "r": region_id, "w": DEM_PARTY_ID,
                "p": [[DEM_PARTY_ID, 550, "Ohio D"], [REP_PARTY_ID, 450, "Ohio R"]],
            }],
        }

    def test_source_note_is_recomputed_from_the_db(
        self, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(export_elections, "EXPORT_US_CHAMBER_FORECASTS", True)
        output_root = self._seed(db, tmp_path)
        self._export(db, output_root, tmp_path)

        entries = {entry["id"]: entry for entry in self._read(output_root / "map-modes.json")["elections"]}
        assert entries["2020-us-senate"]["sourceNote"] == self._NOTE
        assert "sourceNote" not in entries["2018-us-senate"]
        assert "sourceNote" not in entries["current-us-senate"]

    def test_forecast_is_dropped_while_the_flag_is_off(
        self, db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_root = self._seed(db, tmp_path)
        # A forecast entry and file left by an earlier export must not return either.
        (output_root / "results" / "us-senate-forecast.json").write_text("{}", encoding="utf-8")
        old = {"elections": [{"id": "current-us-senate", "type": "us_senate_model", "model": True}],
               "files": {"elections": {"electionsById": {"current-us-senate": "results/us-senate-forecast.json"}}}}
        (output_root / "map-modes.json").write_text(json.dumps(old), encoding="utf-8")

        self._export(db, output_root, tmp_path)

        manifest = self._read(output_root / "map-modes.json")
        assert "current-us-senate" not in [entry["id"] for entry in manifest["elections"]]
        assert "current-us-senate" not in manifest["files"]["elections"]["electionsById"]
        assert "Skipping 'US Senate UNS 2026-11-01'" in capsys.readouterr().out

    def test_single_export_of_a_dropped_forecast_writes_nothing(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed(db, tmp_path)
        output_file = tmp_path / "forecast.json"

        self._export(
            db, output_root, tmp_path, names=("US Senate UNS 2026-11-01",), output_file=output_file
        )

        assert not output_file.exists()
        assert not (output_root / "results" / "us-senate-forecast.json").exists()

    def test_archived_forecast_stays_listed(
        self, db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(export_elections, "EXPORT_US_CHAMBER_FORECASTS", True)
        output_root = self._seed(db, tmp_path)
        self._export(db, output_root, tmp_path)

        manifest = self._read(output_root / "map-modes.json")
        forecast = next(entry for entry in manifest["elections"] if entry["id"] == "current-us-senate")
        assert forecast["model"] is True
        assert manifest["files"]["elections"]["electionsById"]["current-us-senate"] == "results/us-senate-forecast.json"

    def test_dry_run_reports_a_missing_map_and_mode(
        self, db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_root = self._seed(db, tmp_path)
        for path in (output_root / "maps").iterdir():
            path.unlink()
        (output_root / "map-modes-shell.json").write_text(json.dumps({"mapModes": {}}), encoding="utf-8")

        self._export(db, output_root, tmp_path, dry_run=True)

        out = capsys.readouterr().out
        assert "Missing (a real export would fail): pre-built map " in out
        assert "map-1.topo.json (needed by '2020 US Senate Election'," in out
        assert "US Senate UNS 2026-11-01" in out  # reported as skipped, not as needing the map
        assert "Warning: no mapModes entry for map id(s)" in out
        assert "Folding '2020 US Senate Special Election'" in out
        assert f"Would write results: {output_root / 'results' / 'us-senate-2020.json'} (2 seats)" in out
        assert "us-senate-2020-special" not in out
        assert [path.name for path in (output_root / "results").iterdir()] == ["senate-current.json"]

    def test_missing_map_writes_nothing_on_any_page(self, db: Database, tmp_path: Path) -> None:
        # A complete Westminster page goes first; the US page's map is missing. The
        # preflight must refuse before the Westminster page is written.
        output_root = self._seed(db, tmp_path)
        uk_map = db.add_map("Westminster 2010", parliament="westminster")
        uk_seat = db.add_seat(uk_map.id, "Aberavon", region_id=db.add_region(uk_map.id, "Wales").id)
        uk_election = db.add_election(uk_map.id, 2019, "2019 General Election", ElectionType.uk_general)
        db.add_vote(uk_election.id, uk_seat.id, party_id=DEM_PARTY_ID, vote_total=10, elected=True)
        (tmp_path / "650map.json").write_text("{}", encoding="utf-8")
        (tmp_path / "2019election_new.json").write_text("{}", encoding="utf-8")
        uk_root = tmp_path / "electionmaps" / "data"

        for path in (output_root / "maps").iterdir():
            path.unlink()
        before = {path: path.read_bytes() for path in output_root.rglob("*") if path.is_file()}

        with pytest.raises(FileNotFoundError, match=r"nothing was written[\s\S]*map-\d+\.topo\.json"):
            self._export(
                db, output_root, tmp_path,
                page_roots={"electionmaps": uk_root, "uselectionmaps": output_root},
            )

        assert not uk_root.exists()
        after = {path: path.read_bytes() for path in output_root.rglob("*") if path.is_file()}
        assert after == before

    def test_output_file_export_skips_the_preflight(self, db: Database, tmp_path: Path) -> None:
        # No map and no supplemental files: --output-file writes one payload and needs neither.
        output_root = self._seed(db, tmp_path)
        for path in (output_root / "maps").iterdir():
            path.unlink()
        (output_root / "results" / "senate-current.json").unlink()
        output_file = tmp_path / "out.json"

        self._export(db, output_root, tmp_path, names=("2020 US Senate Election",), output_file=output_file)

        assert [special["n"] for special in self._read(output_file)["specials"]] == ["Georgia"]

    def test_dry_run_reports_a_missing_supplemental(
        self, db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_root = self._seed(db, tmp_path)
        (output_root / "results" / "senate-current.json").unlink()

        # The supplemental pass itself still refuses later on, as before the preflight.
        with pytest.raises(FileNotFoundError, match="Prebuilt result file not found"):
            self._export(db, output_root, tmp_path, dry_run=True)

        assert (
            "Missing (a real export would fail): supplemental 'current-senate' file "
            f"{output_root / 'results' / 'senate-current.json'}"
        ) in capsys.readouterr().out

    @pytest.mark.parametrize("to_file", [False, True])
    def test_single_export_of_a_folded_special_is_refused(
        self, db: Database, tmp_path: Path, to_file: bool
    ) -> None:
        output_root = self._seed(db, tmp_path)
        output_file = tmp_path / "special.json" if to_file else None

        with pytest.raises(RuntimeError, match="folded Senate special.*export its parent election"):
            self._export(
                db, output_root, tmp_path,
                names=("2020 US Senate Special Election",), output_file=output_file,
            )
        assert output_file is None or not output_file.exists()

    def test_single_export_of_the_parent_carries_its_specials(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed(db, tmp_path)
        self._export(db, output_root, tmp_path, names=("2020 US Senate Election",))

        payload = self._read(output_root / "results" / "us-senate-2020.json")
        assert [special["n"] for special in payload["specials"]] == ["Georgia"]

    def test_specials_from_two_children_are_merged_by_name(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed(db, tmp_path)
        election = db.get_election_by_name("2020 US Senate Election")
        assert election is not None
        # Added after the Georgia special, but Alabama sorts first.
        later = db.add_election(
            election.map_id, 2020, "2020 US Senate Special Election (Alabama)", ElectionType.us_senate,
            parent_election_id=election.id,
        )
        alabama = next(seat for seat in db.get_seats_for_map(election.map_id) if seat.seat_name == "Alabama")
        db.add_vote(later.id, alabama.id, party_id=REP_PARTY_ID, vote_total=900, elected=True)

        self._export(db, output_root, tmp_path)

        specials = self._read(output_root / "results" / "us-senate-2020.json")["specials"]
        assert [special["n"] for special in specials] == ["Alabama", "Georgia"]
        assert specials[0] == {"n": "Alabama", "w": REP_PARTY_ID, "p": [[REP_PARTY_ID, 900]]}

    def test_special_on_another_map_raises(self, db: Database, tmp_path: Path) -> None:
        output_root = self._seed(db, tmp_path)
        other_map = db.add_map("elsewhere", parliament="us_senate")
        special_id = self._election_id(db, "2020 US Senate Special Election")
        with db.session() as s:
            s.execute(update(Election).where(Election.id == special_id).values(map_id=other_map.id))

        with pytest.raises(RuntimeError, match="is on map .* but its parent"):
            self._export(db, output_root, tmp_path, names=("2020 US Senate Election",))


class TestMissingExportInputs:
    """The export preflight: every branch of ``missing_export_inputs``, on temp dirs."""

    @staticmethod
    def _election(
        name: str, type: ElectionType, map_id: int, parliament: str, map_name: str = "Westminster 2010"
    ) -> Any:
        return SimpleNamespace(
            name=name, type=type, map_id=map_id, parent_election_id=None,
            map=SimpleNamespace(name=map_name, parliament=parliament),
        )

    def test_missing_westminster_template(self, tmp_path: Path) -> None:
        election = self._election("2019 General Election", ElectionType.uk_general, 1, "westminster")
        missing = missing_export_inputs(
            {"electionmaps": [election]}, {"electionmaps": tmp_path / "uk"}, tmp_path,
            single_election_mode=True,
        )
        assert missing == [f"map template {tmp_path / '650map.json'} (needed by '2019 General Election')"]

    def test_by_election_is_skipped(self, tmp_path: Path) -> None:
        election = self._election("Some By-Election", ElectionType.by_election, 1, "westminster")
        assert missing_export_inputs(
            {"electionmaps": [election]}, {"electionmaps": tmp_path / "uk"}, tmp_path,
            single_election_mode=True,
        ) == []

    def test_missing_prebuilt_supplemental_result(self, tmp_path: Path) -> None:
        root = tmp_path / "us"
        missing = missing_export_inputs({}, {"uselectionmaps": root}, tmp_path, single_election_mode=False)
        assert missing == [f"supplemental 'current-senate' file {root / 'results' / 'senate-current.json'}"]

    def test_missing_supplemental_source_file(self, tmp_path: Path) -> None:
        missing = missing_export_inputs(
            {}, {"electionmaps": tmp_path / "uk"}, tmp_path, single_election_mode=False
        )
        assert missing == [
            f"supplemental '2019-general-changed-boundaries' file {tmp_path / '2019election_new.json'}"
        ]

    def test_single_election_mode_skips_supplementals(self, tmp_path: Path) -> None:
        roots = {"electionmaps": tmp_path / "uk", "uselectionmaps": tmp_path / "us"}
        assert missing_export_inputs({}, roots, tmp_path, single_election_mode=True) == []

    def test_a_shared_missing_map_is_reported_once(self, tmp_path: Path) -> None:
        root = tmp_path / "us"
        elections = [
            self._election(f"{year} US House Election", ElectionType.us_house, 24, "us_house")
            for year in (2026, 2028)
        ]
        missing = missing_export_inputs(
            {"uselectionmaps": elections}, {"uselectionmaps": root}, tmp_path, single_election_mode=True
        )
        assert missing == [
            f"pre-built map {root / 'maps' / 'map-24.topo.json'} "
            "(needed by '2026 US House Election', '2028 US House Election')"
        ]

    def test_present_files_report_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / "us"
        (root / "maps").mkdir(parents=True)
        (root / "maps" / "map-24.topo.json").write_text("{}", encoding="utf-8")
        election = self._election("2026 US House Election", ElectionType.us_house, 24, "us_house")
        assert missing_export_inputs(
            {"uselectionmaps": [election]}, {"uselectionmaps": root}, tmp_path, single_election_mode=True
        ) == []


class TestHouseComparisonAcrossDistrictSets:
    """US House compares against the previous House election across maps; others do not."""

    def test_a_reordered_list_never_compares_with_a_newer_year(self) -> None:
        # A hand edit put 2018 above the newer elections; it must still get none.
        entries = [self._house(2018, 25), self._house(2026, 24), self._house(2024, 21)]
        assign_comparison_elections(entries)

        comparisons = {entry["id"]: entry.get("comparisonElectionId") for entry in entries}
        assert comparisons == {"2018-us-house": None, "2026-us-house": "2024-us-house", "2024-us-house": None}

    @staticmethod
    def _house(year: int, map_id: int) -> dict[str, Any]:
        return {"id": f"{year}-us-house", "type": "us_house", "mapId": map_id, "parliament": "us_house"}

    def test_each_house_year_compares_with_the_previous_one_across_maps(self) -> None:
        # Newest first, as the export builds them: 2026 on map 24, 2022-24 on 21, 2018-20 on 25.
        entries = [
            self._house(2026, 24), self._house(2024, 21), self._house(2022, 21),
            self._house(2020, 25), self._house(2018, 25),
        ]
        assign_comparison_elections(entries)

        comparisons = {entry["id"]: entry.get("comparisonElectionId") for entry in entries}
        assert comparisons == {
            "2026-us-house": "2024-us-house",
            "2024-us-house": "2022-us-house",
            "2022-us-house": "2020-us-house",
            "2020-us-house": "2018-us-house",
            "2018-us-house": None,
        }

    def test_house_never_compares_across_parliaments_or_types(self) -> None:
        entries = [
            self._house(2026, 24),
            {"id": "2024-us-president", "type": "us_presidential", "mapId": 22, "parliament": "us_presidential"},
            {"id": "2024-us-senate", "type": "us_senate", "mapId": 23, "parliament": "us_senate"},
        ]
        assign_comparison_elections(entries)

        assert all("comparisonElectionId" not in entry for entry in entries)

    def test_holyrood_and_westminster_still_need_the_same_map(self) -> None:
        entries: list[dict[str, Any]] = [
            {"id": "2026-holyrood", "type": "holyrood_general", "mapId": 30, "parliament": "holyrood"},
            {"id": "2021-holyrood", "type": "holyrood_general", "mapId": 31, "parliament": "holyrood"},
            {"id": "2024-general", "type": "uk_general", "mapId": 2, "parliament": "westminster"},
            {"id": "2019-general", "type": "uk_general", "mapId": 1, "parliament": "westminster"},
            {"id": "2017-general", "type": "uk_general", "mapId": 1, "parliament": "westminster"},
        ]
        assign_comparison_elections(entries)

        comparisons = {entry["id"]: entry.get("comparisonElectionId") for entry in entries}
        assert comparisons == {
            "2026-holyrood": None,
            "2021-holyrood": None,
            "2024-general": None,
            "2019-general": "2017-general",
            "2017-general": None,
        }
