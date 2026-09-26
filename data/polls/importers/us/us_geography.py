"""State, district and seat-name helpers for the US poll importers.

Wikipedia names a race by page slug (``2026_United_States_Senate_election_in_Texas``)
or by section heading (``Maine's 2nd congressional district``). The forecast maps
name the same race by *seat*: ``PA-07`` on the US House map, and a plain state name
or ``Maine CD-2`` on the US Presidential map. This module is the one place that
translates between the two, so the contest layer never hand-rolls a state table.

Seat names produced here are the live ones: every value :func:`house_seat_name`
returns is a seat on the House map, and every value
:func:`president_seat_for_heading` returns is a seat on the Presidential map.
Anything unrecognised — a territory, a nationwide heading, a district that does
not exist — comes back as ``None`` rather than a plausible-looking guess, so the
caller can report it instead of attaching a poll to the wrong race.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

# The 50 states plus the District of Columbia, keyed by the canonical name used
# for seats on the Presidential map.
STATE_POSTAL: dict[str, str] = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "District of Columbia": "DC",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}

# States whose whole delegation is one at-large House district. Their Wikipedia
# pages are the singular "…_election_in_<State>" form and carry no "District N"
# headings, so the seat is always ``XX-01``.
AT_LARGE_STATES: frozenset[str] = frozenset(
    {
        "Alaska",
        "Delaware",
        "North Dakota",
        "South Dakota",
        "Vermont",
        "Wyoming",
    }
)

# House districts per state under the 2024 apportionment, which is what the
# House map's 435 seats are drawn on. Districts outside a state's range are
# rejected rather than turned into a seat name that does not exist.
HOUSE_DISTRICT_COUNTS: dict[str, int] = {
    "Alabama": 7,
    "Alaska": 1,
    "Arizona": 9,
    "Arkansas": 4,
    "California": 52,
    "Colorado": 8,
    "Connecticut": 5,
    "Delaware": 1,
    "Florida": 28,
    "Georgia": 14,
    "Hawaii": 2,
    "Idaho": 2,
    "Illinois": 17,
    "Indiana": 9,
    "Iowa": 4,
    "Kansas": 4,
    "Kentucky": 6,
    "Louisiana": 6,
    "Maine": 2,
    "Maryland": 8,
    "Massachusetts": 9,
    "Michigan": 13,
    "Minnesota": 8,
    "Mississippi": 4,
    "Missouri": 8,
    "Montana": 2,
    "Nebraska": 3,
    "Nevada": 4,
    "New Hampshire": 2,
    "New Jersey": 12,
    "New Mexico": 3,
    "New York": 26,
    "North Carolina": 14,
    "North Dakota": 1,
    "Ohio": 15,
    "Oklahoma": 5,
    "Oregon": 6,
    "Pennsylvania": 17,
    "Rhode Island": 2,
    "South Carolina": 7,
    "South Dakota": 1,
    "Tennessee": 9,
    "Texas": 38,
    "Utah": 4,
    "Vermont": 1,
    "Virginia": 11,
    "Washington": 10,
    "West Virginia": 2,
    "Wisconsin": 8,
    "Wyoming": 1,
}

# The only congressional districts that are Presidential-map seats in their own
# right: Maine and Nebraska split their electoral votes by district.
PRESIDENT_DISTRICT_STATES: dict[str, int] = {"Maine": 2, "Nebraska": 3}

_STATES_BY_LOWER_NAME: dict[str, str] = {name.lower(): name for name in STATE_POSTAL}
_STATES_BY_LOWER_POSTAL: dict[str, str] = {
    postal.lower(): name for name, postal in STATE_POSTAL.items()
}

# Heading spellings of the District of Columbia that are not its canonical name.
_DC_ALIASES: frozenset[str] = frozenset(
    {
        "washington, d.c.",
        "washington d.c.",
        "washington, dc",
        "washington dc",
        "d.c.",
        "dc",
    }
)

_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")
_WHITESPACE_RE = re.compile(r"\s+")

# "Maine CD-2", "Nebraska (CD-3)", "Maine CD 2".
_CD_SUFFIX_RE = re.compile(
    r"^(?P<state>.+?)\s*\(?CD[-\s]?(?P<number>\d{1,2})\)?$",
    re.IGNORECASE,
)
# "Maine's 2nd congressional district", "Nebraska's 2nd district".
_CD_POSSESSIVE_RE = re.compile(
    r"^(?P<state>.+?)['’]s\s+(?P<number>\d{1,2})(?:st|nd|rd|th)\s+"
    r"(?:congressional\s+)?district$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    """Strip footnote markers and collapse whitespace."""
    return _WHITESPACE_RE.sub(" ", _FOOTNOTE_RE.sub("", text)).strip()


def canonical_state(text: str, *, allow_postal: bool = False) -> str | None:
    """Look a state up case-insensitively, returning its canonical spelling.

    Args:
        text: A state name, footnotes and stray whitespace allowed.
        allow_postal: Also accept a postal code ("tx"). Only for text a person
            typed — scraped headings and slugs stay name-only, so a stray "OR"
            or "IN" heading is not taken for a state.

    Returns:
        The canonical state name, or None if the text names no state.
    """
    key = _clean(text).lower()
    name = _STATES_BY_LOWER_NAME.get(key)
    if name is None and allow_postal:
        name = _STATES_BY_LOWER_POSTAL.get(key)
    return name


def state_from_page_slug(slug_or_url: str) -> str | None:
    """Resolve a Wikipedia page slug or URL to the state the race is held in.

    Handles the forms the 2026 index pages link to, e.g.
    ``2026_United_States_Senate_election_in_Texas``,
    ``…_Senate_special_election_in_Florida``,
    ``…_House_of_Representatives_elections_in_Pennsylvania`` and
    ``…_election_in_the_District_of_Columbia``, given either bare or as a full
    ``https://en.wikipedia.org/wiki/…`` URL, percent-encoded or not.

    Args:
        slug_or_url: The page slug or its full URL.

    Returns:
        The canonical state name, or None for a territory page (Guam, American
        Samoa, Puerto Rico, the Northern Mariana Islands, the United States
        Virgin Islands) and for anything that is not a per-state page at all.
    """
    slug = slug_or_url.split("#", 1)[0].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    text = _clean(unquote(slug).replace("_", " "))
    # The state is whatever follows the last " in ", which covers both the
    # "election in X" and "special election in X" forms. No state name contains
    # " in " as a word, so the last separator is always the right one.
    _, separator, tail = text.rpartition(" in ")
    candidate = tail if separator else text
    if candidate.lower().startswith("the "):
        candidate = candidate[4:]
    return canonical_state(candidate)


def house_seat_name(state: str, district: int | None) -> str | None:
    """Name a House-map seat, e.g. ``("Pennsylvania", 7)`` → ``"PA-07"``.

    Args:
        state: Canonical state name.
        district: District number, or None for an at-large state's only seat.
            At-large states are always seat ``01`` whatever is passed.

    Returns:
        The seat name, or None if the state is unknown, is the District of
        Columbia (which has no House seat on the map, only a delegate), or the
        district number is outside the state's range.
    """
    canonical = canonical_state(state)
    if canonical is None:
        return None
    seat_count = HOUSE_DISTRICT_COUNTS.get(canonical)
    if seat_count is None:
        return None
    number = 1 if canonical in AT_LARGE_STATES or district is None else district
    if not 1 <= number <= seat_count:
        return None
    return f"{STATE_POSTAL[canonical]}-{number:02d}"


def president_seat_for_heading(text: str) -> str | None:
    """Resolve a Wikipedia section heading to a Presidential-map seat.

    Accepts a plain state name, the District of Columbia under any of its usual
    spellings ("Washington, D.C.", "Washington DC"), and the Maine and Nebraska
    congressional districts as either ``Maine CD-2``, ``Nebraska (CD-3)`` or
    ``Maine's 2nd congressional district``.

    Args:
        text: The heading text.

    Returns:
        The seat name, or None for a heading that is not a statewide or
        split-district race — "Nationwide", a district of a state that does not
        split its electoral votes, or anything unrecognised. Note that
        "Washington" is the state and only the explicit D.C. spellings give the
        District of Columbia.
    """
    cleaned = _clean(text)
    if not cleaned:
        return None
    if cleaned.lower() in _DC_ALIASES:
        return "District of Columbia"
    for pattern in (_CD_SUFFIX_RE, _CD_POSSESSIVE_RE):
        match = pattern.match(cleaned)
        if match is None:
            continue
        state = canonical_state(match["state"])
        if state is None:
            return None
        district_count = PRESIDENT_DISTRICT_STATES.get(state)
        number = int(match["number"])
        if district_count is None or not 1 <= number <= district_count:
            return None
        return f"{state} CD-{number}"
    return canonical_state(cleaned)


def parent_seat_name(seat_name: str) -> str | None:
    """Name the statewide seat a split-district seat sits inside.

    ``"Maine CD-2"`` → ``"Maine"``. Used by the model's blending step, which
    falls back to the parent state's swing for a district with no polls of its
    own.

    Args:
        seat_name: A Presidential-map seat name.

    Returns:
        The parent state's seat name, or None for a plain statewide seat and for
        anything that is not a Presidential-map district seat.
    """
    match = _CD_SUFFIX_RE.match(_clean(seat_name))
    if match is None:
        return None
    state = canonical_state(match["state"])
    if state is None or state not in PRESIDENT_DISTRICT_STATES:
        return None
    return state
