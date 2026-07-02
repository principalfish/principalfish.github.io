"""Tests for the per-page export partition — routing elections to their front-end page.

The full export writes one data directory per page (``electionmaps`` / ``uselectionmaps``),
partitioning elections by parliament via :func:`partition_elections_by_page` before assembling
each page. These tests pin the routing contract (disjoint parliament sets, matching page keys,
US↔uselectionmaps mapping) so a US election can never be written into the UK data dir, or an
election silently dropped. Pure function; no DB needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_elections import (
    PAGE_OUTPUT_ROOTS,
    PAGE_PARLIAMENTS,
    partition_elections_by_page,
)


class _Map:
    """Minimal stand-in for a Map ORM row exposing only ``parliament``."""

    def __init__(self, parliament: str) -> None:
        self.parliament = parliament


class _LegacyMap:
    """A Map with no ``parliament`` attribute (older Westminster rows predate the column)."""


class _Election:
    """Minimal stand-in for an Election ORM row (only ``.map`` is read by the router)."""

    def __init__(self, eid: str, map_obj: object) -> None:
        self.id = eid
        self.map = map_obj


class TestPageRoutingContract:
    """PAGE_PARLIAMENTS / PAGE_OUTPUT_ROOTS must stay a well-formed, unambiguous partition."""

    def test_page_keys_match_between_parliaments_and_roots(self) -> None:
        # A page in one dict but not the other would KeyError at export time.
        assert PAGE_PARLIAMENTS.keys() == PAGE_OUTPUT_ROOTS.keys()

    def test_parliament_sets_are_disjoint(self) -> None:
        # A parliament in two pages would be exported to both data dirs.
        seen: set[str] = set()
        for parliaments in PAGE_PARLIAMENTS.values():
            assert seen.isdisjoint(parliaments), "parliament assigned to more than one page"
            seen |= parliaments

    def test_known_parliaments_map_to_expected_page(self) -> None:
        assert PAGE_PARLIAMENTS["electionmaps"] == {"westminster", "holyrood"}
        assert PAGE_PARLIAMENTS["uselectionmaps"] == {"us_house", "us_senate", "us_presidential"}

    def test_output_roots_land_in_their_page_directory(self) -> None:
        # Pins that US data can never be written under electionmaps/ (and vice versa).
        assert PAGE_OUTPUT_ROOTS["electionmaps"].parts[-2:] == ("electionmaps", "data")
        assert PAGE_OUTPUT_ROOTS["uselectionmaps"].parts[-2:] == ("uselectionmaps", "data")


class TestPartitionElectionsByPage:
    """A mixed election list is split so each page gets only its own parliaments."""

    def test_splits_uk_and_us_elections(self) -> None:
        uk = _Election("2024-general", _Map("westminster"))
        holyrood = _Election("2021-holyrood", _Map("holyrood"))
        house = _Election("2024-us-house", _Map("us_house"))
        senate = _Election("2024-us-senate", _Map("us_senate"))
        by_page = partition_elections_by_page([uk, house, holyrood, senate])
        assert by_page["electionmaps"] == [uk, holyrood]
        assert by_page["uselectionmaps"] == [house, senate]

    def test_every_page_key_present_even_when_empty(self) -> None:
        # US-only input still yields an (empty) electionmaps bucket, so callers can index it.
        by_page = partition_elections_by_page([_Election("2024-us-house", _Map("us_house"))])
        assert set(by_page) == set(PAGE_PARLIAMENTS)
        assert by_page["electionmaps"] == []

    def test_legacy_map_without_parliament_defaults_to_westminster(self) -> None:
        legacy = _Election("2015-general", _LegacyMap())
        by_page = partition_elections_by_page([legacy])
        assert by_page["electionmaps"] == [legacy]

    def test_election_with_unrouted_parliament_is_dropped(self) -> None:
        # A parliament belonging to no page (e.g. one added to the DB but not yet to a page)
        # is silently excluded from every page rather than crashing the export.
        stray = _Election("2021-senedd", _Map("senedd"))
        by_page = partition_elections_by_page([stray])
        assert all(stray not in bucket for bucket in by_page.values())

    def test_empty_input_yields_empty_buckets(self) -> None:
        by_page = partition_elections_by_page([])
        assert by_page == {page: [] for page in PAGE_PARLIAMENTS}
