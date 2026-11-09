"""Console navigation: the top bar, each section's links and the breadcrumbs.

The navigation is data. :func:`section_links` builds a section's links from the
registry in :mod:`console.sections`, :func:`current_area` says which part of the
console an endpoint belongs to, and :func:`build_nav` turns both into what
``base.html`` renders. A page that is not in :data:`UNSECTIONED_BLUEPRINTS` or
:data:`UNSECTIONED_ENDPOINTS` and maps to no area is a bug, which
``tests/test_console_nav.py`` catches.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from flask import request, url_for

from console.sections import SECTIONS, SECTIONS_BY_SLUG, Capability, Section

ADMIN = "admin"
ALL_POLLS = "polls"

# Endpoints that name the section they belong to in the endpoint itself or in
# their blueprint. The US blueprints serve three sections at once, so their
# endpoints are placed by chamber below.
_BLUEPRINT_AREAS: Mapping[str, str] = {
    "westminster": "westminster",
    "poll_import": "westminster",
    "by_elections": "westminster",
    "uncertainty": "westminster",
    "uncertainty_runs": "westminster",
    "uncertainty_explorer": "westminster",
    "holyrood": "holyrood",
    "polls": ALL_POLLS,
    "admin": ADMIN,
    "site": ADMIN,
    "db_admin": ADMIN,
}

_ENDPOINT_AREAS: Mapping[str, str] = {
    "polls.poll_coverage": "westminster",
    **{f"sections.{section.slug}": section.slug for section in SECTIONS},
    **{
        f"us.{pattern.format(slug=slug)}": slug
        for slug in ("house", "senate", "president")
        for pattern in (
            "{slug}_outputs",
            "{slug}_output_detail",
            "delete_{slug}_output",
            "delete_selected_{slug}_outputs",
        )
    },
    "us.president_matchup": "president",
}

# Pages that belong to no one section: the US import queue and results pages
# serve House, Senate and President together, and the combined "Run US models"
# action has no chamber.
UNSECTIONED_BLUEPRINTS = frozenset({"home", "us_poll_import", "us_results", "static"})
UNSECTIONED_ENDPOINTS = frozenset({"us.run_us_models", "us.set_race_matchup"})

_RACE_CHAMBERS = frozenset({"house", "senate"})


@dataclass(frozen=True)
class NavLink:
    """One link in a section's navigation.

    Attributes:
        label: Text shown for the link.
        endpoint: Endpoint the link points at.
        values: Extra ``url_for`` arguments, as ``(name, value)`` pairs.
        matches: Endpoint prefixes whose pages count as being on this link.
        primary: Whether the link is styled as the section's main action.
    """

    label: str
    endpoint: str
    values: tuple[tuple[str, str], ...] = ()
    matches: tuple[str, ...] = ()
    primary: bool = False

    def is_current(self, endpoint: str | None) -> bool:
        """Whether ``endpoint`` is one of this link's pages."""
        prefixes = self.matches or (self.endpoint,)
        return endpoint is not None and endpoint.startswith(prefixes)


@dataclass(frozen=True)
class NavItem:
    """A link ready to render."""

    label: str
    url: str
    active: bool = False
    primary: bool = False


@dataclass(frozen=True)
class Crumb:
    """One breadcrumb; the last one has no URL."""

    label: str
    url: str | None = None


@dataclass(frozen=True)
class Nav:
    """Everything ``base.html`` needs to draw the navigation."""

    top: tuple[NavItem, ...]
    section: Section | None
    section_items: tuple[NavItem, ...]
    breadcrumbs: tuple[Crumb, ...]


def current_area(
    endpoint: str | None, args: Mapping[str, str] | None = None
) -> str | None:
    """The part of the console an endpoint belongs to.

    Args:
        endpoint: The endpoint, or None for a request that matched no route.
        args: The query string, which names the chamber on the race-matchup
            page.

    Returns:
        A section slug, ``"admin"``, ``"polls"``, or None for a page that
        belongs to no one section.
    """
    if not endpoint:
        return None
    if endpoint in _ENDPOINT_AREAS:
        return _ENDPOINT_AREAS[endpoint]
    if endpoint == "us.race_matchups":
        chamber = ((args or {}).get("chamber") or "").strip().lower()
        return chamber if chamber in _RACE_CHAMBERS else None
    if endpoint in UNSECTIONED_ENDPOINTS:
        return None
    return _BLUEPRINT_AREAS.get(endpoint.partition(".")[0])


def _chamber_outputs(slug: str) -> tuple[str, tuple[str, ...]]:
    return f"us.{slug}_outputs", (
        f"us.{slug}_output",
        f"us.delete_{slug}_output",
        f"us.delete_selected_{slug}_outputs",
    )


_OUTPUTS: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "westminster": (
        "westminster.model_outputs",
        ("westminster.model_output", "westminster.delete_"),
    ),
    "holyrood": (
        "holyrood.holyrood_outputs",
        ("holyrood.holyrood_output", "holyrood.delete_"),
    ),
    "house": _chamber_outputs("house"),
    "senate": _chamber_outputs("senate"),
    "president": _chamber_outputs("president"),
}

