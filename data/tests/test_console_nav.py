"""Tests for console navigation, the section landing pages and the Admin page.

The crawl is a test and not a script because ``tests/conftest.py`` keeps
``Database()`` off the live file; it only ever issues GET requests, and it never
follows a redirect.
"""

from __future__ import annotations

import pytest
from flask import Flask

from console.nav import (
    ADMIN,
    ALL_POLLS,
    UNSECTIONED_BLUEPRINTS,
    UNSECTIONED_ENDPOINTS,
    Nav,
    NavItem,
    build_nav,
    current_area,
    section_links,
)
from console.sections import SECTIONS, SECTIONS_BY_SLUG, Section
from tests.console_fixtures import (  # noqa: F401  (pytest fixtures)
    app,
    crawl,
    seeded_app,
)


def test_the_crawl_from_home_finds_no_error_pages(seeded_app: Flask) -> None:
    pages = crawl(seeded_app.test_client())

    assert len(pages) > 20
    # A dropped link must not silently shrink the crawl.
    for expected in (
        "/westminster",
        "/holyrood",
        "/house",
        "/senate",
        "/president",
        "/admin",
        "/polls",
        "/house/matchups",
        "/senate/matchups",
        "/westminster/uncertainty/results",
    ):
        assert expected in pages, expected


def test_every_parameterless_get_page_is_reachable_from_home(
    seeded_app: Flask,
) -> None:
    reached = {key.partition("?")[0] for key in crawl(seeded_app.test_client())}
    parameterless = {
        rule.rule
        for rule in seeded_app.url_map.iter_rules()
        if "GET" in (rule.methods or set())
        and not rule.arguments
        and not rule.rule.startswith("/static")
    }

    assert sorted(parameterless - reached) == []


def test_every_page_has_navigation(seeded_app: Flask) -> None:
    for key, body in crawl(seeded_app.test_client()).items():
        assert 'class="topnav"' in body, key
        for section in SECTIONS:
            assert f'href="/{section.slug}"' in body, key
        assert 'href="/admin"' in body, key


def test_home_is_links_only(app: Flask) -> None:  # noqa: F811
    body = app.test_client().get("/").get_data(as_text=True)

    assert "<form" not in body
    assert 'method="post"' not in body.lower()
    for section in SECTIONS:
        assert f'href="/{section.slug}"' in body


def test_the_admin_page_holds_the_site_and_database_forms(
    app: Flask,  # noqa: F811
) -> None:
    body = app.test_client().get("/admin").get_data(as_text=True)

    for action in (
        "/site/rebuild",
        "/site/rebuild-database",
        "/db/backup",
        "/db/restore",
    ):
        assert f'action="{action}"' in body


def test_the_holyrood_landing_page_holds_the_one_click_import(
    app: Flask,  # noqa: F811
) -> None:
    body = app.test_client().get("/holyrood").get_data(as_text=True)

    assert 'action="/holyrood/polls/import"' in body
    assert "Import Scottish Polls" in body


def test_only_the_us_landing_pages_hold_the_run_form(app: Flask) -> None:  # noqa: F811
    client = app.test_client()
    for section in SECTIONS:
        body = client.get(f"/{section.slug}").get_data(as_text=True)
        has_form = 'action="/us/run-models"' in body
        assert has_form is (section.slug in {"house", "senate", "president"})


def test_every_nav_link_resolves_and_renders(seeded_app: Flask) -> None:
    client = seeded_app.test_client()
    with seeded_app.test_request_context():
        from console.nav import section_items

        urls = [
            item.url for section in SECTIONS for item in section_items(section)
        ]

    assert urls
    for url in urls:
        assert client.get(url).status_code == 200, url


def test_each_section_nav_is_built_from_its_capabilities() -> None:
    def labels(slug: str) -> list[str]:
        return [link.label for link in section_links(SECTIONS_BY_SLUG[slug])]

    assert labels("westminster") == [
        "Import poll",
        "By-elections",
        "Poll coverage",
        "Run model",
        "Outputs",
        "Uncertainty",
    ]
    assert labels("holyrood") == ["Run model", "Outputs"]
    assert labels("house") == ["Import polls", "Matchups", "Results", "Outputs"]
    assert labels("senate") == ["Import polls", "Matchups", "Results", "Outputs"]
    assert labels("president") == ["Import polls", "Matchup", "Outputs"]


