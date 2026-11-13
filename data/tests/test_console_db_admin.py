"""Tests for the console's backup hook and its Backup / Restore routes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from flask import Flask

import console.admin.db_admin as db_admin
from console import create_app
from console.us.models import MODEL_RUN_LOCK, model_run_slot
from core import backup
from tests.job_fixtures import job_holding_the_slot


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def requested(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Record backup requests instead of starting the background thread."""
    calls: list[bool] = []
    monkeypatch.setattr(backup, "request_backup", lambda: calls.append(True))
    return calls


@pytest.fixture()
def live_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real tiny database at DATABASE_PATH."""
    db = tmp_path / "live" / "elections.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES ('a')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DATABASE_PATH", str(db))
    return db


# --- after_request hook --------------------------------------------------------


def test_a_post_asks_for_a_backup(app: Flask, requested: list[bool]) -> None:
    app.config["TESTING"] = False
    app.add_url_rule("/wrote", "wrote", lambda: "ok", methods=["POST"])
    app.test_client().post("/wrote")

    assert requested == [True]


def test_a_failed_post_still_asks(app: Flask, requested: list[bool]) -> None:
    # A 5xx may have written part of its change before failing.
    app.config["TESTING"] = False
    app.add_url_rule(
        "/half-wrote", "half_wrote", lambda: ("boom", 500), methods=["POST"]
    )
    app.test_client().post("/half-wrote")

    assert requested == [True]


def test_a_refused_post_does_not(app: Flask, requested: list[bool]) -> None:
    # A 4xx never reached a route that writes (here, no route at all).
    app.config["TESTING"] = False
    app.test_client().post("/no-such-route")

    assert requested == []


def test_a_get_does_not(app: Flask, requested: list[bool]) -> None:
    app.config["TESTING"] = False
    app.test_client().get("/no-such-route")

    assert requested == []


def test_testing_mode_never_asks(app: Flask, requested: list[bool]) -> None:
    app.test_client().post("/no-such-route")

    assert requested == []


def test_db_admin_routes_do_not_ask(
    app: Flask, requested: list[bool], live_db: Path
) -> None:
    # The Backup button has just backed up; a restore must not archive itself.
    app.config["TESTING"] = False
    client = app.test_client()
    client.post("/db/backup")
    client.post("/db/restore")

    assert requested == []


# --- routes ----------------------------------------------------------------------


def test_backup_route_archives(app: Flask, live_db: Path) -> None:
    body = app.test_client().post("/db/backup").get_data(as_text=True)

    assert "Archived to" in body
    assert "Archive dir:" in body
    assert len(backup._archives(backup.status().archive_dir)) == 1


def test_backup_route_reports_an_unchanged_database(
    app: Flask, live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup, "backup_database", lambda: None)
    body = app.test_client().post("/db/backup").get_data(as_text=True)

    assert "Unchanged" in body


def test_backup_route_reports_a_failure(
    app: Flask, live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> Path | None:
        raise RuntimeError("the copy didn't come out intact")

    monkeypatch.setattr(backup, "backup_database", fail)
    response = app.test_client().post("/db/backup")

    assert response.status_code == 200
    assert "Backup failed: the copy didn&#39;t come out intact" in response.get_data(
        as_text=True
    )


def test_restore_route_drops_connections_then_restores(
    app: Flask, live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup.backup_database()
    conn = sqlite3.connect(live_db)
    conn.execute("INSERT INTO t VALUES ('b')")
    conn.commit()
    conn.close()

    order: list[str] = []
    monkeypatch.setattr(db_admin, "reset_db", lambda: order.append("reset"))
    real_restore = backup.restore_latest

    def restore() -> Path:
        order.append("restore")
        return real_restore()

    monkeypatch.setattr(backup, "restore_latest", restore)
    body = app.test_client().post("/db/restore").get_data(as_text=True)

    assert order == ["reset", "restore"]
    assert "Restored from" in body
    assert f"{live_db}.prerestore" in body
    conn = sqlite3.connect(live_db)
    assert [r[0] for r in conn.execute("SELECT x FROM t")] == ["a"]
    conn.close()


def test_restore_route_with_nothing_to_restore(app: Flask, live_db: Path) -> None:
    body = app.test_client().post("/db/restore").get_data(as_text=True)

    assert "Restore failed: no archive to restore from" in body
    conn = sqlite3.connect(live_db)
    assert [r[0] for r in conn.execute("SELECT x FROM t")] == ["a"]
    conn.close()


def test_the_admin_page_shows_the_backup_and_restore_buttons(app: Flask) -> None:
    body = app.test_client().get("/admin").get_data(as_text=True)

    assert "Backup now" in body
    assert "Restore newest archive" in body
    assert 'action="/db/backup"' in body
    assert 'action="/db/restore"' in body


def test_a_locked_database_is_reported_not_a_500(
    app: Flask, live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # sqlite3.Error isn't an OSError; an import holding the write lock raises it.
    def locked() -> Path | None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backup, "backup_database", locked)
    response = app.test_client().post("/db/backup")

    assert response.status_code == 200
    assert "Backup failed: database is locked" in response.get_data(as_text=True)


def test_restore_reports_a_locked_database(
    app: Flask, live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked() -> Path:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backup, "restore_latest", locked)
    response = app.test_client().post("/db/restore")

    assert response.status_code == 200
    assert "Restore failed: database is locked" in response.get_data(as_text=True)


# --- the run slot ------------------------------------------------------------------


def _file_text(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT x FROM t")]
    finally:
        conn.close()


def test_a_restore_while_a_job_runs_is_refused_and_the_database_is_untouched(
    app: Flask, live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup.backup_database()
    conn = sqlite3.connect(live_db)
    conn.execute("INSERT INTO t VALUES ('b')")
    conn.commit()
    conn.close()
    before = live_db.read_bytes()
    calls: list[str] = []
    monkeypatch.setattr(db_admin, "reset_db", lambda: calls.append("reset"))
    monkeypatch.setattr(backup, "restore_latest", lambda: calls.append("restore"))

    with job_holding_the_slot() as job:
        response = app.test_client().post("/db/restore")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/admin")
        page = app.test_client().post("/db/restore", follow_redirects=True)
        body = page.get_data(as_text=True)

    assert "Database not restored: another run is in progress" in body
    assert f'href="/runs/{job.id}"' in body
    assert calls == []
    assert live_db.read_bytes() == before
    assert not live_db.with_name(f"{live_db.name}.prerestore").exists()
    assert _file_text(live_db) == ["a", "b"]


def test_a_restore_while_something_else_holds_the_slot_is_refused_without_a_link(
    app: Flask, live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(backup, "restore_latest", lambda: calls.append("restore"))

    with model_run_slot() as free:
        assert free
        body = (
            app.test_client()
            .post("/db/restore", follow_redirects=True)
            .get_data(as_text=True)
        )

    assert "Database not restored: another run is in progress" in body
    assert "See the running job" not in body
    assert calls == []


def test_a_restore_with_the_slot_free_works_as_before_and_frees_it(
    app: Flask, live_db: Path
) -> None:
    backup.backup_database()
    conn = sqlite3.connect(live_db)
    conn.execute("INSERT INTO t VALUES ('b')")
    conn.commit()
    conn.close()

    body = app.test_client().post("/db/restore").get_data(as_text=True)

    assert "Restored from" in body
    assert _file_text(live_db) == ["a"]
    assert not MODEL_RUN_LOCK.locked()


def test_a_restore_that_fails_still_frees_the_slot(
    app: Flask, live_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> Path:
        raise RuntimeError("no archive")

    monkeypatch.setattr(backup, "restore_latest", broken)

    app.test_client().post("/db/restore")

    assert not MODEL_RUN_LOCK.locked()


def test_a_backup_is_not_refused_while_a_job_runs(app: Flask, live_db: Path) -> None:
    with job_holding_the_slot():
        body = app.test_client().post("/db/backup").get_data(as_text=True)

    assert "Backup Database" in body
    assert "another run is in progress" not in body


# --- cross-site requests ----------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
    ],
)
@pytest.mark.parametrize("route", ["/db/backup", "/db/restore"])
def test_a_cross_site_post_is_refused(
    app: Flask,
    live_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    headers: dict[str, str],
) -> None:
    ran: list[str] = []
    monkeypatch.setattr(backup, "backup_database", lambda: ran.append("b"))
    monkeypatch.setattr(backup, "restore_latest", lambda: ran.append("r"))

    response = app.test_client().post(route, headers=headers)

    assert response.status_code == 403
    assert ran == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "http://localhost"},
        {},
    ],
)
def test_a_same_origin_post_is_allowed(
    app: Flask,
    live_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    ran: list[bool] = []

    def fake() -> Path | None:
        ran.append(True)
        return None

    monkeypatch.setattr(backup, "backup_database", fake)
    response = app.test_client().post("/db/backup", headers=headers)

    assert response.status_code == 200
    assert ran == [True]
