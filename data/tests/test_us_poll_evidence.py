"""US metadata survives import; repairs never overwrite stale polling evidence."""
from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from flask import Flask

from db import Database
from model_support.us_poll_evidence import simple_random_sample_moe, validate_contributor
from polls.importers.us.us_polls_common import CandidateReading
from polls.importers.us.us_wikipedia_polls import (
    UsPollRow, build_us_import_plan, commit_us_import_plan,
)
from scripts.backfill_us_poll_evidence import apply_backfill, main, plan_backfill
from scripts.migrate_add_us_poll_evidence import migrate
from tests.console_fixtures import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models" / "us"))
from _common import PollReading, aggregate_national, aggregate_seat_polls, collect_poll_readings


def source_row(population: str = "LV") -> UsPollRow:
    return UsPollRow(
        fieldwork_start=date(2026, 10, 1), fieldwork_end=date(2026, 10, 3),
        date_label="October 1–3, 2026", pollster_label="Example",
        pollster_identifier="example_us_president", sample_size_label=f"1000 ({population})",
        source_url="https://example.org/poll", matchup="Candidate D (D) vs Candidate R (R)",
        contest="president", page_url="https://en.wikipedia.org/wiki/Example",
        heading_path="General election > Nationwide", seat_name=None, seat_id=None,
        map_name="US Presidential 2024", readings=(
            CandidateReading("Democratic", "Candidate D", 48.0),
            CandidateReading("Republican", "Candidate R", 46.0),
        ), sample_size=1000, population=population,
    )


def seed(db: Database, row: UsPollRow) -> int:
    db.add_map(row.map_name)
    db.add_party("Democratic", colour="#0000ff")
    db.add_party("Republican", colour="#ff0000")
    result = commit_us_import_plan(db, row, build_us_import_plan(db, row))
    assert result.poll_id is not None
    return result.poll_id


@pytest.mark.parametrize("population", ["LV", "RV", "A"])
def test_import_roundtrip_and_reading(db: Database, population: str) -> None:
    row = source_row(population)
    poll_id = seed(db, row)
    poll = db.get_poll(poll_id)
    assert poll is not None
    assert (poll.evidence_kind, poll.population_type) == ("individual_poll", population)
    readings = collect_poll_readings(
        db, poll.map_id, date(2026, 10, 1), date(2026, 10, 4), 14,
        {poll.pollster_id: 1.0}, {poll.pollster_id: "Example"},
    )
    assert validate_contributor(readings[0]).n == 1000
    assert readings[0].population_type == population


