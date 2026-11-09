"""Tests for the console US blueprint: registered routes, home card, and outputs pages.

Route tests monkeypatch the blueprint's ``get_db`` to the shared temp-DB fixture,
so the pages render against a fresh SQLite database per test.
"""

from __future__ import annotations

import html
import pickle
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from console import create_app, paths as console_paths
from console.us.models import (
    MODEL_RUN_LOCK,
    REBUILD_TIMEOUT_SECONDS,
    STEP_TIMEOUT_SECONDS,
    US_CHAMBER_CONVERTER,
    US_CHAMBER_SLUGS,
    US_CHAMBERS,
    US_CHAMBERS_BY_SLUG,
    US_RACE_CHAMBER_CONVERTER,
    US_RACE_CHAMBER_SLUGS,
    UsModelRunInterrupted,
    model_run_slot,
    NO_MATCHUP_NOTE,
    UsModelOptions,
    run_us_chamber_and_export,
)
from core.db import Database
from core.schema import ElectionType
from tests.console_fixtures import select_block
from tests.us_ev_fixtures import seed_allocations, set_model_target


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
    seed_allocations(db, {"Big State": 20, "Small State": 3})
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
    set_model_target(db, model.id)
    db.add_vote(model.id, big.id, party_id=dem.id, vote_total=52.0, elected=True)
    db.add_vote(model.id, big.id, party_id=rep.id, vote_total=48.0)
    db.add_vote(model.id, small.id, party_id=rep.id, vote_total=58.0, elected=True)
    db.add_vote(model.id, small.id, party_id=dem.id, vote_total=42.0)

    return {"map_id": m.id, "baseline_id": baseline.id, "model_id": model.id, "dem_id": dem.id, "rep_id": rep.id}


class TestUsRoutesRegistered:
    """create_app wires the US action routes plus all per-chamber endpoints."""

    def test_model_run_routes_exist(self, app: Flask) -> None:
        methods: set[str] = set()
        for rule in app.url_map.iter_rules():
            if str(rule) == f"/{US_CHAMBER_CONVERTER}/model":
                methods |= rule.methods or set()
        assert {"GET", "POST"} <= methods

    def test_the_combined_run_route_is_gone(self, app: Flask) -> None:
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
        assert "/us/run-models" not in rules
        assert "us.run_us_models" not in endpoints
        assert app.test_client().post("/us/run-models").status_code == 404

    def test_import_queue_routes_exist(self, app: Flask) -> None:
        methods = {
            str(rule): rule.methods or set() for rule in app.url_map.iter_rules()
        }
        base = f"/{US_CHAMBER_CONVERTER}/polls/import"
        assert "GET" in methods[base]
        assert "POST" in methods[f"{base}/start"]
        assert "GET" in methods[f"{base}/<token>"]
        for suffix in ("confirm", "skip", "approve-race"):
            assert "POST" in methods[f"{base}/<token>/{suffix}"]
        assert {"GET", "POST"} <= methods[f"{base}/<token>/finish"]

    def test_the_chamber_converters_are_the_chamber_constants(self, app: Flask) -> None:
        """Every ``<chamber>`` rule restricts to one of two shared slug lists."""
        pattern = re.compile(r"<any\((.*?)\):chamber>")
        found: dict[tuple[str, ...], int] = {}
        for rule in app.url_map.iter_rules():
            match = pattern.search(rule.rule)
            if match is not None:
                slugs = tuple(match.group(1).split(","))
                found[slugs] = found.get(slugs, 0) + 1

        assert set(found) == {US_CHAMBER_SLUGS, US_RACE_CHAMBER_SLUGS}
        assert US_CHAMBER_SLUGS == tuple(chamber.slug for chamber in US_CHAMBERS)
        assert set(US_CHAMBER_SLUGS) == {"house", "senate", "president"}
        assert set(US_RACE_CHAMBER_SLUGS) == {"house", "senate"}
        assert US_CHAMBER_CONVERTER == f"<any({','.join(US_CHAMBER_SLUGS)}):chamber>"
        assert (
            US_RACE_CHAMBER_CONVERTER
            == f"<any({','.join(US_RACE_CHAMBER_SLUGS)}):chamber>"
        )

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


US_LANDING_PAGES = ("/house", "/senate", "/president")


class TestLandingPages:
    """Each US section's landing page links its import, model page and outputs."""

    @pytest.mark.parametrize("path", US_LANDING_PAGES)
    def test_landing_renders_the_us_actions(self, app: Flask, path: str) -> None:
        body = app.test_client().get(path).get_data(as_text=True)
        assert "Auto import" in body
        assert f'href="{path}/model">Run model</a>' in body
        assert f'href="{path}/outputs"' in body
        # The model forms live on their own pages, not on the landing page.
        assert "<form" not in body
        assert "run-models" not in body

    @pytest.mark.parametrize("path", US_LANDING_PAGES)
    def test_auto_import_links_to_the_sections_own_review_queue(
        self, app: Flask, path: str
    ) -> None:
        body = app.test_client().get(path).get_data(as_text=True)
        assert (
            f'<a class="button primary button-block" href="{path}/polls/import">'
            "Auto import</a>"
        ) in body
        assert "/us/import" not in body


