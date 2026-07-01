"""Convert FiveThirtyEight Senate results into the project election-JSON shape.

Source (ungated): FiveThirtyEight ``election-results``
    https://raw.githubusercontent.com/fivethirtyeight/election-results/main/election_results_senate.csv

Only ~a third of Senate seats are up each cycle. This converts the regular-class races
(``special == false``) for the given year into one seat per state, keyed by the 538
``state`` field so they join the Senate TopoJSON polygons. States with no race that
cycle simply have no seat and render with the map's neutral fill. Same quirk-handling
as the House converter (party in ``ballot_party``, fusion lines aggregated by candidate);
independents (Sanders/King) map to the "independent" party.

Special elections (e.g. 2024 NE/CA also held one) are skipped here — those states had a
regular race too; standalone specials can be added later as a separate overlay.

Usage:
    python old_data/scripts/usa/convert_538_senate.py \
        --csv /path/to/election_results_senate.csv \
        --year 2024 \
        --out old_data/files/usa/senate-2024.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_538_house import aggregate_unit

# 50 states; excludes DC and territories that may appear in the source.
STATE_ABBREVS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}


def convert(csv_path: Path, year: str) -> dict[str, Any]:
    """Convert the 538 Senate CSV into the project election-JSON structure.

    Args:
        csv_path: Path to ``election_results_senate.csv``.
        year: Election cycle to extract (e.g. ``"2024"``).

    Returns:
        Mapping of state name to ``{"seatInfo": {"current"}, "partyInfo": {...}}`` for
        each state with a regular Senate race that cycle.
    """
    with csv_path.open(encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["cycle"] == year
            and row["stage"] == "general"
            and row["special"] != "true"
            and row["state_abbrev"] in STATE_ABBREVS
        ]

    by_state: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {
        "name": "", "votes": 0, "ballot_parties": [], "winner": False, "state": "",
    }))
    for row in rows:
        candidate_id = row.get("candidate_id") or row["candidate_name"]
        candidate = by_state[row["state_abbrev"]][candidate_id]
        candidate["name"] = row["candidate_name"]
        candidate["state"] = row["state"]
        votes = row["votes"].strip()
        candidate["votes"] += int(votes) if votes else 0
        if row["ballot_party"]:
            candidate["ballot_parties"].append(row["ballot_party"])
        if row["winner"] == "true":
            candidate["winner"] = True

    result: dict[str, Any] = {}
    for state, candidates in by_state.items():
        party_info, winner_key = aggregate_unit(candidates.values(), state)
        # All rows for a state carry the same full name; key the result by it.
        display_name = next(iter(candidates.values()))["state"]
        result[display_name] = {"seatInfo": {"current": winner_key}, "partyInfo": party_info}

    return dict(sorted(result.items()))


def main() -> None:
    """CLI entry point: read the 538 Senate CSV and write the project JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="Path to election_results_senate.csv")
    parser.add_argument("--year", default="2024", help="Election cycle to extract")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    data = convert(args.csv, args.year)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(data)} states to {args.out}")


if __name__ == "__main__":
    main()
