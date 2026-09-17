"""Unit tests for the US geography helpers.

Everything here is pure string work — no network, no database. The live seat
names the helpers must agree with (435 on the House map, 56 on the Presidential
map) were checked against ``/home/philiph/dbs/elections.db`` read-only when this
was written; the expected totals are hard-coded below so the tests never open
the real database.
"""

from __future__ import annotations

import re

import pytest

from polls.importers.us.us_geography import (
    AT_LARGE_STATES,
    HOUSE_DISTRICT_COUNTS,
    PRESIDENT_DISTRICT_STATES,
    STATE_POSTAL,
    house_seat_name,
    parent_seat_name,
    president_seat_for_heading,
    state_from_page_slug,
)

HOUSE_SEAT_COUNT = 435
PRESIDENT_SEAT_COUNT = 56

_HOUSE_SEAT_RE = re.compile(r"^[A-Z]{2}-\d{2}$")

_WIKI = "https://en.wikipedia.org/wiki/"


def _all_house_seat_names() -> set[str]:
    """Every seat name the House map holds, generated from the module's tables."""
    names: set[str] = set()
    for state, count in HOUSE_DISTRICT_COUNTS.items():
        for district in range(1, count + 1):
            name = house_seat_name(state, district)
            assert name is not None
            names.add(name)
    return names


def _all_president_seat_names() -> set[str]:
    """Every seat name the Presidential map holds: states, DC and the CD seats."""
    names = set(STATE_POSTAL)
    for state, count in PRESIDENT_DISTRICT_STATES.items():
        names.update(f"{state} CD-{number}" for number in range(1, count + 1))
    return names


class TestTables:
    def test_states_plus_dc(self) -> None:
        assert len(STATE_POSTAL) == 51
        assert STATE_POSTAL["District of Columbia"] == "DC"
        assert STATE_POSTAL["Pennsylvania"] == "PA"

    def test_postal_codes_are_unique_two_letter_uppercase(self) -> None:
        assert len(set(STATE_POSTAL.values())) == 51
        assert all(re.fullmatch(r"[A-Z]{2}", code) for code in STATE_POSTAL.values())

    def test_at_large_states(self) -> None:
        assert AT_LARGE_STATES == {
            "Alaska",
            "Delaware",
            "North Dakota",
            "South Dakota",
            "Vermont",
            "Wyoming",
        }
        assert AT_LARGE_STATES <= set(STATE_POSTAL)

    def test_at_large_states_are_exactly_the_single_district_states(self) -> None:
        single = {state for state, count in HOUSE_DISTRICT_COUNTS.items() if count == 1}
        assert single == set(AT_LARGE_STATES)

    def test_house_district_counts_cover_the_50_states_only(self) -> None:
        states = set(STATE_POSTAL) - {"District of Columbia"}
        assert set(HOUSE_DISTRICT_COUNTS) == states
        assert sum(HOUSE_DISTRICT_COUNTS.values()) == HOUSE_SEAT_COUNT


