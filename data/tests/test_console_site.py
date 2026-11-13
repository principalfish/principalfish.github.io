"""Tests for the console site blueprint: the Rebuild Database route + admin button."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from console import create_app


@pytest.fixture()
def app() -> Flask:
    application = create_app()
    application.config["TESTING"] = True
    return application


class TestSiteRoutesRegistered:
    """create_app wires the rebuild-database action and no site-data rebuild."""

    def test_rebuild_routes_exist(self, app: Flask) -> None:
        rules = {str(rule) for rule in app.url_map.iter_rules()}
        assert "/site/rebuild" not in rules
        assert "/site/rebuild-database" in rules


class TestAdminPage:
    """The Admin page shows the Rebuild Database button with its health warning."""

    def test_admin_renders_rebuild_database_button(self, app: Flask) -> None:
        body = app.test_client().get("/admin").get_data(as_text=True)
        assert "Rebuild Database" in body
        assert 'action="/site/rebuild"' not in body
        assert "Rebuild Site Data" not in body
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
            "console.admin.site.REBUILD_DB_SCRIPT", Path("/no/such/rebuild_database.py")
        )
        response = app.test_client().post("/site/rebuild-database")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")
