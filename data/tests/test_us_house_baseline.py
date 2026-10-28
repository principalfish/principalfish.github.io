"""House corrections must be complete, reviewable and strictly scoped."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from model_support.us_house_baseline import (
    PARTY_KEY_TO_NAME,
    PROPOSAL,
    BaselineError,
    apply_reviewed,
    audit_database,
    load_proposal,
)
from scripts.audit_us_house_baseline import main


def obj(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


@pytest.fixture
def proposal() -> dict[str, object]:
    return load_proposal()


@pytest.fixture
def baseline_db(tmp_path: Path, proposal: dict[str, object]) -> Path:
    path = tmp_path / "house.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE maps(id INTEGER PRIMARY KEY,name TEXT,parliament TEXT);
            CREATE TABLE elections(id INTEGER PRIMARY KEY,map_id INTEGER,year INTEGER,
                name TEXT,type TEXT,parent_election_id INTEGER,election_date TEXT);
            CREATE TABLE seats(id INTEGER PRIMARY KEY,map_id INTEGER,seat_name TEXT);
            CREATE TABLE parties(id INTEGER PRIMARY KEY,name TEXT);
            CREATE TABLE votes(id INTEGER PRIMARY KEY,election_id INTEGER,seat_id INTEGER,
                party_id INTEGER,candidate_name TEXT,vote_total REAL,elected INTEGER);
            INSERT INTO maps VALUES(21,'US House Districts 2024','us_house');
            INSERT INTO elections VALUES(101102,21,2024,'2024 US House Election',
                'us_house',NULL,'2024-11-05');
            INSERT INTO elections VALUES(999,21,2026,'US House UNS sentinel',
                'model_uns',101102,'2026-10-19');
        """)
        party_ids = {key: i for i, key in enumerate(PARTY_KEY_TO_NAME, 1)}
        connection.executemany("INSERT INTO parties VALUES(?,?)", [
            (party_ids[key], name) for key, name in PARTY_KEY_TO_NAME.items()
        ])
        for seat_id, (district, value) in enumerate(obj(proposal["baseline"]).items(), 1):
            row = obj(value)
            connection.execute("INSERT INTO seats VALUES(?,21,?)", (seat_id, district))
            for party, count in obj(row["votes"]).items():
                connection.execute(
                    "INSERT INTO votes(election_id,seat_id,party_id,candidate_name,vote_total,elected) "
                    "VALUES(101102,?,?,?,?,?)",
                    (seat_id, party_ids[party], None, count, int(row["winner"] == party)),
                )
        connection.execute(
            "INSERT INTO votes VALUES(9999,999,1,1,'Sentinel candidate',123.0,1)"
        )
    return path


def test_proposal_reconciles_official_examples(proposal: dict[str, object]) -> None:
    proposed = obj(proposal["proposed"])
    assert obj(proposed["NY-07"])["democrat"] == 172795
    assert obj(proposed["NY-20"])["democrat"] == 221997
    assert obj(proposed["NY-20"])["republican"] == 141151
    assert obj(proposed["WA-09"])["democrat"] == 182780 + 90601
    assert "republican" not in obj(proposed["WA-09"])
    assert "others" not in obj(proposed["ME-01"])
    assert obj(proposed["FL-20"]) == {"democrat": 100}
    assert obj(proposed["OK-03"]) == {"republican": 100}
    assert proposed["AK-01"] == obj(obj(proposal["baseline"])["AK-01"])["votes"]


@pytest.mark.parametrize("value", [True, -1, 1.5, float("nan"), float("inf"), "123"])
def test_invalid_counts_rejected(tmp_path: Path, proposal: dict[str, object], value: object) -> None:
    obj(obj(proposal["proposed"])["NY-20"])["democrat"] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(proposal))
    with pytest.raises(BaselineError, match="nonnegative integers"):
        load_proposal(path)


