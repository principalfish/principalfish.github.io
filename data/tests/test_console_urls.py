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