class TestUsOutputsPages:
    """The per-chamber outputs list/detail/delete routes against a temp DB."""

    def test_house_outputs_lists_forecast(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_us_house(db)
        monkeypatch.setattr("console.us.views.get_db", lambda: db)
        monkeypatch.setattr("console.us.outputs.get_db", lambda: db)
        response = app.test_client().get("/house/outputs")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "US House Model Outputs" in body
        assert "US House UNS 2026-06-01" in body
        assert f"/house/outputs/{seeded['model_id']}" in body

    def test_house_output_detail_renders(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_us_house(db)
        monkeypatch.setattr("console.us.views.get_db", lambda: db)
        monkeypatch.setattr("console.us.outputs.get_db", lambda: db)
        response = app.test_client().get(f"/house/outputs/{seeded['model_id']}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "US House UNS 2026-06-01" in body
        assert "ME-01" in body

    def test_house_output_detail_unknown_redirects(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.us.views.get_db", lambda: db)
        monkeypatch.setattr("console.us.outputs.get_db", lambda: db)
        response = app.test_client().get("/house/outputs/99999")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/house/outputs")

    def test_delete_house_output(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_us_house(db)
        monkeypatch.setattr("console.us.views.get_db", lambda: db)
        monkeypatch.setattr("console.us.outputs.get_db", lambda: db)
        response = app.test_client().post(f"/house/outputs/{seeded['model_id']}/delete")
        assert response.status_code == 302

        from console.shared.model_outputs import build_output_detail_context

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
        from console.shared.model_outputs import build_output_detail_context

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
        from console.shared.model_outputs import build_output_detail_context

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
        monkeypatch.setattr("console.us.views.get_db", lambda: db)
        monkeypatch.setattr("console.us.outputs.get_db", lambda: db)
        body = app.test_client().get(f"/president/outputs/{seeded['model_id']}").get_data(as_text=True)
        assert "Electoral Votes" in body
        assert "Electoral Votes by Party" in body

    def test_president_list_trend_plots_electoral_votes(self, db: Database, tmp_path: Path) -> None:
        # A president trend cache with an "e" value drives the list chart onto EV.
        import json

        from console.shared.model_outputs import build_outputs_context

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

        from console.shared.model_outputs import build_outputs_context

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


class TestRunUsChamberServiceGating:
    """The model-run service: matchup gating, step order, failures."""

    def test_null_matchup_row_also_skips_the_president(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        # A manual NULL row means "ignore this race's polls" — the model has no
        # more to work with than it does with no row at all.
        db.set_tracked_matchup(map_id, None, None, source="manual")
        runner = _RecordingRunner()

        run = run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["president"], runner=runner, rebuild_history=False
        )

        assert runner.scripts == ["export_elections.py"]
        assert run.skipped == frozenset({"president"})
        assert "=== Run US President model ===" in run.stdout
        assert NO_MATCHUP_NOTE in run.stdout
        assert run.return_code == 0

    def test_the_president_runs_when_a_matchup_is_set(self, db: Database) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")
        runner = _RecordingRunner()

        run = run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["president"], runner=runner, rebuild_history=False
        )

        assert runner.scripts == ["run_us_presidential_model.py", "export_elections.py"]
        assert run.skipped == frozenset()
        assert "ran export_elections.py" in run.stdout

    @pytest.mark.parametrize(
        ("slug", "script", "days"),
        [
            ("house", "run_us_house_model.py", "60"),
            ("senate", "run_us_senate_model.py", "60"),
            ("president", "run_us_presidential_model.py", "120"),
        ],
    )
    def test_model_args_widen_the_poll_window(
        self, db: Database, slug: str, script: str, days: str
    ) -> None:
        map_id = _seed_president_map(db)
        db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")
        runner = _RecordingRunner()

        run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG[slug], runner=runner, rebuild_history=False
        )

        assert runner.args_for(script) == ("--since-days-back", days)
        assert runner.args_for("export_elections.py") == ()

    def test_export_failure_is_surfaced(self, db: Database) -> None:
        runner = _RecordingRunner(return_codes={"export_elections.py": 1})

        run = run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["house"], runner=runner, rebuild_history=False
        )

        assert run.return_code == 1

    def test_rebuild_flag_is_appended_for_the_rebuilt_chamber(
        self, db: Database
    ) -> None:
        runner = _RecordingRunner()

        run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["senate"], runner=runner, rebuild_history=True
        )

        assert runner.args_for("run_us_senate_model.py") == (
            "--since-days-back",
            "60",
            "--rebuild-history",
        )
        assert runner.args_for("export_elections.py") == ()

    def test_every_queue_contest_belongs_to_exactly_one_chamber(self) -> None:
        from importers.polls.us.us_wikipedia_polls import US_CONTESTS

        offered = [
            slug for chamber in US_CHAMBERS_BY_SLUG.values() for slug in chamber.contest_slugs
        ]
        assert sorted(offered) == sorted(contest.slug for contest in US_CONTESTS)


class TestRunUsChamberOptions:
    """The single-chamber service: extra model arguments, dry runs, the export."""

    def test_default_call_runs_the_chambers_own_args_then_exports(
        self, db: Database
    ) -> None:
        runner = _RecordingRunner()

        run = run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["house"], runner=runner, rebuild_history=False
        )

        assert runner.calls == [
            ("run_us_house_model.py", ("--since-days-back", "60")),
            ("export_elections.py", ()),
        ]
        assert run.return_code == 0

    def test_extra_arguments_follow_the_poll_window(self, db: Database) -> None:
        runner = _RecordingRunner()

        run_us_chamber_and_export(
            db,
            US_CHAMBERS_BY_SLUG["senate"],
            runner=runner,
            rebuild_history=True,
            since_days_back=90,
            half_life_days=14.0,
            seat_prior_weight=0.0,
            ignore_seat_polls=True,
        )

        assert runner.args_for("run_us_senate_model.py") == (
            "--since-days-back",
            "90",
            "--half-life-days",
            "14.0",
            "--seat-prior-weight",
            "0.0",
            "--ignore-seat-polls",
            "--rebuild-history",
        )
        assert runner.timeouts["run_us_senate_model.py"] == REBUILD_TIMEOUT_SECONDS

    def test_export_can_be_left_out(self, db: Database) -> None:
        runner = _RecordingRunner()

        run = run_us_chamber_and_export(
            db,
            US_CHAMBERS_BY_SLUG["house"],
            runner=runner,
            rebuild_history=False,
            export=False,
        )

        assert runner.scripts == ["run_us_house_model.py"]
        assert run.return_code == 0

    def test_a_dry_run_never_exports_even_when_asked_to(self, db: Database) -> None:
        runner = _RecordingRunner()

        run_us_chamber_and_export(
            db,
            US_CHAMBERS_BY_SLUG["house"],
            runner=runner,
            rebuild_history=False,
            dry_run=True,
            export=True,
        )

        assert runner.calls == [
            ("run_us_house_model.py", ("--since-days-back", "60", "--dry-run"))
        ]

    def test_a_dry_run_cannot_rebuild_history(self, db: Database) -> None:
        runner = _RecordingRunner()

        with pytest.raises(ValueError, match="dry run cannot rebuild"):
            run_us_chamber_and_export(
                db,
                US_CHAMBERS_BY_SLUG["house"],
                runner=runner,
                rebuild_history=True,
                dry_run=True,
            )

        assert runner.calls == []

    def test_default_options_add_no_arguments(self) -> None:
        for chamber in US_CHAMBERS_BY_SLUG.values():
            assert UsModelOptions().args_for(chamber) == chamber.model_args

    @pytest.mark.parametrize(
        ("slug", "days"), [("house", 60), ("senate", 60), ("president", 120)]
    )
    def test_the_default_poll_window_is_read_from_the_model_args(
        self, slug: str, days: int
    ) -> None:
        assert US_CHAMBERS_BY_SLUG[slug].default_since_days_back == days


