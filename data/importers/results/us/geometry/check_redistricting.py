"""Check which states' House districts match the districts NYT reported for an election.

For every county, NYT lists the districts it voted in (each House race has a reporting unit
per county with votes). This script compares that with the districts a map's geometry puts
over the county (those covering at least ``--threshold`` of the county's area), and scores
each state by the share of its counties where the two sets differ.

A single score is not enough to call a state redrawn: counties that were already split between
districts pick up slivers, so even unchanged states score up to about 0.12 (Colorado, Nevada,
Oregon). What marks a redrawn state is the score *dropping* when the older map is swapped for
the new one, so give the script both maps:

* the new map scores no worse than the old one anywhere, and near 0 in the redrawn states;
* a state is reported redrawn when its score falls by ``REDRAWN_DROP`` or more against the old
  map. Unchanged states score the same on both maps.

Connecticut and Massachusetts report towns, not counties, and Alaska's boroughs differ from
Census counties, so any county NYT lists that the county file lacks is skipped; a state with
too few matched counties is shown as ``n/a``.

Inputs (all paths local; ``ogr2ogr`` converts them):
    --nyt        a NYT House results snapshot (``results-house`` JSON, schema A)
    --districts  the map to test: a district shapefile/GeoJSON/TopoJSON with ``name``
                 ``{ST}-{NN}`` properties (the maps built by build_house_topojson.py)
    --old        optionally, the previous map, to find the states that changed
    --counties   Census county shapefile (cb_*_us_county_500k)

Usage (from ``data/``):
    python importers/results/us/geometry/check_redistricting.py \
        --nyt <nyt-cache>/2026/house-general-<timestamp>.json \
        --districts ../uselectionmaps/data/maps/map-24.topo.json \
        --old ../uselectionmaps/data/maps/map-21.topo.json \
        --counties cb_2024_us_county_500k/cb_2024_us_county_500k.shp
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.strtree import STRtree
from shapely.validation import make_valid

# Equal-area CONUS Albers; areas are compared as shares, so any equal-area projection works.
AREA_CRS = "EPSG:5070"
DEFAULT_THRESHOLD = 0.02
MIN_COUNTIES = 3
# A state is redrawn if its mismatch share falls by at least this against the old map.
REDRAWN_DROP = 0.05

StateScores = dict[str, tuple[float | None, int]]


def nyt_county_districts(snapshot: Mapping[str, Any]) -> dict[str, set[str]]:
    """Map each county FIPS (state + county) to the districts NYT reports it voted in.

    Args:
        snapshot: A parsed NYT schema-A House results file.

    Returns:
        ``{"01097": {"AL-01", ...}}``. Districts are ``{ST}-{NN}`` with at-large as ``-01``.
    """
    out: dict[str, set[str]] = defaultdict(set)
    for race in snapshot["races"]:
        if race.get("office_id") != "H" or "special" in str(race.get("type", "")).lower():
            continue  # a special can be held on the old lines on general-election day
        units = race.get("reporting_units") or []
        state = next((u["state_postal"] for u in units if u.get("level") == "state"), None)
        if state is None:
            continue
        number = int(race.get("seat_number") or 0) or 1
        district = f"{state}-{number:02d}"
        for unit in units:
            if unit.get("level") != "county" or not (unit.get("total_votes") or 0) > 0:
                continue
            out[f"{unit['fips_state']}{unit['fips_county']}"].add(district)
    return dict(out)


def _read_geojson(path: Path, tmp: Path, name: str) -> list[dict[str, Any]]:
    out = tmp / f"{name}.json"
    # GeoJSON and TopoJSON carry no CRS; the maps are in lon/lat.
    source_crs = ["-s_srs", "EPSG:4326"] if path.suffix in (".json", ".geojson") else []
    subprocess.run(
        ["ogr2ogr", "-f", "GeoJSON", *source_crs, "-t_srs", AREA_CRS, str(out), str(path)],
        check=True,
    )
    features: list[dict[str, Any]] = json.loads(out.read_text(encoding="utf-8"))["features"]
    return features


def geometry_county_districts(
    counties: Sequence[Mapping[str, Any]],
    districts: Sequence[Mapping[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, set[str]]:
    """Map each county FIPS to the districts covering at least ``threshold`` of its area.

    Args:
        counties: GeoJSON features with ``GEOID`` (or ``STATEFP`` + ``COUNTYFP``) properties.
        districts: GeoJSON features with a ``name`` property (``{ST}-{NN}``).
        threshold: Minimum share of the county's area a district must cover.
    """
    geoms = [make_valid(shape(d["geometry"])) for d in districts]
    names = [str(d["properties"]["name"]) for d in districts]
    tree = STRtree(geoms)
    out: dict[str, set[str]] = {}
    for county in counties:
        props = county["properties"]
        fips = str(props.get("GEOID") or f"{props['STATEFP']}{props['COUNTYFP']}")
        geom = make_valid(shape(county["geometry"]))
        if geom.is_empty or geom.area == 0:
            continue
        found: set[str] = set()
        for index in tree.query(geom):
            if geom.intersection(geoms[int(index)]).area / geom.area >= threshold:
                found.add(names[int(index)])
        out[fips] = found
    return out


def state_scores(nyt: Mapping[str, set[str]], geometry: Mapping[str, set[str]]) -> StateScores:
    """Score each state by the share of its matched counties whose district sets differ.

    Returns:
        ``{state FIPS prefix: (share or None, counties compared)}``. The share is ``None``
        when too few counties matched between NYT and the county file to say anything.
    """
    mismatched: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for fips, theirs in nyt.items():
        mine = geometry.get(fips)
        if mine is None:
            continue
        state = fips[:2]
        total[state] += 1
        if mine != theirs:
            mismatched[state] += 1
    return {
        state: (mismatched[state] / count if count >= MIN_COUNTIES else None, count)
        for state, count in total.items()
    }


def redrawn_states(old: StateScores, new: StateScores) -> set[str]:
    """State FIPS prefixes whose mismatch share fell by ``REDRAWN_DROP`` or more, old to new."""
    out: set[str] = set()
    for state, (old_share, _) in old.items():
        new_share = new.get(state, (None, 0))[0]
        if old_share is None or new_share is None:
            continue
        if round(old_share - new_share, 9) >= REDRAWN_DROP:
            out.add(state)
    return out


def state_labels(nyt: Mapping[str, set[str]]) -> dict[str, str]:
    """Map each state FIPS prefix to its postal code, read off NYT's district names."""
    return {fips[:2]: sorted(districts)[0][:2] for fips, districts in nyt.items() if districts}


