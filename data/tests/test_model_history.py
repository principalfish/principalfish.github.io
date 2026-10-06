"""History recomputation preserves dates independently across all five models."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "us"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "holyrood"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "westminster"))

import _common
import run_holyrood_uns_model
import run_uns_model
from _common import UsSimulationConfig
from db import Database
from model_support import trends
from model_support.history import HistoryRecomputationError
from model_support.io import OutputPublicationError, publish_json
from model_support.persistence import OutputScope, OutputVote, replace_output
from model_support.trends import TREND_MODELS, reconstruct_trends

from tests import test_holyrood_model as holy
from tests import test_us_model as us
from tests import test_westminster_model as west
from tests.uk_fixtures import seed_holyrood_world


@dataclasses.dataclass
class HistoryCase:
    module: Any
    function: str
    scope: OutputScope
    seat: int
    party: int
    cache: Path
    args: Any
    run_batch: Callable[[], None]
    spec: Any = None


@pytest.fixture(params=list(TREND_MODELS))
def history_case(
    request: pytest.FixtureRequest,
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> HistoryCase:
    model = request.param
    args: Any
    definition = TREND_MODELS[model]
    monkeypatch.setattr(
        trends, "default_trend_path", lambda _: tmp_path / "unused.json"
    )
    if model == "westminster":
        world = request.getfixturevalue("westminster_world")
        cache, _ = west._guard_writes(tmp_path, monkeypatch, only_the_test_database)
        args = west._retrospective_args(
            monkeypatch, world, "--start-date", "2026-06-01", "--end-date", "2026-06-03"
        )
        module, function = run_uns_model, "run_simulation"
        run_batch = lambda: module.run_retrospective(db, args)
        map_id = world.map_id
    elif model == "holyrood":
        world = seed_holyrood_world(db)
        outputs = holy._guard_writes(tmp_path, monkeypatch, only_the_test_database)
        cache = outputs.trend
        args = holy._retro_args(
            start_date="2026-06-01",
            end_date="2026-06-03",
            reset_existing=True,
            dry_run=False,
            election_name=world.constituency_election_name,
        )
        module, function = run_holyrood_uns_model, "run_holyrood_simulation"
        run_batch = lambda: module.run_retrospective(db, args)
        baseline = db.get_election_by_name(world.constituency_election_name)
        assert baseline is not None
        map_id = baseline.map_id
    else:
        dem, rep = us._parties(db)
        election_map, _ = us._seat_map_with_baseline(
            db, model, definition.parliament, {"Seat": {dem.id: 60, rep.id: 40}}
        )
        map_id = election_map.id
        spec = dataclasses.replace(
            us._us_spec(tmp_path, map_name=election_map.name),
            election_type=definition.election_type,
            election_name_prefix=definition.name_prefix,
        )
        cache = spec.trend_cache_json
        module, function = _common, "run_simulation"
        args = module.build_arg_parser(spec).parse_args([])
        run_batch = lambda: module.run_retrospective_range(
            db,
            spec,
            args,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 3),
            lookback_days=365,
            reset_existing=True,
        )
    with closing(sqlite3.connect(only_the_test_database)) as conn:
        seat = conn.execute(
            "SELECT id FROM seats WHERE map_id=? LIMIT 1", (map_id,)
        ).fetchone()[0]
        party = conn.execute("SELECT id FROM parties LIMIT 1").fetchone()[0]
    case = HistoryCase(
        module,
        function,
        OutputScope(definition.election_type, map_id, definition.name_prefix),
        seat,
        party,
        cache,
        args,
        run_batch,
    )
    if model.startswith("us-"):
        case.spec = spec
    return case


def store_old(path: Path, case: HistoryCase, day: int) -> None:
    as_of = date(2026, 6, day)
    replace_output(
        path,
        case.scope,
        as_of,
        f"{case.scope.name_prefix} {as_of}",
        [OutputVote(case.seat, case.party, "old candidate", 17, True)],
    )


def rows(path: Path, scope: OutputScope) -> dict[str, tuple[tuple[Any, ...], ...]]:
    with closing(sqlite3.connect(path)) as conn:
        return {
            name: tuple(
                conn.execute(
                    "SELECT seat_id,party_id,candidate_name,vote_total,elected FROM votes "
                    "WHERE election_id=? ORDER BY seat_id,party_id",
                    (eid,),
                )
            )
            for eid, name in conn.execute(
                "SELECT id,name FROM elections WHERE map_id=? AND type=? ORDER BY name",
                (scope.map_id, scope.election_type),
            )
        }


@pytest.mark.parametrize("continue_on_error", [False, True])
@pytest.mark.parametrize("failed_day", [1, 2])
def test_failed_date_retains_old_rows_and_finalizes_actual_database(
    history_case: HistoryCase,
    db: Database,
    only_the_test_database: Path,
    monkeypatch: pytest.MonkeyPatch,
    continue_on_error: bool,
    failed_day: int,
) -> None:
    case = history_case
    for day in (1, 2, 3, 4):
        store_old(only_the_test_database, case, day)
    foreign_map = db.add_map("Other output map", parliament="other")
    foreign_scope = dataclasses.replace(
        case.scope, map_id=foreign_map.id, name_prefix="Unrelated"
    )
    foreign_seat = db.add_seat(foreign_map.id, "Other seat")
    replace_output(
        only_the_test_database,
        foreign_scope,
        date(2026, 6, 2),
        "Unrelated 2026-06-02",
        [OutputVote(foreign_seat.id, case.party, "untouched", 42, True)],
    )
    foreign_before = rows(only_the_test_database, foreign_scope)
    before = rows(only_the_test_database, case.scope)
    case.args.continue_on_error = continue_on_error
    real_run = getattr(case.module, case.function)
    calls: list[date] = []

    def fail(database: Database, cfg: Any, **kwargs: Any) -> Any:
        calls.append(cfg.as_of_date)
        if cfg.as_of_date.day == failed_day:
            raise RuntimeError("injected calculation failure")
        return real_run(database, cfg, **kwargs)

    monkeypatch.setattr(case.module, case.function, fail)
    expected_error = HistoryRecomputationError if continue_on_error else RuntimeError
    with pytest.raises(expected_error, match="injected calculation failure") as error:
        case.run_batch()
    after = rows(only_the_test_database, case.scope)
    for day in (1, 2, 3, 4):
        name = f"{case.scope.name_prefix} 2026-06-{day:02}"
        replaced = (
            day != failed_day and day <= 3 and (continue_on_error or day < failed_day)
        )
        assert (after[name] != before[name]) is replaced
    assert len(calls) == (3 if continue_on_error else failed_day)
    if continue_on_error:
        assert f"2026-06-{failed_day:02}" in str(error.value)
    assert rows(only_the_test_database, foreign_scope) == foreign_before
    assert json.loads(case.cache.read_text()) == reconstruct_trends(
        only_the_test_database, case.scope
    )


@pytest.mark.parametrize("failed_day", [None, 2])
def test_publication_failure_reports_committed_dates_and_keeps_old_cache(
    history_case: HistoryCase,
    only_the_test_database: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_day: int | None,
) -> None:
    case = history_case
    case.cache.parent.mkdir(parents=True, exist_ok=True)
    case.cache.write_text('[{"old":true}]')
    case.args.continue_on_error = True
    real_run = getattr(case.module, case.function)

    def fail_date(database: Database, cfg: Any, **kwargs: Any) -> Any:
        if cfg.as_of_date.day == failed_day:
            raise RuntimeError("middle date failed")
        return real_run(database, cfg, **kwargs)

    def fail_publish(source: Path, destination: Path) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr(case.module, case.function, fail_date)
    monkeypatch.setattr("model_support.io.os.replace", fail_publish)
    with pytest.raises(OutputPublicationError) as error:
        case.run_batch()
    assert "Database dates committed in this batch: 2026-06-01" in str(error.value)
    assert "2026-06-03" in str(error.value)
    assert "rebuild_model_trends.py" in str(error.value)
    if failed_day:
        assert "middle date failed" in str(error.value)
    assert case.cache.read_text() == '[{"old":true}]'
    assert not list(case.cache.parent.glob(".*.tmp"))


def test_invalid_output_is_rejected_before_replacement(
    history_case: HistoryCase,
    only_the_test_database: Path,
) -> None:
    case = history_case
    store_old(only_the_test_database, case, 1)
    before = rows(only_the_test_database, case.scope)
    case.cache.mkdir(parents=True)
    with pytest.raises(ValueError, match="not a file"):
        case.run_batch()
    assert rows(only_the_test_database, case.scope) == before


def test_wrong_map_scope_is_rejected_before_replacement(
    history_case: HistoryCase,
    only_the_test_database: Path,
) -> None:
    case = history_case
    store_old(only_the_test_database, case, 1)
    before = rows(only_the_test_database, case.scope)
    with closing(sqlite3.connect(only_the_test_database)) as conn, conn:
        conn.execute(
            "UPDATE maps SET parliament='wrong' WHERE id=?", (case.scope.map_id,)
        )
    with pytest.raises(ValueError, match="does not belong"):
        case.run_batch()
    assert rows(only_the_test_database, case.scope) == before
    assert not case.cache.exists()


def test_invalid_baseline_does_not_replace_results_or_republish_cache(
    history_case: HistoryCase,
    db: Database,
    only_the_test_database: Path,
) -> None:
    case = history_case
    name = (
        case.spec.baseline_election_name
        if case.spec is not None
        else case.args.election_name
        if case.function == "run_holyrood_simulation"
        else case.args.baseline_election_name
    )
    baseline = db.get_election_by_name(name)
    assert baseline is not None
    with closing(sqlite3.connect(only_the_test_database)) as conn, conn:
        conn.execute("DELETE FROM votes WHERE election_id=?", (baseline.id,))
    store_old(only_the_test_database, case, 1)
    before = rows(only_the_test_database, case.scope)
    case.cache.parent.mkdir(parents=True, exist_ok=True)
    case.cache.write_text('[{"previous":true}]')
    with pytest.raises(ValueError, match="votes"):
        case.run_batch()
    assert rows(only_the_test_database, case.scope) == before
    assert case.cache.read_text() == '[{"previous":true}]'


@pytest.mark.parametrize(
    "history_case", ["us-house", "us-senate", "us-president"], indirect=True
)
@pytest.mark.parametrize("mode", ["retrospective", "rebuild"])
def test_missing_us_baseline_override_is_rejected_before_publication(
    history_case: HistoryCase,
    db: Database,
    only_the_test_database: Path,
    mode: str,
) -> None:
    case = history_case
    store_old(only_the_test_database, case, 1)
    before = rows(only_the_test_database, case.scope)
    spec = dataclasses.replace(case.spec, seat_baseline_overrides={"Seat": "missing"})
    with pytest.raises(ValueError, match="Unknown baseline election"):
        if mode == "retrospective":
            case.module.run_retrospective_range(
                db,
                spec,
                case.args,
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 3),
                lookback_days=365,
                reset_existing=True,
            )
        else:
            cfg = UsSimulationConfig(
                spec=spec,
                as_of_date=date(2026, 6, 3),
                since_date=date(2026, 5, 1),
                half_life_days=30,
                dry_run=False,
            )
            case.module._rebuild_history(
                db,
                spec,
                case.args,
                cfg,
                first_poll=date(2026, 6, 1),
                lookback_days=365,
            )
    assert rows(only_the_test_database, case.scope) == before
    assert not case.cache.exists()


def test_first_date_and_publication_failure_reports_no_commits(
    history_case: HistoryCase,
    only_the_test_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = history_case
    store_old(only_the_test_database, case, 1)
    before = rows(only_the_test_database, case.scope)

    def fail_date(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("first date failed")

    def fail_publish(source: Path, destination: Path) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr(case.module, case.function, fail_date)
    monkeypatch.setattr("model_support.io.os.replace", fail_publish)
    with pytest.raises(OutputPublicationError) as error:
        case.run_batch()
    assert "No database dates committed in this batch" in str(error.value)
    assert "first date failed" in str(error.value)
    assert rows(only_the_test_database, case.scope) == before


def test_database_destination_is_rejected_before_replacement(
    history_case: HistoryCase,
    db: Database,
    only_the_test_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = history_case
    store_old(only_the_test_database, case, 1)
    before = rows(only_the_test_database, case.scope)
    if case.spec is not None:
        spec = dataclasses.replace(case.spec, trend_cache_json=only_the_test_database)
        case.run_batch = lambda: case.module.run_retrospective_range(
            db,
            spec,
            case.args,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 3),
            lookback_days=365,
            reset_existing=True,
        )
    else:
        name = (
            "HOLYROOD_TREND_CACHE_JSON"
            if case.function == "run_holyrood_simulation"
            else "TREND_CACHE_JSON"
        )
        monkeypatch.setattr(case.module, name, only_the_test_database)
    with pytest.raises(ValueError, match="is the database"):
        case.run_batch()
    assert rows(only_the_test_database, case.scope) == before


@pytest.mark.parametrize(
    "history_case", ["us-house", "us-senate", "us-president"], indirect=True
)
def test_us_cli_stops_after_partial_rebuild_without_metadata(
    history_case: HistoryCase,
    db: Database,
    only_the_test_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = history_case
    assert case.spec is not None
    for day in (1, 2, 3, 4, 5):
        store_old(only_the_test_database, case, day)
    before = rows(only_the_test_database, case.scope)
    real_run = getattr(case.module, case.function)
    calls: list[date] = []

    def fail_middle(database: Database, cfg: Any, **kwargs: Any) -> Any:
        calls.append(cfg.as_of_date)
        if cfg.as_of_date.day == 3:
            raise RuntimeError("middle CLI failure")
        return real_run(database, cfg, **kwargs)

    monkeypatch.setattr(case.module, case.function, fail_middle)
    monkeypatch.setattr(
        case.module,
        "candidate_poll_date_bounds",
        lambda *args, **kwargs: (date(2026, 6, 2), date(2026, 6, 4), None),
    )
    monkeypatch.setattr(
        sys, "argv", ["runner.py", "--rebuild-history", "--continue-on-error"]
    )
    with pytest.raises(HistoryRecomputationError, match="2026-06-03"):
        case.module.main_for_spec(case.spec, db_factory=lambda: db)
    assert calls == [date(2026, 6, day) for day in (2, 3, 4)]
    assert not case.spec.trend_cache_meta_json.exists()
    after = rows(only_the_test_database, case.scope)
    for day in (1, 3, 5):
        name = f"{case.scope.name_prefix} 2026-06-{day:02}"
        assert after[name] == before[name]


@pytest.mark.parametrize(
    "history_case", ["us-house", "us-senate", "us-president"], indirect=True
)
def test_us_metadata_preflight_precedes_rebuild(
    history_case: HistoryCase,
    db: Database,
    only_the_test_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = history_case
    assert case.spec is not None
    store_old(only_the_test_database, case, 1)
    before = rows(only_the_test_database, case.scope)
    spec = dataclasses.replace(case.spec, trend_cache_meta_json=only_the_test_database)
    monkeypatch.setattr(sys, "argv", ["runner.py", "--rebuild-history"])
    with pytest.raises(ValueError, match="is the database"):
        case.module.main_for_spec(spec, db_factory=lambda: db)
    assert rows(only_the_test_database, case.scope) == before
    assert not case.cache.exists()


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize(
    "history_case", ["us-house", "us-senate", "us-president"], indirect=True
)
def test_us_rebuild_prunes_only_after_all_replacements_succeed(
    history_case: HistoryCase,
    db: Database,
    only_the_test_database: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail: bool,
) -> None:
    case = history_case
    assert case.spec is not None
    for day in (1, 2, 3, 4, 5):
        store_old(only_the_test_database, case, day)
    case.args.continue_on_error = True
    real_run = getattr(case.module, case.function)
    publication_count = 0
    real_publish = publish_json

    def count_publish(*args: Any, **kwargs: Any) -> None:
        nonlocal publication_count
        publication_count += 1
        real_publish(*args, **kwargs)

    def fail_middle(database: Database, cfg: Any, **kwargs: Any) -> Any:
        if fail and cfg.as_of_date == date(2026, 6, 3):
            raise RuntimeError("middle rebuild failure")
        return real_run(database, cfg, **kwargs)

    monkeypatch.setattr(case.module, case.function, fail_middle)
    monkeypatch.setattr(trends, "publish_json", count_publish)
    cfg = UsSimulationConfig(
        spec=case.spec,
        as_of_date=date(2026, 6, 4),
        since_date=date(2026, 5, 1),
        half_life_days=30,
        dry_run=False,
    )

    def rebuild() -> None:
        case.module._rebuild_history(
            db,
            case.spec,
            case.args,
            cfg,
            first_poll=date(2026, 6, 2),
            lookback_days=365,
        )

    if fail:
        with pytest.raises(HistoryRecomputationError, match="2026-06-03"):
            rebuild()
    else:
        rebuild()
    dates = rows(only_the_test_database, case.scope)
    assert len(dates) == (5 if fail else 3)
    for day in (1, 5):
        assert (f"{case.scope.name_prefix} 2026-06-{day:02}" in dates) is fail
    assert publication_count == 1
    assert json.loads(case.cache.read_text()) == reconstruct_trends(
        only_the_test_database, case.scope
    )