def test_every_endpoint_belongs_to_an_area_or_is_listed_as_sectionless(
    app: Flask,  # noqa: F811
) -> None:
    for rule in app.url_map.iter_rules():
        blueprint = rule.endpoint.partition(".")[0]
        area = current_area(rule.endpoint, {"chamber": "house"})
        listed = (
            blueprint in {"home", "us_poll_import", "us_results", "static"}
            or rule.endpoint in {"us.run_us_models", "us.set_race_matchup"}
        )
        assert (area is None) is listed, rule.endpoint


def test_the_sectionless_lists_match_the_pinned_ones() -> None:
    assert UNSECTIONED_BLUEPRINTS == {"home", "us_poll_import", "us_results", "static"}
    assert UNSECTIONED_ENDPOINTS == {"us.run_us_models", "us.set_race_matchup"}


@pytest.mark.parametrize(
    ("endpoint", "view_args", "area"),
    [
        ("westminster.model_outputs", {}, "westminster"),
        ("uncertainty_runs.results", {}, "westminster"),
        ("polls.poll_coverage", {}, "westminster"),
        ("polls.poll_list", {}, ALL_POLLS),
        ("polls.poll_detail", {}, ALL_POLLS),
        ("holyrood.holyrood_outputs", {}, "holyrood"),
        ("us.senate_outputs", {}, "senate"),
        ("us.delete_selected_house_outputs", {}, "house"),
        ("us.president_matchup", {}, "president"),
        ("us.race_matchups", {"chamber": "senate"}, "senate"),
        ("us.race_matchups", {"chamber": "house"}, "house"),
        ("us.race_matchups", {"chamber": "House"}, None),
        ("us.race_matchups", {"chamber": "nope"}, None),
        ("us.race_matchups", {}, None),
        ("us.run_us_models", {}, None),
        ("db_admin.backup_database", {}, ADMIN),
        ("site.rebuild_database", {}, ADMIN),
        ("sections.house", {}, "house"),
        (None, {}, None),
    ],
)
def test_current_area(
    endpoint: str | None, view_args: dict[str, str], area: str | None
) -> None:
    assert current_area(endpoint, view_args) == area


def test_a_section_page_shows_its_nav_and_breadcrumbs(
    seeded_app: Flask,
) -> None:
    body = seeded_app.test_client().get("/westminster/outputs").get_data(as_text=True)

    assert 'class="subnav"' in body
    assert 'class="topnav"' in body
    crumbs = body[body.index('class="breadcrumbs"') :]
    crumbs = crumbs[: crumbs.index("</nav>")]
    assert 'href="/westminster">Westminster</a>' in crumbs
    assert '<span aria-current="page">Outputs</span>' in crumbs
    # The link for the page you are on is marked, and only that one.
    subnav = body[body.index('class="subnav"') :]
    subnav = subnav[: subnav.index("</nav>")]
    assert subnav.count('aria-current="page"') == 1
    assert 'class="active" aria-current="page">Outputs</a>' in subnav


def test_pages_outside_a_section_have_no_section_nav(
    seeded_app: Flask,
) -> None:
    client = seeded_app.test_client()
    for path in ("/polls", "/admin", "/us/import", "/"):
        assert 'class="subnav"' not in client.get(path).get_data(as_text=True), path


def _nav(app: Flask, path: str, method: str = "GET") -> Nav:  # noqa: F811
    with app.test_request_context(path, method=method):
        return build_nav()


def _split(spec: str) -> tuple[str, str]:
    """Split ``"POST /path"`` into ``(path, method)``; a bare path is a GET."""
    method, _, path = spec.rpartition(" ")
    return path, method or "GET"


def _active(items: tuple[NavItem, ...]) -> list[str]:
    return [item.label for item in items if item.active]


@pytest.mark.parametrize(
    ("path", "crumbs"),
    [
        ("/", []),
        ("/us/import", [("Home", "/")]),
        ("/admin", [("Home", "/"), ("Admin", None)]),
        ("/polls", [("Home", "/"), ("All polls", None)]),
        ("/holyrood", [("Home", "/"), ("Holyrood", None)]),
        (
            "/westminster/outputs",
            [("Home", "/"), ("Westminster", "/westminster"), ("Outputs", None)],
        ),
        (
            "/president/outputs/3",
            [("Home", "/"), ("US President", "/president"), ("Outputs", None)],
        ),
        (
            "/senate/matchups",
            [("Home", "/"), ("US Senate", "/senate"), ("Matchups", None)],
        ),
    ],
)
def test_breadcrumbs(
    app: Flask,  # noqa: F811
    path: str,
    crumbs: list[tuple[str, str | None]],
) -> None:
    got = [(crumb.label, crumb.url) for crumb in _nav(app, path).breadcrumbs]

    assert got == crumbs