def test_migration_is_nullable_idempotent_and_dry_run() -> None:
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE polls (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO polls VALUES (1)")
        conn.commit()
        migrate(conn, dry_run=True)
        assert len(conn.execute("PRAGMA table_info(polls)").fetchall()) == 1
        migrate(conn, dry_run=False)
        assert conn.execute("SELECT evidence_kind,population_type,evidence_source FROM polls").fetchone() == (None, None, None)
        assert all("already exists" in line for line in migrate(conn, dry_run=False))


@pytest.mark.parametrize("change", ["delete", "reuse", "head", "share", "population"])
def test_repair_rechecks_snapshot_under_lock(db: Database, change: str) -> None:
    row = source_row()
    poll_id = seed(db, row)
    with closing(sqlite3.connect(db.config.database_path)) as conn:
        repairs = plan_backfill(conn, db, [row])
        assert len(repairs) == 1
        if change == "delete":
            conn.execute("DELETE FROM polls WHERE id=?", (poll_id,))
        elif change == "reuse":
            conn.execute("DELETE FROM polls WHERE id=?", (poll_id,))
            conn.execute("INSERT INTO polls (id,pollster_id,map_id,fieldwork_start,fieldwork_end) VALUES (?,1,1,'2020-01-01','2020-01-02')", (poll_id,))
        elif change == "head":
            conn.execute("UPDATE polls SET sample_size=321 WHERE id=?", (poll_id,))
        elif change == "population":
            conn.execute("UPDATE polls SET population_type='RV' WHERE id=?", (poll_id,))
        else:
            conn.execute("UPDATE poll_rows SET percentage=percentage+1 WHERE poll_id=?", (poll_id,))
        conn.commit()
        written, stale = apply_backfill(conn, repairs)
        assert (written, stale) == (0, [poll_id])


def test_repair_recovers_legacy_population_and_rejects_share_mismatch(db: Database) -> None:
    row = source_row("RV")
    poll_id = seed(db, row)
    with closing(sqlite3.connect(db.config.database_path)) as conn:
        conn.execute("UPDATE polls SET evidence_kind=NULL,population_type=NULL,evidence_source=NULL")
        conn.commit()
        assert not plan_backfill(conn, db, [row.model_copy(update={"readings": (CandidateReading("Democratic", "Candidate D", 49),)})])
        repairs = plan_backfill(conn, db, [row])
        assert conn.execute("SELECT evidence_kind FROM polls").fetchone() == (None,)
        assert apply_backfill(conn, repairs) == (1, [])
    assert db.get_poll(poll_id).population_type == "RV"  # type: ignore[union-attr]


def reading(poll_id: int, **kwargs: object) -> PollReading:
    return PollReading(
        poll_id=poll_id, seat_id=None, matchup=None, weight=1,
        shares={1: 48, 2: 46}, region_shares={}, pollster="Example",
        fieldwork_start=date(2026, 1, 1), fieldwork_end=date(2026, 1, 2),
        candidate_count=2, candidate_shares={}, **kwargs,
    )


def test_only_actual_contributors_are_validated() -> None:
    good = reading(1, evidence_kind="aggregate_estimate", source_url="https://example.org")
    bad = replace(reading(2), matchup="ignored")
    zero = replace(reading(3), weight=0)
    contributors: list[PollReading] = []
    sums, weights, _ = aggregate_national([good, bad, zero], None, evidence_contributors=contributors)
    assert contributors == [good]
    assert sums[(None, 1)] / weights[(None, 1)] == 48
    assert validate_contributor(good).n is None
    with pytest.raises(ValueError, match="repair provenance"):
        validate_contributor(replace(good, evidence_kind="individual_poll"))
    with pytest.raises(ValueError, match="unknown evidence kind"):
        validate_contributor(bad)
    contributors.clear()
    aggregate_seat_polls([replace(bad, seat_id=10)], seat_matchups={10: None}, national_matchup=None, policy="tracked", evidence_contributors=contributors)
    assert contributors == []


def test_console_labels_sample_assumptions_and_aggregate_n(db: Database, app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    poll_id = seed(db, source_row())
    monkeypatch.setattr("console.blueprints.polls.get_db", lambda: db)
    client = app.test_client()
    body = client.get(f"/polls/{poll_id}").get_data(as_text=True)
    assert "individual_poll" in body and "Population: LV" in body
    assert "90% confidence, assuming simple random sampling" in body
    with db.session() as session:
        from models import Poll
        poll = session.get(Poll, poll_id)
        assert poll is not None
        poll.evidence_kind = "aggregate_estimate"
    listing = client.get("/polls").get_data(as_text=True)
    assert "aggregate_estimate" in listing and "not applicable" in listing
    assert simple_random_sample_moe(1000) == pytest.approx(2.60074, abs=0.00001)


def test_dry_run_missing_path_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(SystemExit, match="2"):
        main(["--database", str(missing)])
    assert not missing.exists()


def test_aggregate_import_has_no_synthetic_n(db: Database) -> None:
    row = source_row().model_copy(update={"evidence_kind": "aggregate_estimate", "population": None})
    poll_id = seed(db, row)
    poll = db.get_poll(poll_id)
    assert poll is not None
    assert poll.evidence_kind == "aggregate_estimate"
    assert poll.sample_size is None
    assert row.sample_moe is None


@pytest.mark.parametrize("sample_size", [True, 12.5, 0, -1])
def test_invalid_individual_n_is_rejected(sample_size: object) -> None:
    value = reading(4, evidence_kind="individual_poll", sample_size=sample_size, source_url="https://example.org")
    with pytest.raises(ValueError, match="no usable N"):
        validate_contributor(value)


def test_dry_run_uses_snapshot_without_changing_original(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    row = source_row()
    seed(db, row)
    from polls.importers.us.us_wikipedia_polls import UsPollIndex
    from types import SimpleNamespace
    seen: list[str] = []
    def fetch(copy: Database, *_: object) -> UsPollIndex:
        seen.append(copy.config.database_path)
        assert copy.config.database_path != db.config.database_path
        return SimpleNamespace(rows=[row], page_failures={})  # type: ignore[return-value]
    monkeypatch.setattr("scripts.backfill_us_poll_evidence.fetch_us_poll_index", fetch)
    before = Path(db.config.database_path).read_bytes()
    assert main(["--database", db.config.database_path]) == 0
    assert seen
    assert Path(db.config.database_path).read_bytes() == before


def test_local_contributor_selection_matches_central_average() -> None:
    matchup = "Candidate D (D) vs Candidate R (R)"
    used = replace(reading(10), seat_id=4, matchup=matchup, evidence_kind="individual_poll", sample_size=1000, source_url="https://example.org")
    unused = replace(reading(11), seat_id=4, matchup="Other D (D) vs Candidate R (R)")
    zero = replace(reading(12), seat_id=4, matchup=matchup, weight=0)
    collected: list[PollReading] = []
    averages = aggregate_seat_polls([used, unused, zero], seat_matchups={4: matchup}, national_matchup=None, policy="tracked", evidence_contributors=collected)
    assert collected == [used]
    assert averages[4].total_weight == used.weight
    assert validate_contributor(collected[0]).n == 1000
