"""Database reconstruction, scoped repair and complete atomic publication."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
from db import Database
from model_support import trends
from model_support.io import OutputPublicationError, publish_json
from model_support.persistence import (
    OutputScope,
    OutputVote,
    delete_outputs,
    output_dates,
    replace_output,
)
from model_support.trends import (
    TREND_MODELS,
    publish_trends,
    reconstruct_trends,
    trend_batch,
)
from scripts.rebuild_model_trends import main
from tests.us_ev_fixtures import seed_allocations


def setup_scope(db: Database, model: str) -> tuple[OutputScope, list[int], list[int]]:
    definition = TREND_MODELS[model]
    if model == "us-president":
        seed_allocations(db, {"Maine": 2, "Maine CD-1": 1})
    election_map = db.add_map("Test map", parliament=definition.parliament)
    party_ids = [db.add_party("A").id, db.add_party("B").id]
    seats = [
        db.add_seat(election_map.id, name, electoral_votes=ev).id
        for name, ev in [("Maine", 2), ("Maine CD-1", 1), ("Region List 1", 0)]
    ]
    return (
        OutputScope(definition.election_type, election_map.id, definition.name_prefix),
        seats,
        party_ids,
    )


def store(
    path: Path,
    scope: OutputScope,
    day: int,
    seat: int,
    parties: list[int],
    share: float,
    *,
    winner: int = 0,
) -> None:
    as_of = date(2026, 6, day)
    replace_output(
        path,
        scope,
        as_of,
        f"{scope.name_prefix} {as_of}",
        [
            OutputVote(seat, parties[i], "", votes, i == winner)
            for i, votes in enumerate((share, 100 - share))
        ],
        target_election_year=2028 if scope.election_type == "us_presidential_model" else None,
    )


@pytest.mark.parametrize("model", TREND_MODELS)
def test_full_reconstruction_ignores_cache_and_insertion_order(
    db: Database, only_the_test_database: Path, tmp_path: Path, model: str
) -> None:
    scope, seats, parties = setup_scope(db, model)
    for day, share in [(4, 70), (2, 60), (3, 60), (1, 70)]:
        store(only_the_test_database, scope, day, seats[0], parties, share)
    destination = tmp_path / "cache.json"
    destination.write_text("malformed-cache")
    publish_trends(only_the_test_database, scope, destination)
    entries = json.loads(destination.read_text())
    assert [entry["as_of_date"] for entry in entries] == [
        "2026-06-01",
        "2026-06-02",
        "2026-06-04",
    ]
    assert entries[0]["election_id"] > entries[1]["election_id"]
    assert len(output_dates(only_the_test_database, scope)) == 4
    store(only_the_test_database, scope, 2, seats[0], parties, 70)
    publish_trends(only_the_test_database, scope, destination)
    assert [entry["as_of_date"] for entry in json.loads(destination.read_text())] == [
        "2026-06-01",
        "2026-06-03",
        "2026-06-04",
    ]
    # Replacing a former change can also remove its now-redundant successor.
    store(only_the_test_database, scope, 3, seats[0], parties, 70)
    assert [
        entry["as_of_date"]
        for entry in reconstruct_trends(only_the_test_database, scope)
    ] == ["2026-06-01"]


@pytest.mark.parametrize("model", TREND_MODELS)
def test_pruning_recovers_hidden_successor(
    db: Database, only_the_test_database: Path, model: str
) -> None:
    scope, seats, parties = setup_scope(db, model)
    for day, share in [(1, 70), (2, 60), (3, 60), (4, 70)]:
        store(only_the_test_database, scope, day, seats[0], parties, share)
    delete_outputs(only_the_test_database, scope, date(2026, 6, 2), date(2026, 6, 2))
    assert [
        entry["as_of_date"]
        for entry in reconstruct_trends(only_the_test_database, scope)
    ] == ["2026-06-01", "2026-06-03", "2026-06-04"]


def test_ev_only_change_survives_compression(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "us-president")
    store(only_the_test_database, scope, 1, seats[0], parties, 60)
    store(only_the_test_database, scope, 2, seats[1], parties, 60)
    entries = reconstruct_trends(only_the_test_database, scope)
    assert len(entries) == 2
    assert entries[0]["parties"][str(parties[0])] == {"s": 1, "v": 60.0, "e": 2}
    assert entries[1]["parties"][str(parties[0])] == {"s": 1, "v": 60.0, "e": 1}


@pytest.mark.parametrize("model", ["holyrood", "us-president"])
def test_popular_votes_use_nonduplicated_units(
    db: Database, only_the_test_database: Path, model: str
) -> None:
    scope, seats, parties = setup_scope(db, model)
    child = seats[2] if model == "holyrood" else seats[1]
    as_of = date(2026, 6, 1)
    replace_output(
        only_the_test_database,
        scope,
        as_of,
        f"{scope.name_prefix} {as_of}",
        [
            OutputVote(seats[0], parties[0], "", 60, True),
            OutputVote(seats[0], parties[1], "", 40, False),
            OutputVote(child, parties[1], "", 10000, True),
        ],
        target_election_year=2028 if model == "us-president" else None,
    )
    entry = reconstruct_trends(only_the_test_database, scope)[0]
    assert entry["parties"][str(parties[0])]["v"] == 60.0
    assert entry["parties"][str(parties[1])]["v"] == 40.0
    assert entry["parties"][str(parties[1])]["s"] == 1


@pytest.mark.parametrize("bad_scope", ["wrong-map", "mixed-votes"])
def test_scope_rejected_before_publishing(
    db: Database, only_the_test_database: Path, tmp_path: Path, bad_scope: str
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    other = db.add_map("Other", parliament="holyrood")
    other_seat = db.add_seat(other.id, "Foreign")
    store(
        only_the_test_database,
        scope,
        1,
        other_seat.id if bad_scope == "mixed-votes" else seats[0],
        parties,
        60,
    )
    if bad_scope == "wrong-map":
        scope = OutputScope(scope.election_type, other.id, scope.name_prefix)
    destination = tmp_path / "cache.json"
    destination.write_text("[]")
    with pytest.raises(ValueError):
        publish_trends(only_the_test_database, scope, destination)
    assert destination.read_text() == "[]"


def test_same_prefix_foreign_type_and_map_are_ignored(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store(only_the_test_database, scope, 1, seats[0], parties, 60)
    other = db.add_map("Other", parliament="westminster")
    other_seat = db.add_seat(other.id, "Other seat")
    for foreign in [
        OutputScope("model_uns", other.id, "UNS"),
        OutputScope("model_run", scope.map_id, "UNS"),
    ]:
        store(
            only_the_test_database,
            foreign,
            2 if foreign.map_id == other.id else 3,
            other_seat.id if foreign.map_id == other.id else seats[0],
            parties,
            30,
        )
    assert len(reconstruct_trends(only_the_test_database, scope)) == 1


def test_publication_failure_preserves_complete_old_file_and_repair(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    destination = tmp_path / "cache.json"
    publish_json([{"old": True}], destination, repair="retry")
    old = destination.read_bytes()
    store(only_the_test_database, scope, 1, seats[0], parties, 60)

    def fail(source: Path, target: Path) -> None:
        raise OSError("replace failed")

    with monkeypatch.context() as patch:
        patch.setattr("model_support.io.os.replace", fail)
        with pytest.raises(
            OutputPublicationError, match=r"--model westminster.*--database"
        ):
            publish_trends(only_the_test_database, scope, destination)
    assert destination.read_bytes() == old
    assert not list(tmp_path.glob(".*.tmp"))
    assert (
        main(
            [
                "--model",
                "westminster",
                "--map-id",
                str(scope.map_id),
                "--database",
                str(only_the_test_database),
                "--output",
                str(destination),
            ]
        )
        == 0
    )
    assert json.loads(destination.read_text()) == reconstruct_trends(
        only_the_test_database, scope
    )


def test_batch_scans_once_and_finalizes_partial_failure(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    destination = tmp_path / "cache.json"
    calls = []
    real_reconstruct = trends.reconstruct_trends

    def counted(path: Path, requested: OutputScope) -> list[trends.TrendEntry]:
        calls.append(requested)
        return real_reconstruct(path, requested)

    monkeypatch.setattr(trends, "reconstruct_trends", counted)
    with (
        pytest.raises(RuntimeError, match="calculation"),
        trend_batch(only_the_test_database, scope, destination),
    ):
        for day in [1, 2]:
            store(only_the_test_database, scope, day, seats[0], parties, 60 + day)
            publish_trends(only_the_test_database, scope, destination)
        raise RuntimeError("calculation")
    assert len(calls) == 1
    assert len(json.loads(destination.read_text())) == 2
    publish_trends(only_the_test_database, scope, destination)
    assert len(calls) == 2


def test_combined_failure_reports_both(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope, _, _ = setup_scope(db, "westminster")

    def fail(source: Path, target: Path) -> None:
        raise OSError("disk")

    monkeypatch.setattr("model_support.io.os.replace", fail)
    with (
        pytest.raises(
            OutputPublicationError, match="Original model failure: calculation"
        ) as error,
        trend_batch(only_the_test_database, scope, tmp_path / "cache.json"),
    ):
        raise RuntimeError("calculation")
    assert isinstance(error.value.__cause__, RuntimeError)


@pytest.mark.parametrize("model", TREND_MODELS)
def test_cli_dry_run_uses_explicit_scope_and_never_writes(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    scope, seats, parties = setup_scope(db, model)
    store(only_the_test_database, scope, 1, seats[0], parties, 60)
    output = tmp_path / "cache.json"
    output.write_text("corrupt")
    before = only_the_test_database.read_bytes()
    monkeypatch.setattr(
        trends, "default_trend_path", lambda _: tmp_path / "default.json"
    )
    assert (
        main(
            [
                "--model",
                model,
                "--map-id",
                str(scope.map_id),
                "--database",
                str(only_the_test_database),
                "--output",
                str(output),
                "--dry-run",
            ]
        )
        == 0
    )
    assert output.read_text() == "corrupt"
    assert only_the_test_database.read_bytes() == before
    assert not (tmp_path / "default.json").exists()


def test_reconstruction_reads_one_snapshot_during_replacement(
    db: Database, only_the_test_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store(only_the_test_database, scope, 1, seats[0], parties, 60)
    with closing(sqlite3.connect(only_the_test_database)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    original_validate = trends._validate_scope

    def replace_after_validation(
        conn: sqlite3.Connection, requested: OutputScope
    ) -> None:
        original_validate(conn, requested)
        # Validation has established the read snapshot. The replacement commits
        # before metadata/metrics reads, which must still see the prior result.
        store(only_the_test_database, scope, 1, seats[0], parties, 80)

    with monkeypatch.context() as patch:
        patch.setattr(trends, "_validate_scope", replace_after_validation)
        entries = reconstruct_trends(only_the_test_database, scope)
    assert entries[0]["parties"][str(parties[0])]["v"] == 60.0
    assert (
        reconstruct_trends(only_the_test_database, scope)[0]["parties"][
            str(parties[0])
        ]["v"]
        == 80.0
    )


@pytest.mark.parametrize("model", TREND_MODELS)
def test_identical_serialized_metrics_and_zero_totals(
    db: Database, only_the_test_database: Path, model: str
) -> None:
    scope, seats, parties = setup_scope(db, model)
    day1 = date(2026, 6, 1)
    day2 = date(2026, 6, 2)
    for day in [day1, day2]:
        replace_output(
            only_the_test_database,
            scope,
            day,
            f"{scope.name_prefix} {day}",
            [
                OutputVote(seats[0], party, "", 0, i == 0)
                for i, party in enumerate(parties)
            ],
            target_election_year=2028 if model == "us-president" else None,
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert len(entries) == 1
    assert entries[0]["parties"][str(parties[1])]["v"] == 0.0
    assert ("e" in entries[0]["parties"][str(parties[0])]) == (model == "us-president")


def test_cli_missing_source_never_creates_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(SystemExit):
        main(
            [
                "--model",
                "westminster",
                "--map-id",
                "1",
                "--database",
                str(missing),
                "--output",
                str(tmp_path / "cache.json"),
            ]
        )
    assert not missing.exists()


def test_cli_refuses_database_as_destination(
    db: Database, only_the_test_database: Path
) -> None:
    scope, _, _ = setup_scope(db, "westminster")
    before = only_the_test_database.read_bytes()
    with pytest.raises(SystemExit):
        main(
            [
                "--model",
                "westminster",
                "--map-id",
                str(scope.map_id),
                "--database",
                str(only_the_test_database),
                "--output",
                str(only_the_test_database),
            ]
        )
    assert only_the_test_database.read_bytes() == before
