"""Console URL shape: matchup URLs build from the chamber; every form posts on."""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

import pytest
from flask import Flask, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.routing import MapAdapter
from werkzeug.routing.exceptions import BuildError

from tests.console_fixtures import (  # noqa: F401  (pytest fixtures)
    app,
    crawl,
    seeded_app,
)


def test_the_new_matchup_urls_build_from_the_chamber(
    app: Flask,  # noqa: F811
) -> None:
    with app.test_request_context():
        assert url_for("us.race_matchups", chamber="house") == "/house/matchups"
        assert url_for("us.race_matchups", chamber="senate") == "/senate/matchups"
        with pytest.raises(BuildError):
            url_for("us.race_matchups")
        # The converter itself refuses a chamber that has no per-race matchups.
        with pytest.raises(ValueError):
            url_for("us.race_matchups", chamber="president")


def test_the_shared_us_pages_build_from_the_chamber(
    app: Flask,  # noqa: F811
) -> None:
    with app.test_request_context():
        assert url_for("us_poll_import.import_form", chamber="house") == (
            "/house/polls/import"
        )
        assert url_for("us_poll_import.start", chamber="senate") == (
            "/senate/polls/import/start"
        )
        for endpoint, suffix in (
            ("queue", ""),
            ("confirm", "/confirm"),
            ("skip", "/skip"),
            ("approve_race", "/approve-race"),
            ("finish", "/finish"),
        ):
            assert url_for(
                f"us_poll_import.{endpoint}", chamber="president", token="abc"
            ) == f"/president/polls/import/abc{suffix}"
        assert url_for("us_results.us_results_form", chamber="senate") == (
            "/senate/results"
        )
        assert url_for("us_results.us_results_preview", chamber="house") == (
            "/house/results/preview"
        )
        assert url_for(
            "us_results.us_results_confirm", chamber="house", token="abc"
        ) == "/house/results/confirm/abc"
        assert url_for("us_results.us_results_export", chamber="senate") == (
            "/senate/results/export"
        )
        assert url_for("us_results.us_results_current_senate") == (
            "/senate/results/current-senate"
        )
        assert url_for(
            "us.set_race_matchup", chamber="house", map_id=3, seat_id=4
        ) == "/house/matchups/3/4"
        with pytest.raises(BuildError):
            url_for("us_poll_import.import_form")
        with pytest.raises(BuildError):
            url_for("us.set_race_matchup", map_id=3, seat_id=4)
        # The results pages cover the two chambers with seat results only.
        with pytest.raises(ValueError):
            url_for("us_results.us_results_form", chamber="president")
        with pytest.raises(ValueError):
            url_for("us.set_race_matchup", chamber="president", map_id=3, seat_id=4)


_OLD_US_PATHS = (
    "/us/import",
    "/us/import/start",
    "/us/import/abc123",
    "/us/import/abc123/confirm",
    "/us/import/abc123/skip",
    "/us/import/abc123/approve-race",
    "/us/import/abc123/finish",
    "/us/results",
    "/us/results/preview",
    "/us/results/confirm/abc123",
    "/us/results/export",
    "/us/results/current-senate",
    "/us/run-models",
    "/us/matchups",
    "/us/matchups/1/2",
)


def test_no_old_us_rule_is_registered(app: Flask) -> None:  # noqa: F811
    rules = [str(rule) for rule in app.url_map.iter_rules()]

    assert [rule for rule in rules if rule.startswith("/us/")] == []


@pytest.mark.parametrize("path", _OLD_US_PATHS)
def test_the_old_us_urls_no_longer_exist(
    app: Flask,  # noqa: F811
    path: str,
) -> None:
    client = app.test_client()

    assert client.get(path).status_code == 404
    assert client.post(path).status_code == 404


def test_the_president_has_none_of_the_chamber_only_pages(
    app: Flask,  # noqa: F811
) -> None:
    client = app.test_client()

    for path in ("/president/results", "/president/results/export"):
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 404
    assert client.post("/president/matchups/1/2").status_code == 404
    assert client.get("/house/results/current-senate").status_code == 404
    assert client.post("/house/results/current-senate").status_code == 404


FORM = re.compile(r"<form\b([^>]*)>", re.IGNORECASE)
ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')


def _post_actions(body: str) -> list[str]:
    actions = []
    for tag in FORM.findall(body):
        attributes = {k.lower(): html.unescape(v) for k, v in ATTRIBUTE.findall(tag)}
        if attributes.get("method", "").lower() == "post":
            actions.append(attributes["action"])
    return actions


def _endpoint(adapter: MapAdapter, target: str, method: str) -> str | None:
    """The endpoint a site-local link resolves to, or None if it does not."""
    url = urlparse(target)
    if url.scheme or url.netloc or not url.path.startswith("/"):
        return None
    try:
        endpoint, _ = adapter.match(url.path, method=method)
    except HTTPException:
        return None
    return str(endpoint)


def test_every_post_form_on_a_crawled_page_posts_to_a_rule_that_allows_it(
    seeded_app: Flask,  # noqa: F811
) -> None:
    pages = crawl(seeded_app.test_client())
    adapter = seeded_app.url_map.bind("localhost")
    actions = [(page, a) for page, body in pages.items() for a in _post_actions(body)]

    assert len(actions) > 10
    unroutable = [
        (page, action)
        for page, action in actions
        if _endpoint(adapter, action, "POST") is None
    ]
    assert unroutable == []
