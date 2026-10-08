"""Database EV authority across historical and presidential target years."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import joinedload

DATA_DIR = Path(__file__).resolve().parents[1]
for directory in (
    DATA_DIR / "models" / "us",
    DATA_DIR / "old_data" / "scripts" / "usa",
):
    sys.path.insert(0, str(directory))

import _common as us
from console.services.model_outputs import build_output_detail_context
from db import Database
from electoral_votes import (
    ElectoralVoteError,
    get_electoral_votes,
    get_electoral_votes_sqlite,
)
from import_presidential_elections import import_presidential
from model_support import trends
from model_support.persistence import OutputScope, OutputVote, replace_output
from model_support.trends import reconstruct_trends
from models import Election, ElectionType, Map, Seat, USElectoralVoteAllocation
from scripts.export_elections import _export_page
from scripts.migrate_us_electoral_votes import migrate
from tests.us_ev_fixtures import seed_canonical_allocations, set_model_target


@dataclass(frozen=True)
class PresidentialWorld:
    map_id: int
    party_id: int
    other_party_id: int
    seat_id: int
    baseline_id: int
    spec: us.UsModelSpec


def presidential_world(db: Database, tmp_path: Path) -> PresidentialWorld:
    seed_canonical_allocations(db)
    election_map = db.add_map("Presidential map", parliament="us_presidential")
    party = db.add_party("Democratic")
    other_party = db.add_party("Republican")
    seat = db.add_seat(election_map.id, "California", electoral_votes=999)
    baseline = db.add_election(
        election_map.id, 2024, "Baseline", ElectionType.us_presidential
    )
    db.add_vote(baseline.id, seat.id, party_id=party.id, vote_total=60, elected=True)
    db.add_vote(baseline.id, seat.id, party_id=other_party.id, vote_total=40)
    spec = us.UsModelSpec(
        map_name=election_map.name,
        baseline_election_name=baseline.name,
        election_type="us_presidential_model",
        election_name_prefix="US President UNS",
        trend_cache_json=tmp_path / "trends.json",
        trend_cache_meta_json=tmp_path / "meta.json",
        target_election_year=2028,
    )
    return PresidentialWorld(
        election_map.id, party.id, other_party.id, seat.id, baseline.id, spec
    )


def export_payload(
    db: Database, tmp_path: Path, election_id: int
) -> dict[str, Any]:
    destination = tmp_path / "output.json"
    with db.session() as session:
        election = session.scalars(
            select(Election)
            .where(Election.id == election_id)
            .options(joinedload(Election.map))
        ).one()
        _export_page(
            session=session,
            elections=[election],
            output_root=tmp_path,
            parliaments={"us_presidential"},
            args=argparse.Namespace(
                dry_run=False, output_file=destination, legacy_files_dir=tmp_path
            ),
            manifest_parties=[],
            manifest_regions_by_map_id={},
            has_electorate=True,
            single_election_mode=True,
        )
    return dict(json.loads(destination.read_text()))


@pytest.mark.parametrize("years", [(2020, 2024), (2024, 2020)])
def test_import_order_refresh_and_replace_do_not_own_weights(
    db: Database, tmp_path: Path, years: tuple[int, int]
) -> None:
    seed_canonical_allocations(db)
    for name in (
        "Democratic", "Republican", "Libertarian", "US Green", "Independent", "Others"
    ):
        db.add_party(name)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({
        "California": {
            "seatInfo": {"current": "democrat", "electoral_votes": -999},
            "partyInfo": {"democrat": {"name": "D", "total": 60}},
        },
        "Maine CD-1": {
            "seatInfo": {"current": "republican"},
            "partyInfo": {"republican": {"name": "R", "total": 40}},
        },
    }))
    for year in years:
        import_presidential(db, source, year, f"Actual {year}")
    for year in years:
        import_presidential(db, source, year, f"Actual {year}", refresh=True)
    with db.session() as session:
        for seat in session.scalars(select(Seat)):
            assert seat.electoral_votes is None
            seat.electoral_votes = 888
        allocation = session.get(USElectoralVoteAllocation, (2020, "California"))
        assert allocation is not None
        allocation.electoral_votes = 52
    for year, expected in [(2020, 55), (2024, 52)]:
        election = db.get_election_by_name(f"Actual {year}")
        assert election is not None
        payload = export_payload(db, tmp_path, election.id)
        units = {unit["n"]: unit for unit in payload["seats"]}
        assert units["California"]["ev"] == expected
        assert units["Maine CD-1"]["ev"] == 1
    import_presidential(db, source, 2024, "Replaced actual", replace=True)
    with db.session() as session:
        assert get_electoral_votes(session, 2024, ["California"]) == {"California": 52}
        assert get_electoral_votes(session, 2020, ["California"]) == {"California": 55}
        assert len(session.scalars(select(USElectoralVoteAllocation)).all()) == 392


def test_import_missing_bootstrap_fails_before_replace(
    db: Database, tmp_path: Path
) -> None:
    with db.session() as session:
        session.add(Map(id=22, name="Existing", parliament="us_presidential"))
    seat = db.add_seat(22, "California")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"California": {}}))
    with pytest.raises(ElectoralVoteError, match="migrate_us_electoral_votes"):
        import_presidential(db, source, 2024, "Missing era", replace=True)
    assert db.get_map(22) is not None
    assert [row.id for row in db.get_seats_for_map(22)] == [seat.id]


def test_actual_and_model_exports_follow_their_own_allocation(
    db: Database, tmp_path: Path
) -> None:
    world = presidential_world(db, tmp_path)
    model = db.add_election(
        world.map_id, 2026, "US President UNS 2026-06-01",
        ElectionType.us_presidential_model,
    )
    set_model_target(db, model.id, 2020)
    db.add_vote(
        model.id, world.seat_id, party_id=world.party_id, vote_total=60, elected=True
    )
    actual = export_payload(db, tmp_path, world.baseline_id)
    forecast = export_payload(db, tmp_path, model.id)
    assert actual["schema"] == forecast["schema"] == "pf-results-v4"
    assert actual["seats"][0]["ev"] == 54
    assert forecast["seats"][0]["ev"] == 55
    with db.session() as session:
        allocation = session.get(USElectoralVoteAllocation, (2010, "California"))
        assert allocation is not None
        allocation.electoral_votes = 61
    assert export_payload(db, tmp_path, model.id)["seats"][0]["ev"] == 61
    assert export_payload(db, tmp_path, world.baseline_id)["seats"][0]["ev"] == 54
    previous = (tmp_path / "output.json").read_bytes()
    with db.session() as session:
        session.execute(delete(USElectoralVoteAllocation).where(
            USElectoralVoteAllocation.era_year == 2010,
            USElectoralVoteAllocation.unit_name == "California",
        ))
    with pytest.raises(ElectoralVoteError, match="California"):
        export_payload(db, tmp_path, model.id)
    assert (tmp_path / "output.json").read_bytes() == previous


def test_forecast_dry_run_persistence_and_retargeting_agree(
    db: Database, tmp_path: Path
) -> None:
    world = presidential_world(db, tmp_path)
    cfg = us.UsSimulationConfig(
        spec=world.spec,
        as_of_date=date(2026, 6, 1),
        since_date=date(2026, 5, 1),
        half_life_days=30,
        dry_run=True,
        target_election_year=2020,
    )
    dry_result = us.run_simulation(db, cfg)
    persisted = us.run_simulation(db, dataclasses.replace(cfg, dry_run=False))
    assert dry_result[5] == persisted[5] == {"Democratic": 55}
    election = db.get_election_by_name(persisted[0])
    assert election is not None
    assert election.year == 2026
    assert election.election_date == date(2026, 6, 1)
    assert election.target_election_year == 2020
    with db.session() as session:
        allocation = session.get(USElectoralVoteAllocation, (2020, "California"))
        assert allocation is not None
        allocation.electoral_votes = 50
    retargeted = us.run_simulation(
        db, dataclasses.replace(cfg, dry_run=False, target_election_year=2028)
    )
    assert retargeted[5] == {"Democratic": 50}
    election = db.get_election_by_name(retargeted[0])
    assert election is not None and election.target_election_year == 2028
    with db.session() as session:
        assert len(session.scalars(select(Election).where(
            Election.type == ElectionType.us_presidential_model
        )).all()) == 1
    before = db.get_votes_for_election(election.id)
    with pytest.raises(ElectoralVoteError, match="2032"):
        us.run_simulation(
            db, dataclasses.replace(cfg, dry_run=False, target_election_year=2032)
        )
    unchanged = db.get_election_by_name(retargeted[0])
    assert unchanged is not None and unchanged.target_election_year == 2028
    assert [vote.id for vote in db.get_votes_for_election(election.id)] == [
        vote.id for vote in before
    ]


def test_replacement_failure_preserves_old_votes_and_target(
    db: Database, only_the_test_database: Path, tmp_path: Path
) -> None:
    world = presidential_world(db, tmp_path)
    scope = OutputScope(
        "us_presidential_model", world.map_id, world.spec.election_name_prefix
    )
    as_of = date(2026, 6, 1)
    name = f"{scope.name_prefix} {as_of}"
    votes = [OutputVote(world.seat_id, world.party_id, "D", 60, True)]
    replace_output(
        only_the_test_database, scope, as_of, name, votes, target_election_year=2020
    )
    with closing(sqlite3.connect(only_the_test_database)) as conn, conn:
        before = list(conn.iterdump())
        conn.execute(
            "CREATE TRIGGER fail_vote BEFORE INSERT ON votes "
            "BEGIN SELECT RAISE(ABORT, 'failed vote'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="failed vote"):
        replace_output(
            only_the_test_database, scope, as_of, name, votes, target_election_year=2028
        )
    with closing(sqlite3.connect(only_the_test_database)) as conn, conn:
        conn.execute("DROP TRIGGER fail_vote")
        assert list(conn.iterdump()) == before


def test_trends_and_console_resolve_each_election_year(
    db: Database, only_the_test_database: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = presidential_world(db, tmp_path)
    scope = OutputScope(
        "us_presidential_model", world.map_id, world.spec.election_name_prefix
    )
    ids = []
    for day, target in [(1, 2020), (2, 2028), (3, 2020)]:
        as_of = date(2026, 6, day)
        _, eid = replace_output(
            only_the_test_database, scope, as_of, f"{scope.name_prefix} {as_of}",
            [OutputVote(world.seat_id, world.party_id, "D", 60, True)],
            target_election_year=target,
        )
        ids.append(eid)
    requested: list[tuple[int, int]] = []
    real_reader = get_electoral_votes_sqlite

    def record_reader(
        conn: sqlite3.Connection, year: int, units: set[str]
    ) -> dict[str, int]:
        assert conn.in_transaction
        requested.append((id(conn), year))
        return real_reader(conn, year, units)

    monkeypatch.setattr(trends, "get_electoral_votes_sqlite", record_reader)
    entries = reconstruct_trends(only_the_test_database, scope)
    assert sorted(year for _, year in requested) == [2020, 2028]
    assert len({connection_id for connection_id, _ in requested}) == 1
    assert [entry["parties"][str(world.party_id)]["e"] for entry in entries] == [
        55, 54, 55
    ]
    context = build_output_detail_context(
        db, election_id=ids[0], election_type=ElectionType.us_presidential_model,
        baseline_types=[ElectionType.us_presidential], page=1,
    )
    assert context is not None
    party = next(
        row for row in context["party_totals"] if row["party_name"] == "Democratic"
    )
    assert party["electoral_votes"] == 55
    assert party["ev_diff_vs_base"] == 1
    with db.session() as session:
        allocation = session.get(USElectoralVoteAllocation, (2010, "California"))
        assert allocation is not None
        allocation.electoral_votes = 60
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [entry["parties"][str(world.party_id)]["e"] for entry in entries] == [
        60, 54, 60
    ]


def test_canonical_presidential_map_supports_forecasts_and_trends(
    db: Database, tmp_path: Path
) -> None:
    world = presidential_world(db, tmp_path)
    with db.session() as session:
        election_map = session.get(Map, world.map_id)
        assert election_map is not None
        election_map.parliament = "us_presidential"
    cfg = us.UsSimulationConfig(
        spec=world.spec,
        as_of_date=date(2026, 6, 1),
        since_date=date(2026, 5, 1),
        half_life_days=30,
        dry_run=False,
    )
    assert us.run_simulation(db, cfg)[5] == {"Democratic": 54}


def test_legacy_label_requires_migration_not_runtime_alias(
    db: Database, tmp_path: Path
) -> None:
    world = presidential_world(db, tmp_path)
    with db.session() as session:
        election_map = session.get(Map, world.map_id)
        assert election_map is not None
        election_map.parliament = "us_president"
    cfg = us.UsSimulationConfig(
        spec=world.spec,
        as_of_date=date(2026, 6, 1),
        since_date=date(2026, 5, 1),
        half_life_days=30,
        dry_run=False,
    )
    with pytest.raises(ValueError, match="does not belong"):
        us.run_simulation(db, cfg)
    with closing(sqlite3.connect(db.config.database_path)) as conn:
        migrate(conn, dry_run=False, legacy_forecast_target_year=2028)
    assert us.run_simulation(db, cfg)[5] == {"Democratic": 54}


@pytest.mark.parametrize(
    "mode",
    ["single", "retrospective", "automatic", "rebuild", "metadata", "retarget_gap"],
)
def test_cli_target_survives_every_config_path(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    world = presidential_world(db, tmp_path)
    pollster = db.add_pollster("National", "national")
    for day in [1, 3]:
        when = date(2026, 6, day)
        poll = db.add_poll(pollster.id, world.map_id, when, when)
        db.add_poll_row(poll.id, world.party_id, 60)
        db.add_poll_row(poll.id, world.other_party_id, 40)
    spec = world.spec
    scope = OutputScope(spec.election_type, world.map_id, spec.election_name_prefix)
    if mode in {"automatic", "rebuild", "metadata", "retarget_gap"}:
        days = [1, 3] if mode in {"metadata", "retarget_gap"} else [1]
        for day in days:
            when = date(2026, 6, day)
            replace_output(
                Path(db.config.database_path),
                scope,
                when,
                f"{scope.name_prefix} {when}",
                [OutputVote(world.seat_id, world.party_id, "D", 60, True)],
                target_election_year=2020 if mode == "metadata" and day == 3 else 2028,
            )
    class FixedDate(date):
        @classmethod
        def today(cls) -> FixedDate:
            return cls(2026, 6, 5)

    monkeypatch.setattr(us, "date", FixedDate)
    flags = ["--target-election-year", "2020", "--since-date", "2026-05-06"]
    if mode == "retrospective":
        flags += ["--start-date", "2026-06-01", "--end-date", "2026-06-03"]
    elif mode == "rebuild":
        flags += ["--rebuild-history"]
    else:
        flags += ["--as-of-date", "2026-06-05"]
    runs: list[us.UsSimulationConfig] = []
    real_run = us.run_simulation

    def record_run(
        database: Database, cfg: us.UsSimulationConfig, **kwargs: Any
    ) -> tuple[Any, ...]:
        runs.append(cfg)
        return tuple(real_run(database, cfg, **kwargs))

    monkeypatch.setattr(us, "run_simulation", record_run)
    monkeypatch.setattr(sys, "argv", ["model.py", *flags])
    assert us.main_for_spec(spec, db_factory=lambda: db) == 0
    assert runs and all(cfg.target_election_year == 2020 for cfg in runs)
    if mode == "metadata":
        assert [(cfg.as_of_date.day, cfg.dry_run) for cfg in runs] == [
            (2, False), (3, True)
        ]
    if mode == "retarget_gap":
        assert [(cfg.as_of_date.day, cfg.dry_run) for cfg in runs] == [
            (2, False), (3, False)
        ]
    if mode not in {"retrospective", "metadata"}:
        assert max(cfg.as_of_date for cfg in runs) == date(2026, 6, 3)
    if mode != "retrospective":
        metadata = json.loads(spec.trend_cache_meta_json.read_text())
        assert metadata["target_election_year"] == 2020
    with db.session() as session:
        models = session.scalars(select(Election).where(
            Election.type == ElectionType.us_presidential_model
        )).all()
        changed = {cfg.as_of_date for cfg in runs if not cfg.dry_run}
        assert all(
            election.target_election_year == 2020
            for election in models
            if election.election_date in changed
        )
        if mode in {"metadata", "retarget_gap", "automatic"}:
            historical = next(
                election for election in models
                if election.election_date == date(2026, 6, 1)
            )
            assert historical.target_election_year == 2028
        if mode == "retarget_gap":
            current = next(
                election for election in models
                if election.election_date == date(2026, 6, 3)
            )
            assert current.target_election_year == 2020


@pytest.mark.parametrize(
    "election_type",
    ["us_house_model", "us_senate_model", "model_uns", "holyrood_uns"],
)
def test_nonpresidential_replacement_accepts_legacy_schema(
    tmp_path: Path, election_type: str
) -> None:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE elections "
            "(id INTEGER PRIMARY KEY, map_id INTEGER, year INTEGER, "
            "name TEXT, type TEXT, election_date TEXT)"
        )
    scope = OutputScope(election_type, 1, "Forecast")
    replace_output(path, scope, date(2026, 6, 1), "Forecast 2026-06-01", [])
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT year FROM elections").fetchone() == (2026,)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(elections)")}
        assert "target_election_year" not in columns


def test_legacy_presidential_trends_report_required_migration(
    db: Database, only_the_test_database: Path, tmp_path: Path
) -> None:
    world = presidential_world(db, tmp_path)
    with closing(sqlite3.connect(only_the_test_database)) as conn, conn:
        conn.execute("ALTER TABLE elections DROP COLUMN target_election_year")
    scope = OutputScope(
        "us_presidential_model", world.map_id, world.spec.election_name_prefix
    )
    with pytest.raises(ElectoralVoteError, match="migrate_us_electoral_votes"):
        reconstruct_trends(only_the_test_database, scope)


@pytest.mark.parametrize("raw_target", ["0", "-1", "invalid", "2028.5"])
def test_invalid_cli_target_is_rejected_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_target: str
) -> None:
    spec = us.UsModelSpec(
        map_name="Unused", baseline_election_name="Unused",
        election_type="us_presidential_model", election_name_prefix="US President UNS",
        trend_cache_json=tmp_path / "trends.json",
        trend_cache_meta_json=tmp_path / "meta.json", target_election_year=2028,
    )

    def no_database() -> Database:
        pytest.fail("invalid target must not open a database")

    monkeypatch.setattr(sys, "argv", ["model.py", "--target-election-year", raw_target])
    with pytest.raises(SystemExit) as error:
        us.main_for_spec(spec, db_factory=no_database)
    assert error.value.code == 2


def test_cli_target_is_presidential_only_and_defaults_to_2028(tmp_path: Path) -> None:
    spec = us.UsModelSpec(
        map_name="Unused", baseline_election_name="Unused",
        election_type="us_presidential_model", election_name_prefix="US President UNS",
        trend_cache_json=tmp_path / "trends.json",
        trend_cache_meta_json=tmp_path / "meta.json", target_election_year=2028,
    )
    parser = us.build_arg_parser(spec)
    assert parser.parse_args([]).target_election_year == 2028
    override = parser.parse_args(["--target-election-year", "2020"])
    assert override.target_election_year == 2020
    for kind in ["us_house_model", "us_senate_model"]:
        nonpresidential = dataclasses.replace(
            spec, election_type=kind, target_election_year=None
        )
        with pytest.raises(SystemExit):
            us.build_arg_parser(nonpresidential).parse_args(
                ["--target-election-year", "2028"]
            )