class TestStateFromPageSlug:
    @pytest.mark.parametrize(
        ("slug", "expected"),
        [
            ("2026_United_States_Senate_election_in_Texas", "Texas"),
            ("2026_United_States_Senate_special_election_in_Florida", "Florida"),
            ("2026_United_States_Senate_special_election_in_Ohio", "Ohio"),
            (
                "2026_United_States_House_of_Representatives_elections_in_Pennsylvania",
                "Pennsylvania",
            ),
            (
                "2026_United_States_House_of_Representatives_election_in_Alaska",
                "Alaska",
            ),
            ("2026_United_States_Senate_election_in_New_Hampshire", "New Hampshire"),
            # A leading "the " on the state part is dropped.
            (
                "2026_United_States_House_of_Representatives_election_in_the_"
                "District_of_Columbia",
                "District of Columbia",
            ),
            # Full URLs, fragments and query strings.
            (f"{_WIKI}2026_United_States_Senate_election_in_Georgia", "Georgia"),
            (
                f"{_WIKI}2026_United_States_Senate_election_in_Georgia#Polling",
                "Georgia",
            ),
            (f"{_WIKI}2026_United_States_Senate_election_in_Maine?action=raw", "Maine"),
            (f"{_WIKI}2026_United_States_Senate_election_in_Iowa/", "Iowa"),
            # Percent-encoded spaces and underscores.
            ("2026_United_States_Senate_election_in_New%20Mexico", "New Mexico"),
            (
                "2026_United_States_Senate_election_in_South%5FCarolina",
                "South Carolina",
            ),
            # A bare state name is accepted too.
            ("Texas", "Texas"),
            ("north_carolina", "North Carolina"),
        ],
    )
    def test_resolves(self, slug: str, expected: str) -> None:
        assert state_from_page_slug(slug) == expected

    @pytest.mark.parametrize(
        "slug",
        [
            "2026_United_States_House_of_Representatives_election_in_Guam",
            "2026_American_Samoa_House_of_Representatives_election_in_American_Samoa",
            "2026_United_States_House_of_Representatives_election_in_Puerto_Rico",
            "2026_United_States_House_of_Representatives_election_in_the_Northern_"
            "Mariana_Islands",
            "2026_United_States_House_of_Representatives_election_in_the_United_"
            "States_Virgin_Islands",
        ],
    )
    def test_territories_are_none(self, slug: str) -> None:
        assert state_from_page_slug(slug) is None

    @pytest.mark.parametrize(
        "slug",
        [
            "2026_United_States_Senate_elections",
            "2026_United_States_House_of_Representatives_elections",
            "Opinion_polling_for_the_2026_United_States_House_of_Representatives_"
            "elections",
            "2026_United_States_Senate_election_in_Narnia",
            "",
            "   ",
        ],
    )
    def test_unrecognised_is_none(self, slug: str) -> None:
        assert state_from_page_slug(slug) is None


class TestHouseSeatName:
    @pytest.mark.parametrize(
        ("state", "district", "expected"),
        [
            ("Pennsylvania", 7, "PA-07"),
            ("Alabama", 1, "AL-01"),
            ("California", 52, "CA-52"),
            ("Texas", 38, "TX-38"),
            ("New York", 26, "NY-26"),
            ("Maine", 2, "ME-02"),
            ("Nebraska", 3, "NE-03"),
            # Case-insensitive on the state name.
            ("pennsylvania", 7, "PA-07"),
        ],
    )
    def test_numbered_districts(
        self, state: str, district: int, expected: str
    ) -> None:
        assert house_seat_name(state, district) == expected

    @pytest.mark.parametrize("state", sorted(AT_LARGE_STATES))
    def test_at_large_states_are_always_seat_01(self, state: str) -> None:
        postal = STATE_POSTAL[state]
        assert house_seat_name(state, None) == f"{postal}-01"
        assert house_seat_name(state, 1) == f"{postal}-01"
        # A stray district number on an at-large state still gives seat 01.
        assert house_seat_name(state, 3) == f"{postal}-01"

    def test_missing_district_on_a_multi_district_state_is_seat_01(self) -> None:
        assert house_seat_name("Pennsylvania", None) == "PA-01"

    @pytest.mark.parametrize(
        ("state", "district"),
        [
            # DC has a delegate, not a seat on the House map.
            ("District of Columbia", 1),
            ("District of Columbia", None),
            ("Guam", 1),
            ("Narnia", 1),
            ("", 1),
            # Out of the state's range.
            ("Pennsylvania", 18),
            ("Pennsylvania", 0),
            ("Pennsylvania", -1),
            ("Maine", 3),
        ],
    )
    def test_none_cases(self, state: str, district: int | None) -> None:
        assert house_seat_name(state, district) is None

    def test_generated_set_is_the_435_live_seat_names(self) -> None:
        names = _all_house_seat_names()
        assert len(names) == HOUSE_SEAT_COUNT
        assert all(_HOUSE_SEAT_RE.fullmatch(name) for name in names)
        # Spot checks against names read from the live House map.
        assert {"AK-01", "AL-01", "PA-07", "CA-52", "TX-38", "WY-01", "ME-02"} <= names
        assert "DC-01" not in names

    def test_every_returnable_name_is_a_live_seat(self) -> None:
        names = _all_house_seat_names()
        districts: list[int | None] = [None, -1, 0, 1, 2, 3, 52, 53, 99]
        for state in list(STATE_POSTAL) + ["Guam", "Narnia"]:
            for district in districts:
                seat = house_seat_name(state, district)
                assert seat is None or seat in names


