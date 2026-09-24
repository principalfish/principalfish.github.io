"""The console's app-wide same-origin guard and its pinned host names.

``db_admin``'s own routes are covered in ``test_console_db_admin.py``; these
tests check other blueprints refuse a cross-site POST before doing anything,
and that a request for any other host name is not answered.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from console import create_app

CROSS_SITE = [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://evil.example"},
    {"Origin": "null"},
]
SAME_ORIGIN = [
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"Origin": "http://localhost"},
    {},
]


@pytest.fixture()
def app() -> Flask:
    app = create_app()
    app.config["TESTING"] = True
    return app


class _Tripwire:
    """Stands in for a route's first real step; records that it was reached."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls += 1
        raise RuntimeError("reached the route body")


@pytest.mark.parametrize("headers", CROSS_SITE)
def test_the_guard_is_app_wide(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    # A blueprint that never registered the hook itself: the hour-long rebuild.
    tripwire = _Tripwire()
    monkeypatch.setattr("console.blueprints.site.run_python_script", tripwire)

    response = app.test_client().post("/site/rebuild-database", headers=headers)

    assert response.status_code == 403
    assert tripwire.calls == 0


@pytest.mark.parametrize("host", ["127.0.0.1:5055", "localhost:5055", "localhost"])
def test_the_console_answers_its_own_host_names(app: Flask, host: str) -> None:
    response = app.test_client().get("/us/import", headers={"Host": host})

    assert response.status_code == 200


def test_another_host_name_is_not_answered(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hostile domain re-pointed at 127.0.0.1 would otherwise be same-origin
    # with its own page, and its POSTs would pass the header check.
    tripwire = _Tripwire()
    monkeypatch.setattr("console.blueprints.us.get_db", tripwire)

    response = app.test_client().post(
        "/us/run-models",
        headers={"Host": "evil.example:5055", "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 400
    assert tripwire.calls == 0


@pytest.mark.parametrize("headers", CROSS_SITE)
@pytest.mark.parametrize("route", ["/us/run-models", "/us/import/start"])
def test_a_cross_site_post_is_refused_before_the_route_runs(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    headers: dict[str, str],
) -> None:
    # Each route's first real step: the run's database handle, and the start
    # form's validation (which runs before the start route touches the DB).
    tripwire = _Tripwire()
    monkeypatch.setattr("console.blueprints.us.get_db", tripwire)
    monkeypatch.setattr(
        "console.blueprints.us_poll_import.UsQueueStartForm",
        SimpleNamespace(model_validate=tripwire),
    )

    response = app.test_client().post(route, headers=headers)

    assert response.status_code == 403
    assert tripwire.calls == 0


@pytest.mark.parametrize("route", ["/us/run-models", "/us/import/start"])
def test_the_tripwire_is_reached_without_the_cross_site_header(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    # Proves the refusal test above would notice a missing guard.
    tripwire = _Tripwire()
    monkeypatch.setattr("console.blueprints.us.get_db", tripwire)
    monkeypatch.setattr(
        "console.blueprints.us_poll_import.UsQueueStartForm",
        SimpleNamespace(model_validate=tripwire),
    )

    with pytest.raises(RuntimeError, match="reached the route body"):
        app.test_client().post(route)

    assert tripwire.calls == 1


@pytest.mark.parametrize("headers", SAME_ORIGIN)
def test_a_same_origin_post_reaches_the_route(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    tripwire = _Tripwire()
    monkeypatch.setattr("console.blueprints.us.get_db", tripwire)

    with pytest.raises(RuntimeError, match="reached the route body"):
        app.test_client().post("/us/run-models", headers=headers)

    assert tripwire.calls == 1


def test_a_cross_site_get_is_not_refused(app: Flask) -> None:
    response = app.test_client().get(
        "/us/import",
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 200
