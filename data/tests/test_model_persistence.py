"""Atomic replacement and ownership boundaries for all five model outputs."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

for engine in ("westminster", "holyrood", "us"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / engine))

import _common as us
import run_holyrood_uns_model as holyrood
import run_uns_model as westminster
from db import Database
from model_support.persistence import OutputScope, output_dates
from models import ElectionType

from tests.uk_fixtures import seed_holyrood_world, seed_westminster_world

AS_OF = date(2026, 6, 1)
KINDS = ("westminster", "holyrood", "us_house", "us_senate", "us_presidential")
ROWS = [
    {"seat_id": 1, "party_id": 1, "vote_total": 60.0, "elected": True},
    {"seat_id": 1, "party_id": 2, "vote_total": 40.0, "elected": False},
]


@dataclass(frozen=True)
class Adapter:
    kind: str
    scope: OutputScope
    path: Path
    trend: Path
    spec: us.UsModelSpec | None

    def persist(self, rows: list[dict[str, Any]]) -> tuple[str, int]:
        name = f"{self.scope.name_prefix} {AS_OF.isoformat()}"
        args = (self.scope.map_id, AS_OF, name, rows, {1: "A", 2: "B"}, self.path)
        if self.kind == "westminster":
            return cast(tuple[str, int], westminster.persist_projection(*args))
        if self.kind == "holyrood":
            return cast(tuple[str, int], holyrood.persist_projection(*args))
        assert self.spec is not None
        return cast(tuple[str, int], us.persist_projection(self.spec, *args))

    def dates(self) -> set[date]:
        if self.kind == "westminster":
            return cast(
                set[date],
                westminster.existing_trend_dates(
                    self.trend, self.path, map_id=self.scope.map_id
                ),
            )
        if self.kind == "holyrood":
            return cast(
                set[date],
                holyrood.existing_trend_dates(
                    self.trend, self.path, map_id=self.scope.map_id
                ),
            )
        assert self.spec is not None
        return cast(
            set[date],
            us.existing_trend_dates(self.spec, self.path, map_id=self.scope.map_id),
        )

    def delete(self, *, reset: bool) -> tuple[int, int]:
        if self.kind == "westminster":
            if reset:
                return cast(
                    tuple[int, int],
                    westminster.reset_existing_model_outputs(
                        AS_OF, AS_OF, self.path, self.trend, map_id=self.scope.map_id
                    )[:2],
                )
            return cast(
                tuple[int, int],
                westminster.delete_model_uns_for_as_of_date(
                    AS_OF, self.path, map_id=self.scope.map_id
                ),
            )
        if self.kind == "holyrood":
            if reset:
                return cast(
                    tuple[int, int],
                    holyrood.reset_existing_model_outputs(
                        AS_OF, AS_OF, self.path, self.trend, map_id=self.scope.map_id
                    )[:2],
                )
            return cast(
                tuple[int, int],
                holyrood.delete_holyrood_uns_for_as_of_date(
                    AS_OF, self.path, map_id=self.scope.map_id
                ),
            )
        assert self.spec is not None
        if reset:
            return cast(
                tuple[int, int],
                us.reset_existing_model_outputs(
                    self.spec, AS_OF, AS_OF, self.path, map_id=self.scope.map_id
                )[:2],
            )
        return cast(
            tuple[int, int],
            us.delete_model_for_as_of_date(
                self.spec, AS_OF, self.path, map_id=self.scope.map_id
            ),
        )


@pytest.fixture(params=KINDS)
def adapter(
    request: pytest.FixtureRequest,
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
) -> Adapter:
    kind = str(request.param)
    primary_map = db.add_map("Primary output map")
    prefix = {
        "westminster": "UNS",
        "holyrood": "Holyrood UNS",
        "us_house": "US House UNS",
        "us_senate": "US Senate UNS",
        "us_presidential": "US President UNS",
    }[kind]
    election_type = {
        "westminster": "model_uns",
        "holyrood": "holyrood_uns",
    }.get(kind, f"{kind}_model")
    trend = tmp_path / "trends.json"
    spec = us.UsModelSpec(
        map_name=primary_map.name,
        baseline_election_name="Unused baseline",
        election_type=election_type,
        election_name_prefix=prefix,
        trend_cache_json=trend,
        trend_cache_meta_json=tmp_path / "meta.json",
    )
    return Adapter(
        kind,
        OutputScope(election_type, primary_map.id, prefix),
        only_the_test_database,
        trend,
        spec,
    )


def _snapshot(path: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    with closing(sqlite3.connect(path)) as conn:
        return (
            conn.execute("SELECT * FROM elections ORDER BY id").fetchall(),
            conn.execute("SELECT * FROM votes ORDER BY id").fetchall(),
        )


def test_insert_failure_preserves_complete_old_result(adapter: Adapter) -> None:
    adapter.persist(ROWS)
    before = _snapshot(adapter.path)
    with closing(sqlite3.connect(adapter.path)) as conn, conn:
        conn.execute(
            "CREATE TRIGGER fail_second_vote BEFORE INSERT ON votes "
            "WHEN NEW.party_id = 2 BEGIN SELECT RAISE(ABORT, 'second vote'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="second vote"):
        adapter.persist([{**row, "vote_total": 123.0} for row in ROWS])
    assert _snapshot(adapter.path) == before
    with closing(sqlite3.connect(adapter.path)) as conn, conn:
        conn.execute("DROP TRIGGER fail_second_vote")
    adapter.persist([{**row, "vote_total": 123.0} for row in ROWS])
    elections, votes = _snapshot(adapter.path)
    assert len(elections) == 1
    assert len(votes) == 2
    assert [row[5] for row in votes] == [123.0, 123.0]


@pytest.mark.parametrize("foreign", ["map", "type"])
def test_foreign_same_named_result_survives_uniqueness_conflict(
    adapter: Adapter, foreign: str
) -> None:
    scope = adapter.scope
    with closing(sqlite3.connect(adapter.path)) as conn, conn:
        old_cursor = conn.execute(
            "INSERT INTO elections (map_id, year, name, type, election_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                scope.map_id,
                2026,
                f"{scope.name_prefix} {AS_OF} (rerun)",
                scope.election_type,
                str(AS_OF),
            ),
        )
        conn.execute(
            "INSERT INTO votes (election_id, seat_id, party_id, vote_total, elected) "
            "VALUES (?, 1, 1, 888, 1)",
            (old_cursor.lastrowid,),
        )
        cursor = conn.execute(
            "INSERT INTO elections (map_id, year, name, type, election_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                scope.map_id + 1 if foreign == "map" else scope.map_id,
                2026,
                f"{scope.name_prefix} {AS_OF}",
                "uk_general" if foreign == "type" else scope.election_type,
                str(AS_OF),
            ),
        )
        conn.execute(
            "INSERT INTO votes (election_id, seat_id, party_id, vote_total, elected) "
            "VALUES (?, 1, 1, 777, 1)",
            (cursor.lastrowid,),
        )
    before = _snapshot(adapter.path)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        adapter.persist(ROWS)
    assert _snapshot(adapter.path) == before


@pytest.mark.parametrize("reset", [False, True], ids=["single", "range"])
def test_deletion_and_dates_require_type_map_and_supported_name(
    adapter: Adapter, reset: bool
) -> None:
    _, old_id = adapter.persist(ROWS)
    scope = adapter.scope
    with closing(sqlite3.connect(adapter.path)) as conn, conn:
        for map_id, kind, name in (
            (
                scope.map_id + 1,
                scope.election_type,
                f"{scope.name_prefix} {AS_OF} other",
            ),
            (scope.map_id, "uk_general", f"{scope.name_prefix} {AS_OF} actual"),
            (scope.map_id + 1, scope.election_type, f"{scope.name_prefix} 2026-05-02"),
            (scope.map_id, "uk_general", f"{scope.name_prefix} 2026-05-03"),
            (scope.map_id, scope.election_type, f"{scope.name_prefix} {AS_OF}junk"),
            (scope.map_id, scope.election_type, f"{scope.name_prefix} 2026-05-04junk"),
            (scope.map_id, scope.election_type, f"{scope.name_prefix} 2026-13-45"),
            (scope.map_id, scope.election_type, "Unrelated 2026-06-01"),
            (scope.map_id, scope.election_type, f"{scope.name_prefix} 2026-06-02"),
        ):
            cursor = conn.execute(
                "INSERT INTO elections (map_id, year, name, type) VALUES (?,2026,?,?)",
                (map_id, name, kind),
            )
            conn.execute(
                "INSERT INTO votes (election_id, seat_id, party_id, vote_total, elected) "
                "VALUES (?, 1, 1, 777, 0)",
                (cursor.lastrowid,),
            )
        conn.execute(
            "INSERT INTO elections (map_id, year, name, type) VALUES (?,2026,?,?)",
            (scope.map_id, f"{scope.name_prefix} {AS_OF} (rerun)", scope.election_type),
        )
    # Unverified dates from another cache must not affect scoped bookkeeping.
    adapter.trend.write_text(json.dumps([{"as_of_date": "2026-05-01"}]))
    assert adapter.dates() == {AS_OF, date(2026, 6, 2)}
    before_elections, before_votes = _snapshot(adapter.path)
    assert adapter.delete(reset=reset) == (2, 2)
    after_elections, after_votes = _snapshot(adapter.path)
    assert after_elections == [
        row
        for row in before_elections
        if row[0] != old_id and not row[3].endswith(" (rerun)")
    ]
    assert after_votes == [row for row in before_votes if row[1] != old_id]
    assert adapter.dates() == {date(2026, 6, 2)}


@pytest.mark.parametrize("kind", KINDS)
def test_runner_failure_rolls_back_then_successful_rerun_replaces_once(
    kind: str,
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run: Callable[[], object]
    if kind == "westminster":
        world = seed_westminster_world(db)
        scope = OutputScope("model_uns", world.map_id, "UNS")
        cfg = westminster.SimulationConfig(
            map_name=world.map_name,
            baseline_election_name=world.baseline_election_name,
            as_of_date=AS_OF,
            since_date=date(2026, 5, 1),
            half_life_days=30.0,
            output_csv=None,
            dry_run=False,
        )
        run = lambda: westminster.run_simulation(db, cfg)
        monkeypatch.setattr(
            westminster, "update_trend_cache_json", lambda *_a, **_k: None
        )
    elif kind == "holyrood":
        holy_world = seed_holyrood_world(db)
        scope = OutputScope("holyrood_uns", holy_world.map_id, "Holyrood UNS")
        holy_cfg = holyrood.HolyroodSimulationConfig(
            constituency_election_name=holy_world.constituency_election_name,
            as_of_date=AS_OF,
            since_date=date(2026, 5, 1),
            dry_run=False,
        )
        run = lambda: holyrood.run_holyrood_simulation(db, holy_cfg)
        monkeypatch.setattr(holyrood, "update_trend_cache_json", lambda *_a, **_k: None)
    else:
        seat_map = db.add_map("Runner map", parliament=kind)
        region = db.add_region(seat_map.id, "Region")
        seat = db.add_seat(seat_map.id, "Alabama", region_id=region.id)
        dem = db.add_party("Democratic")
        rep = db.add_party("Republican")
        baseline = db.add_election(
            seat_map.id,
            2024,
            "Baseline",
            ElectionType(kind),
        )
        db.add_vote(
            baseline.id,
            seat.id,
            candidate_name="A",
            vote_total=600.0,
            elected=True,
            party_id=dem.id,
        )
        db.add_vote(
            baseline.id,
            seat.id,
            candidate_name="B",
            vote_total=400.0,
            elected=False,
            party_id=rep.id,
        )
        spec = us.UsModelSpec(
            map_name=seat_map.name,
            baseline_election_name=baseline.name,
            election_type=f"{kind}_model",
            election_name_prefix=f"{kind} UNS",
            trend_cache_json=tmp_path / "trends.json",
            trend_cache_meta_json=tmp_path / "meta.json",
        )
        scope = OutputScope(spec.election_type, seat_map.id, spec.election_name_prefix)
        us_cfg = us.UsSimulationConfig(
            spec=spec,
            as_of_date=AS_OF,
            since_date=date(2026, 5, 1),
            half_life_days=30.0,
            dry_run=False,
        )
        run = lambda: us.run_simulation(db, us_cfg)
        monkeypatch.setattr(us, "update_trend_cache_json", lambda *_a, **_k: None)

    run()
    before = _snapshot(only_the_test_database)
    with closing(sqlite3.connect(only_the_test_database)) as conn, conn:
        model_id = conn.execute(
            "SELECT id FROM elections WHERE type = ? AND map_id = ?",
            (scope.election_type, scope.map_id),
        ).fetchone()[0]
        seat_id, party_id = conn.execute(
            "SELECT seat_id, party_id FROM votes WHERE election_id = ? ORDER BY id LIMIT 1 OFFSET 1",
            (model_id,),
        ).fetchone()
        conn.execute(
            f"CREATE TRIGGER fail_runner_vote BEFORE INSERT ON votes "
            f"WHEN NEW.seat_id = {int(seat_id)} AND NEW.party_id = {int(party_id)} "
            "BEGIN SELECT RAISE(ABORT, 'runner vote failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="runner vote failure"):
        run()
    assert _snapshot(only_the_test_database) == before
    with closing(sqlite3.connect(only_the_test_database)) as conn, conn:
        conn.execute("DROP TRIGGER fail_runner_vote")
    run()
    assert output_dates(only_the_test_database, scope) == {AS_OF}
    assert _snapshot(only_the_test_database) == before