class TestPresidentSeatForHeading:
    @pytest.mark.parametrize(
        ("heading", "expected"),
        [
            ("Pennsylvania", "Pennsylvania"),
            ("New Hampshire", "New Hampshire"),
            ("Nevada", "Nevada"),
            # The state, not the district — this is the ambiguity to get right.
            ("Washington", "Washington"),
            ("Washington, D.C.", "District of Columbia"),
            ("Washington D.C.", "District of Columbia"),
            ("Washington, DC", "District of Columbia"),
            ("Washington DC", "District of Columbia"),
            ("D.C.", "District of Columbia"),
            ("District of Columbia", "District of Columbia"),
            ("Maine's 2nd congressional district", "Maine CD-2"),
            ("Maine’s 1st congressional district", "Maine CD-1"),
            ("Maine CD-2", "Maine CD-2"),
            ("Maine CD 2", "Maine CD-2"),
            ("Nebraska (CD-3)", "Nebraska CD-3"),
            ("Nebraska's 2nd district", "Nebraska CD-2"),
            ("Nebraska CD-1", "Nebraska CD-1"),
            # Footnote markers and stray whitespace are stripped.
            ("Nevada[a]", "Nevada"),
            ("  North   Carolina ", "North Carolina"),
            ("nebraska cd-3", "Nebraska CD-3"),
        ],
    )
    def test_resolves(self, heading: str, expected: str) -> None:
        assert president_seat_for_heading(heading) == expected

    @pytest.mark.parametrize(
        "heading",
        [
            "Nationwide",
            "Statewide",
            "General election",
            "Opinion polling",
            "JD Vance vs. Gavin Newsom",
            "Puerto Rico",
            "Guam",
            # States that do not split their electoral votes have no CD seats.
            "Pennsylvania's 7th congressional district",
            "Pennsylvania CD-7",
            # Districts outside Maine's two and Nebraska's three.
            "Maine's 3rd congressional district",
            "Nebraska (CD-4)",
            "Maine CD-0",
            "",
            "   ",
        ],
    )
    def test_none_cases(self, heading: str) -> None:
        assert president_seat_for_heading(heading) is None

    def test_generated_set_is_the_56_live_seat_names(self) -> None:
        names = _all_president_seat_names()
        assert len(names) == PRESIDENT_SEAT_COUNT
        assert {
            "District of Columbia",
            "Maine CD-1",
            "Maine CD-2",
            "Nebraska CD-1",
            "Nebraska CD-2",
            "Nebraska CD-3",
        } <= names

    def test_every_live_seat_name_round_trips(self) -> None:
        for name in _all_president_seat_names():
            assert president_seat_for_heading(name) == name

    def test_every_returnable_name_is_a_live_seat(self) -> None:
        names = _all_president_seat_names()
        headings = [
            "Nationwide",
            "Washington, D.C.",
            "Washington",
            "Maine's 2nd congressional district",
            "Nebraska (CD-3)",
            "Pennsylvania CD-7",
            "Guam",
            *_all_president_seat_names(),
        ]
        for heading in headings:
            seat = president_seat_for_heading(heading)
            assert seat is None or seat in names


class TestParentSeatName:
    @pytest.mark.parametrize(
        ("seat", "expected"),
        [
            ("Maine CD-2", "Maine"),
            ("Maine CD-1", "Maine"),
            ("Nebraska CD-3", "Nebraska"),
        ],
    )
    def test_district_seats(self, seat: str, expected: str) -> None:
        assert parent_seat_name(seat) == expected

    @pytest.mark.parametrize(
        "seat",
        [
            "Maine",
            "Nebraska",
            "Washington",
            "District of Columbia",
            "PA-07",
            "Florida CD-1",
            "Narnia CD-1",
            "",
        ],
    )
    def test_none_cases(self, seat: str) -> None:
        assert parent_seat_name(seat) is None

    def test_every_district_seat_has_its_state_as_parent(self) -> None:
        for name in _all_president_seat_names():
            parent = parent_seat_name(name)
            if " CD-" in name:
                assert parent == name.split(" CD-")[0]
            else:
                assert parent is None
