"""Database reconstruction, scoped repair and complete atomic publication."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from db import Database
from model_support import trends
from model_support.io import OutputPublicationError, publish_json
from model_support.persistence import (
    OutputScope,
    OutputVote,
    UncertaintyPayload,
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
    if model == "us-house":
        assert entries["schema"] == "pf-us-model-trends-v1"
        entries = entries["points"]
    assert [entry["as_of_date"] for entry in entries] == [
        "2026-06-01",
        "2026-06-02",
        *(["2026-06-03"] if model == "us-house" else []),
        "2026-06-04",
    ]
    assert entries[0]["election_id"] > entries[1]["election_id"]
    assert len(output_dates(only_the_test_database, scope)) == 4
    store(only_the_test_database, scope, 2, seats[0], parties, 70)
    publish_trends(only_the_test_database, scope, destination)
    republished = json.loads(destination.read_text())
    if model == "us-house":
        republished = republished["points"]
    assert [entry["as_of_date"] for entry in republished] == [
        "2026-06-01",
        *(["2026-06-02"] if model == "us-house" else []),
        "2026-06-03",
        "2026-06-04",
    ]
    # Replacing a former change can also remove its now-redundant successor.
    store(only_the_test_database, scope, 3, seats[0], parties, 70)
    assert [
        entry["as_of_date"]
        for entry in reconstruct_trends(only_the_test_database, scope)
    ] == (["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]
          if model == "us-house" else ["2026-06-01"])


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
    assert len(entries) == (2 if model == "us-house" else 1)
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
    pl: tuple[float, ...] = (0.9999, 0.0001),
    hung: float = 0.2688,
) -> dict[str, object]:
    """A stored national block: published fields plus private diagnostics.

    By default the first party holds every majority not hung, so the odds add
    up as the export requires.
    """
    if pm is None:
        pm = (round(1 - hung, 4), 0.0)
    return {
        "budget": {"parties": {"secret": 1}},
        "config_hash": "c0ffee",
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
        only_the_test_database, scope, 1, seats[0], parties, 60,
        national_block(parties),
    )
    (entry,) = reconstruct_trends(only_the_test_database, scope)
    assert entry["parties"] == {
        # 55.86 -> 55.9 and 44.14 -> 44.1: rounded to 1 dp, not truncated.
        str(parties[0]): {
            "s": 1, "v": 60.0, "vl": 55.9, "vh": 64.0, "sl": 0, "sh": 1, "pm": 0.7312, "pl": 0.9999,
        },
        # A zero probability is left out; 0.0001 is kept.
        str(parties[1]): {"s": 0, "v": 40.0, "vl": 36.0, "vh": 44.1, "sl": 0, "sh": 1, "pl": 0.0001},
    }
    assert entry["hung"] == 0.2688
    text = json.dumps(entry)
    for private in ("budget", "config_hash", "input_hash", "seed", "hist", "739901"):
        assert private not in text


def test_runs_without_a_summary_are_unchanged(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(only_the_test_database, scope, 1, seats[0], parties, 60, None)
    store_with_summary(
        only_the_test_database, scope, 2, seats[0], parties, 70,
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
        only_the_test_database, scope, 1, seats[0], parties, 60,
        national_block(parties, hung=0.25),
    )
    store_with_summary(
        only_the_test_database, scope, 2, seats[0], parties, 60,
        national_block(parties, hung=0.30),
    )
    store_with_summary(
        only_the_test_database, scope, 3, seats[0], parties, 65,
        national_block(parties, hung=0.35),
    )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [(e["as_of_date"], e.get("hung")) for e in entries] == [
        ("2026-06-01", 0.25),
        ("2026-06-03", 0.35),
    ]


def test_first_summary_is_kept_after_an_equal_run_without_one(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(only_the_test_database, scope, 1, seats[0], parties, 60, None)
    store_with_summary(
        only_the_test_database, scope, 2, seats[0], parties, 60,
        national_block(parties),
    )
    store_with_summary(
        only_the_test_database, scope, 3, seats[0], parties, 60,
        national_block(parties, hung=0.5),
    )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [(e["as_of_date"], e.get("hung")) for e in entries] == [
        ("2026-06-01", None),
        ("2026-06-02", 0.2688),
    ]


def test_equal_run_without_a_summary_after_one_with_is_dropped(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(
        only_the_test_database, scope, 1, seats[0], parties, 60,
        national_block(parties),
    )
    store_with_summary(only_the_test_database, scope, 2, seats[0], parties, 60, None)
    # A later equal run with a summary is still a duplicate of day 1.
    store_with_summary(
        only_the_test_database, scope, 3, seats[0], parties, 60,
        national_block(parties, hung=0.5),
    )
    store_with_summary(only_the_test_database, scope, 4, seats[0], parties, 65, None)
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [(e["as_of_date"], e.get("hung")) for e in entries] == [
        ("2026-06-01", 0.2688),
        ("2026-06-04", None),
    ]


def polled_block(parties: list[int], counts: tuple[int, ...]) -> dict[str, object]:
    """A national block whose Westminster budget records these poll counts."""
    block = national_block(parties)
    block["budget"] = {
        "parties": {str(party): {"poll_count": count} for party, count in zip(parties, counts)}
    }
    return block


def test_run_with_no_polls_is_left_out(
    db: Database, only_the_test_database: Path
) -> None:
    """With nothing polled the run is the baseline with no error: a spike."""
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, counts in ((1, 60, (2, 2)), (2, 75, (0, 0)), (3, 65, (1, 1))):
        store_with_summary(
            only_the_test_database, scope, day, seats[0], parties, share,
            polled_block(parties, counts),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [e["as_of_date"] for e in entries] == ["2026-06-01", "2026-06-03"]


def test_run_after_a_skipped_one_still_collapses_onto_the_last_entry(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    for day, share, counts in ((1, 60, (2, 2)), (2, 75, (0, 0)), (3, 60, (1, 1))):
        store_with_summary(
            only_the_test_database, scope, day, seats[0], parties, share,
            polled_block(parties, counts),
        )
    entries = reconstruct_trends(only_the_test_database, scope)
    assert [e["as_of_date"] for e in entries] == ["2026-06-01"]


def test_run_with_any_polled_party_is_kept(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(
        only_the_test_database, scope, 1, seats[0], parties, 60,
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
        only_the_test_database, scope, 1, seats[0], parties, 60,
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
    assert main([
        "--model", "westminster", "--map-id", str(scope.map_id),
        "--database", str(only_the_test_database), "--output", str(output),
    ]) == 1
    assert not output.exists()


def test_unreadable_stored_format_fails_reconstruction(
    db: Database, only_the_test_database: Path
) -> None:
    scope, seats, parties = setup_scope(db, "westminster")
    store_with_summary(
        only_the_test_database, scope, 1, seats[0], parties, 60,
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
    # Includes both vote and seat bounds for every party in the full history.
    assert size < 300_000, size