def check_redistricting(
    nyt_path: Path,
    districts_path: Path,
    counties_path: Path,
    threshold: float = DEFAULT_THRESHOLD,
) -> StateScores:
    """Score one map against NYT's counties end to end; see the module docstring."""
    snapshot = json.loads(nyt_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        counties = _read_geojson(counties_path, Path(tmp), "counties")
        districts = _read_geojson(districts_path, Path(tmp), "districts")
    geometry = geometry_county_districts(counties, districts, threshold)
    return state_scores(nyt_county_districts(snapshot), geometry)


def _fmt(share: float | None) -> str:
    return "n/a" if share is None else f"{share:.2f}"


def main() -> None:
    """CLI entry point: print a score per state, worst first."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--nyt", required=True, type=Path)
    parser.add_argument("--districts", required=True, type=Path)
    parser.add_argument("--old", type=Path)
    parser.add_argument("--counties", required=True, type=Path)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()

    new = check_redistricting(args.nyt, args.districts, args.counties, args.threshold)
    old = (
        check_redistricting(args.nyt, args.old, args.counties, args.threshold)
        if args.old
        else None
    )
    labels = state_labels(nyt_county_districts(json.loads(args.nyt.read_text(encoding="utf-8"))))
    redrawn = redrawn_states(old, new) if old is not None else set()
    ranked = new if old is None else old

    def worst_first(fips: str) -> float:
        share = ranked.get(fips, (None, 0))[0]
        return -(share if share is not None else -1.0)

    print("state  counties   old   new" if old is not None else "state  counties  mismatch")
    for fips in sorted(new, key=worst_first):
        share, count = new[fips]
        label = labels.get(fips, fips)
        if old is not None:
            flag = "  <- redrawn" if fips in redrawn else ""
            old_share = old.get(fips, (None, 0))[0]
            print(f"{label:<6} {count:>8}  {_fmt(old_share):>4}  {_fmt(share):>4}{flag}")
        else:
            print(f"{label:<6} {count:>8}  {_fmt(share):>8}")
    if old is not None:
        print("redrawn:", " ".join(sorted(labels.get(f, f) for f in redrawn)))


if __name__ == "__main__":
    main()
