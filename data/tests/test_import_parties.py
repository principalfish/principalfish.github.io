"""Party seed import tests against an isolated database."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text

from db import Database

OLD_SCRIPTS = Path(__file__).resolve().parents[1] / "old_data" / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, OLD_SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parties = _load("import_parties")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("SDLP", "SDLP"),
        ("UKIP", "UKIP"),
        ("Democratic Unionist Party", "DUP"),
        ("Social Democratic and Labour Party", "SDLP"),
        ("Scottish National Party", "SNP"),
        ("Ulster Unionist Party", "UUP"),
        ("Sinn Féin", "sinnfein"),
        ("  People's Party!  ", "peoplesparty"),
        ("Reform UK", "reformuk"),
        ("ABCDEFG", "abcdefg"),
        ("", ""),
    ],
)
def test_generate_short_name(name: str, expected: str) -> None:
    assert parties.generate_short_name(name) == expected


@pytest.mark.parametrize("dry_run", [False, True])
def test_upsert_creates_only_requested_party(db: Database, dry_run: bool) -> None:
    unrelated = db.add_party("Unrelated", short_name="untouched", colour="#123456")
    message = parties.upsert_party(db, "New Party", "newparty", None, dry_run, True)

    stored = db.get_party_by_name("New Party")
    if dry_run:
        assert message.startswith("[dry-run] would create: New Party")
        assert stored is None
    else:
        assert message.startswith("created: New Party")
        assert stored is not None
        assert stored.id != unrelated.id
        assert (stored.short_name, stored.colour) == ("newparty", None)
    other = db.get_party_by_name("Unrelated")
    assert other is not None
    assert (other.id, other.short_name, other.colour) == (
        unrelated.id,
        "untouched",
        "#123456",
    )
    assert len(db.get_all_parties()) == (1 if dry_run else 2)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("short_name", "colour"),
    [("new", "#111111"), ("old", None), ("new", "#222222")],
)
def test_upsert_updates_changed_fields_without_replacing_row(
    db: Database, dry_run: bool, short_name: str, colour: str | None
) -> None:
    existing = db.add_party("Target", short_name="old", colour="#111111")
    unrelated = db.add_party("Other Target", short_name="other", colour="#333333")

    message = parties.upsert_party(db, "Target", short_name, colour, dry_run, False)

    assert message.startswith(
        "[dry-run] would update: Target" if dry_run else "updated: Target"
    )
    assert ("short_name" in message) == (short_name != "old")
    assert ("colour" in message) == (colour != "#111111")
    stored = db.get_party_by_name("Target")
    assert stored is not None
    assert stored.id == existing.id
    assert (stored.short_name, stored.colour) == (
        ("old", "#111111") if dry_run else (short_name, colour)
    )
    other = db.get_party_by_name("Other Target")
    assert other is not None
    assert (other.id, other.short_name, other.colour) == (
        unrelated.id,
        "other",
        "#333333",
    )
    assert len(db.get_all_parties()) == 2


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("skip_existing", [False, True])
def test_upsert_unchanged_or_skipped_party_is_preserved(
    db: Database, dry_run: bool, skip_existing: bool
) -> None:
    existing = db.add_party("Target", short_name="same", colour=None)
    message = parties.upsert_party(
        db,
        "Target",
        "different" if skip_existing else "same",
        None,
        dry_run,
        skip_existing,
    )
    assert message == (
        "skipped existing: Target" if skip_existing else "unchanged: Target"
    )
    stored = db.get_party_by_name("Target")
    assert stored is not None
    assert (stored.id, stored.short_name, stored.colour) == (existing.id, "same", None)
    assert len(db.get_all_parties()) == 1


def _party_columns(db: Database) -> list[str]:
    with db.engine.connect() as connection:
        rows = connection.execute(text("PRAGMA table_info(parties)"))
        return [row[1] for row in rows]


@pytest.mark.parametrize("dry_run", [False, True])
def test_drop_long_name_removes_only_obsolete_column(
    db: Database, dry_run: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    original = db.add_party("Keep", short_name="keep", colour="#123456")
    with db.engine.begin() as connection:
        connection.execute(text("ALTER TABLE parties ADD COLUMN long_name TEXT"))
        connection.execute(text("UPDATE parties SET long_name = 'obsolete'"))

    parties.drop_long_name_column(db, dry_run)

    assert ("long_name" in _party_columns(db)) is dry_run
    stored = db.get_party_by_name("Keep")
    assert stored is not None
    assert (stored.id, stored.short_name, stored.colour) == (
        original.id,
        "keep",
        "#123456",
    )
    output = capsys.readouterr().out
    expected_message = "would run" if dry_run else "removed obsolete column"
    assert expected_message in output


@pytest.mark.parametrize("dry_run", [False, True])
def test_drop_long_name_already_absent_is_safe(
    db: Database, dry_run: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _party_columns(db)
    parties.drop_long_name_column(db, dry_run)
    assert _party_columns(db) == before
    assert "already absent, skipping" in capsys.readouterr().out


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("skip_existing", [False, True])
def test_main_uses_configured_database_and_reports_actions(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
    skip_existing: bool,
) -> None:
    existing = db.add_party("Existing", short_name="old", colour="#111111")
    same = db.add_party("Same", short_name="same", colour=None)
    unrelated = db.add_party("Unrelated", short_name="leave", colour="#333333")
    monkeypatch.setenv("DATABASE_PATH", db.config.database_path)
    monkeypatch.setattr(
        parties,
        "PARTY_DEFINITIONS",
        (
            {"name": "Existing", "colour": "#222222"},
            {"name": "Same", "colour": None},
            {"name": "Created", "colour": "#444444"},
        ),
    )
    argv = ["import_parties.py"]
    if dry_run:
        argv.append("--dry-run")
    if skip_existing:
        argv.append("--skip-existing")
    monkeypatch.setattr(sys, "argv", argv)

    parties.main()

    stored = db.get_party_by_name("Existing")
    assert stored is not None
    assert stored.id == existing.id
    assert (stored.short_name, stored.colour) == (
        ("old", "#111111") if dry_run or skip_existing else ("existing", "#222222")
    )
    new = db.get_party_by_name("Created")
    if dry_run:
        assert new is None
    else:
        assert new is not None
        assert new.id not in {existing.id, same.id, unrelated.id}
        assert (new.short_name, new.colour) == ("created", "#444444")
    rows = {
        party.name: (party.id, party.short_name, party.colour)
        for party in db.get_all_parties()
    }
    assert rows["Same"] == (same.id, "same", None)
    assert rows["Unrelated"] == (unrelated.id, "leave", "#333333")
    assert len(rows) == (3 if dry_run else 4)
    output = capsys.readouterr().out
    assert "Created: 1" in output
    assert f"Updated: {0 if skip_existing else 1}" in output
    assert f"Unchanged: {2 if skip_existing else 1}" in output
    assert ("Dry-run mode: no database writes" in output) is dry_run