# ── The per-chamber model pages ───────────────────────────────────────────

US_MODEL_SCRIPTS = {
    "house": "run_us_house_model.py",
    "senate": "run_us_senate_model.py",
    "president": "run_us_presidential_model.py",
}
US_SLUGS = list(US_MODEL_SCRIPTS)


def _model_form(
    overrides: dict[str, str] | None = None, *, drop: tuple[str, ...] = ()
) -> dict[str, str]:
    """A valid real-run post for a US model page: not a dry run, export ticked.

    Every value differs from the form's defaults, so a command that carries them
    can only have got them from the post.
    """
    form = {
        "since_days_back": "45",
        "half_life_days": "21.0",
        "seat_prior_weight": "2.0",
        "dry_run": "false",
        "export_when_done": "on",
    }
    form.update(overrides or {})
    return {name: value for name, value in form.items() if name not in drop}


def _track_president(db: Database) -> None:
    """Choose a national matchup, so the President's model is not skipped."""
    map_id = _seed_president_map(db)
    db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")


def _alerts(body: str) -> list[str]:
    return [html.unescape(message.strip()) for message in _ALERT.findall(body)]


class TestModelFormPage:
    """GET /<chamber>/model draws the chamber's run form."""

    @pytest.mark.parametrize("slug", US_SLUGS)
    def test_the_form_opens_with_the_chambers_defaults(
        self, app: Flask, slug: str
    ) -> None:
        chamber = US_CHAMBERS_BY_SLUG[slug]

        response = app.test_client().get(f"/{slug}/model")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert f"<h2>Run {chamber.label} Model</h2>" in body
        assert f'action="/{slug}/model"' in body
        window = select_block(body, "since_days_back")
        assert f'<option value="{chamber.default_since_days_back}" selected>' in window
        assert '<option value="30.0" selected>' in select_block(body, "half_life_days")
        assert '<option value="1.0" selected>' in select_block(
            body, "seat_prior_weight"
        )
        # Safe by default: a preview that exports, once switched to a real run.
        assert '<option value="true" selected>' in select_block(body, "dry_run")
        assert '<input type="checkbox" name="export_when_done" checked>' in body
        assert '<input type="checkbox" name="rebuild_history">' in body
        assert '<input type="checkbox" name="ignore_seat_polls">' in body
        assert "A dry run never exports" in body
        # Date-range backfills are not offered.
        assert "--start-date" not in body
        assert "--continue-on-error" not in body

    @pytest.mark.parametrize("slug", US_SLUGS)
    def test_the_rebuild_confirm_names_the_one_chamber(
        self, app: Flask, slug: str
    ) -> None:
        label = US_CHAMBERS_BY_SLUG[slug].label
        body = app.test_client().get(f"/{slug}/model").get_data(as_text=True)

        match = re.search(r'<form[^>]*onsubmit="([^"]*)"', body)
        assert match is not None
        onsubmit = html.unescape(match.group(1))
        assert onsubmit.startswith("return !this.rebuild_history.checked || confirm(")
        assert f"Rebuild {label} history?" in onsubmit
        assert "ALL" not in onsubmit
        assert "can take hours" in onsubmit

    def test_an_unknown_chamber_has_no_page(self, app: Flask) -> None:
        assert app.test_client().get("/congress/model").status_code == 404


@pytest.fixture()
def model_db(db: Database, monkeypatch: pytest.MonkeyPatch) -> Database:
    """The temp database behind the US routes."""
    monkeypatch.setattr("console.us.views.get_db", lambda: db)
    return db