@pytest.mark.parametrize("problem", ["missing", "provenance", "candidate", "category", "alias", "winner"])
def test_inconsistent_proposal_rejected(tmp_path: Path, proposal: dict[str, object], problem: str) -> None:
    candidates = cast(list[dict[str, object]], proposal["candidates"])
    if problem == "missing":
        del obj(proposal["proposed"])["NY-20"]
    elif problem == "provenance":
        proposal["source_sha256"] = "bad"
    elif problem == "candidate":
        candidates[0]["votes"] = 99
    elif problem == "category":
        cast(list[dict[str, object]], candidates[0]["ballot_lines"])[0]["label"] = "Void"
    elif problem == "alias":
        cast(list[dict[str, object]], proposal["aliases"])[0]["party"] = "others"
    else:
        obj(obj(proposal["baseline"])["NY-20"])["winner"] = "republican"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(proposal))
    with pytest.raises(BaselineError):
        load_proposal(path)


def test_audit_is_read_only_and_apply_atomic_idempotent(
    baseline_db: Path, proposal: dict[str, object],
) -> None:
    original = baseline_db.read_bytes()
    report = audit_database(baseline_db, proposal)
    assert baseline_db.read_bytes() == original
    assert len(cast(list[object], report["changes"])) == 51
    assert apply_reviewed(baseline_db, proposal, report)
    corrected = baseline_db.read_bytes()
    assert not apply_reviewed(baseline_db, proposal, report)
    assert baseline_db.read_bytes() == corrected
    with sqlite3.connect(baseline_db) as connection:
        assert connection.execute("SELECT * FROM votes WHERE id=9999").fetchone() == (
            9999, 999, 1, 1, "Sentinel candidate", 123.0, 1,
        )
        assert connection.execute("SELECT count(*) FROM votes WHERE elected=1 AND election_id=101102").fetchone() == (435,)
        assert connection.execute("SELECT count(*) FROM votes WHERE candidate_name IS NOT NULL AND election_id=101102").fetchone() == (0,)


@pytest.mark.parametrize("sql", [
    "UPDATE votes SET vote_total=99 WHERE id=1",
    "UPDATE votes SET candidate_name='Changed' WHERE id=1",
    "UPDATE votes SET elected=1 WHERE id=1",
    "UPDATE elections SET election_date='2024-11-06' WHERE id=101102",
    "UPDATE seats SET seat_name='BAD' WHERE id=1",
    "DELETE FROM votes WHERE id=1",
])
def test_changes_since_review_rejected(
    baseline_db: Path, proposal: dict[str, object], sql: str,
) -> None:
    report = audit_database(baseline_db, proposal)
    with sqlite3.connect(baseline_db) as connection:
        connection.execute(sql)
    stale = baseline_db.read_bytes()
    with pytest.raises(BaselineError):
        apply_reviewed(baseline_db, proposal, report)
    assert baseline_db.read_bytes() == stale


@pytest.mark.parametrize("sql", [
    "UPDATE elections SET type='model_uns' WHERE id=101102",
    "UPDATE maps SET parliament='westminster' WHERE id=21",
    "UPDATE votes SET vote_total=99 WHERE id=1",
    "DELETE FROM votes WHERE id=1",
    "INSERT INTO votes(election_id,seat_id,party_id,vote_total,elected) VALUES(101102,1,1,100,0)",
])
def test_partial_or_wrong_scope_cannot_be_reviewed(
    baseline_db: Path, proposal: dict[str, object], sql: str,
) -> None:
    with sqlite3.connect(baseline_db) as connection:
        connection.execute(sql)
    with pytest.raises(BaselineError):
        audit_database(baseline_db, proposal)


def test_rollback_on_write_failure(baseline_db: Path, proposal: dict[str, object]) -> None:
    report = audit_database(baseline_db, proposal)
    with sqlite3.connect(baseline_db) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_repair BEFORE UPDATE ON votes
            WHEN NEW.vote_total=221997 BEGIN SELECT RAISE(ABORT,'test failure'); END;
        """)
    original = baseline_db.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        apply_reviewed(baseline_db, proposal, report)
    assert baseline_db.read_bytes() == original


def test_missing_database_is_never_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    assert main(["--database", str(missing), "--report", str(tmp_path / "report.json")]) == 1
    assert not missing.exists()


def test_curated_input_is_unchanged() -> None:
    assert len(obj(load_proposal(PROPOSAL)["baseline"])) == 435
