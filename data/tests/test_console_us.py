"""Tests for the console US blueprint: registered routes, home card, and outputs pages.

Route tests monkeypatch the blueprint's ``get_db`` to the shared temp-DB fixture,
so the pages render against a fresh SQLite database per test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask

from db import Database
from models import ElectionType

from console import create_app
from console.services.us_models import UsModelRun, run_us_models_and_export


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


def _seed_us_house(db: Database) -> dict[str, int]:
    """Build a US House map, two parties, two districts, a 2024 baseline, and one forecast output."""
    m = db.add_map("US House Districts 2024")
    region = db.add_region(m.id, "New England")
    dem = db.add_party("Democratic", colour="#1d4ed8")
    rep = db.add_party("Republican", colour="#dc2626")
    first = db.add_seat(m.id, "ME-01", region_id=region.id, electorate=1000)
    second = db.add_seat(m.id, "ME-02", region_id=region.id, electorate=1000)

    baseline = db.add_election(m.id, 2024, "2024 US House Election", ElectionType.us_house)
    db.add_vote(baseline.id, first.id, party_id=dem.id, vote_total=500.0, elected=True)
    db.add_vote(baseline.id, first.id, party_id=rep.id, vote_total=300.0)
    db.add_vote(baseline.id, second.id, party_id=rep.id, vote_total=450.0, elected=True)
    db.add_vote(baseline.id, second.id, party_id=dem.id, vote_total=400.0)

    model = db.add_election(m.id, 2026, "US House UNS 2026-06-01", ElectionType.us_house_model)
    db.add_vote(model.id, first.id, party_id=dem.id, vote_total=55.0, elected=True)
    db.add_vote(model.id, first.id, party_id=rep.id, vote_total=45.0)
    db.add_vote(model.id, second.id, party_id=dem.id, vote_total=51.0, elected=True)
    db.add_vote(model.id, second.id, party_id=rep.id, vote_total=49.0)

    return {"map_id": m.id, "baseline_id": baseline.id, "model_id": model.id, "dem_id": dem.id, "rep_id": rep.id}


def _seed_us_president(db: Database) -> dict[str, int]:
    """Build a US Presidential map with electoral votes, a 2024 baseline, and one forecast.

    Two elector units: Big (EV 20, flips Rep→Dem in the forecast) and Small (EV 3, stays Rep).
    """
    m = db.add_map("US Presidential 2024")
    region = db.add_region(m.id, "Pacific")
    dem = db.add_party("Democratic", colour="#1d4ed8")
    rep = db.add_party("Republican", colour="#dc2626")
    big = db.add_seat(m.id, "Big State", region_id=region.id, electoral_votes=20)
    small = db.add_seat(m.id, "Small State", region_id=region.id, electoral_votes=3)

    baseline = db.add_election(m.id, 2024, "2024 US Presidential Election", ElectionType.us_presidential)
    db.add_vote(baseline.id, big.id, party_id=rep.id, vote_total=51.0, elected=True)
    db.add_vote(baseline.id, big.id, party_id=dem.id, vote_total=49.0)
    db.add_vote(baseline.id, small.id, party_id=rep.id, vote_total=60.0, elected=True)
    db.add_vote(baseline.id, small.id, party_id=dem.id, vote_total=40.0)

    model = db.add_election(m.id, 2028, "US President UNS 2028-06-01", ElectionType.us_presidential_model)
    db.add_vote(model.id, big.id, party_id=dem.id, vote_total=52.0, elected=True)
    db.add_vote(model.id, big.id, party_id=rep.id, vote_total=48.0)
    db.add_vote(model.id, small.id, party_id=rep.id, vote_total=58.0, elected=True)
    db.add_vote(model.id, small.id, party_id=dem.id, vote_total=42.0)

    return {"map_id": m.id, "baseline_id": baseline.id, "model_id": model.id, "dem_id": dem.id, "rep_id": rep.id}


class TestUsRoutesRegistered:
    """create_app wires the US action routes plus all per-chamber endpoints."""

    def test_action_routes_exist(self, app: Flask) -> None:
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert "/us/import-polls" in rules
        assert "/us/run-models" in rules

    def test_per_chamber_endpoints_exist(self, app: Flask) -> None:
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
        for slug in ("house", "president", "senate"):
            assert f"us.{slug}_outputs" in endpoints
            assert f"us.{slug}_output_detail" in endpoints
            assert f"us.delete_{slug}_output" in endpoints
            assert f"us.delete_selected_{slug}_outputs" in endpoints


class TestHomeCard:
    """The home dashboard shows the USA action card."""

    def test_home_renders_usa_actions(self, app: Flask) -> None:
        body = app.test_client().get("/").get_data(as_text=True)
        for needle in (
            "Import US Polls",
            "Run US Models",
            "View House Outputs",
            "View President Outputs",
            "View Senate Outputs",
        ):
            assert needle in body


class TestUsOutputsPages:
    """The per-chamber outputs list/detail/delete routes against a temp DB."""

    def test_house_outputs_lists_forecast(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_us_house(db)
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        response = app.test_client().get("/us/house/outputs")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "US House Model Outputs" in body
        assert "US House UNS 2026-06-01" in body
        assert f"/us/house/outputs/{seeded['model_id']}" in body

    def test_house_output_detail_renders(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_us_house(db)
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        response = app.test_client().get(f"/us/house/outputs/{seeded['model_id']}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "US House UNS 2026-06-01" in body
        assert "ME-01" in body

    def test_house_output_detail_unknown_redirects(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        response = app.test_client().get("/us/house/outputs/99999")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/us/house/outputs")

    def test_delete_house_output(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_us_house(db)
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        response = app.test_client().post(f"/us/house/outputs/{seeded['model_id']}/delete")
        assert response.status_code == 302

        from console.services.model_outputs import build_output_detail_context

        context = build_output_detail_context(
            db,
            election_id=seeded["model_id"],
            election_type=ElectionType.us_house_model,
            baseline_types=[ElectionType.us_house],
            page=1,
        )
        assert context is None


class TestPresidentElectoralVotes:
    """The President output shows electoral votes; House/Senate show only seats."""

    def test_detail_context_reports_electoral_votes(self, db: Database) -> None:
        from console.services.model_outputs import build_output_detail_context

        seeded = _seed_us_president(db)
        context = build_output_detail_context(
            db,
            election_id=seeded["model_id"],
            election_type=ElectionType.us_presidential_model,
            baseline_types=[ElectionType.us_presidential],
            page=1,
        )
        assert context is not None
        assert context["shows_electoral_votes"] is True
        by_party = {row["party_name"]: row for row in context["party_totals"]}
        # Forecast: Dem wins Big (20 EV), Rep wins Small (3 EV).
        assert by_party["Democratic"]["electoral_votes"] == 20
        assert by_party["Republican"]["electoral_votes"] == 3
        # Baseline: Rep won both (23 EV) → Dem +20, Rep −20.
        assert by_party["Democratic"]["ev_diff_vs_base"] == 20
        assert by_party["Republican"]["ev_diff_vs_base"] == -20
        # EV leader ranks first.
        assert context["party_totals"][0]["party_name"] == "Democratic"

    def test_house_detail_context_hides_electoral_votes(self, db: Database) -> None:
        from console.services.model_outputs import build_output_detail_context

        seeded = _seed_us_house(db)
        context = build_output_detail_context(
            db,
            election_id=seeded["model_id"],
            election_type=ElectionType.us_house_model,
            baseline_types=[ElectionType.us_house],
            page=1,
        )
        assert context is not None
        assert context["shows_electoral_votes"] is False
        assert all(row["electoral_votes"] == 0 for row in context["party_totals"])

    def test_president_detail_page_renders_ev_column(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_us_president(db)
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        body = app.test_client().get(f"/us/president/outputs/{seeded['model_id']}").get_data(as_text=True)
        assert "Electoral Votes" in body
        assert "Electoral Votes by Party" in body

    def test_president_list_trend_plots_electoral_votes(self, db: Database, tmp_path: Path) -> None:
        # A president trend cache with an "e" value drives the list chart onto EV.
        import json

        from console.services.model_outputs import build_outputs_context

        seeded = _seed_us_president(db)
        dem, rep = str(seeded["dem_id"]), str(seeded["rep_id"])
        trend = tmp_path / "us-president-trends.json"
        trend.write_text(
            json.dumps([
                {
                    "election_id": 1,
                    "election_name": "US President UNS 2028-06-01",
                    "as_of_date": "2028-06-01",
                    "parties": {dem: {"s": 1, "v": 49.0, "e": 20}, rep: {"s": 1, "v": 48.0, "e": 3}},
                }
            ]),
            encoding="utf-8",
        )
        context = build_outputs_context(
            db,
            election_type=ElectionType.us_presidential_model,
            trend_cache_path=trend,
            show_all=True,
        )
        assert context["shows_electoral_votes"] is True
        dem_dataset = next(d for d in context["trend_data"]["seats_datasets"] if d["label"] == "Democratic")
        assert dem_dataset["data"] == [20]  # electoral votes, not the state count (1)

    def test_house_list_trend_plots_seats(self, db: Database, tmp_path: Path) -> None:
        import json

        from console.services.model_outputs import build_outputs_context

        seeded = _seed_us_house(db)
        dem, rep = str(seeded["dem_id"]), str(seeded["rep_id"])
        trend = tmp_path / "us-house-trends.json"
        trend.write_text(
            json.dumps([
                {
                    "election_id": 1,
                    "election_name": "US House UNS 2026-06-29",
                    "as_of_date": "2026-06-29",
                    "parties": {dem: {"s": 234, "v": 52.0}, rep: {"s": 199, "v": 45.0}},
                }
            ]),
            encoding="utf-8",
        )
        context = build_outputs_context(
            db,
            election_type=ElectionType.us_house_model,
            trend_cache_path=trend,
            show_all=True,
        )
        assert context["shows_electoral_votes"] is False
        dem_dataset = next(d for d in context["trend_data"]["seats_datasets"] if d["label"] == "Democratic")
        assert dem_dataset["data"] == [234]


class _RecordingRunner:
    """Stand-in for ``run_python_script`` that records calls instead of shelling out."""

    def __init__(self, return_codes: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._return_codes = return_codes or {}

    def __call__(
        self, script: Path, *args: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((script.name, args))
        code = self._return_codes.get(script.name, 0)
        return subprocess.CompletedProcess(
            args=[str(script), *args],
            returncode=code,
            stdout=f"ran {script.name}",
            stderr="boom" if code else "",
        )

    @property
    def scripts(self) -> list[str]:
        return [name for name, _ in self.calls]

    def args_for(self, script_name: str) -> tuple[str, ...]:
        return next(args for name, args in self.calls if name == script_name)


def _seed_president_map(db: Database) -> int:
    """Create the presidential map the national tracked matchup hangs off."""
    return db.add_map("US Presidential 2024").id


class TestRunUsModelsAndExport:
    """The model-run service: matchup gating, step order, rebuild flags, failures."""

    def test_president_skipped_when_no_matchup_set(self, db: Database) -> None:
        _seed_president_map(db)
        runner = _RecordingRunner()

        run = run_us_models_and_export(db, runner=runner)

        assert runner.scripts == [
            "run_us_house_model.py",
            "run_us_senate_model.py",
            "export_elections.py",
        ]
        assert run.skipped == frozenset({"president"})
        assert "SKIPPED: no tracked presidential matchup set" in run.stdout
        assert "=== Run US President model ===" in run.stdout
        assert run.return_code == 0

    def test_null_matchup_row_also_skips_the_president(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        # A manual NULL row means "ignore this race's polls" — the model has no
        # more to work with than it does with no row at all.
        db.set_tracked_matchup(map_id, None, None, source="manual")
        runner = _RecordingRunner()

        run = run_us_models_and_export(db, runner=runner)

        assert "run_us_presidential_model.py" not in runner.scripts
        assert run.skipped == frozenset({"president"})
        assert "SKIPPED: no tracked presidential matchup set" in run.stdout

    def test_all_four_steps_run_when_matchup_set(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, "Vance (R) vs Newsom (D)", source="manual")
        runner = _RecordingRunner()

        run = run_us_models_and_export(db, runner=runner)

        assert runner.scripts == [
            "run_us_house_model.py",
            "run_us_presidential_model.py",
            "run_us_senate_model.py",
            "export_elections.py",
        ]
        assert run.skipped == frozenset()
        assert run.return_code == 0
        assert "SKIPPED" not in run.stdout
        assert "ran export_elections.py" in run.stdout

    def test_model_args_widen_the_poll_window(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, "Vance (R) vs Newsom (D)", source="manual")
        runner = _RecordingRunner()

        run_us_models_and_export(db, runner=runner)

        assert runner.args_for("run_us_house_model.py") == ("--since-days-back", "60")
        assert runner.args_for("run_us_senate_model.py") == ("--since-days-back", "60")
        assert runner.args_for("run_us_presidential_model.py") == ("--since-days-back", "120")
        assert runner.args_for("export_elections.py") == ()

    def test_failing_step_stops_the_run(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, "Vance (R) vs Newsom (D)", source="manual")
        runner = _RecordingRunner(return_codes={"run_us_presidential_model.py": 2})

        run = run_us_models_and_export(db, runner=runner)

        assert runner.scripts == ["run_us_house_model.py", "run_us_presidential_model.py"]
        assert run.return_code == 2
        assert "=== Run US President model ===\nboom" in run.stderr

    def test_export_failure_is_surfaced(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, "Vance (R) vs Newsom (D)", source="manual")
        runner = _RecordingRunner(return_codes={"export_elections.py": 1})

        run = run_us_models_and_export(db, runner=runner)

        assert run.return_code == 1

    def test_rebuild_flag_only_for_named_chambers(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, "Vance (R) vs Newsom (D)", source="manual")
        runner = _RecordingRunner()

        run_us_models_and_export(db, runner=runner, rebuild={"senate"})

        assert runner.args_for("run_us_senate_model.py") == (
            "--since-days-back",
            "60",
            "--rebuild-history",
        )
        assert runner.args_for("run_us_house_model.py") == ("--since-days-back", "60")
        assert "--rebuild-history" not in runner.args_for("run_us_presidential_model.py")
        assert runner.args_for("export_elections.py") == ()

    def test_unknown_rebuild_slug_is_ignored(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, "Vance (R) vs Newsom (D)", source="manual")
        runner = _RecordingRunner()

        run_us_models_and_export(db, runner=runner, rebuild={"congress"})

        assert all("--rebuild-history" not in args for _name, args in runner.calls)

    def test_skipped_chamber_is_not_a_failure_for_later_steps(self, db: Database) -> None:
        _seed_president_map(db)
        runner = _RecordingRunner(return_codes={"run_us_house_model.py": 5})

        run = run_us_models_and_export(db, runner=runner)

        # House fails first, so the president is never even reached for skipping.
        assert runner.scripts == ["run_us_house_model.py"]
        assert run.return_code == 5
        assert run.skipped == frozenset()


class TestRunUsModelsRoute:
    """POST /us/run-models renders whatever the service produced."""

    def test_route_renders_service_output(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        captured: dict[str, object] = {}

        def fake_run(database: Database) -> UsModelRun:
            captured["db"] = database
            return UsModelRun(
                stdout="=== Run US President model ===\nSKIPPED: no tracked presidential matchup set",
                stderr="",
                return_code=0,
                skipped=frozenset({"president"}),
            )

        monkeypatch.setattr("console.blueprints.us.run_us_models_and_export", fake_run)

        response = app.test_client().post("/us/run-models")

        assert response.status_code == 200
        assert captured["db"] is db
        body = response.get_data(as_text=True)
        assert "Run US Models" in body
        assert "SKIPPED: no tracked presidential matchup set" in body