# The US sections have no model page; they run from the landing page form.
_MODEL_FORMS: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "westminster": ("westminster.model_run_form", ("westminster.model_run",)),
    "holyrood": (
        "holyrood.run_holyrood_model_form",
        ("holyrood.run_holyrood_model",),
    ),
}


def section_links(section: Section) -> tuple[NavLink, ...]:
    """A section's navigation links, in display order."""
    links: list[NavLink] = []
    if section.has(Capability.POLL_IMPORT):
        links.append(
            NavLink(
                "Import poll",
                "poll_import.import_poll_form",
                matches=("poll_import.",),
                primary=True,
            )
        )
    if section.has(Capability.QUEUE_IMPORT):
        links.append(
            NavLink(
                "Import polls",
                "us_poll_import.import_form",
                matches=("us_poll_import.",),
                primary=True,
            )
        )
    if section.has(Capability.BY_ELECTIONS):
        links.append(
            NavLink(
                "By-elections",
                "by_elections.by_election_form",
                matches=("by_elections.",),
            )
        )
    if section.has(Capability.POLL_COVERAGE):
        links.append(NavLink("Poll coverage", "polls.poll_coverage"))
    if section.has(Capability.MATCHUPS):
        if section.slug == "president":
            links.append(NavLink("Matchup", "us.president_matchup"))
        else:
            links.append(
                NavLink(
                    "Matchups",
                    "us.race_matchups",
                    values=(("chamber", section.slug),),
                    matches=("us.race_matchups",),
                )
            )
    if section.has(Capability.NYT_RESULTS):
        links.append(
            NavLink("Results", "us_results.us_results_form", matches=("us_results.",))
        )
    if section.slug in _MODEL_FORMS:
        endpoint, matches = _MODEL_FORMS[section.slug]
        links.append(NavLink("Run model", endpoint, matches=matches))
    endpoint, matches = _OUTPUTS[section.slug]
    links.append(NavLink("Outputs", endpoint, matches=matches))
    if section.has(Capability.UNCERTAINTY):
        links.append(
            NavLink(
                "Uncertainty",
                "uncertainty.uncertainty_hub",
                matches=(
                    "uncertainty.",
                    "uncertainty_runs.",
                    "uncertainty_explorer.",
                ),
            )
        )
    return tuple(links)


def _item(link: NavLink, current_endpoint: str | None = None) -> NavItem:
    return NavItem(
        label=link.label,
        url=url_for(link.endpoint, **dict(link.values)),
        active=link.is_current(current_endpoint),
        primary=link.primary,
    )


def section_items(
    section: Section, current_endpoint: str | None = None
) -> tuple[NavItem, ...]:
    """A section's links with their URLs, marking the one for ``current_endpoint``."""
    return tuple(_item(link, current_endpoint) for link in section_links(section))


def _top_items(area: str | None) -> tuple[NavItem, ...]:
    entries = (
        *(
            (section.label, f"sections.{section.slug}", section.slug)
            for section in SECTIONS
        ),
        ("All polls", "polls.poll_list", ALL_POLLS),
        ("Admin", "admin.admin_page", ADMIN),
    )
    return tuple(
        NavItem(label, url_for(endpoint), active=entry_area == area)
        for label, endpoint, entry_area in entries
    )


def _breadcrumbs(
    endpoint: str | None, area: str | None, items: tuple[NavItem, ...]
) -> tuple[Crumb, ...]:
    """Home, then the section (or Admin, or All polls), then the current page."""

    def crumb(label: str, page_endpoint: str) -> Crumb:
        # The page you are on is not a link.
        here = endpoint == page_endpoint
        return Crumb(label, None if here else url_for(page_endpoint))

    crumbs: list[Crumb] = []
    if endpoint != "home.home":
        crumbs.append(crumb("Home", "home.home"))
    section = SECTIONS_BY_SLUG.get(area) if area else None
    if section is not None:
        landing = f"sections.{section.slug}"
        crumbs.append(crumb(section.label, landing))
        current = next((item for item in items if item.active), None)
        if current is not None:
            crumbs.append(Crumb(current.label))
    elif area == ADMIN:
        crumbs.append(crumb("Admin", "admin.admin_page"))
    elif area == ALL_POLLS:
        crumbs.append(crumb("All polls", "polls.poll_list"))
    return tuple(crumbs)


def build_nav() -> Nav:
    """The navigation for the current request."""
    endpoint = request.endpoint
    area = current_area(endpoint, request.args)
    section = SECTIONS_BY_SLUG.get(area) if area else None
    items = section_items(section, endpoint) if section else ()
    return Nav(
        top=_top_items(area),
        section=section,
        section_items=items,
        breadcrumbs=_breadcrumbs(endpoint, area, items),
    )


def nav_context() -> dict[str, Nav]:
    """Template context processor: expose the navigation as ``nav``."""
    return {"nav": build_nav()}
