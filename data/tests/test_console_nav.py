"""Tests for console navigation, the section landing pages and the Admin page.

The crawl is a test and not a script because ``tests/conftest.py`` keeps
``Database()`` off the live file; it only ever issues GET requests, and it never
follows a redirect.
"""

from __future__ import annotations

import re
import struct
from xml.etree import ElementTree

import pytest
from flask import Flask

from console.nav import (
    ADMIN,
    ALL_POLLS,
    EXPORT,
    RUNS,
    UNSECTIONED_BLUEPRINTS,
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
        "/export",
        "/runs",
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
        assert 'href="/export"' in body, key
        assert 'href="/runs"' in body, key


def test_home_is_links_only(app: Flask) -> None:  # noqa: F811
    body = app.test_client().get("/").get_data(as_text=True)

    assert "<form" not in body
    assert 'method="post"' not in body.lower()
    for section in SECTIONS:
        assert f'href="/{section.slug}"' in body


def test_the_admin_page_holds_the_database_forms_and_no_site_export(
    app: Flask,  # noqa: F811
) -> None:
    body = app.test_client().get("/admin").get_data(as_text=True)

    for action in ("/site/rebuild-database", "/db/backup", "/db/restore"):
        assert f'action="{action}"' in body
    assert 'action="/site/rebuild"' not in body
    assert "Rebuild Site Data" not in body


def test_the_holyrood_landing_page_holds_the_one_click_import(
    app: Flask,  # noqa: F811
) -> None:
    body = app.test_client().get("/holyrood").get_data(as_text=True)

    assert 'action="/holyrood/polls/import"' in body
    assert "Import Scottish Polls" in body


def test_every_landing_page_links_to_its_own_model_page(
    app: Flask,  # noqa: F811
) -> None:
    client = app.test_client()
    for section in SECTIONS:
        body = client.get(f"/{section.slug}").get_data(as_text=True)
        assert f'href="/{section.slug}/model">Run model</a>' in body
        assert "run-models" not in body


def test_every_nav_link_resolves_and_renders(seeded_app: Flask) -> None:
    client = seeded_app.test_client()
    with seeded_app.test_request_context():
        from console.nav import section_items

        urls = [item.url for section in SECTIONS for item in section_items(section)]

    assert urls
    for url in urls:
        assert client.get(url).status_code == 200, url


def test_each_section_nav_is_built_from_its_capabilities() -> None:
    def labels(slug: str) -> list[str]:
        return [link.label for link in section_links(SECTIONS_BY_SLUG[slug])]

    assert labels("westminster") == [
        "Polls",
        "Import poll",
        "By-elections",
        "Poll coverage",
        "Run model",
        "Outputs",
        "Uncertainty",
    ]
    assert labels("holyrood") == ["Polls", "Run model", "Outputs"]
    assert labels("house") == [
        "Polls",
        "Auto import",
        "Matchups",
        "Results",
        "Run model",
        "Outputs",
    ]
    assert labels("senate") == [
        "Polls",
        "Auto import",
        "Matchups",
        "Results",
        "Run model",
        "Outputs",
    ]
    assert labels("president") == [
        "Polls",
        "Auto import",
        "Matchup",
        "Run model",
        "Outputs",
    ]


def test_every_endpoint_belongs_to_an_area_or_is_listed_as_sectionless(
    app: Flask,  # noqa: F811
) -> None:
    for rule in app.url_map.iter_rules():
        blueprint = rule.endpoint.partition(".")[0]
        area = current_area(rule.endpoint, {"chamber": "house", "section": "house"})
        listed = blueprint in {"home", "static"}
        assert (area is None) is listed, rule.endpoint


def test_the_sectionless_list_matches_the_pinned_one() -> None:
    assert UNSECTIONED_BLUEPRINTS == {"home", "static"}


@pytest.mark.parametrize(
    ("endpoint", "view_args", "area"),
    [
        ("westminster.model_outputs", {}, "westminster"),
        ("uncertainty_runs.results", {}, "westminster"),
        ("polls.poll_coverage", {}, "westminster"),
        ("polls.poll_list", {}, ALL_POLLS),
        ("polls.poll_detail", {}, ALL_POLLS),
        ("polls.section_poll_list", {"section": "westminster"}, "westminster"),
        ("polls.section_poll_list", {"section": "holyrood"}, "holyrood"),
        ("polls.section_poll_list", {"section": "president"}, "president"),
        ("polls.section_poll_list", {"section": "nope"}, None),
        ("polls.section_poll_list", {}, None),
        ("holyrood.holyrood_outputs", {}, "holyrood"),
        ("us.senate_outputs", {}, "senate"),
        ("us.delete_selected_house_outputs", {}, "house"),
        ("us.president_matchup", {}, "president"),
        ("us.race_matchups", {"chamber": "senate"}, "senate"),
        ("us.race_matchups", {"chamber": "house"}, "house"),
        ("us.race_matchups", {"chamber": "House"}, None),
        ("us.race_matchups", {"chamber": "nope"}, None),
        ("us.race_matchups", {}, None),
        ("us.model_run_form", {"chamber": "house"}, "house"),
        ("us.model_run_form", {"chamber": "senate"}, "senate"),
        ("us.model_run", {"chamber": "president"}, "president"),
        ("us.model_run_form", {"chamber": "President"}, None),
        ("us.model_run_form", {"chamber": "nope"}, None),
        ("us.model_run", {}, None),
        ("us_poll_import.import_form", {"chamber": "house"}, "house"),
        ("us_poll_import.queue", {"chamber": "senate"}, "senate"),
        ("us_poll_import.finish", {"chamber": "president"}, "president"),
        ("us_poll_import.import_form", {"chamber": "nope"}, None),
        ("us_poll_import.import_form", {}, None),
        ("us_results.us_results_form", {"chamber": "house"}, "house"),
        ("us_results.us_results_confirm", {"chamber": "senate"}, "senate"),
        ("us_results.us_results_current_senate", {"chamber": "senate"}, "senate"),
        ("us_results.us_results_form", {"chamber": "president"}, None),
        ("us_results.us_results_form", {}, None),
        ("us.set_race_matchup", {"chamber": "house"}, "house"),
        ("us.set_race_matchup", {"chamber": "senate"}, "senate"),
        ("us.set_race_matchup", {"chamber": "president"}, None),
        ("us.set_race_matchup", {}, None),
        ("db_admin.backup_database", {}, ADMIN),
        ("site.rebuild_database", {}, ADMIN),
        ("export.export_form", {}, EXPORT),
        ("export.export_run", {}, EXPORT),
        ("runs.run_list", {}, RUNS),
        ("runs.run_detail", {}, RUNS),
        ("runs.run_status", {}, RUNS),
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
    for path in ("/polls", "/admin", "/export", "/runs", "/"):
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
        (
            "/house/polls/import",
            [("Home", "/"), ("US House", "/house"), ("Auto import", None)],
        ),
        (
            "/senate/results",
            [("Home", "/"), ("US Senate", "/senate"), ("Results", None)],
        ),
        ("/admin", [("Home", "/"), ("Admin", None)]),
        ("/export", [("Home", "/"), ("Export", None)]),
        ("/runs", [("Home", "/"), ("Runs", None)]),
        (
            "/runs/20261110T120000000000Z-1a2b3c4d",
            [("Home", "/"), ("Runs", "/runs"), ("Run", None)],
        ),
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
        ("/house/polls/import", ["US House"]),
        ("/president/polls/import", ["US President"]),
        ("/senate/results", ["US Senate"]),
        ("POST /senate/results/current-senate", ["US Senate"]),
        ("POST /house/matchups/1/2", ["US House"]),
        ("/holyrood", ["Holyrood"]),
        ("/admin", ["Admin"]),
        ("/export", ["Export"]),
        ("POST /export", ["Export"]),
        ("/runs", ["Runs"]),
        ("/runs/20261110T120000000000Z-1a2b3c4d", ["Runs"]),
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
        ("/westminster/polls", "Polls"),
        ("/holyrood/polls", "Polls"),
        ("/house/polls", "Polls"),
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
        ("/house/polls/import", "Auto import"),
        ("/senate/polls/import/abc/finish", "Auto import"),
        ("POST /president/polls/import/start", "Auto import"),
        ("/house/results", "Results"),
        ("POST /senate/results/preview", "Results"),
        ("POST /senate/results/current-senate", "Results"),
        ("POST /senate/matchups/1/2", "Matchups"),
        ("/president/matchup", "Matchup"),
        ("/house/model", "Run model"),
        ("/senate/model", "Run model"),
        ("POST /president/model", "Run model"),
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
        "/house/polls/import",
        "/senate/polls/import",
        "/president/polls/import",
        "/senate/matchups",
        "/president/matchup",
        "/holyrood/outputs",
        "/polls",
        "/admin",
    ):
        assert f'href="{href}"' in cards, href
    assert 'class="button primary button-block" href="/house/polls/import"' in cards


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


# --- favicon ----------------------------------------------------------------------

# The raster icon comes first and the SVG last: browsers that take the last suitable
# icon then pick the SVG, and those that cannot read it use the PNG.
ICON_LINKS = (
    ("/static/favicon.png", "image/png"),
    ("/static/favicon.svg", "image/svg+xml"),
)


PNG_ICON, SVG_ICON = (url for url, _ in ICON_LINKS)


def test_every_crawled_page_links_both_icons(seeded_app: Flask) -> None:
    pages = crawl(seeded_app.test_client())

    assert len(pages) > 20
    for key, body in pages.items():
        icons = re.findall(r'<link rel="icon" href="([^"]+)" type="([^"]+)"', body)
        assert icons == list(ICON_LINKS), key
    assert 'sizes="32x32"' in next(iter(pages.values()))


@pytest.mark.parametrize(("url", "content_type"), ICON_LINKS)
def test_the_icons_are_served_with_an_image_type(
    app: Flask,  # noqa: F811
    url: str,
    content_type: str,
) -> None:
    response = app.test_client().get(url)

    assert response.status_code == 200
    assert response.mimetype == content_type
    assert len(response.data) > 100


def test_the_svg_icon_is_self_contained(app: Flask) -> None:  # noqa: F811
    root = ElementTree.fromstring(app.test_client().get(SVG_ICON).data)

    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox") == "0 0 32 32"
    tags = {element.tag.rpartition("}")[2] for element in root.iter()}
    assert tags <= {"svg", "rect", "g", "circle"}
    # Nothing may point at another file or host.
    for element in root.iter():
        for name, value in element.attrib.items():
            assert "href" not in name, name
            assert "url(" not in value and "//" not in value, (name, value)


def test_the_png_icon_is_a_32_by_32_png(app: Flask) -> None:  # noqa: F811
    data = app.test_client().get(PNG_ICON).data

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (32, 32)
