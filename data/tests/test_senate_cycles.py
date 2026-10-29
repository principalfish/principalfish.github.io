"""Pinned Senate fields, atomic saved context and dated export integration."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models/us"))

import _common
from _common import UsModelSpec, UsSimulationConfig
from run_us_senate_model import SPEC
from db import Database
from model_support.persistence import OutputScope, delete_outputs
from model_support.senate_cycles import (
    DEFAULT_SENATE_CYCLES,
    resolve_senate_cycle,
    senate_cycle_from_snapshot,
    senate_cycle_snapshot,
)
from models import Election, ElectionType
from scripts.export.manifest import (
    assign_comparison_elections,
    build_map_modes_with_regions,
)
from scripts.export.senate import (
    configure_senate_forecast,
    latest_senate_run,
    pinned_roster_payload,
    public_senate_forecast,
    senate_cycle_for_election,
)
from tests.test_us_model import _add_poll, _parties


@pytest.mark.parametrize("day,year,count", [
    ("2026-01-01", 2026, 35), ("2026-12-31", 2026, 35),
    ("2027-01-01", 2028, 34), ("2028-12-31", 2028, 34),
])
def test_explicit_cycle_boundaries(day: str, year: int, count: int) -> None:
    cycle = resolve_senate_cycle(date.fromisoformat(day))
    assert cycle.target_year == year
    assert len(cycle.races) == count
    if year == 2028:
        assert cycle.baseline_id == "2022-us-senate"
        assert cycle.regular_class == 3
        assert not cycle.baseline_overrides
        with pytest.raises(ValueError, match="verified.*roster"):
            cycle.require_chamber()


@pytest.mark.parametrize("day", ["2025-12-31", "2029-01-01"])
def test_unknown_dates_fail_closed(day: str) -> None:
    with pytest.raises(ValueError, match="No pinned Senate cycle"):
        resolve_senate_cycle(date.fromisoformat(day))


def test_2026_roster_slots_and_control_policy() -> None:
    cycle = resolve_senate_cycle(date(2026, 6, 29))
    roster = cycle.require_chamber()
    assert len(roster) == len({(m.state, m.seat_class) for m in roster}) == 100
    contested = {(race.state, race.seat_class) for race in cycle.races}
    holdovers = [
        member for member in roster
        if (member.state, member.seat_class) not in contested
    ]
    assert len(holdovers) == 65
    assert sum(m.party == "republican" for m in holdovers) == 31
    assert sum(m.party == "democrat" for m in holdovers) == 32
    assert cycle.baseline_overrides == {
        "Florida": "2022-us-senate", "Ohio": "2022-us-senate",
    }
    policy = cycle.control_policy
    assert policy is not None
    assert policy.vice_president == "republican"
    assert ("Maine", "Angus S. King, Jr.", "democrat") in policy.alignments
    assert ("Vermont", "Bernard Sanders", "democrat") in policy.alignments
    assert ("Nebraska", "Dan Osborn", None) in policy.alignments


def _config_copy(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    path = tmp_path / "cycles.json"
    payload = json.loads(DEFAULT_SENATE_CYCLES.read_text())
    path.write_text(json.dumps(payload))
    roster_name = payload["cycles"][0]["roster"]["file"]
    (tmp_path / roster_name).write_bytes(
        (DEFAULT_SENATE_CYCLES.parent / roster_name).read_bytes()
    )
    return path, payload


@pytest.mark.parametrize(
    "case", ["schema", "overlap", "duplicate", "class", "missing", "checksum"],
)
def test_invalid_configuration_never_falls_back(tmp_path: Path, case: str) -> None:
    path, payload = _config_copy(tmp_path)
    cycle = payload["cycles"][0]
    if case == "schema":
        payload["schema"] = 2
    elif case == "overlap":
        payload["cycles"][1]["effective_from"] = "2026-12-31"
    elif case == "duplicate":
        cycle["specials"].append({
            "state": "Alabama", "seat_class": 3, "baseline_id": "2022-us-senate",
        })
    elif case == "class":
        cycle["specials"][0]["seat_class"] = 4
    elif case == "missing":
        cycle["regular_states"].pop()
    else:
        (tmp_path / cycle["roster"]["file"]).write_text("{}")
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        resolve_senate_cycle(date(2026, 10, 19), path)


def test_saved_context_ignores_later_files_and_detects_mutation(tmp_path: Path) -> None:
    path, payload = _config_copy(tmp_path)
    cycle = resolve_senate_cycle(date(2026, 10, 19), path)
    snapshot = senate_cycle_snapshot(cycle)
    path.unlink()
    (tmp_path / payload["cycles"][0]["roster"]["file"]).unlink()
    saved = senate_cycle_from_snapshot(snapshot, date(2026, 10, 19))
    assert saved.races == cycle.races
    assert saved.require_chamber() == cycle.require_chamber()
    assert saved.control_policy == cycle.control_policy
    original_roster = json.loads(cycle.roster_json or "{}")
    assert pinned_roster_payload(saved)["seats"] == original_roster["seats"]
    snapshot["roster_json"] = "{}"
    with pytest.raises(ValueError, match="checksum"):
        senate_cycle_from_snapshot(snapshot, date(2026, 10, 19))


@pytest.fixture
def senate_world(db: Database, tmp_path: Path) -> UsModelSpec:
    dem, rep = _parties(db)
    senate = db.add_map(SPEC.map_name, parliament="us_senate")
    house = db.add_map("US House Districts 2024", parliament="us_house")
    region = db.add_region(senate.id, "Everywhere")
    elections = {
        year: db.add_election(
            senate.id, year, f"{year} US Senate Election", ElectionType.us_senate
        )
        for year in (2020, 2022)
    }
    roster = resolve_senate_cycle(date(2026, 1, 1)).require_chamber()
    for state in sorted({member.state for member in roster}):
        seat = db.add_seat(senate.id, state, region_id=region.id)
        for year in elections:
            cls = 2 if year == 2020 else 3
            if not any(
                member.state == state and member.seat_class == cls
                for member in roster
            ):
                continue
            for party, total in ((dem, 400 + year % 100), (rep, 600 - year % 100)):
                db.add_vote(
                    elections[year].id, seat.id, party_id=party.id, vote_total=total
                )
    pollster = db.add_pollster("Fixture", "fixture")
    for day in (date(2026, 10, 19), date(2026, 12, 31), date(2027, 1, 2)):
        _add_poll(
            db, map_id=house.id, pollster=pollster, end=day,
            rows=[(dem.id, 49.0), (rep.id, 51.0)],
        )
    return dataclasses.replace(
        SPEC, trend_cache_json=tmp_path / "trends.json",
        trend_cache_meta_json=tmp_path / "meta.json",
    )


def _cfg(spec: UsModelSpec, day: date, *, dry_run: bool = True) -> UsSimulationConfig:
    return UsSimulationConfig(spec, day, day - timedelta(days=60), 14, dry_run)


def test_direct_configs_rebind_date_and_preserve_the_2026_estimator(
    db: Database, senate_world: UsModelSpec
) -> None:
    cfg = _cfg(senate_world, date(2026, 10, 19))
    expected_spec = dataclasses.replace(senate_world, senate_cycle_config=None,
        seat_name_allowlist=cfg.spec.seat_name_allowlist,
        seat_baseline_overrides={"Florida": "2022-us-senate", "Ohio": "2022-us-senate"})
    before = _common.prepare_us_projection(db, _cfg(expected_spec, cfg.as_of_date))
    after = _common.prepare_us_projection(db, cfg)
    assert after.projected_votes == before.projected_votes
    assert after.region_diff_rows == before.region_diff_rows
    changed = dataclasses.replace(cfg, as_of_date=date(2027, 1, 1))
    assert changed.spec.senate_cycle is not None
    assert changed.spec.senate_cycle.target_year == 2028
    assert changed.target_election_year is None  # No presidential EV validation.
    assert len(_common.prepare_us_projection(db, changed).seats) == 34


def test_dry_run_has_no_context_table(db: Database, senate_world: UsModelSpec) -> None:
    _common.run_simulation(db, _cfg(senate_world, date(2026, 10, 19)))
    with db.session() as session:
        query = "SELECT 1 FROM sqlite_master WHERE name='model_context'"
        assert session.execute(text(query)).first() is None


def test_saved_context_is_atomic_exportable_and_removed_with_output(
    db: Database, senate_world: UsModelSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(senate_world, date(2026, 10, 19), dry_run=False)
    name, rows, *_ = _common.run_simulation(db, cfg)
    election = db.get_election_by_name(name)
    assert election is not None
    with db.session() as session:
        query = "SELECT payload_json FROM model_context WHERE election_id=:id"
        encoded = session.execute(text(query), {"id": election.id}).scalar_one()
        exported = senate_cycle_for_election(session, election)
        assert exported is not None and exported.races == cfg.spec.senate_cycle.races
    # A failed vote insertion rolls back the context and election replacement too.
    bad_rows = [
        *rows, {"seat_id": None, "party_id": 1, "vote_total": 1.0, "elected": False},
    ]
    with pytest.raises((TypeError, sqlite3.IntegrityError)):
        _common.persist_projection(cfg.spec, election.map_id, cfg.as_of_date, name,
                                   bad_rows, {}, _common.database_file(db))
    with db.session() as session:
        assert session.execute(
            text(query), {"id": election.id}
        ).scalar_one() == encoded
    monkeypatch.setattr(
        "scripts.export.senate.resolve_senate_cycle",
        lambda _: pytest.fail("Saved export consulted current config"),
    )
    with db.session() as session:
        assert senate_cycle_for_election(session, election) is not None
    scope = OutputScope("us_senate_model", election.map_id, "US Senate UNS")
    delete_outputs(
        _common.database_file(db), scope, cfg.as_of_date, cfg.as_of_date
    )
    with db.session() as session:
        assert session.execute(
            text("SELECT COUNT(*) FROM model_context")
        ).scalar_one() == 0


def test_legacy_field_must_match_before_borrowing_roster(
    db: Database, senate_world: UsModelSpec
) -> None:
    cfg = _cfg(senate_world, date(2026, 10, 19), dry_run=False)
    name, *_ = _common.run_simulation(db, cfg)
    election = db.get_election_by_name(name)
    assert election is not None
    with db.session() as session:
        session.execute(text("DELETE FROM model_context"))
        assert senate_cycle_for_election(session, election) is not None
        session.execute(text(
            "DELETE FROM votes WHERE election_id=:id AND seat_id="
            "(SELECT MIN(seat_id) FROM votes WHERE election_id=:id)"
        ), {"id": election.id})
        assert senate_cycle_for_election(session, election) is None


@pytest.mark.parametrize("invalid", [
    "unscoped_name", "date_disagrees", "unknown_legacy_year", "saved_field",
])
def test_senate_export_refuses_invalid_saved_or_legacy_context(
    db: Database, senate_world: UsModelSpec, invalid: str,
) -> None:
    cfg = _cfg(senate_world, date(2026, 10, 19), dry_run=False)
    name, *_ = _common.run_simulation(db, cfg)
    election = db.get_election_by_name(name)
    assert election is not None
    with db.session() as session:
        if invalid == "unscoped_name":
            election.name = "Custom projection"
        elif invalid == "date_disagrees":
            election.election_date = date(2026, 10, 18)
        elif invalid == "unknown_legacy_year":
            session.execute(text("DELETE FROM model_context"))
            election.name = "US Senate UNS 2028-10-19"
            election.election_date = date(2028, 10, 19)
        else:
            session.execute(text(
                "DELETE FROM votes WHERE election_id=:id AND seat_id="
                "(SELECT MIN(seat_id) FROM votes WHERE election_id=:id)"
            ), {"id": election.id})
        if invalid in {"date_disagrees", "saved_field"}:
            with pytest.raises(ValueError, match="date disagrees|field disagrees"):
                senate_cycle_for_election(session, election)
        else:
            assert senate_cycle_for_election(session, election) is None


def test_pinned_senate_roster_cannot_be_exported_without_saved_json() -> None:
    cycle = resolve_senate_cycle(date(2026, 10, 19))
    with pytest.raises(ValueError, match="roster is unavailable"):
        pinned_roster_payload(dataclasses.replace(cycle, roster_json=None))


def _args() -> argparse.Namespace:
    return argparse.Namespace(half_life_days=14, seat_prior_weight=1.0, dry_run=False,
        ignore_seat_polls=False, progress_every=0, continue_on_error=False)


def test_history_resolves_each_cycle_and_latest_export_is_dated(
    db: Database, senate_world: UsModelSpec
) -> None:
    _common.run_retrospective_range(
        db, senate_world, _args(), start_date=date(2026, 12, 31),
        end_date=date(2027, 1, 2), lookback_days=60, reset_existing=False)
    # Re-saving an older date must not make it the current website forecast.
    _common.run_simulation(db, _cfg(senate_world, date(2026, 12, 31), dry_run=False))
    with db.session() as session:
        latest = latest_senate_run(session)
        assert latest is not None and latest.name.endswith("2027-01-02")
        for election in session.scalars(select(Election).where(
            Election.type == ElectionType.us_senate_model
        )):
            cycle = senate_cycle_for_election(session, election)
            assert cycle is not None
            expected = 2026 if "2026-12-31" in election.name else 2028
            assert cycle.target_year == expected


def test_unknown_history_period_is_rejected_before_writes(
    db: Database, senate_world: UsModelSpec
) -> None:
    with pytest.raises(ValueError, match="No pinned Senate cycle"):
        _common.run_retrospective_range(
        db, senate_world, _args(), start_date=date(2028, 12, 31),
            end_date=date(2029, 1, 1), lookback_days=60, reset_existing=False)
    with db.session() as session:
        assert list(session.scalars(select(Election).where(
            Election.type == ElectionType.us_senate_model
        ))) == []


def test_cli_gap_fill_and_cap_do_not_reuse_static_field(
    db: Database, senate_world: UsModelSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    _common.run_simulation(db, _cfg(senate_world, date(2026, 12, 30), dry_run=False))
    monkeypatch.setattr(sys, "argv", ["senate", "--as-of-date", "2027-01-02"])
    assert _common.main_for_spec(senate_world, lambda: db) == 0
    with db.session() as session:
        runs = list(session.scalars(select(Election).where(
            Election.type == ElectionType.us_senate_model
        )))
        assert len(runs) == 4
        cycles = [senate_cycle_for_election(session, run) for run in runs]
        assert all(cycle is not None for cycle in cycles)
        years = {cycle.target_year for cycle in cycles if cycle is not None}
        assert years == {2026, 2028}
    # An explicit requested cycle cannot silently cap into a different cycle.
    monkeypatch.setattr(sys, "argv", ["senate", "--as-of-date", "2027-01-01"])
    with pytest.raises(ValueError, match="outside the requested Senate cycle"):
        _common.main_for_spec(senate_world, lambda: db)


def test_export_metadata_uses_forecast_cycle_not_export_clock() -> None:
    shell = (Path(__file__).resolve().parents[2]
             / "uselectionmaps/data/map-modes-shell.json")
    config = json.loads(shell.read_text())
    for day, year in ((date(2026, 10, 19), 2026), (date(2027, 1, 1), 2028)):
        cycle = resolve_senate_cycle(day)
        public = public_senate_forecast(cycle, 23)
        output = configure_senate_forecast(config, public)
        features = output["parliamentFeatures"]
        senate = features["us_senate"]
        assert senate["nextElectionYear"] == year
        assert senate["predict"]["regularClass"] == cycle.regular_class
        modes = build_map_modes_with_regions(output["mapModes"], {}, current_year=2035,
                                             parliament_features=features)
        assert modes["23"]["senateClassNextElection"][str(cycle.regular_class)] == year
        assert len(modes["23"]["senateSpecialElections"]) == (2 if year == 2026 else 0)
        if year == 2026:
            assert senate["predict"]["chamberElectionId"] != "current-senate"
            assert senate["predict"]["chamberElectionId"] in senate["hiddenElectionIds"]
        else:
            assert "chamberElectionId" not in senate["predict"]
            assert senate["predict"]["tabs"] == [
                {"key": "seatsup", "label": "Seats up (2028)"},
            ]
        for chamber in ("us_house", "us_presidential"):
            assert (output["parliamentFeatures"][chamber]
                    == config["parliamentFeatures"][chamber])
    entries = [
        {"id": "current-us-senate", "type": "us_senate_model",
         "baselineElectionId": "2022-us-senate"},
        {"id": "2020-us-senate"}, {"id": "2022-us-senate"},
    ]
    assign_comparison_elections(entries)
    assert entries[0]["comparisonElectionId"] == "2022-us-senate"


def test_rebuild_resolves_both_sides_of_rollover(
    db: Database, senate_world: UsModelSpec
) -> None:
    for day in (date(2026, 12, 31), date(2027, 1, 2)):
        _common.run_simulation(db, _cfg(senate_world, day, dry_run=False))
    cfg = _cfg(senate_world, date(2027, 1, 2), dry_run=False)
    _common._rebuild_history(db, senate_world, _args(), cfg,
                            first_poll=date(2026, 12, 31), lookback_days=60)
    with db.session() as session:
        runs = list(session.scalars(select(Election).where(
            Election.type == ElectionType.us_senate_model)))
        assert len(runs) == 3
        for run in runs:
            cycle = senate_cycle_for_election(session, run)
            assert cycle is not None
            assert len(cycle.races) == (35 if run.year == 2026 else 34)


def test_later_wall_clock_and_live_roster_cannot_change_2026(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LaterDate(date):
        @classmethod
        def today(cls) -> LaterDate:
            return cls(2027, 2, 1)

    original_read = Path.read_text

    def no_live_files(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.name in {"senate-current.json", "map-modes-shell.json"}:
            raise AssertionError("Model read mutable frontend input")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(_common, "date", LaterDate)
    monkeypatch.setattr(Path, "read_text", no_live_files)
    cfg = _cfg(SPEC, date(2026, 12, 31))
    assert cfg.spec.senate_cycle is not None
    assert cfg.spec.senate_cycle.target_year == 2026
    assert len(cfg.spec.senate_cycle.races) == 35


@pytest.mark.parametrize("day", [date(2026, 10, 19), date(2027, 1, 2)])
def test_full_export_uses_frozen_roster_and_metadata_refresh_preserves_it(
    db: Database, senate_world: UsModelSpec, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, day: date,
) -> None:
    from tests.test_export_ordering import _export, _prepare_page, _write_json
    import export_elections

    name, *_ = _common.run_simulation(db, _cfg(senate_world, day, dry_run=False))
    election = db.get_election_by_name(name)
    assert election is not None
    output = _prepare_page(tmp_path, [election.map_id, 23])
    source = (Path(__file__).resolve().parents[2]
              / "uselectionmaps/data/map-modes-shell.json")
    shell = json.loads(source.read_text())
    shell["mapModes"][str(election.map_id)] = shell["mapModes"]["23"]
    _write_json(output / "map-modes-shell.json", shell)
    current = {"schema": "pf-results-v4", "seats": []}
    _write_json(output / "results/senate-current.json", current)
    baselines = [
        db.get_election_by_name(f"{year} US Senate Election")
        for year in (2020, 2022)
    ]
    ids = [election.id, *(b.id for b in baselines if b is not None)]
    _export(db, tmp_path, output, ids, {"us_senate"})
    manifest_path = output / "map-modes.json"
    manifest = json.loads(manifest_path.read_text())
    assert json.loads((output / "results/senate-current.json").read_text()) == current
    forecast = manifest["senateForecast"]
    feature = manifest["parliamentFeatures"]["us_senate"]
    if day.year == 2026:
        roster_id = feature["predict"]["chamberElectionId"]
        roster_path = manifest["files"]["elections"]["electionsById"][roster_id]
        roster = json.loads((output / roster_path).read_text())
        assert sum(len(seat["members"]) for seat in roster["seats"]) == 100
        assert roster_id == forecast["rosterElectionId"]
        assert roster_id in feature["hiddenElectionIds"]
    else:
        assert forecast["rosterElectionId"] is None
        assert "chamberElectionId" not in feature["predict"]
        assert all(tab["key"] != "chamber" for tab in feature["predict"]["tabs"])
        entry = next(e for e in manifest["elections"]
                     if e["id"] == "current-us-senate")
        assert entry["comparisonElectionId"] == "2022-us-senate"
    # A metadata-only refresh must not restore the shell's mutable current roster.
    monkeypatch.setattr(export_elections, "Database", lambda: db)
    monkeypatch.setattr(
        export_elections, "PAGE_OUTPUT_ROOTS", {"uselectionmaps": output}
    )
    monkeypatch.setattr(export_elections, "parse_args", lambda: argparse.Namespace(
        election_name=None, current_simulation=False, metadata_only=True,
        dry_run=False, output_base=None,
    ))
    export_elections.main()
    refreshed = json.loads(manifest_path.read_text())
    assert refreshed["senateForecast"] == forecast
    assert refreshed["parliamentFeatures"]["us_senate"] == feature
    for key in (
        "senateClassNextElection", "senateSpecialElections", "senateForecastYear",
    ):
        assert (refreshed["mapModes"][str(election.map_id)][key]
                == manifest["mapModes"][str(election.map_id)][key])



def test_latest_senate_export_excludes_other_maps_and_prefixes(db: Database) -> None:
    canonical = db.add_map("US Senate 2024", parliament="us_senate")
    custom = db.add_map("Custom Senate", parliament="us_senate")
    expected = db.add_election(
        canonical.id, 2026, "US Senate UNS 2026-10-19",
        ElectionType.us_senate_model,
    )
    db.add_election(
        custom.id, 2028, "US Senate UNS 2028-10-19",
        ElectionType.us_senate_model,
    )
    db.add_election(
        canonical.id, 2028, "Custom Senate UNS 2028-10-20",
        ElectionType.us_senate_model,
    )
    # A same-scope older rerun has the greatest id but is still not the latest date.
    db.add_election(
        canonical.id, 2026, "US Senate UNS 2026-10-18",
        ElectionType.us_senate_model,
    )
    with db.session() as session:
        selected = latest_senate_run(session)
        assert selected is not None and selected.id == expected.id


def test_latest_senate_export_does_not_fall_back_to_a_custom_map(db: Database) -> None:
    custom = db.add_map("Custom Senate", parliament="us_senate")
    db.add_election(
        custom.id, 2028, "US Senate UNS 2028-10-19",
        ElectionType.us_senate_model,
    )
    with db.session() as session:
        assert latest_senate_run(session) is None


def test_cli_retrospective_does_not_bind_today_outside_pinned_cycles(
    db: Database, senate_world: UsModelSpec, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LaterDate(date):
        @classmethod
        def today(cls) -> LaterDate:
            return cls(2035, 1, 1)

    monkeypatch.setattr(_common, "date", LaterDate)
    monkeypatch.setattr(sys, "argv", [
        "senate", "--start-date", "2026-10-19", "--end-date", "2026-10-19",
        "--dry-run", "--no-uncertainty",
    ])
    assert _common.main_for_spec(senate_world, lambda: db) == 0
