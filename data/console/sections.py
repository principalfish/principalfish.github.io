"""The console's five sections and what each one offers.

A section is one parliament the console manages: Westminster, Holyrood, and the
three US contests. The registry is the single place that says which sections
exist, how each appears, which ``Map.parliament`` value its data carries and
which section-specific tools it has, so pages and filters read it instead of
testing endpoint names or repeating strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# The map every Westminster importer and model targets.
WESTMINSTER_MAP_NAME = "UK Constituencies post 2022"


class Capability(StrEnum):
    """A tool that only some sections have.

    ``POLL_IMPORT`` is the single-poll import form (Westminster only);
    ``QUEUE_IMPORT`` is the automatic import through the review queue (the US
    sections); ``POLL_COVERAGE`` is the poll-coverage report, which only covers
    Westminster.
    """

    UNCERTAINTY = "uncertainty"
    POLL_IMPORT = "poll_import"
    POLL_COVERAGE = "poll_coverage"
    QUEUE_IMPORT = "queue_import"
    BY_ELECTIONS = "by_elections"
    MATCHUPS = "matchups"
    NYT_RESULTS = "nyt_results"
    CURRENT_SENATE = "current_senate"


@dataclass(frozen=True)
class Section:
    """One console section.

    Attributes:
        slug: URL segment and registry key.
        label: Name shown in navigation and headings.
        parliament: The ``Map.parliament`` value that identifies this section's
            polls, model outputs and exported pages.
        capabilities: The section-specific tools it offers.
    """

    slug: str
    label: str
    parliament: str
    capabilities: frozenset[Capability] = frozenset()

    def has(self, capability: Capability) -> bool:
        """Whether this section offers ``capability``."""
        return capability in self.capabilities


SECTIONS: tuple[Section, ...] = (
    Section(
        slug="westminster",
        label="Westminster",
        parliament="westminster",
        capabilities=frozenset(
            {
                Capability.UNCERTAINTY,
                Capability.POLL_IMPORT,
                Capability.POLL_COVERAGE,
                Capability.BY_ELECTIONS,
            }
        ),
    ),
    Section(slug="holyrood", label="Holyrood", parliament="holyrood"),
    Section(
        slug="house",
        label="US House",
        parliament="us_house",
        capabilities=frozenset(
            {Capability.QUEUE_IMPORT, Capability.MATCHUPS, Capability.NYT_RESULTS}
        ),
    ),
    Section(
        slug="senate",
        label="US Senate",
        parliament="us_senate",
        capabilities=frozenset(
            {
                Capability.QUEUE_IMPORT,
                Capability.MATCHUPS,
                Capability.NYT_RESULTS,
                Capability.CURRENT_SENATE,
            }
        ),
    ),
    Section(
        slug="president",
        label="US President",
        parliament="us_presidential",
        capabilities=frozenset({Capability.QUEUE_IMPORT, Capability.MATCHUPS}),
    ),
)

SECTIONS_BY_SLUG: dict[str, Section] = {section.slug: section for section in SECTIONS}
