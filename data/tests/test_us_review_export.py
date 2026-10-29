"""Full US export regressions using only fixture databases and scratch files."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from db import Database
from scripts import export_elections
from tests.test_us_simulation import _seed_house
from _common import run_simulation


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("has_model", [False, True])
def test_main_exports_house_trends_and_removes_absent_model_sidecars(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    dry_run: bool, has_model: bool,
) -> None:
    cfg = _seed_house(db, tmp_path)
    db.add_party("Others")
    if has_model:
        run_simulation(db, replace(cfg, dry_run=False, no_uncertainty=True))
    root = tmp_path / "page"
    maps = root / "maps"
    maps.mkdir(parents=True)
    (maps / "map-1.topo.json").write_text("{}")
    (maps / "map-23.topo.json").write_text("{}")
    (root / "map-modes-shell.json").write_text(json.dumps({
        "mapModes": {}, "parliamentFeatures": {},
    }))
    results = root / "results"
    results.mkdir()
    (results / "senate-current.json").write_text('{"seats": []}')
    stale = [results / f"us-{model}-uncertainty.json"
             for model in ("house", "senate", "president")]
    for path in stale:
        path.write_text("saved stale sidecar")
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    monkeypatch.setattr(export_elections, "Database", lambda: db)
    monkeypatch.setattr(export_elections, "PAGE_OUTPUT_ROOTS", {"uselectionmaps": root})
    monkeypatch.setattr(export_elections, "PAGE_PARLIAMENTS", {
        "uselectionmaps": {"us_house", "us_senate", "us_presidential"},
    })
    monkeypatch.setattr(sys, "argv", [
        "export_elections.py", "--legacy-files-dir", str(tmp_path),
        *(["--dry-run"] if dry_run else []),
    ])
    export_elections.main()
    if dry_run:
        after = {path.relative_to(root): path.read_bytes()
                 for path in root.rglob("*") if path.is_file()}
        assert after == before
        return
    assert not any(path.exists() for path in stale)
    trends = results / "us-house-trends.json"
    assert trends.exists() == has_model
    if has_model:
        document = json.loads(trends.read_text())
        assert document["schema"] == "pf-us-model-trends-v1"
        assert document["model"] == "house"
        assert len(document["points"]) == 1
        assert document["points"][0]["as_of_date"] == cfg.as_of_date.isoformat()
        assert sum(row["s"] for row in document["points"][0]["parties"].values()) == 3