@pytest.mark.parametrize(
    ("path", "top"),
    [
        ("/", []),
        ("/us/import", []),
        ("/holyrood", ["Holyrood"]),
        ("/admin", ["Admin"]),
        ("POST /db/backup", ["Admin"]),
        ("/polls", ["All polls"]),
        ("/senate/matchups", ["US Senate"]),
        ("/president/matchup", ["US President"]),
        ("/westminster/uncertainty/sims", ["Westminster"]),
    ],
)
def test_the_top_bar_marks_the_current_area(
    app: Flask,  # noqa: F811
    path: str,
    top: list[str],
) -> None:
    assert _active(_nav(app, *_split(path)).top) == top


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("/westminster", None),
        ("/westminster/polls/import", "Import poll"),
        ("/westminster/by-elections", "By-elections"),
        ("/westminster/polls/coverage", "Poll coverage"),
        ("/westminster/model", "Run model"),
        ("/westminster/outputs/4", "Outputs"),
        ("POST /westminster/outputs/4/delete", "Outputs"),
        ("/westminster/uncertainty", "Uncertainty"),
        ("/westminster/uncertainty/results/2", "Uncertainty"),
        ("/westminster/uncertainty/sims/3/seat/9", "Uncertainty"),
        ("/holyrood/model", "Run model"),
        ("/holyrood/outputs/7", "Outputs"),
        ("/house/matchups", "Matchups"),
        ("/president/matchup", "Matchup"),
        ("/house/outputs", "Outputs"),
        ("/senate/outputs/5", "Outputs"),
        ("POST /president/outputs/delete-selected", "Outputs"),
        ("POST /house/outputs/5/delete", "Outputs"),
    ],
)
def test_the_section_nav_marks_the_current_link(
    app: Flask,  # noqa: F811
    path: str,
    label: str | None,
) -> None:
    items = _nav(app, *_split(path)).section_items

    assert items
    assert _active(items) == ([label] if label else [])


def test_the_matchups_link_belongs_to_its_own_chamber(
    app: Flask,  # noqa: F811
) -> None:
    house = _nav(app, "/house/matchups").section_items
    senate = _nav(app, "/senate/matchups").section_items

    assert "/house/matchups" in [item.url for item in house]
    assert "/senate/matchups" in [item.url for item in senate]


def test_a_request_that_matches_no_route_still_gets_navigation(
    app: Flask,  # noqa: F811
) -> None:
    response = app.test_client().get("/no-such-page")

    assert response.status_code == 404
    nav = _nav(app, "/no-such-page")
    assert nav.section is None and nav.section_items == ()
    assert nav.breadcrumbs[0].label == "Home"


def test_home_cards_link_to_each_sections_pages(app: Flask) -> None:  # noqa: F811
    body = app.test_client().get("/").get_data(as_text=True)
    cards = body[body.index("console-columns") :]

    for href in (
        "/westminster",
        "/westminster/outputs",
        "/westminster/polls/import",
        "/us/import",
        "/senate/matchups",
        "/president/matchup",
        "/holyrood/outputs",
        "/polls",
        "/admin",
    ):
        assert f'href="{href}"' in cards, href
    assert 'class="button primary button-block" href="/us/import"' in cards


@pytest.mark.parametrize("section", SECTIONS, ids=lambda section: section.slug)
def test_a_sections_label_is_used_everywhere_it_is_named(
    app: Flask,  # noqa: F811
    section: Section,
) -> None:
    client = app.test_client()
    landing = client.get(f"/{section.slug}").get_data(as_text=True)
    home = client.get("/").get_data(as_text=True)

    assert f"<h1>{section.label}</h1>" in landing
    assert f">{section.label}</a>" in landing  # the top bar entry
    assert f">{section.label}</a></h2>" in home  # the home card heading
    assert f'aria-label="{section.label}"' in landing  # the section nav
