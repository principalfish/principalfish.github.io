"""Tests for the console US blueprint: registered routes, home card, and outputs pages.

Route tests monkeypatch the blueprint's ``get_db`` to the shared temp-DB fixture,
so the pages render against a fresh SQLite database per test.
"""

from __future__ import annotations

import html
import pickle
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from db import Database
from models import ElectionType

from console import create_app
from console import paths as console_paths
from console.services.us_models import (
    MODEL_RUN_LOCK,
    REBUILD_TIMEOUT_SECONDS,
    STEP_TIMEOUT_SECONDS,
    US_CHAMBERS_BY_SLUG,
    UsModelRun,
    UsModelRunInterrupted,
    model_run_slot,
    run_us_chamber_and_export,
    run_us_models_and_export,
)


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
        assert "/us/run-models" in rules

    def test_import_queue_routes_exist(self, app: Flask) -> None:
        methods = {
            str(rule): rule.methods or set() for rule in app.url_map.iter_rules()
        }
        assert "GET" in methods["/us/import"]
        assert "POST" in methods["/us/import/start"]
        assert "GET" in methods["/us/import/<token>"]
        for suffix in ("confirm", "skip", "approve-group"):
            assert "POST" in methods[f"/us/import/<token>/{suffix}"]
        assert {"GET", "POST"} <= methods["/us/import/<token>/finish"]

    def test_legacy_import_route_is_gone(self, app: Flask) -> None:
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
        assert "/us/import-polls" not in rules
        assert "us.us_import_polls" not in endpoints
        assert app.test_client().post("/us/import-polls").status_code == 404

    def test_legacy_import_script_paths_are_gone(self) -> None:
        for name in (
            "US_HOUSE_POLLS_IMPORT_SCRIPT",
            "US_SENATE_POLLS_IMPORT_SCRIPT",
            "US_PRESIDENT_POLLS_IMPORT_SCRIPT",
        ):
            assert not hasattr(console_paths, name)

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

    def test_import_us_polls_links_to_the_review_queue(self, app: Flask) -> None:
        body = app.test_client().get("/").get_data(as_text=True)
        assert (
            '<a class="button primary button-block" href="/us/import">'
            "Import US Polls</a>"
        ) in body
        assert "/us/import-polls" not in body


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
        self.timeouts: dict[str, int] = {}
        self._return_codes = return_codes or {}

    def __call__(
        self, script: Path, *args: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((script.name, args))
        self.timeouts[script.name] = timeout
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

        def fake_run(
            database: Database,
            *,
            runner: object = None,
            rebuild: frozenset[str] = frozenset(),
        ) -> UsModelRun:
            captured["db"] = database
            captured["rebuild"] = rebuild
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
        # No box ticked: an ordinary run, rebuilding nothing.
        assert captured["rebuild"] == frozenset()
        body = response.get_data(as_text=True)
        assert "Run US Models" in body
        assert "SKIPPED: no tracked presidential matchup set" in body

    @pytest.mark.parametrize(
        ("fail", "error", "error_name", "partial"),
        [
            (
                "run_us_house_model.py",
                subprocess.TimeoutExpired(
                    ["python", "run_us_house_model.py"],
                    600,
                    output=b"HOUSE step reached 2026-09-10\n",
                ),
                "TimeoutExpired",
                "HOUSE step reached 2026-09-10",
            ),
            (
                "run_us_house_model.py",
                FileNotFoundError(2, "No such file or directory"),
                "FileNotFoundError",
                None,
            ),
            (
                "export_elections.py",
                subprocess.TimeoutExpired(["python", "export_elections.py"], 300),
                "TimeoutExpired",
                None,
            ),
        ],
    )
    def test_an_interrupted_run_renders_a_result_not_a_500(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        fail: str,
        error: Exception,
        error_name: str,
        partial: str | None,
    ) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        runner = _TimeoutRecorder(fail=fail, error=error)
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        response = app.test_client().post("/us/run-models")

        assert response.status_code == 200
        body = html.unescape(response.get_data(as_text=True))
        assert "Run US Models" in body
        assert "The run did not finish" in body
        assert "The export did not run, or did not finish." in body
        assert error_name in body
        if partial is not None:
            assert partial in body
        # The sequence stopped at the failing step.
        assert [name for name, _ in runner.timeouts][-1] == fail

    def test_a_run_whose_model_fails_leads_with_the_note(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        runner = _RecordingRunner(return_codes={"run_us_house_model.py": 2})
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        body = html.unescape(
            app.test_client().post("/us/run-models").get_data(as_text=True)
        )

        assert "The run did not finish" in body
        assert "ran run_us_house_model.py" in body

    def test_a_successful_run_has_no_note(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        recording_runner: _RecordingRunner,
    ) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)

        body = app.test_client().post("/us/run-models").get_data(as_text=True)

        assert "did not finish" not in body

    def test_an_interrupted_run_shows_the_chambers_that_finished(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)
        runner = _TimeoutRecorder(
            fail="export_elections.py",
            error=subprocess.TimeoutExpired(["python", "export_elections.py"], 300),
        )
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        body = html.unescape(
            app.test_client().post("/us/run-models").get_data(as_text=True)
        )

        # Both models that ran finished; only the export died.
        assert "=== Run US House model ===" in body
        assert "=== Run US Senate model ===" in body
        assert (
            "Export elections to static data files did not finish: TimeoutExpired"
            in body
        )


class TestRebuildAllHistory:
    """The home page's "rebuild all US history" box on Run US Models."""

    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)

    def test_ticked_rebuilds_every_chamber_then_exports(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")

        response = app.test_client().post(
            "/us/run-models", data={"rebuild_history": "on"}
        )

        assert response.status_code == 200
        assert recording_runner.scripts == [
            "run_us_house_model.py",
            "run_us_presidential_model.py",
            "run_us_senate_model.py",
            "export_elections.py",
        ]
        for script in recording_runner.scripts[:3]:
            assert recording_runner.args_for(script)[-1] == "--rebuild-history"
            # A rebuild at the ordinary step timeout would be killed part-way.
            assert recording_runner.timeouts[script] == REBUILD_TIMEOUT_SECONDS
        assert recording_runner.timeouts["export_elections.py"] == STEP_TIMEOUT_SECONDS
        body = html.unescape(response.get_data(as_text=True))
        assert "Rebuild US History" in body
        # The page shows the command that ran, poll windows included.
        assert (
            "run_us_house_model.py --since-days-back 60 --rebuild-history" in body
        )
        assert not MODEL_RUN_LOCK.locked()

    @pytest.mark.parametrize(
        ("form", "refused"),
        [({"rebuild_history": "on"}, "History not rebuilt"), ({}, "Models not run")],
    )
    def test_a_run_in_progress_refuses_it_and_runs_nothing(
        self,
        app: Flask,
        recording_runner: _RecordingRunner,
        form: dict[str, str],
        refused: str,
    ) -> None:
        # An ordinary run is refused too: its export would publish whatever
        # half-cleared history a rebuild in progress had left.
        client = app.test_client()
        with MODEL_RUN_LOCK:
            response = client.post("/us/run-models", data=form)

        assert response.status_code == 302
        assert response.headers["Location"] == "/"
        assert recording_runner.calls == []
        assert _flashes(client, response)[-1] == (
            f"{refused}: another US model run or history rebuild is in progress; "
            "wait for it to finish, then try again."
        )

    def test_a_rebuild_whose_model_fails_says_history_may_be_partial(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Exiting non-zero stops the sequence like a timeout does, after the
        # runner may have cleared that chamber's points.
        runner = _RecordingRunner(return_codes={"run_us_senate_model.py": 1})
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        response = app.test_client().post(
            "/us/run-models", data={"rebuild_history": "on"}
        )

        body = html.unescape(response.get_data(as_text=True))
        assert "the chamber it stopped on may be partial" in body
        assert "export_elections.py" not in runner.scripts

    def test_an_interrupted_rebuild_says_history_may_be_partial(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _TimeoutRecorder(
            fail="run_us_senate_model.py",
            error=subprocess.TimeoutExpired(["python", "run_us_senate_model.py"], 3600),
        )
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        response = app.test_client().post(
            "/us/run-models", data={"rebuild_history": "on"}
        )

        assert response.status_code == 200
        body = html.unescape(response.get_data(as_text=True))
        assert "Rebuild US History" in body
        assert "the chamber it stopped on may be partial" in body
        assert "Re-run the rebuild" in body
        assert "=== Run US House model ===" in body
        assert ("run_us_senate_model.py", REBUILD_TIMEOUT_SECONDS) in runner.timeouts
        assert not MODEL_RUN_LOCK.locked()

    def test_the_home_page_offers_the_box_behind_a_warning(self, app: Flask) -> None:
        body = html.unescape(app.test_client().get("/").get_data(as_text=True))

        form = body[body.index('action="/us/run-models"') :]
        form = form[: form.index("</form>")]
        # No ``value``: the browser then sends "on", which the route matches.
        assert '<input type="checkbox" name="rebuild_history">' in form
        assert "Rebuild all US history" in form
        assert "return !this.rebuild_history.checked || confirm(" in form
        assert "can take hours" in form


# ── Matchup pages ─────────────────────────────────────────────────────────

VANCE_NEWSOM = "Vance (R) vs Newsom (D)"
VANCE_SHAPIRO = "Vance (R) vs Shapiro (D)"
PAXTON_TALARICO = "Paxton (R) vs Talarico (D)"
CORNYN_TALARICO = "Cornyn (R) vs Talarico (D)"
OSSOFF_COLLINS = "Ossoff (D) vs Collins (R)"


def _add_matchup_polls(
    db: Database,
    map_id: int,
    matchup: str,
    ends: list[date],
    *,
    seat_id: int | None = None,
) -> list[int]:
    """Store one two-candidate poll per fieldwork end date; return their ids."""
    pollster = db.get_pollster_by_identifier("test_pollster") or db.add_pollster(
        "Test Pollster", "test_pollster"
    )
    parties = {party.name: party.id for party in db.get_all_parties()}
    for name in ("Democratic", "Republican"):
        if name not in parties:
            parties[name] = db.add_party(name).id
    poll_ids: list[int] = []
    for end in ends:
        poll = db.add_poll(
            pollster.id,
            map_id,
            end - timedelta(days=3),
            end,
            matchup=matchup,
            seat_id=seat_id,
        )
        db.add_poll_row(poll.id, parties["Republican"], 48.0, candidate_name="Rep Candidate")
        db.add_poll_row(poll.id, parties["Democratic"], 46.0, candidate_name="Dem Candidate")
        poll_ids.append(poll.id)
    return poll_ids


def _seed_president_matchups(db: Database) -> int:
    """A presidential map with national polls in two matchups and one state poll."""
    map_id = db.add_map("US Presidential 2024").id
    region = db.add_region(map_id, "Mountain")
    nevada = db.add_seat(map_id, "Nevada", region_id=region.id, electoral_votes=6)
    _add_matchup_polls(db, map_id, VANCE_NEWSOM, [date(2026, 8, 1), date(2026, 9, 10)])
    _add_matchup_polls(db, map_id, VANCE_SHAPIRO, [date(2026, 7, 4)])
    # A statewide poll's label must never be offered as a national matchup.
    _add_matchup_polls(db, map_id, "Vance (R) vs Buttigieg (D)", [date(2026, 9, 1)], seat_id=nevada.id)
    return map_id


def _seed_senate_races(db: Database) -> dict[str, int]:
    """A Senate map with Texas (two stored matchups) and Georgia (one)."""
    map_id = db.add_map("US Senate 2024").id
    region = db.add_region(map_id, "West South Central")
    texas = db.add_seat(map_id, "Texas", region_id=region.id)
    georgia = db.add_seat(map_id, "Georgia", region_id=region.id)
    _add_matchup_polls(
        db, map_id, PAXTON_TALARICO, [date(2026, 8, 20), date(2026, 9, 5)], seat_id=texas.id
    )
    _add_matchup_polls(db, map_id, CORNYN_TALARICO, [date(2026, 6, 1)], seat_id=texas.id)
    _add_matchup_polls(db, map_id, OSSOFF_COLLINS, [date(2026, 9, 2)], seat_id=georgia.id)
    return {"map_id": map_id, "texas": texas.id, "georgia": georgia.id}


def _squash(text: str) -> str:
    """Collapse runs of whitespace, so assertions ignore template indentation."""
    return " ".join(text.split())


_ALERT = re.compile(r'<div class="alert">(.*?)</div>', re.DOTALL)


def _flashes(client: FlaskClient[Response], response: Response) -> list[str]:
    """Follow ``response``'s redirect and return the flashed messages it renders.

    Read from the page rather than the session: the installed Flask stubs type
    ``session_transaction`` as returning None.
    """
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    return [html.unescape(message.strip()) for message in _ALERT.findall(body)]


@pytest.fixture()
def recording_runner(monkeypatch: pytest.MonkeyPatch) -> _RecordingRunner:
    """Replace the US blueprint's subprocess runner with a recorder."""
    runner = _RecordingRunner()
    monkeypatch.setattr("console.blueprints.us.run_python_script", runner)
    return runner


class TestMatchupRoutesRegistered:
    """create_app wires both matchup pages."""

    def test_matchup_routes_exist(self, app: Flask) -> None:
        methods = {
            str(rule): rule.methods or set() for rule in app.url_map.iter_rules()
        }
        assert {"GET", "POST"} <= methods["/us/president/matchup"]
        assert "GET" in methods["/us/matchups"]
        assert "POST" in methods["/us/matchups/<int:map_id>/<int:seat_id>"]

    def test_home_links_to_matchup_pages(self, app: Flask) -> None:
        body = app.test_client().get("/").get_data(as_text=True)
        assert 'href="/us/president/matchup"' in body
        assert 'href="/us/matchups?chamber=senate"' in body
        assert 'href="/us/matchups?chamber=house"' in body

    def test_import_page_links_to_matchup_pages(self, app: Flask) -> None:
        body = app.test_client().get("/us/import").get_data(as_text=True)
        assert 'href="/us/president/matchup"' in body
        assert 'href="/us/matchups?chamber=senate"' in body


class TestPresidentMatchupPage:
    """GET/POST /us/president/matchup."""

    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)

    def test_lists_national_labels_with_counts_and_latest_date(
        self, app: Flask, db: Database
    ) -> None:
        _seed_president_matchups(db)

        body = app.test_client().get("/us/president/matchup").get_data(as_text=True)

        assert f'value="{VANCE_NEWSOM}"' in body
        assert "2 poll(s), latest ending" in body
        assert "2026-09-10" in body
        assert f'value="{VANCE_SHAPIRO}"' in body
        assert "1 poll(s), latest ending" in body
        assert "Buttigieg" not in body
        assert "None" in body
        assert "<strong>Currently tracking:</strong> nothing </p>" in _squash(body)

    def test_valid_label_is_saved_as_manual(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        map_id = _seed_president_matchups(db)
        client = app.test_client()

        response = client.post("/us/president/matchup", data={"matchup": VANCE_SHAPIRO})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/us/president/matchup")
        tracked = db.get_tracked_matchup(map_id, None)
        assert tracked is not None
        assert (tracked.matchup, tracked.source) == (VANCE_SHAPIRO, "manual")
        assert _flashes(client, response) == [f"Now tracking {VANCE_SHAPIRO} nationally."]
        assert recording_runner.calls == []

        body = client.get("/us/president/matchup").get_data(as_text=True)
        assert f"{VANCE_SHAPIRO} (manual)" in body

    def test_unknown_label_is_rejected_with_a_flash(self, app: Flask, db: Database) -> None:
        map_id = _seed_president_matchups(db)
        db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")
        client = app.test_client()

        # A statewide label is stored, but not nationally.
        for label in ("Vance (R) vs Harris (D)", "Vance (R) vs Buttigieg (D)"):
            response = client.post("/us/president/matchup", data={"matchup": label})
            assert response.status_code == 302
            assert _flashes(client, response) == [f"Not saved: {label!r} is not a stored national matchup."]

        tracked = db.get_tracked_matchup(map_id, None)
        assert tracked is not None and tracked.matchup == VANCE_NEWSOM

    def test_missing_choice_is_rejected(self, app: Flask, db: Database) -> None:
        map_id = _seed_president_matchups(db)
        client = app.test_client()

        response = client.post("/us/president/matchup", data={})

        assert _flashes(client, response) == ["Choose a matchup, or None."]
        assert db.get_tracked_matchup(map_id, None) is None

    def test_none_deletes_the_national_row(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        map_id = _seed_president_matchups(db)
        db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")
        client = app.test_client()

        response = client.post("/us/president/matchup", data={"matchup": ""})

        assert response.status_code == 302
        assert db.get_tracked_matchup(map_id, None) is None
        assert "Cleared the national matchup" in _flashes(client, response)[0]

    def test_none_with_nothing_set_says_there_was_nothing_to_clear(
        self, app: Flask, db: Database
    ) -> None:
        _seed_president_matchups(db)
        client = app.test_client()

        response = client.post("/us/president/matchup", data={"matchup": ""})

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "No national matchup was set, so there was nothing to clear."
        ]

    def test_rebuild_runs_the_president_model_with_rebuild_history(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        _seed_president_matchups(db)

        response = app.test_client().post(
            "/us/president/matchup",
            data={"matchup": VANCE_NEWSOM, "rebuild_history": "on"},
        )

        assert response.status_code == 200
        assert recording_runner.calls == [
            (
                "run_us_presidential_model.py",
                ("--since-days-back", "120", "--rebuild-history"),
            ),
            ("export_elections.py", ()),
        ]
        body = response.get_data(as_text=True)
        assert "Rebuild US President History" in body
        assert 'href="/us/president/matchup"' in body

    def test_rebuild_is_refused_when_none_is_chosen(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        _seed_president_matchups(db)
        client = app.test_client()

        response = client.post(
            "/us/president/matchup", data={"matchup": "", "rebuild_history": "on"}
        )

        assert response.status_code == 302
        assert recording_runner.calls == []
        assert _flashes(client, response)[-1].startswith("History not rebuilt")

    def test_rejected_label_never_rebuilds(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        _seed_president_matchups(db)

        app.test_client().post(
            "/us/president/matchup",
            data={"matchup": "Nobody (R) vs Nobody (D)", "rebuild_history": "on"},
        )

        assert recording_runner.calls == []

    def test_missing_map_redirects_home(self, app: Flask) -> None:
        client = app.test_client()

        response = client.get("/us/president/matchup")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        assert "No map named 'US Presidential 2024'" in _flashes(client, response)[0]


class TestPollDetailShowsUsScope:
    """GET /polls/<id> shows the seat, the matchup and each row's candidate."""

    def test_seat_matchup_and_candidates(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.blueprints.polls.get_db", lambda: db)
        map_id = db.add_map("US Senate 2024").id
        alaska = db.add_seat(map_id, "Alaska")
        rep = db.add_party("Republican")
        dem = db.add_party("Democratic")
        pollster = db.add_pollster("Alaska Survey Research", "asr_us_senate")
        poll = db.add_poll(
            pollster.id,
            map_id,
            date(2026, 9, 1),
            date(2026, 9, 4),
            matchup="Sullivan (R) vs Peltola (D) vs Sullivan (R)",
            seat_id=alaska.id,
        )
        # Two Republicans: both rows must survive the party-keyed matrix.
        db.add_poll_row(poll.id, rep.id, 40.0, candidate_name="Dan S. Sullivan")
        db.add_poll_row(poll.id, rep.id, 7.0, candidate_name="Dan J. Sullivan")
        db.add_poll_row(poll.id, dem.id, 45.0, candidate_name="Mary Peltola")

        response = app.test_client().get(f"/polls/{poll.id}")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "<strong>Seat:</strong> Alaska" in body
        assert "<strong>Matchup:</strong> Sullivan (R) vs Peltola (D) vs Sullivan (R)" in body
        assert "<th>Candidate</th>" in body
        for name, value in (
            ("Dan S. Sullivan", "40.0"),
            ("Dan J. Sullivan", "7.0"),
            ("Mary Peltola", "45.0"),
        ):
            assert f"<td>{name}</td>" in body
            assert f"<td>{value}</td>" in body

    def test_party_only_poll_has_no_us_fields(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.blueprints.polls.get_db", lambda: db)
        map_id = db.add_map("Westminster 2024").id
        lab = db.add_party("Labour")
        pollster = db.add_pollster("YouGov", "yougov")
        poll = db.add_poll(pollster.id, map_id, date(2026, 9, 1), date(2026, 9, 2))
        db.add_poll_row(poll.id, lab.id, 30.0)

        body = app.test_client().get(f"/polls/{poll.id}").get_data(as_text=True)

        assert "Seat:" not in body
        assert "Matchup:" not in body
        assert "Candidate" not in body
        assert "<td>30.0</td>" in body


class TestRaceMatchupsPage:
    """GET /us/matchups and POST /us/matchups/<map_id>/<seat_id>."""

    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)

    def test_lists_auto_manual_and_effective_values(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        map_id = seeded["map_id"]
        # Texas: the importer chose Paxton, the user overrode to Cornyn.
        db.set_tracked_matchup(map_id, seeded["texas"], PAXTON_TALARICO, source="auto")
        db.set_tracked_matchup(map_id, seeded["texas"], CORNYN_TALARICO, source="manual")
        # Georgia: automatic only.
        db.set_tracked_matchup(map_id, seeded["georgia"], OSSOFF_COLLINS, source="auto")

        response = app.test_client().get("/us/matchups?chamber=senate")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "US Senate Race Matchups" in body
        assert "2 race(s) on US Senate 2024" in body
        texas_row = body[body.index('<th scope="row">Texas</th>'):]
        texas_row = texas_row[: texas_row.index("</tr>")]
        assert f"<td> {CORNYN_TALARICO} (manual) </td>" in _squash(texas_row)
        assert f"<td>{PAXTON_TALARICO}</td>" in texas_row
        assert f"{PAXTON_TALARICO} — 2 poll(s), latest" in texas_row
        assert "2026-09-05" in texas_row
        assert f"{CORNYN_TALARICO} — 1 poll(s), latest" in texas_row
        assert f"/us/matchups/{map_id}/{seeded['texas']}" in texas_row
        georgia_row = body[body.index('<th scope="row">Georgia</th>'):]
        georgia_row = georgia_row[: georgia_row.index("</tr>")]
        assert f"{OSSOFF_COLLINS} (auto)" in georgia_row
        # Georgia sorts before Texas.
        assert body.index("Georgia</th>") < body.index("Texas</th>")

    def test_untracked_race_with_polls_is_listed(self, app: Flask, db: Database) -> None:
        _seed_senate_races(db)

        body = app.test_client().get("/us/matchups?chamber=senate").get_data(as_text=True)

        assert body.count("Not tracked (polls ignored)") == 2

    def test_tracked_race_without_polls_is_listed(self, app: Flask, db: Database) -> None:
        map_id = db.add_map("US House Districts 2024").id
        seat = db.add_seat(map_id, "TX-07")
        db.set_tracked_matchup(map_id, seat.id, None, source="manual")

        body = app.test_client().get("/us/matchups?chamber=house").get_data(as_text=True)

        assert "US House Race Matchups" in body
        assert '<th scope="row">TX-07</th>' in body
        assert "Ignored (manual)" in body

    @pytest.mark.parametrize("query", ["?chamber=president", "?chamber=congress", ""])
    def test_unknown_chamber_is_404(self, app: Flask, db: Database, query: str) -> None:
        _seed_senate_races(db)

        assert app.test_client().get(f"/us/matchups{query}").status_code == 404

    def test_set_is_kept_through_a_later_auto_update(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        seeded = _seed_senate_races(db)
        map_id, texas = seeded["map_id"], seeded["texas"]
        db.set_tracked_matchup(map_id, texas, PAXTON_TALARICO, source="auto")
        client = app.test_client()

        response = client.post(
            f"/us/matchups/{map_id}/{texas}",
            data={"action": "set", "matchup": CORNYN_TALARICO},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/us/matchups?chamber=senate")
        assert _flashes(client, response) == [f"Texas: The race now tracks {CORNYN_TALARICO}."]
        assert recording_runner.calls == []

        # A later import picks a new lead table.
        outcome = db.set_tracked_matchup(map_id, texas, "Paxton (R) vs Allred (D)", source="auto")

        assert outcome == "kept_manual"
        tracked = db.get_tracked_matchup(map_id, texas)
        assert tracked is not None
        assert (tracked.matchup, tracked.source, tracked.auto_matchup) == (
            CORNYN_TALARICO,
            "manual",
            "Paxton (R) vs Allred (D)",
        )

    def test_auto_restores_the_automatic_matchup(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        map_id, texas = seeded["map_id"], seeded["texas"]
        db.set_tracked_matchup(map_id, texas, PAXTON_TALARICO, source="auto")
        db.set_tracked_matchup(map_id, texas, CORNYN_TALARICO, source="manual")
        client = app.test_client()

        response = client.post(f"/us/matchups/{map_id}/{texas}", data={"action": "auto"})

        tracked = db.get_tracked_matchup(map_id, texas)
        assert tracked is not None
        assert (tracked.matchup, tracked.source) == (PAXTON_TALARICO, "auto")
        assert _flashes(client, response) == ["Texas: The race now follows its automatic matchup."]

    def test_auto_without_a_tracked_row_says_there_was_nothing_to_reset(
        self, app: Flask, db: Database
    ) -> None:
        # Like clearing an unset national matchup: nothing to do, not a refusal.
        seeded = _seed_senate_races(db)
        client = app.test_client()

        response = client.post(
            f"/us/matchups/{seeded['map_id']}/{seeded['texas']}", data={"action": "auto"}
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "Texas: The race is not tracked, so there was nothing to reset."
        ]
        assert db.get_tracked_matchup(seeded["map_id"], seeded["texas"]) is None

    @pytest.mark.parametrize("legacy_auto_row", [False, True])
    def test_auto_on_a_race_the_importer_never_set_stops_tracking_it(
        self, app: Flask, db: Database, legacy_auto_row: bool
    ) -> None:
        seeded = _seed_senate_races(db)
        map_id, texas = seeded["map_id"], seeded["texas"]
        # Set by hand only, so the importer's auto_matchup is NULL.
        db.set_tracked_matchup(map_id, texas, CORNYN_TALARICO, source="manual")
        if legacy_auto_row:
            # What "Use automatic" used to leave behind: (NULL, auto).
            db.clear_tracked_matchup_override(map_id, texas)
        client = app.test_client()

        response = client.post(f"/us/matchups/{map_id}/{texas}", data={"action": "auto"})

        assert db.get_tracked_matchup(map_id, texas) is None
        (message,) = _flashes(client, response)
        assert message.startswith("Texas: The importer has not chosen a matchup")
        assert "now not tracked" in message
        page = client.get("/us/matchups?chamber=senate").get_data(as_text=True)
        texas_row = page[page.index('<th scope="row">Texas</th>'):]
        assert "Not tracked (polls ignored)" in texas_row[: texas_row.index("</tr>")]

    def test_ignore_stores_null_as_manual(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        map_id, texas = seeded["map_id"], seeded["texas"]
        db.set_tracked_matchup(map_id, texas, PAXTON_TALARICO, source="auto")
        client = app.test_client()

        response = client.post(f"/us/matchups/{map_id}/{texas}", data={"action": "ignore"})

        tracked = db.get_tracked_matchup(map_id, texas)
        assert tracked is not None
        assert (tracked.matchup, tracked.source, tracked.auto_matchup) == (
            None,
            "manual",
            PAXTON_TALARICO,
        )
        assert _flashes(client, response) == ["Texas: The race's polls are now ignored."]

    def test_label_stored_for_a_different_seat_is_rejected(
        self, app: Flask, db: Database
    ) -> None:
        seeded = _seed_senate_races(db)
        map_id, texas = seeded["map_id"], seeded["texas"]
        db.set_tracked_matchup(map_id, texas, PAXTON_TALARICO, source="auto")
        client = app.test_client()

        response = client.post(
            f"/us/matchups/{map_id}/{texas}",
            data={"action": "set", "matchup": OSSOFF_COLLINS},
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [
            f"Texas: not saved — {OSSOFF_COLLINS!r} is not a matchup stored for this race."
        ]
        tracked = db.get_tracked_matchup(map_id, texas)
        assert tracked is not None
        assert (tracked.matchup, tracked.source) == (PAXTON_TALARICO, "auto")

    def test_set_without_a_label_is_rejected(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        client = app.test_client()

        response = client.post(f"/us/matchups/{seeded['map_id']}/{seeded['texas']}", data={"action": "set"})

        assert "not a matchup stored for this race" in _flashes(client, response)[0]
        assert db.get_tracked_matchup(seeded["map_id"], seeded["texas"]) is None

    def test_unknown_action_is_rejected(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        client = app.test_client()

        response = client.post(
            f"/us/matchups/{seeded['map_id']}/{seeded['texas']}", data={"action": "delete"}
        )

        assert _flashes(client, response) == ["Texas: not saved — Unknown matchup action 'delete'."]

    @pytest.mark.parametrize("action", ["ignore", "set", "auto"])
    def test_seat_on_another_map_is_flashed_not_500(
        self, app: Flask, db: Database, action: str
    ) -> None:
        seeded = _seed_senate_races(db)
        house_map_id = db.add_map("US House Districts 2024").id
        house_seat = db.add_seat(house_map_id, "TX-07")
        client = app.test_client()

        response = client.post(
            f"/us/matchups/{seeded['map_id']}/{house_seat.id}",
            data={"action": action, "matchup": PAXTON_TALARICO},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/us/matchups?chamber=senate")
        assert _flashes(client, response)[0].startswith("TX-07: not saved — ")
        assert db.get_tracked_matchups_for_map(seeded["map_id"]) == []
        assert db.get_tracked_matchups_for_map(house_map_id) == []

    def test_seat_on_another_map_names_the_owning_map(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        house_map_id = db.add_map("US House Districts 2024").id
        house_seat = db.add_seat(house_map_id, "TX-07")
        client = app.test_client()

        response = client.post(f"/us/matchups/{seeded['map_id']}/{house_seat.id}", data={"action": "ignore"})

        assert _flashes(client, response) == [
            f"TX-07: not saved — seat {house_seat.id} belongs to map {house_map_id},"
            f" not map {seeded['map_id']}"
        ]

    def test_map_without_race_matchups_is_flashed(self, app: Flask, db: Database) -> None:
        president_map_id = _seed_president_matchups(db)
        nevada = db.get_seats_for_map(president_map_id)[0]
        client = app.test_client()

        response = client.post(
            f"/us/matchups/{president_map_id}/{nevada.id}", data={"action": "ignore"}
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [f"Map #{president_map_id} has no per-race matchups."]
        assert db.get_tracked_matchup(president_map_id, nevada.id) is None

    def test_rebuild_runs_the_chamber_model_with_rebuild_history(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        seeded = _seed_senate_races(db)

        response = app.test_client().post(
            f"/us/matchups/{seeded['map_id']}/{seeded['texas']}",
            data={"action": "set", "matchup": PAXTON_TALARICO, "rebuild_history": "on"},
        )

        assert response.status_code == 200
        assert recording_runner.calls == [
            ("run_us_senate_model.py", ("--since-days-back", "60", "--rebuild-history")),
            ("export_elections.py", ()),
        ]
        body = response.get_data(as_text=True)
        assert "Rebuild US Senate History" in body
        assert 'href="/us/matchups?chamber=senate"' in body

    def test_house_rebuild_uses_the_house_model(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        map_id = db.add_map("US House Districts 2024").id
        seat = db.add_seat(map_id, "TX-07")

        app.test_client().post(
            f"/us/matchups/{map_id}/{seat.id}",
            data={"action": "ignore", "rebuild_history": "on"},
        )

        assert recording_runner.scripts == ["run_us_house_model.py", "export_elections.py"]
        assert recording_runner.args_for("run_us_house_model.py")[-1] == "--rebuild-history"

    def test_refused_change_never_rebuilds(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        seeded = _seed_senate_races(db)

        app.test_client().post(
            f"/us/matchups/{seeded['map_id']}/{seeded['texas']}",
            data={"action": "set", "matchup": OSSOFF_COLLINS, "rebuild_history": "on"},
        )

        assert recording_runner.calls == []


class TestRunUsChamberAndExport:
    """The single-chamber form of the model-run service."""

    def test_president_skipped_without_a_matchup(self, db: Database) -> None:
        _seed_president_map(db)
        runner = _RecordingRunner()

        run = run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["president"], runner=runner, rebuild_history=True
        )

        assert runner.scripts == ["export_elections.py"]
        assert run.skipped == frozenset({"president"})

    def test_failing_model_is_not_exported(self, db: Database) -> None:
        runner = _RecordingRunner(return_codes={"run_us_senate_model.py": 1})

        run = run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["senate"], runner=runner, rebuild_history=False
        )

        assert runner.calls == [("run_us_senate_model.py", ("--since-days-back", "60"))]
        assert run.return_code == 1


# ── Rebuild timeouts ──────────────────────────────────────────────────────────


class _TimeoutRecorder:
    """Runner that records each step's timeout and can fail one script."""

    def __init__(self, *, fail: str | None = None, error: Exception | None = None) -> None:
        self.timeouts: list[tuple[str, int]] = []
        self._fail = fail
        self._error = error

    def __call__(
        self, script: Path, *args: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.timeouts.append((script.name, timeout))
        if script.name == self._fail and self._error is not None:
            raise self._error
        return subprocess.CompletedProcess(
            args=[str(script), *args], returncode=0, stdout="", stderr=""
        )


class TestRebuildTimeout:
    def test_the_rebuild_timeout_is_much_larger_than_a_step(self) -> None:
        assert REBUILD_TIMEOUT_SECONDS >= 10 * STEP_TIMEOUT_SECONDS

    def test_only_rebuilt_chambers_get_the_rebuild_timeout(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")
        runner = _TimeoutRecorder()

        run_us_models_and_export(db, runner=runner, rebuild={"senate"})

        assert runner.timeouts == [
            ("run_us_house_model.py", STEP_TIMEOUT_SECONDS),
            ("run_us_presidential_model.py", STEP_TIMEOUT_SECONDS),
            ("run_us_senate_model.py", REBUILD_TIMEOUT_SECONDS),
            ("export_elections.py", STEP_TIMEOUT_SECONDS),
        ]

    def test_a_timed_out_step_propagates_from_the_service(self, db: Database) -> None:
        runner = _TimeoutRecorder(
            fail="run_us_senate_model.py",
            error=subprocess.TimeoutExpired(["python", "run_us_senate_model.py"], 3600),
        )

        with pytest.raises(UsModelRunInterrupted) as caught:
            run_us_chamber_and_export(
                db, US_CHAMBERS_BY_SLUG["senate"], runner=runner, rebuild_history=True
            )
        # Still a SubprocessError, chained to the original, for older callers.
        assert isinstance(caught.value, subprocess.SubprocessError)
        assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)
        # Rebuilt from its args, so copy and pickle keep the partial run.
        copied = pickle.loads(pickle.dumps(caught.value))
        assert (copied.step, copied.partial) == (
            caught.value.step,
            caught.value.partial,
        )
        assert str(copied) == str(caught.value)

    def test_an_interrupted_run_keeps_the_steps_that_finished(
        self, db: Database
    ) -> None:
        # House finishes; Senate times out after printing a line; no export.
        runner = _TimeoutRecorder(
            fail="run_us_senate_model.py",
            error=subprocess.TimeoutExpired(
                ["python", "run_us_senate_model.py"],
                300,
                output=b"SENATE reached 2026-09-10\n",
                stderr=b"warning: slow\n",
            ),
        )

        with pytest.raises(UsModelRunInterrupted) as caught:
            run_us_models_and_export(db, runner=runner)

        interrupted = caught.value
        assert interrupted.step == "Run US Senate model"
        assert str(interrupted).startswith("Run US Senate model did not finish:")
        partial = interrupted.partial
        assert partial.return_code == 1
        # President has no tracked matchup here, so it was skipped, not run.
        assert partial.skipped == frozenset({"president"})
        assert partial.stdout.split("\n=== ")[0] == "=== Run US House model ===\n"
        senate = "=== Run US Senate model ===\nSENATE reached 2026-09-10"
        assert senate in partial.stdout
        assert "Export" not in partial.stdout
        assert partial.stderr == "=== Run US Senate model ===\nwarning: slow\n"


class TestRebuildRouteFailure:
    """A rebuild whose subprocess dies renders a result page, not a 500."""

    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)

    @pytest.mark.parametrize(
        ("error", "error_name", "partial"),
        [
            (
                subprocess.TimeoutExpired(
                    ["python", "run_us_presidential_model.py"],
                    3600,
                    output=b"REBUILD-HISTORY from=2026-08-01 to=2026-09-10\n",
                ),
                "TimeoutExpired",
                "REBUILD-HISTORY from=2026-08-01 to=2026-09-10",
            ),
            (FileNotFoundError(2, "No such file or directory"), "FileNotFoundError", None),
        ],
    )
    def test_the_page_says_history_may_be_partial(
        self,
        app: Flask,
        db: Database,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        error_name: str,
        partial: str | None,
    ) -> None:
        _seed_president_matchups(db)
        runner = _TimeoutRecorder(fail="run_us_presidential_model.py", error=error)
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        response = app.test_client().post(
            "/us/president/matchup",
            data={"matchup": VANCE_NEWSOM, "rebuild_history": "on"},
        )

        assert response.status_code == 200
        body = html.unescape(response.get_data(as_text=True))
        assert "Rebuild US President History" in body
        assert "trend history may be partial" in body
        assert "Re-run the rebuild" in body
        assert error_name in body
        if partial is not None:
            assert partial in body
        # The export never runs over a half-rebuilt history.
        assert [name for name, _timeout in runner.timeouts] == ["run_us_presidential_model.py"]
        assert 'href="/us/president/matchup"' in body

    def test_a_race_rebuild_timeout_is_caught_too(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_senate_races(db)
        runner = _TimeoutRecorder(
            fail="run_us_senate_model.py",
            error=subprocess.TimeoutExpired(["python", "run_us_senate_model.py"], 3600),
        )
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        response = app.test_client().post(
            f"/us/matchups/{seeded['map_id']}/{seeded['texas']}",
            data={"action": "set", "matchup": PAXTON_TALARICO, "rebuild_history": "on"},
        )

        assert response.status_code == 200
        body = html.unescape(response.get_data(as_text=True))
        assert "Rebuild US Senate History" in body
        assert "trend history may be partial" in body


class TestModelRunSlot:
    """Only one US model run or history rebuild runs at a time."""

    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.blueprints.us.get_db", lambda: db)

    def _post_texas_rebuild(
        self,
        app: Flask,
        db: Database,
    ) -> tuple[FlaskClient[Response], Response]:
        seeded = _seed_senate_races(db)
        client = app.test_client()
        response = client.post(
            f"/us/matchups/{seeded['map_id']}/{seeded['texas']}",
            data={"action": "set", "matchup": PAXTON_TALARICO, "rebuild_history": "on"},
        )
        return client, response

    def test_a_run_in_progress_refuses_the_rebuild(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        with MODEL_RUN_LOCK:
            client, response = self._post_texas_rebuild(app, db)

        assert response.status_code == 302
        assert response.headers["Location"] == "/us/matchups?chamber=senate"
        assert recording_runner.calls == []
        assert _flashes(client, response)[-1] == (
            "History not rebuilt: another US model run or history rebuild is in "
            "progress; wait for it to finish, then try again."
        )

    def test_the_lock_is_free_after_a_rebuild(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        _, response = self._post_texas_rebuild(app, db)

        assert response.status_code == 200
        assert not MODEL_RUN_LOCK.locked()

    def test_a_chamber_rebuild_whose_model_fails_says_history_may_be_partial(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _RecordingRunner(return_codes={"run_us_senate_model.py": 1})
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        _, response = self._post_texas_rebuild(app, db)

        body = html.unescape(response.get_data(as_text=True))
        assert "trend history may be partial" in body
        assert "Re-run the rebuild" in body

    def test_the_lock_is_free_after_a_failed_rebuild(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _TimeoutRecorder(
            fail="run_us_senate_model.py",
            error=subprocess.TimeoutExpired(["python", "run_us_senate_model.py"], 3600),
        )
        monkeypatch.setattr("console.blueprints.us.run_python_script", runner)

        _, response = self._post_texas_rebuild(app, db)

        assert "trend history may be partial" in html.unescape(
            response.get_data(as_text=True)
        )
        assert not MODEL_RUN_LOCK.locked()

    def test_a_senate_rebuild_in_progress_blocks_a_president_rebuild(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        # Every run ends with the export, which would publish the Senate's
        # half-cleared history, so one run at a time across chambers.
        _seed_president_matchups(db)

        with MODEL_RUN_LOCK:
            response = app.test_client().post(
                "/us/president/matchup",
                data={"matchup": VANCE_NEWSOM, "rebuild_history": "on"},
            )

        assert response.status_code == 302
        assert recording_runner.calls == []

    def test_the_slot_is_held_for_the_run_and_released_after(self) -> None:
        with model_run_slot() as free:
            assert free
            assert MODEL_RUN_LOCK.locked()
            with model_run_slot() as second:
                assert not second
            # A refused second request must not release the first's hold.
            assert MODEL_RUN_LOCK.locked()
        assert not MODEL_RUN_LOCK.locked()

    def test_the_slot_is_released_when_the_run_raises(self) -> None:
        with pytest.raises(RuntimeError):
            with model_run_slot():
                raise RuntimeError("boom")
        assert not MODEL_RUN_LOCK.locked()
