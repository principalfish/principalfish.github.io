"""Build the "Current Senate" composition snapshot from congress-legislators data.

Unlike the election result files (one cycle's contests), this is the *current* makeup of
all 100 seats: per state, its two sitting senators, each with the party, name and Senate
class (1/2/3 — permanent; the year that class is next up is derived downstream from the
map's `senateClassCycle` config). The state's map colour is the combination
of the pair — both same party, or "split" when they differ. Independents (Sanders, King)
count as their own party, so their states read as split.

Source (canonical, maintained): @unitedstates/congress-legislators
    https://unitedstates.github.io/congress-legislators/legislators-current.json

This is a snapshot, not an election, so it is generated directly to a JSON file (not via
the DB/election export); re-run it when the chamber changes.

Usage:
    python old_data/scripts/usa/convert_senate_current.py \
        --legislators /path/to/legislators-current.json \
        --out ../uselectionmaps/data/results/senate-current.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regions import division_for_state


def _region_key(state: str) -> str:
    """Normalised region key for a state's division (matches the front-end's resolveRegion).

    The 2024 Senate map stores numeric region ids that the front-end resolves to a
    normalised key (lowercase, alphanumerics only); a string ``r`` passes through raw, so
    we pre-normalise here so the division filter behaves identically on both Senate views.
    """
    return re.sub(r"[^a-z0-9]", "", division_for_state(state).lower())

PARTY_KEY = {"Democrat": "democrat", "Republican": "republican", "Independent": "independent"}

# Note: a senator's `class` (1/2/3) is permanent, so it is the only cycle data stored here.
# The year each class is *next* up is derived downstream from the map's `senateClassCycle`
# config (base years + period) at export time, so it never needs editing in this snapshot.

ABBREV_TO_STATE = {
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


def build(legislators: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the ``pf-senate-current-v1`` payload from current-legislators records.

    Args:
        legislators: Parsed ``legislators-current.json`` (list of legislator dicts).

    Returns:
        Dict ``{"schema": "pf-senate-current-v1", "seats": [...]}`` with one seat per
        state: ``n`` (state), ``r`` (division key), ``w`` (combined party / "split"),
        ``members`` (two entries, each ``{party, name, class, up}``).
    """
    by_state: dict[str, list[dict[str, Any]]] = {}
    for leg in legislators:
        term = leg["terms"][-1]
        if term["type"] != "sen":
            continue
        state = ABBREV_TO_STATE.get(term["state"])
        if state is None:
            continue  # skip non-state senators if any
        senator_class = term.get("class")
        by_state.setdefault(state, []).append({
            "party": PARTY_KEY.get(term.get("party", ""), "others"),
            "name": leg["name"].get("official_full") or f"{leg['name'].get('first', '')} {leg['name'].get('last', '')}".strip(),
            "class": senator_class,
        })

    seats: list[dict[str, Any]] = []
    for state in sorted(by_state):
        members = sorted(by_state[state], key=lambda s: s["class"] or 0)
        parties = {m["party"] for m in members}
        winner = next(iter(parties)) if len(parties) == 1 else "split"
        # Region is the state's Census division (normalised key) so the division filter
        # behaves the same as on the 2024 Senate map.
        seats.append({"n": state, "r": _region_key(state), "w": winner, "members": members})

    return {"schema": "pf-senate-current-v1", "seats": seats}


def main() -> None:
    """CLI entry point: read legislators-current.json and write the snapshot JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legislators", required=True, type=Path, help="legislators-current.json")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    legislators = json.loads(args.legislators.read_text(encoding="utf-8"))
    payload = build(legislators)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['seats'])} states to {args.out}")


if __name__ == "__main__":
    main()