class TestModelRun:
    """POST /<chamber>/model validates the form, runs the model, maybe exports."""

    @pytest.mark.parametrize("slug", US_SLUGS)
    def test_a_real_run_posts_only_its_own_chamber_then_exports(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
        slug: str,
    ) -> None:
        _track_president(model_db)

        response = app.test_client().post(
            f"/{slug}/model", data=_model_form({"ignore_seat_polls": "on"})
        )

        assert response.status_code == 200
        assert recording_runner.calls == [
            (
                US_MODEL_SCRIPTS[slug],
                (
                    "--since-days-back",
                    "45",
                    "--half-life-days",
                    "21.0",
                    "--seat-prior-weight",
                    "2.0",
                    "--ignore-seat-polls",
                ),
            ),
            ("export_elections.py", ()),
        ]
        assert recording_runner.timeouts == {
            US_MODEL_SCRIPTS[slug]: STEP_TIMEOUT_SECONDS,
            "export_elections.py": STEP_TIMEOUT_SECONDS,
        }
        body = html.unescape(response.get_data(as_text=True))
        assert (
            f"{US_MODEL_SCRIPTS[slug]} --since-days-back 45 --half-life-days 21.0 "
            "--seat-prior-weight 2.0 --ignore-seat-polls → export_elections.py"
        ) in body
        assert "The run finished and the export was requested" in body
        assert "ran export_elections.py" in body
        # The posted values are drawn back into the form.
        assert '<option value="45" selected>' in select_block(body, "since_days_back")
        assert '<input type="checkbox" name="ignore_seat_polls" checked>' in body
        assert not MODEL_RUN_LOCK.locked()

    @pytest.mark.parametrize("slug", US_SLUGS)
    def test_export_off_runs_the_model_alone(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
        slug: str,
    ) -> None:
        _track_president(model_db)

        response = app.test_client().post(
            f"/{slug}/model", data=_model_form(drop=("export_when_done",))
        )

        assert recording_runner.scripts == [US_MODEL_SCRIPTS[slug]]
        body = response.get_data(as_text=True)
        assert "Export when done was off, so nothing was exported." in body
        assert "export_elections.py" not in body
        assert '<input type="checkbox" name="export_when_done">' in body

    @pytest.mark.parametrize("slug", US_SLUGS)
    def test_a_dry_run_skips_the_export_even_with_the_box_ticked(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
        slug: str,
    ) -> None:
        _track_president(model_db)

        response = app.test_client().post(
            f"/{slug}/model",
            data=_model_form({"dry_run": "true", "export_when_done": "on"}),
        )

        assert recording_runner.scripts == [US_MODEL_SCRIPTS[slug]]
        assert recording_runner.args_for(US_MODEL_SCRIPTS[slug])[-1] == "--dry-run"
        body = response.get_data(as_text=True)
        assert "Dry run: the model computed without writing anything" in body
        assert "export_elections.py" not in body

    def test_a_post_without_the_dry_run_field_is_a_dry_run(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
    ) -> None:
        app.test_client().post("/house/model", data=_model_form(drop=("dry_run",)))

        assert recording_runner.scripts == ["run_us_house_model.py"]
        assert "--dry-run" in recording_runner.args_for("run_us_house_model.py")

    @pytest.mark.parametrize("slug", US_SLUGS)
    def test_rebuild_history_uses_the_flag_and_the_rebuild_timeout(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
        slug: str,
    ) -> None:
        _track_president(model_db)

        response = app.test_client().post(
            f"/{slug}/model", data=_model_form({"rebuild_history": "on"})
        )

        script = US_MODEL_SCRIPTS[slug]
        assert recording_runner.scripts == [script, "export_elections.py"]
        assert recording_runner.args_for(script)[-1] == "--rebuild-history"
        assert "--dry-run" not in recording_runner.args_for(script)
        # A rebuild at the ordinary step timeout would be killed part-way.
        assert recording_runner.timeouts[script] == REBUILD_TIMEOUT_SECONDS
        assert recording_runner.timeouts["export_elections.py"] == STEP_TIMEOUT_SECONDS
        body = html.unescape(response.get_data(as_text=True))
        assert f"{script} --since-days-back 45" in body
        assert "--rebuild-history → export_elections.py" in body

    def test_rebuild_history_with_a_dry_run_is_refused(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
    ) -> None:
        response = app.test_client().post(
            "/house/model",
            data=_model_form({"dry_run": "true", "rebuild_history": "on"}),
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        [alert] = _alerts(body)
        assert alert.startswith("Invalid model argument value: ")
        assert "cannot rebuild history" in alert
        assert recording_runner.calls == []
        # The form is drawn again as posted, so only the conflict needs fixing.
        assert '<input type="checkbox" name="rebuild_history" checked>' in body

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("since_days_back", "0"),
            ("since_days_back", "-5"),
            ("since_days_back", "abc"),
            ("since_days_back", "30.5"),
            ("half_life_days", "0"),
            ("half_life_days", "-1"),
            ("half_life_days", "inf"),
            ("half_life_days", "nan"),
            ("seat_prior_weight", "-1"),
            ("seat_prior_weight", "inf"),
            ("seat_prior_weight", "nan"),
            ("seat_prior_weight", "abc"),
        ],
    )
    def test_an_invalid_value_flashes_and_runs_nothing(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
        field: str,
        value: str,
    ) -> None:
        response = app.test_client().post(
            "/senate/model", data=_model_form({field: value})
        )

        assert response.status_code == 200
        [alert] = _alerts(response.get_data(as_text=True))
        assert alert.startswith(f"Invalid model argument value: {field}: ")
        assert recording_runner.calls == []

    def test_a_zero_seat_prior_weight_is_valid(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
    ) -> None:
        app.test_client().post(
            "/house/model", data=_model_form({"seat_prior_weight": "0"})
        )

        args = recording_runner.args_for("run_us_house_model.py")
        assert args[args.index("--seat-prior-weight") + 1] == "0.0"

    @pytest.mark.parametrize("slug", US_SLUGS)
    @pytest.mark.parametrize(
        ("form", "refused"),
        [
            ({"rebuild_history": "on"}, "History not rebuilt"),
            ({}, "{label} model not run"),
            ({"dry_run": "true"}, "{label} model not run"),
        ],
    )
    def test_a_run_in_progress_refuses_it_and_runs_nothing(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
        slug: str,
        form: dict[str, str],
        refused: str,
    ) -> None:
        _track_president(model_db)
        client = app.test_client()
        with model_run_slot() as free:
            assert free
            response = client.post(f"/{slug}/model", data=_model_form(form))

        assert response.status_code == 302
        assert response.headers["Location"] == f"/{slug}/model"
        assert recording_runner.calls == []
        assert _flashes(client, response)[-1] == (
            f"{refused.format(label=US_CHAMBERS_BY_SLUG[slug].label)}: "
            "another US model run or history rebuild is in progress; "
            "wait for it to finish, then try again."
        )

    def test_the_president_is_skipped_without_a_tracked_matchup(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
    ) -> None:
        _seed_president_map(model_db)

        response = app.test_client().post("/president/model", data=_model_form())

        assert response.status_code == 200
        # Nothing ran, so there is nothing to export either.
        assert recording_runner.calls == []
        body = html.unescape(response.get_data(as_text=True))
        assert NO_MATCHUP_NOTE in body
        assert "(skipped: no tracked matchup)" in body
        assert "export_elections.py" not in body

    def test_a_missing_export_script_is_refused_before_anything_runs(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        missing = tmp_path / "missing.py"
        monkeypatch.setattr("console.us.views.EXPORT_ELECTION_SCRIPT", missing)
        client = app.test_client()

        response = client.post("/house/model", data=_model_form())

        assert response.status_code == 302
        assert response.headers["Location"] == "/house/model"
        assert recording_runner.calls == []
        assert _flashes(client, response)[-1] == f"Script not found: {missing}"

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
        model_db: Database,
        monkeypatch: pytest.MonkeyPatch,
        fail: str,
        error: Exception,
        error_name: str,
        partial: str | None,
    ) -> None:
        runner = _TimeoutRecorder(fail=fail, error=error)
        monkeypatch.setattr("console.us.views.run_python_script", runner)

        response = app.test_client().post("/house/model", data=_model_form())

        assert response.status_code == 200
        body = html.unescape(response.get_data(as_text=True))
        assert "The run did not finish" in body
        assert "The export did not run, or did not finish." in body
        assert "failed with exit code 1" in body
        assert error_name in body
        if partial is not None:
            assert partial in body
        # The sequence stopped at the failing step.
        assert [name for name, _ in runner.timeouts][-1] == fail
        assert not MODEL_RUN_LOCK.locked()

    def test_an_interrupted_dry_run_says_nothing_needs_repairing(
        self, app: Flask, model_db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _TimeoutRecorder(
            fail="run_us_house_model.py",
            error=subprocess.TimeoutExpired(["python", "run_us_house_model.py"], 300),
        )
        monkeypatch.setattr("console.us.views.run_python_script", runner)

        body = html.unescape(
            app.test_client()
            .post("/house/model", data=_model_form({"dry_run": "true"}))
            .get_data(as_text=True)
        )

        assert "The dry run did not finish. It writes nothing" in body
        assert "export may be out of step" not in body

    def test_an_interrupted_rebuild_says_history_may_be_partial(
        self, app: Flask, model_db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _TimeoutRecorder(
            fail="run_us_senate_model.py",
            error=subprocess.TimeoutExpired(["python", "run_us_senate_model.py"], 3600),
        )
        monkeypatch.setattr("console.us.views.run_python_script", runner)

        response = app.test_client().post(
            "/senate/model", data=_model_form({"rebuild_history": "on"})
        )

        assert response.status_code == 200
        body = html.unescape(response.get_data(as_text=True))
        assert "this chamber's trend history may be partial" in body
        assert "Re-run the rebuild" in body
        assert ("run_us_senate_model.py", REBUILD_TIMEOUT_SECONDS) in runner.timeouts
        assert not MODEL_RUN_LOCK.locked()

    def test_a_model_that_fails_leads_with_the_note_and_skips_the_export(
        self, app: Flask, model_db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _RecordingRunner(return_codes={"run_us_house_model.py": 2})
        monkeypatch.setattr("console.us.views.run_python_script", runner)

        body = html.unescape(
            app.test_client().post("/house/model", data=_model_form()).get_data(
                as_text=True
            )
        )

        assert runner.scripts == ["run_us_house_model.py"]
        assert "The run did not finish" in body
        assert "ran run_us_house_model.py" in body
        assert "The run failed with exit code 2" in body

    def test_an_export_that_fails_is_shown_as_a_failed_run(
        self, app: Flask, model_db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _RecordingRunner(return_codes={"export_elections.py": 1})
        monkeypatch.setattr("console.us.views.run_python_script", runner)

        body = html.unescape(
            app.test_client().post("/house/model", data=_model_form()).get_data(
                as_text=True
            )
        )

        assert runner.scripts == ["run_us_house_model.py", "export_elections.py"]
        assert "<strong>Exit code:</strong> 1</p>" in body
        assert "The run failed with exit code 1" in body

    def test_a_successful_run_has_no_interruption_note(
        self,
        app: Flask,
        model_db: Database,
        recording_runner: _RecordingRunner,
    ) -> None:
        body = app.test_client().post("/house/model", data=_model_form()).get_data(
            as_text=True
        )

        assert "did not finish" not in body
        assert "<strong>Exit code:</strong> 0</p>" in body


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
    monkeypatch.setattr("console.us.views.run_python_script", runner)
    return runner


class TestMatchupRoutesRegistered:
    """create_app wires both matchup pages."""

    def test_matchup_routes_exist(self, app: Flask) -> None:
        methods = {
            str(rule): rule.methods or set() for rule in app.url_map.iter_rules()
        }
        assert {"GET", "POST"} <= methods["/president/matchup"]
        assert "GET" in methods["/<any(house,senate):chamber>/matchups"]
        assert "POST" in methods[
            "/<any(house,senate):chamber>/matchups/<int:map_id>/<int:seat_id>"
        ]

    def test_landing_pages_link_to_their_matchup_pages(self, app: Flask) -> None:
        client = app.test_client()
        assert 'href="/president/matchup"' in client.get("/president").get_data(
            as_text=True
        )
        for chamber in ("senate", "house"):
            body = client.get(f"/{chamber}").get_data(as_text=True)
            assert f'href="/{chamber}/matchups"' in body

    def test_import_pages_link_to_their_own_matchup_page(self, app: Flask) -> None:
        client = app.test_client()
        president = client.get("/president/polls/import").get_data(as_text=True)
        assert 'href="/president/matchup"' in president
        assert 'href="/senate/matchups"' not in president
        for chamber in ("senate", "house"):
            body = client.get(f"/{chamber}/polls/import").get_data(as_text=True)
            assert f'href="/{chamber}/matchups"' in body
            assert 'href="/president/matchup"' not in body


class TestPresidentMatchupPage:
    """GET/POST /president/matchup."""

    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.us.views.get_db", lambda: db)

    def test_lists_national_labels_with_counts_and_latest_date(
        self, app: Flask, db: Database
    ) -> None:
        _seed_president_matchups(db)

        body = app.test_client().get("/president/matchup").get_data(as_text=True)

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

        response = client.post("/president/matchup", data={"matchup": VANCE_SHAPIRO})

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/president/matchup")
        tracked = db.get_tracked_matchup(map_id, None)
        assert tracked is not None
        assert (tracked.matchup, tracked.source) == (VANCE_SHAPIRO, "manual")
        assert _flashes(client, response) == [f"Now tracking {VANCE_SHAPIRO} nationally."]
        assert recording_runner.calls == []

        body = client.get("/president/matchup").get_data(as_text=True)
        assert f"{VANCE_SHAPIRO} (manual)" in body

    def test_unknown_label_is_rejected_with_a_flash(self, app: Flask, db: Database) -> None:
        map_id = _seed_president_matchups(db)
        db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")
        client = app.test_client()

        # A statewide label is stored, but not nationally.
        for label in ("Vance (R) vs Harris (D)", "Vance (R) vs Buttigieg (D)"):
            response = client.post("/president/matchup", data={"matchup": label})
            assert response.status_code == 302
            assert _flashes(client, response) == [f"Not saved: {label!r} is not a stored national matchup."]

        tracked = db.get_tracked_matchup(map_id, None)
        assert tracked is not None and tracked.matchup == VANCE_NEWSOM

    def test_missing_choice_is_rejected(self, app: Flask, db: Database) -> None:
        map_id = _seed_president_matchups(db)
        client = app.test_client()

        response = client.post("/president/matchup", data={})

        assert _flashes(client, response) == ["Choose a matchup, or None."]
        assert db.get_tracked_matchup(map_id, None) is None

    def test_none_deletes_the_national_row(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        map_id = _seed_president_matchups(db)
        db.set_tracked_matchup(map_id, None, VANCE_NEWSOM, source="manual")
        client = app.test_client()

        response = client.post("/president/matchup", data={"matchup": ""})

        assert response.status_code == 302
        assert db.get_tracked_matchup(map_id, None) is None
        assert "Cleared the national matchup" in _flashes(client, response)[0]

    def test_none_with_nothing_set_says_there_was_nothing_to_clear(
        self, app: Flask, db: Database
    ) -> None:
        _seed_president_matchups(db)
        client = app.test_client()

        response = client.post("/president/matchup", data={"matchup": ""})

        assert response.status_code == 302
        assert _flashes(client, response) == [
            "No national matchup was set, so there was nothing to clear."
        ]

    def test_rebuild_runs_the_president_model_with_rebuild_history(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        _seed_president_matchups(db)

        response = app.test_client().post(
            "/president/matchup",
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
        assert 'href="/president/matchup"' in body

    def test_rebuild_is_refused_when_none_is_chosen(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        _seed_president_matchups(db)
        client = app.test_client()

        response = client.post(
            "/president/matchup", data={"matchup": "", "rebuild_history": "on"}
        )

        assert response.status_code == 302
        assert recording_runner.calls == []
        assert _flashes(client, response)[-1].startswith("History not rebuilt")

    def test_rejected_label_never_rebuilds(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        _seed_president_matchups(db)

        app.test_client().post(
            "/president/matchup",
            data={"matchup": "Nobody (R) vs Nobody (D)", "rebuild_history": "on"},
        )

        assert recording_runner.calls == []

    def test_missing_map_redirects_home(self, app: Flask) -> None:
        client = app.test_client()

        response = client.get("/president/matchup")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
        assert "No map named 'US Presidential 2024'" in _flashes(client, response)[0]


class TestPollDetailShowsUsScope:
    """GET /polls/<id> shows the seat, the matchup and each row's candidate."""

    def test_seat_matchup_and_candidates(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.polls.views.get_db", lambda: db)
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
        monkeypatch.setattr("console.polls.views.get_db", lambda: db)
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
    """GET /house|senate/matchups and POST /<chamber>/matchups/<map_id>/<seat_id>."""

    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.us.views.get_db", lambda: db)

    def test_lists_auto_manual_and_effective_values(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        map_id = seeded["map_id"]
        # Texas: the importer chose Paxton, the user overrode to Cornyn.
        db.set_tracked_matchup(map_id, seeded["texas"], PAXTON_TALARICO, source="auto")
        db.set_tracked_matchup(map_id, seeded["texas"], CORNYN_TALARICO, source="manual")
        # Georgia: automatic only.
        db.set_tracked_matchup(map_id, seeded["georgia"], OSSOFF_COLLINS, source="auto")

        response = app.test_client().get("/senate/matchups")

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
        assert f"/senate/matchups/{map_id}/{seeded['texas']}" in texas_row
        georgia_row = body[body.index('<th scope="row">Georgia</th>'):]
        georgia_row = georgia_row[: georgia_row.index("</tr>")]
        assert f"{OSSOFF_COLLINS} (auto)" in georgia_row
        # Georgia sorts before Texas.
        assert body.index("Georgia</th>") < body.index("Texas</th>")

    def test_untracked_race_with_polls_is_listed(self, app: Flask, db: Database) -> None:
        _seed_senate_races(db)

        body = app.test_client().get("/senate/matchups").get_data(as_text=True)

        assert body.count("Not tracked (polls ignored)") == 2

    def test_tracked_race_without_polls_is_listed(self, app: Flask, db: Database) -> None:
        map_id = db.add_map("US House Districts 2024").id
        seat = db.add_seat(map_id, "TX-07")
        db.set_tracked_matchup(map_id, seat.id, None, source="manual")

        body = app.test_client().get("/house/matchups").get_data(as_text=True)

        assert "US House Race Matchups" in body
        assert '<th scope="row">TX-07</th>' in body
        assert "Ignored (manual)" in body

    @pytest.mark.parametrize(
        "path", ["/president/matchups", "/congress/matchups", "/matchups"]
    )
    def test_unknown_chamber_is_404(self, app: Flask, db: Database, path: str) -> None:
        _seed_senate_races(db)

        assert app.test_client().get(path).status_code == 404

    def test_set_is_kept_through_a_later_auto_update(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        seeded = _seed_senate_races(db)
        map_id, texas = seeded["map_id"], seeded["texas"]
        db.set_tracked_matchup(map_id, texas, PAXTON_TALARICO, source="auto")
        client = app.test_client()

        response = client.post(
            f"/senate/matchups/{map_id}/{texas}",
            data={"action": "set", "matchup": CORNYN_TALARICO},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/senate/matchups")
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

        response = client.post(f"/senate/matchups/{map_id}/{texas}", data={"action": "auto"})

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
            f"/senate/matchups/{seeded['map_id']}/{seeded['texas']}", data={"action": "auto"}
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

        response = client.post(f"/senate/matchups/{map_id}/{texas}", data={"action": "auto"})

        assert db.get_tracked_matchup(map_id, texas) is None
        (message,) = _flashes(client, response)
        assert message.startswith("Texas: The importer has not chosen a matchup")
        assert "now not tracked" in message
        page = client.get("/senate/matchups").get_data(as_text=True)
        texas_row = page[page.index('<th scope="row">Texas</th>'):]
        assert "Not tracked (polls ignored)" in texas_row[: texas_row.index("</tr>")]

    def test_ignore_stores_null_as_manual(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        map_id, texas = seeded["map_id"], seeded["texas"]
        db.set_tracked_matchup(map_id, texas, PAXTON_TALARICO, source="auto")
        client = app.test_client()

        response = client.post(f"/senate/matchups/{map_id}/{texas}", data={"action": "ignore"})

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
            f"/senate/matchups/{map_id}/{texas}",
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

        response = client.post(f"/senate/matchups/{seeded['map_id']}/{seeded['texas']}", data={"action": "set"})

        assert "not a matchup stored for this race" in _flashes(client, response)[0]
        assert db.get_tracked_matchup(seeded["map_id"], seeded["texas"]) is None

    def test_unknown_action_is_rejected(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        client = app.test_client()

        response = client.post(
            f"/senate/matchups/{seeded['map_id']}/{seeded['texas']}", data={"action": "delete"}
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
            f"/senate/matchups/{seeded['map_id']}/{house_seat.id}",
            data={"action": action, "matchup": PAXTON_TALARICO},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/senate/matchups")
        assert _flashes(client, response)[0].startswith("TX-07: not saved — ")
        assert db.get_tracked_matchups_for_map(seeded["map_id"]) == []
        assert db.get_tracked_matchups_for_map(house_map_id) == []

    def test_seat_on_another_map_names_the_owning_map(self, app: Flask, db: Database) -> None:
        seeded = _seed_senate_races(db)
        house_map_id = db.add_map("US House Districts 2024").id
        house_seat = db.add_seat(house_map_id, "TX-07")
        client = app.test_client()

        response = client.post(f"/senate/matchups/{seeded['map_id']}/{house_seat.id}", data={"action": "ignore"})

        assert _flashes(client, response) == [
            f"TX-07: not saved — seat {house_seat.id} belongs to map {house_map_id},"
            f" not map {seeded['map_id']}"
        ]

    def test_map_without_race_matchups_is_flashed(self, app: Flask, db: Database) -> None:
        president_map_id = _seed_president_matchups(db)
        nevada = db.get_seats_for_map(president_map_id)[0]
        db.add_map("US Senate 2024")  # so the page the refusal returns to renders
        client = app.test_client()

        response = client.post(
            f"/senate/matchups/{president_map_id}/{nevada.id}", data={"action": "ignore"}
        )

        assert response.status_code == 302
        assert _flashes(client, response) == [f"Map #{president_map_id} has no per-race matchups."]
        assert db.get_tracked_matchup(president_map_id, nevada.id) is None

    def test_a_chamber_that_does_not_own_the_map_is_refused(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        seeded = _seed_senate_races(db)
        map_id, texas = seeded["map_id"], seeded["texas"]
        db.add_map("US House Districts 2024")  # so the page it returns to renders
        client = app.test_client()

        response = client.post(
            f"/house/matchups/{map_id}/{texas}",
            data={"action": "ignore", "rebuild_history": "on"},
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/house/matchups")
        assert _flashes(client, response) == [
            f"Map #{map_id} is not the US House race map."
        ]
        assert db.get_tracked_matchup(map_id, texas) is None
        assert recording_runner.calls == []

    def test_the_president_is_not_a_matchup_chamber(
        self, app: Flask, db: Database
    ) -> None:
        seeded = _seed_senate_races(db)

        response = app.test_client().post(
            f"/president/matchups/{seeded['map_id']}/{seeded['texas']}",
            data={"action": "ignore"},
        )

        assert response.status_code == 404

    def test_rebuild_runs_the_chamber_model_with_rebuild_history(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        seeded = _seed_senate_races(db)

        response = app.test_client().post(
            f"/senate/matchups/{seeded['map_id']}/{seeded['texas']}",
            data={"action": "set", "matchup": PAXTON_TALARICO, "rebuild_history": "on"},
        )

        assert response.status_code == 200
        assert recording_runner.calls == [
            ("run_us_senate_model.py", ("--since-days-back", "60", "--rebuild-history")),
            ("export_elections.py", ()),
        ]
        body = response.get_data(as_text=True)
        assert "Rebuild US Senate History" in body
        assert 'href="/senate/matchups"' in body

    def test_house_rebuild_uses_the_house_model(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        map_id = db.add_map("US House Districts 2024").id
        seat = db.add_seat(map_id, "TX-07")

        app.test_client().post(
            f"/house/matchups/{map_id}/{seat.id}",
            data={"action": "ignore", "rebuild_history": "on"},
        )

        assert recording_runner.scripts == ["run_us_house_model.py", "export_elections.py"]
        assert recording_runner.args_for("run_us_house_model.py")[-1] == "--rebuild-history"

    def test_refused_change_never_rebuilds(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        seeded = _seed_senate_races(db)

        app.test_client().post(
            f"/senate/matchups/{seeded['map_id']}/{seeded['texas']}",
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

    def test_only_the_rebuild_step_gets_the_rebuild_timeout(self, db: Database) -> None:
        runner = _TimeoutRecorder()

        run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["senate"], runner=runner, rebuild_history=True
        )

        assert runner.timeouts == [
            ("run_us_senate_model.py", REBUILD_TIMEOUT_SECONDS),
            ("export_elections.py", STEP_TIMEOUT_SECONDS),
        ]

    def test_a_plain_run_uses_the_step_timeout_throughout(self, db: Database) -> None:
        runner = _TimeoutRecorder()

        run_us_chamber_and_export(
            db, US_CHAMBERS_BY_SLUG["senate"], runner=runner, rebuild_history=False
        )

        assert runner.timeouts == [
            ("run_us_senate_model.py", STEP_TIMEOUT_SECONDS),
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

    def test_an_interrupted_run_keeps_what_the_dying_step_printed(
        self, db: Database
    ) -> None:
        # The Senate model times out after printing a line; no export.
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
            run_us_chamber_and_export(
                db, US_CHAMBERS_BY_SLUG["senate"], runner=runner, rebuild_history=False
            )

        interrupted = caught.value
        assert interrupted.step == "Run US Senate model"
        assert str(interrupted).startswith("Run US Senate model did not finish:")
        partial = interrupted.partial
        assert partial.return_code == 1
        assert partial.skipped == frozenset()
        senate = "=== Run US Senate model ===\nSENATE reached 2026-09-10"
        assert senate in partial.stdout
        assert "Export" not in partial.stdout
        assert partial.stderr == "=== Run US Senate model ===\nwarning: slow\n"


class TestRebuildRouteFailure:
    """A rebuild whose subprocess dies renders a result page, not a 500."""

    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.us.views.get_db", lambda: db)

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
        monkeypatch.setattr("console.us.views.run_python_script", runner)

        response = app.test_client().post(
            "/president/matchup",
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
        assert 'href="/president/matchup"' in body

    def test_a_race_rebuild_timeout_is_caught_too(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = _seed_senate_races(db)
        runner = _TimeoutRecorder(
            fail="run_us_senate_model.py",
            error=subprocess.TimeoutExpired(["python", "run_us_senate_model.py"], 3600),
        )
        monkeypatch.setattr("console.us.views.run_python_script", runner)

        response = app.test_client().post(
            f"/senate/matchups/{seeded['map_id']}/{seeded['texas']}",
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
        monkeypatch.setattr("console.us.views.get_db", lambda: db)

    def _post_texas_rebuild(
        self,
        app: Flask,
        db: Database,
    ) -> tuple[FlaskClient[Response], Response]:
        seeded = _seed_senate_races(db)
        client = app.test_client()
        response = client.post(
            f"/senate/matchups/{seeded['map_id']}/{seeded['texas']}",
            data={"action": "set", "matchup": PAXTON_TALARICO, "rebuild_history": "on"},
        )
        return client, response

    def test_a_run_in_progress_refuses_the_rebuild(
        self, app: Flask, db: Database, recording_runner: _RecordingRunner
    ) -> None:
        with MODEL_RUN_LOCK:
            client, response = self._post_texas_rebuild(app, db)

        assert response.status_code == 302
        assert response.headers["Location"] == "/senate/matchups"
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
        monkeypatch.setattr("console.us.views.run_python_script", runner)

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
        monkeypatch.setattr("console.us.views.run_python_script", runner)

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
                "/president/matchup",
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
