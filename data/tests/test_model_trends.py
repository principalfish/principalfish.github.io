"""Database reconstruction, scoped repair and complete atomic publication."""

from __future__ import annotations

import itertools
import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from core.db import Database
from models.support import trends
from models.support.io import OutputPublicationError, publish_json
from models.support.persistence import (
    OutputScope,
    OutputVote,
    UncertaintyPayload,
    delete_outputs,
    output_dates,
    replace_output,
)
from models.support.trends import (
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
        target_election_year=2028
        if scope.election_type == "us_presidential_model"
        else None,
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
    # Replacing a former change can also remove its now-redundant successor
    # (Westminster still keeps its latest run, so its last point is current).
    store(only_the_test_database, scope, 3, seats[0], parties, 70)
    assert [
        entry["as_of_date"]
        for entry in reconstruct_trends(only_the_test_database, scope)
    ] == ["2026-06-01"] + (["2026-06-04"] if model == "westminster" else [])


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
        patch.setattr("models.support.io.os.replace", fail)
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

    monkeypatch.setattr("models.support.io.os.replace", fail)
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
    days = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
    for day in days:
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
    # Identical runs collapse; Westminster also keeps its latest one.
    assert [entry["as_of_date"] for entry in entries] == [
        "2026-06-01",
        *(["2026-06-03"] if model == "westminster" else []),
    ]
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


# ── Uncertainty keys ─────────────────────────────────────────────────────────


def national_block(
    party_ids: list[int],
    *,
    bands: tuple[tuple[float, float], ...] = ((55.86, 64.04), (35.95, 44.14)),
    pm: tuple[float, ...] | None = None,
    pl: tuple[float, ...] = (0.999, 0.001),
    hung: float = 0.269,
) -> dict[str, object]:
    """A stored national block: published fields plus private diagnostics.

    By default the first party holds every majority not hung, so the odds add
    up as the export requires.
    """
    if pm is None:
        pm = (round(1 - hung, 3), 0.0)
    return {
        "budget": {"parties": {"secret": 1}},
        "config_hash": "c0ffee",
        "kernel_version": "pf-westminster-mc-v1",
        "input_hash": "feed",
        "seed": 739901,
        "simulations": 10000,
        "interval": 0.9,
        "hung": hung,
        "parties": {
            str(party): {
                "v": list(band),
                "s": [0, 1, 1],
                "pm": majority,
                "pl": largest,
                "hist": {"start": 0, "counts": [5000, 5000]},
            }
            for party, band, majority, largest in zip(party_ids, bands, pm, pl)
        },
    }


def store_with_summary(
    path: Path,
    scope: OutputScope,
    day: int,
    seat: int,
    parties: list[int],
    share: float,
    national: dict[str, object] | None,
) -> None:
    as_of = date(2026, 6, day)
    replace_output(
        path,
        scope,
        as_of,
        f"{scope.name_prefix} {as_of}",
        [
            OutputVote(seat, parties[i], "", votes, i == 0)
            for i, votes in enumerate((share, 100 - share))
        ],
        uncertainty=None if national is None else UncertaintyPayload(national),
    )


def test_summary_adds_rounded_bands_nonzero_odds_and_hung(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(
        only_the_test_database,
        scope,
        1,
        seats[0],
        parties,
        60,
        national_block(parties),
    )
    (entry,) = reconstruct_trends(only_the_test_database, scope)
    assert entry["parties"] == {
        # 55.86 -> 55.9 and 44.14 -> 44.1: rounded to 1 dp, not truncated. The
        # mean seats sm come from the histogram (5,000 runs on 0, 5,000 on 1).
        str(parties[0]): {
            "s": 1,
            "v": 60.0,
            "vl": 55.9,
            "vh": 64.0,
            "sl": 0,
            "sh": 1,
            "sm": 0.5,
            "pm": 0.731,
            "pl": 0.999,
        },
        # A zero probability is left out; 0.001 is kept.
        str(parties[1]): {
            "s": 0,
            "v": 40.0,
            "vl": 36.0,
            "vh": 44.1,
            "sl": 0,
            "sh": 1,
            "sm": 0.5,
            "pl": 0.001,
        },
    }
    assert entry["hung"] == 0.269
    text = json.dumps(entry)
    for private in ("budget", "config_hash", "input_hash", "seed", "hist", "739901"):
        assert private not in text


def test_runs_without_a_summary_are_unchanged(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(only_the_test_database, scope, 1, seats[0], parties, 60, None)
    store_with_summary(
        only_the_test_database,
        scope,
        2,
        seats[0],
        parties,
        70,
        national_block(parties),
    )
    first, second = reconstruct_trends(only_the_test_database, scope)
    assert first == {
        "election_id": first["election_id"],
        "election_name": "UNS 2026-06-01",
        "as_of_date": "2026-06-01",
        "parties": {
            str(parties[0]): {"s": 1, "v": 60.0},
            str(parties[1]): {"s": 0, "v": 40.0},
        },
    }
    assert "hung" in second


def test_equal_projection_collapses_whatever_its_odds(
    db: Database, only_the_test_database: Path
) -> None:
    """The seed changes daily, so equal projections differ only by noise."""
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(
        only_the_test_database,
        scope,
        1,
        seats[0],
        parties,
        60,
        national_block(parties, hung=0.25),
    )
    store_with_summary(
        only_the_test_database,
        scope,
        2,
        seats[0],
        parties,
        60,
        national_block(parties, hung=0.30),
    )
    store_with_summary(
        only_the_test_database,
        scope,
        3,
        seats[0],
        parties,
        65,
        national_block(parties, hung=0.35),
    )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [(e["as_of_date"], e.get("hung")) for e in entries] == [
        ("2026-06-01", 0.25),
        ("2026-06-03", 0.35),
    ]


def test_latest_run_is_kept_with_its_mean_even_when_its_centrals_repeat(
    db: Database, only_the_test_database: Path
) -> None:
    """The tracker's last point must be the run the headline and table show."""
    scope, seats, parties = setup_scope(db, "westminster")
    for day, counts in ((1, [5000, 5000]), (2, [4000, 6000]), (3, [2000, 8000])):
        block = national_block(parties, hung=0.25 + day / 100)
        stored: Any = block["parties"]
        for party in stored.values():
            party["hist"]["counts"] = counts
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            60,
            block,
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    # Day 2 still collapses onto day 1; day 3, the latest, is kept.
    assert [(e["as_of_date"], e["hung"]) for e in entries] == [
        ("2026-06-01", 0.26),
        ("2026-06-03", 0.28),
    ]
    assert entries[-1]["parties"][str(parties[0])]["sm"] == 0.8
    assert entries[-1]["parties"][str(parties[0])]["s"] == 1


def test_latest_polled_run_is_kept_when_an_unpolled_run_follows(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, counts in ((1, 60, (2, 2)), (2, 60, (1, 1)), (3, 75, (0, 0))):
        block = polled_block(parties, counts)
        if day == 2:
            stored: Any = block["parties"]
            for party in stored.values():
                party["hist"]["counts"] = [2000, 8000]
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            block,
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    # The unpolled day 3 is left out, so day 2 is the latest keepable run.
    assert [e["as_of_date"] for e in entries] == ["2026-06-01", "2026-06-02"]
    assert entries[-1]["parties"][str(parties[0])]["sm"] == 0.8


def test_first_summary_is_kept_after_an_equal_run_without_one(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(only_the_test_database, scope, 1, seats[0], parties, 60, None)
    store_with_summary(
        only_the_test_database,
        scope,
        2,
        seats[0],
        parties,
        60,
        national_block(parties),
    )
    store_with_summary(
        only_the_test_database,
        scope,
        3,
        seats[0],
        parties,
        60,
        national_block(parties, hung=0.5),
    )
    # A changed latest run, so the equal one before it still collapses.
    store_with_summary(
        only_the_test_database,
        scope,
        4,
        seats[0],
        parties,
        65,
        national_block(parties, hung=0.6),
    )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [(e["as_of_date"], e.get("hung")) for e in entries] == [
        ("2026-06-01", None),
        ("2026-06-02", 0.269),
        ("2026-06-04", 0.6),
    ]


def test_equal_runs_preserve_summary_gaps(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(
        only_the_test_database,
        scope,
        1,
        seats[0],
        parties,
        60,
        national_block(parties),
    )
    store_with_summary(only_the_test_database, scope, 2, seats[0], parties, 60, None)
    # A later equal run must restart the bands after the explicit gap.
    store_with_summary(
        only_the_test_database,
        scope,
        3,
        seats[0],
        parties,
        60,
        national_block(parties, hung=0.5),
    )
    store_with_summary(only_the_test_database, scope, 4, seats[0], parties, 65, None)
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [(e["as_of_date"], e.get("hung")) for e in entries] == [
        ("2026-06-01", 0.269),
        ("2026-06-02", None),
        ("2026-06-03", 0.5),
        ("2026-06-04", None),
    ]


@pytest.mark.parametrize("field", ["config_hash", "kernel_version"])
def test_equal_central_runs_preserve_each_identity_boundary(
    db: Database,
    only_the_test_database: Path,
    field: str,
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day in range(1, 5):
        block = national_block(parties, hung=0.1 * day)
        if day in (2, 3):
            block[field] = "changed-identity"
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            60,
            block,
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [e["as_of_date"] for e in entries] == [
        "2026-06-01",
        "2026-06-02",
        "2026-06-04",
    ]
    assert entries[0]["segment_id"] == entries[2]["segment_id"]
    assert entries[0]["segment_id"] != entries[1]["segment_id"]
    assert isinstance(entries[0]["segment_id"], str)


@pytest.mark.parametrize("field", ["config_hash", "kernel_version"])
@pytest.mark.parametrize("value", [None, "", " ", 1])
def test_unknown_identity_runs_are_one_continuous_segment(
    db: Database,
    only_the_test_database: Path,
    field: str,
    value: object,
) -> None:
    """Legacy summaries without an identity continue each other, so equal ones collapse."""
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share in ((1, 60), (2, 60), (3, 60), (4, 65)):
        block = national_block(parties, hung=0.1 * day)
        if value is None:
            block.pop(field)
        else:
            block[field] = value
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            block,
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [e["as_of_date"] for e in entries] == ["2026-06-01", "2026-06-04"]
    assert all(e["segment_id"] is None for e in entries)


def _identity_block(parties: list[int], identity: str | None) -> dict[str, object]:
    """A national block with this config identity, or none (a legacy summary)."""
    block = national_block(parties)
    if identity is None:
        block.pop("config_hash")
    else:
        block["config_hash"] = identity
    return block


@pytest.mark.parametrize(
    ("identities", "kept_days"),
    [
        # Known -> unknown is continuous: equal central values collapse.
        (("a", None), [1]),
        (("a", None, "a"), [1]),
        # The first known identity after unknown ones draws no break but is kept,
        # so the tracker can see a later change away from it.
        ((None, "a"), [1, 2]),
        ((None, "a", "b"), [1, 2, 3]),
        # A known change is kept even across an unknown run in between.
        (("a", None, "b"), [1, 3]),
        (("a", "b", None), [1, 2]),
    ],
)
def test_mixed_known_and_unknown_identities_keep_each_known_change(
    db: Database,
    only_the_test_database: Path,
    identities: tuple[str | None, ...],
    kept_days: list[int],
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day, identity in enumerate(identities, start=1):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            60,
            _identity_block(parties, identity),
        )
    # A changed latest run (always kept), so the runs above collapse as they
    # would mid-history.
    last = len(identities) + 1
    store_with_summary(
        only_the_test_database,
        scope,
        last,
        seats[0],
        parties,
        65,
        _identity_block(parties, identities[-1]),
    )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [e["as_of_date"] for e in entries] == [
        f"2026-06-0{day}" for day in [*kept_days, last]
    ]


def test_known_change_after_a_kept_unknown_run_is_still_a_boundary(
    db: Database, only_the_test_database: Path
) -> None:
    """The comparison is with the last known identity, not the last kept run's."""
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, identity in ((1, 60, "a"), (2, 65, None), (3, 65, "b")):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            _identity_block(parties, identity),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [e["as_of_date"] for e in entries] == [
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
    ]
    assert entries[1]["segment_id"] is None


def _tracker_breaks(runs: list[tuple[str, bool, str | None]]) -> list[str]:
    """The poll tracker's break rule (polltracker.js) over (date, has hung, segment id).

    A break precedes a run where uncertainty appears or disappears, or where its
    known segment id differs from the last known one; unknown ids continue.
    """
    breaks: list[str] = []
    previous_uncertain: bool | None = None
    last_segment: str | None = None
    for as_of_date, uncertain, segment in runs:
        if previous_uncertain is not None and (
            uncertain != previous_uncertain
            or (
                uncertain
                and segment is not None
                and last_segment is not None
                and segment != last_segment
            )
        ):
            breaks.append(as_of_date)
        previous_uncertain = uncertain
        if uncertain and segment is not None:
            last_segment = segment
    return breaks


# Per run: no summary, a summary without identity, or a summary with identity a/b.
# "unpolled" (a summary whose budget shows no polls) appears only in the listed cases.
_RUN_KINDS = ("none", "unknown", "a", "b")


@pytest.mark.parametrize(
    "kinds",
    [
        *itertools.product(_RUN_KINDS, repeat=3),
        ("unknown", "a", "unknown", "b"),
        ("a", "unknown", "unknown", "a"),
        ("none", "unknown", "a", "none"),
        ("unpolled", "unpolled", "a", "a"),
        ("unpolled", "a", "unpolled", "b"),
        ("unpolled", "none", "unknown", "a"),
        ("unpolled", "unknown", "unpolled", "a"),
    ],
)
def test_kept_runs_carry_every_tracker_break(
    db: Database, only_the_test_database: Path, kinds: tuple[str, ...]
) -> None:
    """The tracker reads only kept runs; it must draw the breaks all runs would.

    A leading unpolled run counts as one without a summary; a later one is not
    part of the history at all.
    """
    scope, seats, parties = setup_scope(db, "westminster")
    stored: list[tuple[str, bool, str | None]] = []
    seen_polled = False
    for day, kind in enumerate(kinds, start=1):
        block: dict[str, object] | None
        if kind == "none":
            block = None
        elif kind == "unpolled":
            block = polled_block(parties, (0, 0))
        else:
            block = _identity_block(parties, None if kind == "unknown" else kind)
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            60,
            block,
        )
        as_of_date = f"2026-06-0{day}"
        if kind == "unpolled":
            if not seen_polled:
                stored.append((as_of_date, False, None))
            continue
        seen_polled = True
        segment = None if block is None or kind == "unknown" else kind
        stored.append((as_of_date, block is not None, segment))
    entries = reconstruct_trends(only_the_test_database, scope)
    exported = [(e["as_of_date"], "hung" in e, e.get("segment_id")) for e in entries]
    # Exported ids are hashes; map them back to the stored kind for the comparison.
    by_date = {as_of_date: segment for as_of_date, _, segment in stored}
    kept = [
        (as_of_date, uncertain, by_date[as_of_date] if segment else None)
        for as_of_date, uncertain, segment in exported
    ]
    assert _tracker_breaks(kept) == _tracker_breaks(stored)


@pytest.mark.parametrize("identity", [None, "a"])
def test_summary_appearing_or_disappearing_is_a_boundary(
    db: Database, only_the_test_database: Path, identity: str | None
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day, has_summary in ((1, False), (2, True), (3, True), (4, False)):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            60,
            _identity_block(parties, identity) if has_summary else None,
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [(e["as_of_date"], "hung" in e) for e in entries] == [
        ("2026-06-01", False),
        ("2026-06-02", True),
        ("2026-06-04", False),
    ]


# SHA-256 of '["c0ffee","pf-westminster-mc-v1"]', national_block's default identity.
# Pinned so a change to the canonical encoding (which would split every published
# trend into new segments) fails loudly.
DEFAULT_SEGMENT_ID = "f9fd7580b43e72e4d81987c90b04441cff95d11c35d2983d0c474d013b25d293"


def test_segment_id_is_sha256_of_canonical_config_and_kernel() -> None:
    assert trends._segment_id("c0ffee", "pf-westminster-mc-v1") == DEFAULT_SEGMENT_ID
    # Order matters: the pair is a JSON array, not a sorted set.
    assert trends._segment_id("pf-westminster-mc-v1", "c0ffee") != DEFAULT_SEGMENT_ID


def test_westminster_runs_order_compress_and_share_one_segment(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    # Stored out of date order; days 2 and 4 repeat their predecessor's projection.
    for day, share in [(4, 65), (1, 60), (5, 60), (2, 60), (3, 65)]:
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            national_block(parties, hung=round(0.1 * day, 1)),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [
        (e["as_of_date"], e["parties"][str(parties[0])]["v"], e["hung"])
        for e in entries
    ] == [
        ("2026-06-01", 60.0, 0.1),
        ("2026-06-03", 65.0, 0.3),
        ("2026-06-05", 60.0, 0.5),
    ]
    assert [e["segment_id"] for e in entries] == [DEFAULT_SEGMENT_ID] * 3


def polled_block(parties: list[int], counts: tuple[int, ...]) -> dict[str, object]:
    """A national block whose Westminster budget records these poll counts."""
    block = national_block(parties)
    block["budget"] = {
        "parties": {
            str(party): {"poll_count": count} for party, count in zip(parties, counts)
        }
    }
    return block


def test_run_with_no_polls_is_left_out(
    db: Database, only_the_test_database: Path
) -> None:
    """With nothing polled the run is the baseline with no error: a spike."""
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, counts in ((1, 60, (2, 2)), (2, 75, (0, 0)), (3, 65, (1, 1))):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            polled_block(parties, counts),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [e["as_of_date"] for e in entries] == ["2026-06-01", "2026-06-03"]


def test_leading_unpolled_runs_are_kept_central_only_and_collapse(
    db: Database, only_the_test_database: Path
) -> None:
    """Before any poll the run is the election result: kept, but without bands."""
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, counts in ((1, 75, (0, 0)), (2, 75, (0, 0)), (3, 60, (2, 2))):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            polled_block(parties, counts),
        )
    first, polled = reconstruct_trends(only_the_test_database, scope)
    assert first == {
        "election_id": first["election_id"],
        "election_name": "UNS 2026-06-01",
        "as_of_date": "2026-06-01",
        "parties": {
            str(parties[0]): {"s": 1, "v": 75.0},
            str(parties[1]): {"s": 0, "v": 25.0},
        },
    }
    assert polled["as_of_date"] == "2026-06-03"
    assert polled["hung"] == 0.269
    assert polled["segment_id"] == DEFAULT_SEGMENT_ID
    leader = polled["parties"][str(parties[0])]
    assert {"vl", "vh", "sl", "sh", "pm", "pl"} <= set(leader)


def test_leading_unpolled_runs_with_different_centrals_are_each_kept(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, counts in ((1, 75, (0, 0)), (2, 70, (0, 0)), (3, 60, (2, 2))):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            polled_block(parties, counts),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [
        (e["as_of_date"], e["parties"][str(parties[0])]["v"], "hung" in e)
        for e in entries
    ] == [
        ("2026-06-01", 75.0, False),
        ("2026-06-02", 70.0, False),
        ("2026-06-03", 60.0, True),
    ]
    assert all(
        set(fields) == {"s", "v"}
        for entry in entries[:2]
        for fields in entry["parties"].values()
    )


def test_history_of_only_unpolled_runs_is_central_only(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day in (1, 2, 3):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            75,
            polled_block(parties, (0, 0)),
        )
    # Equal runs collapse to the first, and the latest is kept.
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [entry["as_of_date"] for entry in entries] == ["2026-06-01", "2026-06-03"]
    for entry in entries:
        assert "hung" not in entry
        assert "segment_id" not in entry
        assert entry["parties"] == {
            str(parties[0]): {"s": 1, "v": 75.0},
            str(parties[1]): {"s": 0, "v": 25.0},
        }


def test_first_polled_run_keeps_its_uncertainty_after_equal_unpolled_runs(
    db: Database, only_the_test_database: Path
) -> None:
    """Equal central values, but uncertainty appears: a tracker break, so kept."""
    scope, seats, parties = setup_scope(db, "westminster")
    for day, counts in ((1, (0, 0)), (2, (0, 0)), (3, (1, 1)), (4, (1, 1))):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            60,
            polled_block(parties, counts),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    # Day 2 collapses onto day 1; day 4, equal to day 3, is the latest run.
    assert [(e["as_of_date"], "hung" in e) for e in entries] == [
        ("2026-06-01", False),
        ("2026-06-03", True),
        ("2026-06-04", True),
    ]
    assert "segment_id" not in entries[0]
    assert entries[1]["segment_id"] == DEFAULT_SEGMENT_ID


def test_unpolled_run_after_the_first_polled_one_is_still_left_out(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, counts in (
        (1, 75, (0, 0)),
        (2, 60, (2, 2)),
        (3, 75, (0, 0)),
        (4, 65, (1, 1)),
    ):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            polled_block(parties, counts),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [(e["as_of_date"], "hung" in e) for e in entries] == [
        ("2026-06-01", False),
        ("2026-06-02", True),
        ("2026-06-04", True),
    ]


def test_run_after_a_skipped_one_still_collapses_onto_the_last_entry(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, counts in (
        (1, 60, (2, 2)),
        (2, 75, (0, 0)),
        (3, 60, (1, 1)),
        (4, 65, (1, 1)),
    ):
        store_with_summary(
            only_the_test_database,
            scope,
            day,
            seats[0],
            parties,
            share,
            polled_block(parties, counts),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [e["as_of_date"] for e in entries] == ["2026-06-01", "2026-06-04"]


def test_run_with_any_polled_party_is_kept(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(
        only_the_test_database,
        scope,
        1,
        seats[0],
        parties,
        60,
        polled_block(parties, (1, 0)),
    )
    (entry,) = reconstruct_trends(only_the_test_database, scope)
    assert entry["as_of_date"] == "2026-06-01"


def test_parties_missing_from_either_side(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    stray = db.add_party("C").id
    # The block lists only the first trend party, plus one with no votes.
    store_with_summary(
        only_the_test_database,
        scope,
        1,
        seats[0],
        parties,
        60,
        national_block([parties[0], stray]),
    )
    (entry,) = reconstruct_trends(only_the_test_database, scope)
    assert set(entry["parties"]) == {str(parties[0]), str(parties[1])}
    assert entry["parties"][str(parties[1])] == {"s": 0, "v": 40.0}
    assert "vl" in entry["parties"][str(parties[0])]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda block: block.pop("hung"), "national.hung"),
        (lambda block: block["parties"]["1"].update(v=[70, 60]), "share interval"),
        (lambda block: block["parties"]["1"].update(pm=1.5), "outside 0-1"),
        (lambda block: block["parties"]["1"].update(pl=True), "expected a number"),
    ],
)
def test_malformed_summary_fails_reconstruction(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    change: Any,
    message: str,
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    assert parties[0] == 1
    block = national_block(parties)
    change(block)
    store_with_summary(only_the_test_database, scope, 1, seats[0], parties, 60, block)
    with pytest.raises(ValueError, match=message):
        reconstruct_trends(only_the_test_database, scope)
    # The CLI reports it as a failure rather than publishing a partial file.
    output = tmp_path / "cache.json"
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
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_unreadable_stored_format_fails_reconstruction(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(
        only_the_test_database,
        scope,
        1,
        seats[0],
        parties,
        60,
        national_block(parties),
    )
    with closing(sqlite3.connect(only_the_test_database)) as conn:
        conn.execute("UPDATE model_uncertainty SET schema = 'pf-model-uncertainty-v0'")
        conn.commit()
    with pytest.raises(ValueError, match="unreadable.*pf-model-uncertainty-v0"):
        reconstruct_trends(only_the_test_database, scope)


def test_only_dated_runs_summaries_are_read(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(only_the_test_database, scope, 1, seats[0], parties, 60, None)
    # In the scope's type and map, but not a dated run, with a block that
    # would fail if it were read.
    with closing(sqlite3.connect(only_the_test_database)) as conn:
        undated = conn.execute(
            "INSERT INTO elections (map_id, year, name, type) VALUES (?,?,?,?)",
            (scope.map_id, 2026, "UNS scratch", scope.election_type),
        ).lastrowid
        conn.execute(
            "INSERT INTO model_uncertainty (election_id, schema, national_json) "
            "VALUES (?, 'pf-model-uncertainty-v1', '{\"bad\": true}')",
            (undated,),
        )
        conn.commit()
    (entry,) = reconstruct_trends(only_the_test_database, scope)
    assert "hung" not in entry


def test_full_backfill_trend_size(
    db: Database, only_the_test_database: Path, tmp_path: Path
) -> None:
    """A synthetic full history: 830 daily runs, every one with a summary.

    Shaped like the live Westminster history: 13 parties, 650 seats, and the
    projection changing on 323 of 824 days (equal days collapse). Every run's
    national block has a band for each party and three non-zero odds.
    """
    party_ids = [db.add_party(f"P{i}").id for i in range(13)]
    election_map = db.add_map("Synthetic", parliament="westminster")
    scope = OutputScope("model_uns", election_map.id, "UNS")
    with closing(sqlite3.connect(only_the_test_database)) as conn:
        seat_ids = [
            conn.execute(
                "INSERT INTO seats (map_id, seat_name) VALUES (?, ?)",
                (election_map.id, f"Seat {i}"),
            ).lastrowid
            for i in range(650)
        ]
        runs = 830
        for day in range(runs):
            as_of = date(2024, 7, 5) + timedelta(days=day)
            election_id = conn.execute(
                "INSERT INTO elections (map_id, year, name, type) VALUES (?,?,?,?)",
                (election_map.id, as_of.year, f"UNS {as_of}", "model_uns"),
            ).lastrowid
            # 39% of days move the projection, as 323 of 824 live days do.
            step = (day * 323) // 824
            leader_seats = 250 + step % 97
            winners = [
                party_ids[0 if seat < leader_seats else 1 + seat % 12]
                for seat in range(650)
            ]
            conn.executemany(
                "INSERT INTO votes (election_id, seat_id, party_id, candidate_name, "
                "vote_total, elected) VALUES (?,?,?,?,?,?)",
                [
                    (election_id, seat, winner, "", 31234.5 + step, 1)
                    for seat, winner in zip(seat_ids, winners)
                ]
                + [
                    (election_id, seat_ids[0], party, "", 1234.5 + step, 0)
                    for party in party_ids
                ],
            )
            hung = round(0.5 + day / 10000, 4)
            national = national_block(
                party_ids,
                bands=tuple((22.84 + i, 30.59 + i) for i in range(13)),
                pm=(round(1 - hung - 0.0002, 4), 0.0002) + (0.0,) * 11,
                pl=(0.9697, 0.0279, 0.0024) + (0.0,) * 10,
                hung=hung,
            )
            conn.execute(
                "INSERT INTO model_uncertainty (election_id, schema, national_json) "
                "VALUES (?,?,?)",
                (election_id, "pf-model-uncertainty-v1", json.dumps(national)),
            )
        conn.commit()
    destination = tmp_path / "trends.json"
    publish_trends(only_the_test_database, scope, destination)
    entries = json.loads(destination.read_text())
    assert 300 <= len(entries) <= 340
    assert all("hung" in entry for entry in entries)
    size = destination.stat().st_size
    # Includes both bounds and the 1 dp mean seats per party, and a
    # config/kernel segment hash per point.
    assert size < 360_000, size
