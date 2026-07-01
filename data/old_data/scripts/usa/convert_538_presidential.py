"""Convert FiveThirtyEight presidential results into the project election-JSON shape.

Source (ungated): FiveThirtyEight ``election-results``
    https://raw.githubusercontent.com/fivethirtyeight/election-results/main/election_results_presidential.csv

538 already encodes the Electoral College's 56 voting units: 50 states + DC, plus the
Maine/Nebraska statewide "at-large" units (state_abbrev ``ME`` / ``NE``, worth 2 EV each)
and their congressional-district units (``M1``/``M2``, ``N1``/``N2``/``N3``, 1 EV each).
Puerto Rico (``PR``) is dropped (no electoral votes).

Output matches the project shape, with ``electoral_votes`` added to ``seatInfo`` and seats
keyed by the 538 ``state`` field so they join the presidential TopoJSON polygons
("California", "Maine CD-1", …). The two statewide ME/NE units have no polygon — they are
tally-only — but still carry their 2 EV.

Usage:
    python old_data/scripts/usa/convert_538_presidential.py \
        --csv /path/to/election_results_presidential.csv \
        --year 2024 \
        --out old_data/files/usa/presidential-2024.json
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_538_house import aggregate_unit

# The Maine/Nebraska congressional-district units and their parent statewide unit. 538
# occasionally omits a state's CD units for a cycle where it did not split (e.g. Nebraska in
# 2004, which voted uniformly Republican), but the presidential map still carries those seats,
# so any missing CD unit is backfilled from its parent's statewide result (see backfill below).
ME_NE_CD_UNITS = {
    "Maine": ["Maine CD-1", "Maine CD-2"],
    "Nebraska": ["Nebraska CD-1", "Nebraska CD-2", "Nebraska CD-3"],
}

# Electoral votes per 538 unit (2024 apportionment; ME/NE statewide = 2, their CD units = 1).
ELECTORAL_VOTES = {
    "AL": 9, "AK": 3, "AZ": 11, "AR": 6, "CA": 54, "CO": 10, "CT": 7, "DE": 3, "DC": 3,
    "FL": 30, "GA": 16, "HI": 4, "ID": 4, "IL": 19, "IN": 11, "IA": 6, "KS": 6, "KY": 8,
    "LA": 8, "ME": 2, "MD": 10, "MA": 11, "MI": 15, "MN": 10, "MS": 6, "MO": 10, "MT": 4,
    "NE": 2, "NV": 6, "NH": 4, "NJ": 14, "NM": 5, "NY": 28, "NC": 16, "ND": 3, "OH": 17,
    "OK": 7, "OR": 8, "PA": 19, "RI": 4, "SC": 9, "SD": 3, "TN": 11, "TX": 40, "UT": 6,
    "VT": 3, "VA": 13, "WA": 12, "WV": 4, "WI": 10, "WY": 3,
    "M1": 1, "M2": 1, "N1": 1, "N2": 1, "N3": 1,
}


def convert(csv_path: Path, year: str) -> dict[str, Any]:
    """Convert the 538 presidential CSV into the project election-JSON structure.

    Args:
        csv_path: Path to ``election_results_presidential.csv``.
        year: Election cycle to extract (e.g. ``"2024"``).

    Returns:
        Mapping of unit display name (538 ``state``) to
        ``{"seatInfo": {"current", "electoral_votes"}, "partyInfo": {...}}``.
    """
    with csv_path.open(encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["cycle"] == year and row["stage"] == "general" and row["state_abbrev"] in ELECTORAL_VOTES
        ]

    by_unit: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {
        "name": "", "votes": 0, "ballot_parties": [], "winner": False, "state": "",
    }))
    for row in rows:
        unit = row["state_abbrev"]
        candidate_id = row.get("candidate_id") or row["candidate_name"]
        candidate = by_unit[unit][candidate_id]
        candidate["name"] = row["candidate_name"]
        candidate["state"] = row["state"]
        votes = row["votes"].strip()
        candidate["votes"] += int(votes) if votes else 0
        if row["ballot_party"]:
            candidate["ballot_parties"].append(row["ballot_party"])
        if row["winner"] == "true":
            candidate["winner"] = True

    result: dict[str, Any] = {}
    for unit, candidates in by_unit.items():
        party_info, winner_key = aggregate_unit(candidates.values(), unit)
        # All rows for a unit carry the same full name; key the result by it.
        display_name = next(iter(candidates.values()))["state"]
        result[display_name] = {
            "seatInfo": {"current": winner_key, "electoral_votes": ELECTORAL_VOTES[unit]},
            "partyInfo": party_info,
        }

    _backfill_me_ne_cd_units(result)
    return dict(sorted(result.items()))


def _backfill_me_ne_cd_units(result: dict[str, Any]) -> None:
    """Add any Maine/Nebraska CD unit the source omitted, from its parent's statewide result.

    A cycle where Maine or Nebraska did not split (e.g. Nebraska 2004) can be missing its CD
    units in the 538 data, but the presidential map still has those seats. Each missing CD unit
    inherits the parent state's winner and vote breakdown (district-level popular vote being
    unavailable) so the electoral-vote tally stays complete. Mutates ``result`` in place;
    a no-op when every CD unit is already present.

    Args:
        result: Unit display name → ``{"seatInfo", "partyInfo"}`` mapping, mutated in place.
    """
    for parent, cd_units in ME_NE_CD_UNITS.items():
        if parent not in result:
            continue
        for cd_unit in cd_units:
            if cd_unit in result:
                continue
            result[cd_unit] = {
                "seatInfo": {
                    "current": result[parent]["seatInfo"]["current"],
                    "electoral_votes": 1,
                },
                "partyInfo": copy.deepcopy(result[parent]["partyInfo"]),
            }


def main() -> None:
    """CLI entry point: read the 538 presidential CSV and write the project JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="Path to election_results_presidential.csv")
    parser.add_argument("--year", default="2024", help="Election cycle to extract")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    data = convert(args.csv, args.year)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total_ev = sum(v["seatInfo"]["electoral_votes"] for v in data.values())
    print(f"Wrote {len(data)} units ({total_ev} electoral votes) to {args.out}")


if __name__ == "__main__":
    main()
