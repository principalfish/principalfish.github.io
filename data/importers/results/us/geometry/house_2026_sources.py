"""Sources for the House district sets behind the US maps.

``build_house_topojson.py`` reads this table to build the district maps:

* map 21 (2022 and 2024 elections): Census cb_2024 cd119, no replacements.
* map 24 (2026 election): cb_2024 cd119 for the states that kept their plan, with the
  states in ``REDRAWN_2026`` taken from the Census 120th-Congress TIGER/Line files instead.
* map 25 (2018 and 2020 elections): Census cb_2018 cd116, no replacements. North Carolina's
  2019 court-ordered plan (used in 2020) is in no Census file, so NC 2020 is drawn on the
  2018 boundaries.

Why Census files stand in for each state's own enacted-plan download: the Census Bureau
published the 120th-Congress districts (``TIGER2026/CD``, files dated 2026-10-06) as one
file per state. Every redrawn state's plan is in them, so one source covers all nine states
and the cartographic outline can be reused to clip them. The TIGER/Line files are
full-resolution and include water, so the builder clips them to the cartographic state
outline before stitching. When Census publishes cb_2026 cd120 cartographic files, drop
``REDRAWN_2026`` and build the map from those alone.

The 120th-Congress files were fetched on 2026-11-07. Which states count as redrawn comes from
comparing NYT's 2026 county results with the 2024 district map (``check_redistricting.py``):
nine states moved whole counties between districts.
Colorado, Nevada and Oregon only show slivers on counties that were already split, so they are
not redrawn. Missouri passed a new plan in 2025, and the 120th-Congress file carries it, but the
2026 election was held on the 2024 plan, so Missouri is deliberately left out.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

TIGER_CD120_URL = "https://www2.census.gov/geo/tiger/TIGER2026/CD/tl_2026_{fips}_cd120.zip"
TIGER_CD120_DISTRICT_FIELD = "CD120FP"
ALLOWED_HOSTS = frozenset({"www2.census.gov"})

# Census cartographic boundary files (1:500k) for the states that did not move.
CB_2024_CD119_URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip"
CB_2018_CD116_URL = "https://www2.census.gov/geo/tiger/GENZ2018/shp/cb_2018_us_cd116_500k.zip"


@dataclass(frozen=True)
class StateSource:
    """Where one redrawn state's 2026 districts come from."""

    state: str
    fips: str
    name: str
    note: str

    @property
    def url(self) -> str:
        """The Census 120th-Congress TIGER/Line download for this state."""
        return TIGER_CD120_URL.format(fips=self.fips)

    @property
    def filename(self) -> str:
        """The shapefile's base name once the zip is extracted."""
        return f"tl_2026_{self.fips}_cd120"


REDRAWN_2026: dict[str, StateSource] = {
    s.state: s
    for s in (
        StateSource("AL", "01", "Alabama", "AL-02 moved whole counties from AL-01"),
        StateSource("CA", "06", "California", "Proposition 50 plan, 52 districts"),
        StateSource("FL", "12", "Florida", "mid-decade plan"),
        StateSource("LA", "22", "Louisiana", "mid-decade plan, six districts"),
        StateSource("NC", "37", "North Carolina", "NC-01 and NC-03 swapped eastern counties"),
        StateSource("OH", "39", "Ohio", "redistricting commission plan"),
        StateSource("TN", "47", "Tennessee", "mid-decade plan"),
        StateSource("TX", "48", "Texas", "mid-decade plan, 38 districts"),
        StateSource("UT", "49", "Utah", "court-ordered plan"),
    )
}


def check_url(url: str) -> str:
    """Return ``url`` if it is an https URL on a Census host, otherwise raise ``ValueError``."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"refusing to fetch {url!r}: not an https Census URL")
    return url
