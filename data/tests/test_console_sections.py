"""Tests for the console's section registry."""

from __future__ import annotations

import pytest
from flask import Flask

from console.sections import (
    SECTIONS,
    SECTIONS_BY_SLUG,
    WESTMINSTER_MAP_NAME,
    Capability,
)
from console.us.models import US_CHAMBERS
from core.db import Database
from core.schema import ElectionType
from export.export_elections import PAGE_PARLIAMENTS
from tests.console_fixtures import app  # noqa: F401  (pytest fixture)
from tests.us_ev_fixtures import seed_allocations, set_model_target


def test_there_are_five_sections_in_navigation_order() -> None:
    assert [section.slug for section in SECTIONS] == [
        "westminster",
        "holyrood",
        "house",
        "senate",
        "president",
    ]


def test_every_parliament_is_a_value_maps_can_carry() -> None:
    """A section's ``parliament`` filters polls and outputs, so it must exist."""
    known = set().union(*PAGE_PARLIAMENTS.values())
    assert {section.parliament for section in SECTIONS} == known


def test_slugs_labels_and_parliaments_are_unique() -> None:
    for field in ("slug", "label", "parliament"):
        values = [getattr(section, field) for section in SECTIONS]
        assert len(values) == len(set(values)), field


def test_the_lookup_covers_every_section() -> None:
    assert SECTIONS_BY_SLUG == {section.slug: section for section in SECTIONS}


def test_each_us_chamber_is_a_section() -> None:
    """The US output routes look their section up by chamber slug."""
    for chamber in US_CHAMBERS:
        assert chamber.slug in SECTIONS_BY_SLUG


def test_only_westminster_has_uncertainty_pages() -> None:
    assert [
        section.slug for section in SECTIONS if section.has(Capability.UNCERTAINTY)
    ] == ["westminster"]


def test_section_specific_tools_stay_in_their_sections() -> None:
    def having(capability: Capability) -> list[str]:
        return [section.slug for section in SECTIONS if section.has(capability)]

    assert having(Capability.POLL_IMPORT) == ["westminster"]
    assert having(Capability.POLL_COVERAGE) == ["westminster"]
    assert having(Capability.QUEUE_IMPORT) == ["house", "senate", "president"]
    assert having(Capability.BY_ELECTIONS) == ["westminster"]
    assert having(Capability.MATCHUPS) == ["house", "senate", "president"]
    assert having(Capability.NYT_RESULTS) == ["house", "senate"]
    assert having(Capability.CURRENT_SENATE) == ["senate"]


def test_the_westminster_map_name_matches_the_database_map() -> None:
    assert WESTMINSTER_MAP_NAME == "UK Constituencies post 2022"


@pytest.mark.parametrize(
    ("section", "election_type", "path", "has_links"),
    [
        ("westminster", ElectionType.model_uns, "/models/outputs", True),
        ("holyrood", ElectionType.holyrood_uns, "/holyrood/outputs", False),
        ("house", ElectionType.us_house_model, "/us/house/outputs", False),
        ("senate", ElectionType.us_senate_model, "/us/senate/outputs", False),
        (
            "president",
            ElectionType.us_presidential_model,
            "/us/president/outputs",
            False,
        ),
    ],
)
def test_only_the_westminster_output_page_links_to_uncertainty(
    app: Flask,  # noqa: F811
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    election_type: ElectionType,
    path: str,
    has_links: bool,
) -> None:
    for module in (
        "console.westminster.model",
        "console.westminster.outputs",
        "console.holyrood.model",
        "console.holyrood.outputs",
        "console.us.views",
        "console.us.outputs",
    ):
        monkeypatch.setattr(f"{module}.get_db", lambda: db)
    parliament = SECTIONS_BY_SLUG[section].parliament
    election_map = db.add_map(f"{section} map", parliament=parliament)
    election = db.add_election(election_map.id, 2026, "A model run", election_type)
    if election_type == ElectionType.us_presidential_model:
        # The presidential detail page needs a target year and its electoral votes.
        seed_allocations(db, {"One": 3})
        set_model_target(db, election.id)

    response = app.test_client().get(f"{path}/{election.id}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert ("Uncertainty results" in body) is has_links
    assert ("Error budget" in body) is has_links


def test_labels_are_the_display_names() -> None:
    assert [section.label for section in SECTIONS] == [
        "Westminster",
        "Holyrood",
        "US House",
        "US Senate",
        "US President",
    ]
