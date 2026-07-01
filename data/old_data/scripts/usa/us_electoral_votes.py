"""Per-era US Electoral College vote tables and lookup helpers.

The number of electoral votes for each state changes with each decennial
census-based reapportionment. This module records the EV allocation for the
49 "whole-unit" tally units (48 non-ME/NE states + the District of Columbia)
per apportionment era, and exposes helpers to resolve the EV for any unit in a
given presidential election year.

Maine and Nebraska split their electoral votes by congressional district, so
they are never in ``EV_BY_ERA``; they are special-cased (statewide "at-large"
tally = 2, each CD unit = 1) and folded in by ``ev_map_for_year``.
"""

from __future__ import annotations

# Era year → {display_name → electoral votes} for the 49 whole-unit tally units
# (48 non-ME/NE states + DC). Each era's 49-unit sum plus the fixed ME (2+1+1)
# and NE (2+1+1+1) structure totals 538.
EV_BY_ERA: dict[int, dict[str, int]] = {
    2000: {
        "Alabama": 9, "Alaska": 3, "Arizona": 10, "Arkansas": 6, "California": 55,
        "Colorado": 9, "Connecticut": 7, "Delaware": 3, "District of Columbia": 3,
        "Florida": 27, "Georgia": 15, "Hawaii": 4, "Idaho": 4, "Illinois": 21,
        "Indiana": 11, "Iowa": 7, "Kansas": 6, "Kentucky": 8, "Louisiana": 9,
        "Maryland": 10, "Massachusetts": 12, "Michigan": 17, "Minnesota": 10,
        "Mississippi": 6, "Missouri": 11, "Montana": 3, "Nevada": 5,
        "New Hampshire": 4, "New Jersey": 15, "New Mexico": 5, "New York": 31,
        "North Carolina": 15, "North Dakota": 3, "Ohio": 20, "Oklahoma": 7,
        "Oregon": 7, "Pennsylvania": 21, "Rhode Island": 4, "South Carolina": 8,
        "South Dakota": 3, "Tennessee": 11, "Texas": 34, "Utah": 5, "Vermont": 3,
        "Virginia": 13, "Washington": 11, "West Virginia": 5, "Wisconsin": 10,
        "Wyoming": 3,
    },
    2010: {
        "Alabama": 9, "Alaska": 3, "Arizona": 11, "Arkansas": 6, "California": 55,
        "Colorado": 9, "Connecticut": 7, "Delaware": 3, "District of Columbia": 3,
        "Florida": 29, "Georgia": 16, "Hawaii": 4, "Idaho": 4, "Illinois": 20,
        "Indiana": 11, "Iowa": 6, "Kansas": 6, "Kentucky": 8, "Louisiana": 8,
        "Maryland": 10, "Massachusetts": 11, "Michigan": 16, "Minnesota": 10,
        "Mississippi": 6, "Missouri": 10, "Montana": 3, "Nevada": 6,
        "New Hampshire": 4, "New Jersey": 14, "New Mexico": 5, "New York": 29,
        "North Carolina": 15, "North Dakota": 3, "Ohio": 18, "Oklahoma": 7,
        "Oregon": 7, "Pennsylvania": 20, "Rhode Island": 4, "South Carolina": 9,
        "South Dakota": 3, "Tennessee": 11, "Texas": 38, "Utah": 6, "Vermont": 3,
        "Virginia": 13, "Washington": 12, "West Virginia": 5, "Wisconsin": 10,
        "Wyoming": 3,
    },
    2020: {
        "Alabama": 9, "Alaska": 3, "Arizona": 11, "Arkansas": 6, "California": 54,
        "Colorado": 10, "Connecticut": 7, "Delaware": 3, "District of Columbia": 3,
        "Florida": 30, "Georgia": 16, "Hawaii": 4, "Idaho": 4, "Illinois": 19,
        "Indiana": 11, "Iowa": 6, "Kansas": 6, "Kentucky": 8, "Louisiana": 8,
        "Maryland": 10, "Massachusetts": 11, "Michigan": 15, "Minnesota": 10,
        "Mississippi": 6, "Missouri": 10, "Montana": 4, "Nevada": 6,
        "New Hampshire": 4, "New Jersey": 14, "New Mexico": 5, "New York": 28,
        "North Carolina": 16, "North Dakota": 3, "Ohio": 17, "Oklahoma": 7,
        "Oregon": 8, "Pennsylvania": 19, "Rhode Island": 4, "South Carolina": 9,
        "South Dakota": 3, "Tennessee": 11, "Texas": 40, "Utah": 6, "Vermont": 3,
        "Virginia": 13, "Washington": 12, "West Virginia": 4, "Wisconsin": 10,
        "Wyoming": 3,
    },
}


def _era_for_year(year: int) -> int:
    """Return the apportionment era key for a presidential election year.

    2000–2008 → 2000, 2012–2020 → 2010, 2024 and later → 2020.
    """
    if year <= 2008:
        return 2000
    if year <= 2020:
        return 2010
    return 2020


def ev_for(unit_name: str, year: int) -> int:
    """Return the electoral votes for a tally unit in a given election year.

    ME/NE units are era-independent: the statewide "at-large" tally
    (``"Maine"`` / ``"Nebraska"``) is 2, and each congressional-district unit
    (``"Maine CD-*"`` / ``"Nebraska CD-*"``) is 1. All other units are looked
    up in ``EV_BY_ERA`` for the year's era.

    Args:
        unit_name: Full display name of the tally unit (e.g. ``"California"``,
            ``"District of Columbia"``, ``"Nebraska CD-2"``).
        year: Four-digit presidential election year.

    Returns:
        The electoral-vote weight for that unit and year.

    Raises:
        KeyError: If ``unit_name`` is not a known unit for the year's era
            (a mis-named unit is a bug, not a silent zero).
    """
    if unit_name == "Maine" or unit_name == "Nebraska":
        return 2
    if unit_name.startswith("Maine CD-") or unit_name.startswith("Nebraska CD-"):
        return 1
    return EV_BY_ERA[_era_for_year(year)][unit_name]


def ev_map_for_year(year: int) -> dict[str, int]:
    """Return a complete ``{unit_name: EV}`` map for a whole presidential year.

    Starts from the era's 49-unit table and adds the fixed ME/NE units so the
    result covers every tally unit on the presidential map (sums to 538).

    Args:
        year: Four-digit presidential election year.

    Returns:
        Dict mapping every presidential tally-unit display name to its EV.
    """
    ev_map = dict(EV_BY_ERA[_era_for_year(year)])
    ev_map.update({
        "Maine": 2,
        "Maine CD-1": 1,
        "Maine CD-2": 1,
        "Nebraska": 2,
        "Nebraska CD-1": 1,
        "Nebraska CD-2": 1,
        "Nebraska CD-3": 1,
    })
    return ev_map
