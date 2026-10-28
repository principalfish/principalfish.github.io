"""A fixed export scenario whose outputs are pinned as goldens.

``tests/fixtures/export_golden/no_uncertainty.json`` holds what the code
before the uncertainty export produced for this scenario: the Westminster
trend file, and every file plus the printed output of a full export, a dry
run and a ``--current-simulation`` export. ``test_export_golden.py`` reruns
it on today's code with no stored uncertainty and requires identical text.

The scenario uses only APIs that predate the uncertainty export, so the same
module regenerates the goldens on that code: ``python -m tests.export_golden
<output.json>`` from ``data/``.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from config import DatabaseConfig
from db import Database
from model_support.persistence import (
    OutputScope,
    OutputVote,
    replace_output,
    westminster_uns_scope,
)
from model_support.trends import publish_trends
from tests.uk_fixtures import WestminsterWorld, seed_westminster_world

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

GOLDEN_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "export_golden"
    / "no_uncertainty.json"
)

# A hand-authored shell, so the goldens do not depend on the committed one.
SHELL: dict[str, Any] = {
    "defaultElection": "current-prediction",
    "misc": {"title": "Golden"},
    "parliamentFeatures": {
        "westminster": {
            "features": ["predict", "pollTracker"],
            "polltrackerDataPath": "results/model_output_trends.json",
        }
    },
    "mapModes": {"1": {"projection": "mercator"}},
    "partyKeyAliases": {},
}

# The 2019 changed-boundaries supplemental is read from the legacy directory.
LEGACY_2019 = {
    "Hexham": {
        "seatInfo": {"region": "North East England", "current": "conservative"},
        "partyInfo": {"conservative": {"total": 20000}, "labour": {"total": 15000}},
    }
}

# (as-of day in June 2026, Labour share in each seat). Day 2 repeats day 1, so
# the trend file collapses it; day 3 moves.
RUNS: tuple[tuple[int, float], ...] = ((1, 52.0), (2, 52.0), (3, 47.5))


def seed_model_runs(db: Database, world: WestminsterWorld) -> OutputScope:
    """Store three ``model_uns`` runs on the world's map, without uncertainty."""
    scope = westminster_uns_scope(world.map_id)
    labour = world.party_ids["Labour"]
    conservative = world.party_ids["Conservative"]
    reform = world.party_ids["Reform UK"]
    for day, share in RUNS:
        as_of = date(2026, 6, day)
        votes = []
        for seat_id in world.seat_ids.values():
            rest = 100.0 - share
            votes += [
                OutputVote(seat_id, labour, "", share * 400, share > 50),
                OutputVote(seat_id, conservative, "", rest * 250, share <= 50),
                OutputVote(seat_id, reform, "", rest * 150, False),
            ]
        replace_output(
            Path(db.config.database_path), scope, as_of, f"UNS {as_of}", votes
        )
    return scope


def read_files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run_export(
    db: Database,
    root: Path,
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    shell: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run ``export_elections.main`` on ``db`` into ``root/out``.

    Args:
        db: The database to export.
        root: Scratch directory; the page's data directory is ``root/out``
            (files already there are kept, as in a real checkout).
        argv: Extra command-line arguments.
        monkeypatch: Used to point the export at ``db`` and ``root``.
        shell: The page's ``map-modes-shell.json``; :data:`SHELL` by default.

    Returns:
        ``{"files": {relpath: text}, "stdout": text}``, with ``root`` in the
        printed paths replaced by ``<root>``.
    """
    # Imported here, not at the top: the scripts directory joins sys.path
    # only after the imports, and this module also runs on older code.
    import export_elections

    output_root = root / "out"
    legacy = root / "legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "650map_new.json").write_text('{"type":"Topology"}', encoding="utf-8")
    (legacy / "2019election_new.json").write_text(
        json.dumps(LEGACY_2019), encoding="utf-8"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "map-modes-shell.json").write_text(
        json.dumps(SHELL if shell is None else shell), encoding="utf-8"
    )
    monkeypatch.setattr(export_elections, "Database", lambda: db)
    monkeypatch.setattr(
        export_elections, "PAGE_OUTPUT_ROOTS", {"electionmaps": output_root}
    )
    monkeypatch.setattr(
        export_elections,
        "PAGE_PARLIAMENTS",
        {"electionmaps": {"westminster", "holyrood"}},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["export_elections.py", "--legacy-files-dir", str(legacy), *argv],
    )
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        export_elections.main()
    return {
        "files": read_files(output_root),
        "stdout": printed.getvalue().replace(str(root), "<root>"),
    }


def golden_outputs(
    db: Database,
    world: WestminsterWorld,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Produce every pinned output for a seeded world with model runs.

    Args:
        db: The database, seeded with ``world`` and :func:`seed_model_runs`.
        world: Its ids.
        root: An empty scratch directory (each export gets its own).
        monkeypatch: Used to point the export at ``db`` and ``root``.

    Returns:
        ``{"trends": text, "full"/"dry_run"/"current_simulation":
        {"files": {relpath: text}, "stdout": text}}``.
    """
    trends = root / "trends.json"
    publish_trends(
        Path(db.config.database_path), westminster_uns_scope(world.map_id), trends
    )
    return {
        "trends": trends.read_text(encoding="utf-8"),
        "full": run_export(db, root / "full", [], monkeypatch),
        "dry_run": run_export(db, root / "dry", ["--dry-run"], monkeypatch),
        "current_simulation": run_export(
            db,
            root / "current",
            ["--current-simulation", "--output-root", str(root / "current" / "out")],
            monkeypatch,
        ),
    }


@contextlib.contextmanager
def _scratch_world() -> Iterator[tuple[Database, WestminsterWorld, Path]]:
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        db = Database(
            DatabaseConfig.model_construct(database_path=str(root / "golden.db"))
        )
        db.create_tables()
        world = seed_westminster_world(db)
        seed_model_runs(db, world)
        try:
            yield db, world, root / "work"
        finally:
            db.engine.dispose()


def main(argv: list[str]) -> int:
    """Write the goldens for the code on ``sys.path`` to ``argv[0]``."""
    destination = Path(argv[0])
    with _scratch_world() as (db, world, root), pytest.MonkeyPatch.context() as patch:
        # Nothing here should open a default Database(); if something did,
        # it must not reach the database .env names.
        patch.setenv("DATABASE_PATH", str(root / "unused-default.db"))
        outputs = golden_outputs(db, world, root, patch)
    destination.write_text(
        json.dumps(outputs, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
