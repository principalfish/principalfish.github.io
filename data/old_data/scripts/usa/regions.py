"""US Census Bureau divisions, and the state -> division map.

The US maps group seats by the 9 Census *divisions* (a flat list — no region/division
hierarchy), which makes the region filter useful without 50 single-state entries. The
map's state borders still come from the geometry's per-state ``properties.region``; this
division grouping only drives the DB region a seat belongs to (and so the filter).

Reference: https://www2.census.gov/geo/pdfs/maps-data/maps/reference/us_regdiv.pdf
"""

from __future__ import annotations

# Full state name (and DC) -> Census division.
STATE_DIVISION = {
    # Northeast
    "Connecticut": "New England", "Maine": "New England", "Massachusetts": "New England",
    "New Hampshire": "New England", "Rhode Island": "New England", "Vermont": "New England",
    "New Jersey": "Mid-Atlantic", "New York": "Mid-Atlantic", "Pennsylvania": "Mid-Atlantic",
    # Midwest
    "Illinois": "East North Central", "Indiana": "East North Central", "Michigan": "East North Central",
    "Ohio": "East North Central", "Wisconsin": "East North Central",
    "Iowa": "West North Central", "Kansas": "West North Central", "Minnesota": "West North Central",
    "Missouri": "West North Central", "Nebraska": "West North Central", "North Dakota": "West North Central",
    "South Dakota": "West North Central",
    # South
    "Delaware": "South Atlantic", "Florida": "South Atlantic", "Georgia": "South Atlantic",
    "Maryland": "South Atlantic", "North Carolina": "South Atlantic", "South Carolina": "South Atlantic",
    "Virginia": "South Atlantic", "West Virginia": "South Atlantic", "District of Columbia": "South Atlantic",
    "Alabama": "East South Central", "Kentucky": "East South Central", "Mississippi": "East South Central",
    "Tennessee": "East South Central",
    "Arkansas": "West South Central", "Louisiana": "West South Central", "Oklahoma": "West South Central",
    "Texas": "West South Central",
    # West
    "Arizona": "Mountain", "Colorado": "Mountain", "Idaho": "Mountain", "Montana": "Mountain",
    "Nevada": "Mountain", "New Mexico": "Mountain", "Utah": "Mountain", "Wyoming": "Mountain",
    "Alaska": "Pacific", "California": "Pacific", "Hawaii": "Pacific", "Oregon": "Pacific",
    "Washington": "Pacific",
}


def division_for_state(state_name: str) -> str:
    """Return the Census division for a full state name (e.g. "Texas" -> "West South Central")."""
    return STATE_DIVISION[state_name]


# Two-letter state abbreviation -> full name. The single canonical table shared by the
# importers (via us_import) and the current-Senate converter, so the 50 rows are typed once.
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
