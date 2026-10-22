"""Without stored uncertainty, exports are byte-identical to the code before it.

The goldens were produced by ``tests/export_golden.py`` on the code before
the uncertainty export (commit b10516b9) and are compared as exact text.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from db import Database
from model_support.persistence import (
    OutputVote,
    UncertaintyPayload,
    load_uncertainty,
    replace_output,
    westminster_uns_scope,
)
from tests.export_golden import GOLDEN_PATH, RUNS, golden_outputs, seed_model_runs
from tests.uk_fixtures import WestminsterWorld


@pytest.fixture()
def golden() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return loaded


def _national_only(world: WestminsterWorld) -> UncertaintyPayload:
    labour = str(world.party_ids["Labour"])
    return UncertaintyPayload(
        {
            "simulations": 2,
            "interval": 0.9,
            "hung": 0.5,
            "parties": {
                labour: {
                    "v": [40.0, 55.0],
                    "s": [2, 3, 4],
                    "pm": 0.5,
                    "pl": 1.0,
                    "hist": {"start": 2, "counts": [1, 0, 1]},
                }
            },
        }
    )


def test_no_uncertainty_rows_reproduce_the_goldens(
    db: Database,
    westminster_world: WestminsterWorld,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    golden: dict[str, Any],
) -> None:
    seed_model_runs(db, westminster_world)
    outputs = golden_outputs(db, westminster_world, tmp_path, monkeypatch)
    # The scenario has a model run, so the export did look for its summary.
    assert "results/prediction-simulation.json" in outputs["full"]["files"]
    assert outputs == golden


def test_unmigrated_database_reproduces_the_goldens(
    db: Database,
    westminster_world: WestminsterWorld,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    golden: dict[str, Any],
) -> None:
    with closing(sqlite3.connect(db.config.database_path)) as conn:
        conn.execute("DROP TABLE model_uncertainty")
        conn.commit()
    seed_model_runs(db, westminster_world)
    assert golden_outputs(db, westminster_world, tmp_path, monkeypatch) == golden


def test_national_only_summary_leaves_the_exports_unchanged(
    db: Database,
    westminster_world: WestminsterWorld,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    golden: dict[str, Any],
) -> None:
    """A pruned run has no seat or region block: no ``u``, no file."""
    seed_model_runs(db, westminster_world)
    # Re-save the exported (latest) run with a national-only summary.
    path = Path(db.config.database_path)
    scope = westminster_uns_scope(westminster_world.map_id)
    with closing(sqlite3.connect(path)) as conn:
        election_id, name = conn.execute(
            "SELECT id, name FROM elections WHERE type = 'model_uns' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        votes = conn.execute(
            "SELECT seat_id, party_id, candidate_name, vote_total, elected "
            "FROM votes WHERE election_id = ?",
            (election_id,),
        ).fetchall()
    as_of = scope.date_from_name(name)
    assert as_of is not None and as_of.day == RUNS[-1][0]
    _, new_id = replace_output(
        path,
        scope,
        as_of,
        name,
        [OutputVote(s, p, c, v, bool(e)) for s, p, c, v, e in votes],
        uncertainty=_national_only(westminster_world),
    )
    stored = load_uncertainty(path, new_id)
    assert stored is not None and stored.seats is None and stored.regions is None

    outputs = golden_outputs(db, westminster_world, tmp_path, monkeypatch)
    for mode in ("full", "dry_run", "current_simulation"):
        assert outputs[mode] == golden[mode]
    # The trend file is the one output a national block changes.
    assert outputs["trends"] != golden["trends"]
    assert json.loads(outputs["trends"])[-1]["hung"] == 0.5
