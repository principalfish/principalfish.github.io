"""Tests for the console site blueprint: the Rebuild Database route + home button."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask

from console import create_app


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


class TestSiteRoutesRegistered:
    """create_app wires both site actions."""

    def test_rebuild_routes_exist(self, app: Flask) -> None:
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert "/site/rebuild" in rules
        assert "/site/rebuild-database" in rules


class TestHomeCard:
    """The Site card shows the Rebuild Database button with its health warning."""

    def test_home_renders_rebuild_database_button(self, app: Flask) -> None:
        body = app.test_client().get("/").get_data(as_text=True)
        assert "Rebuild Database" in body
        assert 'action="/site/rebuild-database"' in body
        # Health-warning confirm mentions the destructive rewrite + preservation.
        assert "Rebuild the DATABASE from source" in body
        assert "polls and model runs are preserved" in body.lower()


class TestRebuildDatabaseGuard:
    """The route guards a missing orchestrator script instead of running it."""

    def test_missing_script_redirects_without_running(
        self, app: Flask, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Point the route at a non-existent script so the guard fires and the
        # orchestrator is never launched against the live database.
        monkeypatch.setattr(
            "console.blueprints.site.REBUILD_DB_SCRIPT", Path("/no/such/rebuild_database.py")
        )
        response = app.test_client().post("/site/rebuild-database")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
