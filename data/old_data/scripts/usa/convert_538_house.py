"""Convert FiveThirtyEight US House results into the project election-JSON shape.

Source data (ungated, authoritative): FiveThirtyEight ``election-results``
    https://raw.githubusercontent.com/fivethirtyeight/election-results/main/election_results_house.csv

The output matches the existing Westminster/Holyrood election-JSON shape consumed by
the importers:

    {
      "TX-01": {
        "seatInfo": {"current": "republican"},
        "partyInfo": {
          "republican": {"total": 250000, "name": "Jane Doe"},
          "democrat":   {"total": 180000, "name": "John Smith"}
        }
      },
      ...
    }

Quirks handled (see the 2024 data):
    - Party is in ``ballot_party`` (REP/DEM/LIB/GRE/IND/…), not ``party``.
    - Fusion voting (NY/CT): a candidate appears on several ballot lines; rows are
      grouped by candidate and their votes summed, then assigned one party (a major
      party line wins precedence).
    - Louisiana runs a "jungle primary" — its winners are in the ``jungle primary``
      stage rather than ``general``; both stages are read.
    - Special elections (``special == true``) are excluded: a special general shares the
      ``general`` stage with the regular general, so counting both double-counts a seat's
      turnout and scrambles its winner (e.g. NY-19/TX-34 2022, WI-08 2024).
    - Ranked-choice voting (Alaska, Maine): 538 emits one row per candidate *per round*
      (``ranked_choice_round`` = 1, 2, …). Only each district's final round is kept — the
      decisive two-candidate tally — so rounds aren't summed (which otherwise ~4x-inflates
      totals and can lift an eliminated-transfer runner-up above the actual winner).
    - Unopposed seats (``unopposed == true``, e.g. FL/LA/OK) report no vote totals, so the
      district aggregates to zero. The sole winner is given a nominal 100% total
      (:data:`UNOPPOSED_NOMINAL_VOTES`) so the seat flows through normally rather than being
      dropped as a zero-vote seat by the forecast model.
    - Non-voting delegates (GU/PR/VI) are dropped by restricting to the 435 voting
      district codes the geometry defines (passed via ``--valid-districts`` or implied
      by skipping rows whose state has no voting districts).

Usage:
    python old_data/scripts/usa/convert_538_house.py \
        --csv /path/to/election_results_house.csv \
        --year 2024 \
        --out old_data/files/usa/house-2024.json
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

# Non-state jurisdictions whose House members are non-voting delegates; excluded so
# the output is exactly the 435 voting districts.
NON_VOTING_STATES = {"GU", "PR", "VI", "MP", "AS", "DC"}

# FiveThirtyEight ``ballot_party`` code -> project party key (the keys understood by
# import_house_elections.py and the export party-key map). Anything unmapped (write-ins,
# minor/fusion lines such as WFP/CRV/CON) folds into "others".
BALLOT_PARTY_TO_KEY = {
    "REP": "republican",
    "DEM": "democrat",
    "DFL": "democrat",  # Minnesota Democratic–Farmer–Labor
    "LIB": "libertarian",
    "GRE": "usgreen",
    "IND": "independent",
}

# Major-party codes take precedence when a fusion candidate runs on several lines.
PARTY_PRECEDENCE = ["REP", "DEM", "DFL", "LIB", "GRE", "IND"]

# Nominal vote total assigned to an unopposed winner. Unopposed US House races report no
# vote counts (the candidate is often not even on the ballot), so 538 leaves votes blank
# and the seat aggregates to zero. Giving the sole winner a nominal total makes them 100%
# so the seat imports, projects (as a safe hold), and displays like any other seat instead
# of being dropped downstream as a zero-vote seat. The magnitude is a placeholder — only the
# 100% share is meaningful.
UNOPPOSED_NOMINAL_VOTES = 100


def district_code(state_abbrev: str, office_seat_name: str) -> str:
    """Build a ``{ST}-{NN}`` district code from a state and 538 seat name.

    538 names every district "District N" (at-large states use "District 1"), which
    matches the Census ``CD119FP`` convention once at-large ``00`` is mapped to ``01``.

    Args:
        state_abbrev: Two-letter state abbreviation (e.g. ``"TX"``).
        office_seat_name: 538 seat label, e.g. ``"District 7"``.

    Returns:
        A zero-padded district code, e.g. ``"TX-07"``.
    """
    number = int(office_seat_name.replace("District", "").strip())
    return f"{state_abbrev}-{number:02d}"


def resolve_candidate_party(ballot_parties: list[str]) -> str:
    """Resolve a single project party key for a (possibly fusion) candidate.

    Args:
        ballot_parties: All 538 ``ballot_party`` codes the candidate appeared under.

    Returns:
        The project party key, preferring major parties, else the first mapped code,
        else ``"others"``.
    """
    for code in PARTY_PRECEDENCE:
        if code in ballot_parties:
            return BALLOT_PARTY_TO_KEY[code]
    for code in ballot_parties:
        if code in BALLOT_PARTY_TO_KEY:
            return BALLOT_PARTY_TO_KEY[code]
    return "others"


def aggregate_unit(
    candidates: Iterable[dict[str, Any]], unit_label: str
) -> tuple[dict[str, dict[str, Any]], str]:
    """Aggregate one unit's candidate rows into ``(party_info, winner_key)``.

    Shared by the House/Senate/President converters. Skips blank noise rows, sums fusion
    ballot lines per resolved party, keeps the highest-polling candidate's name, resolves
    the winner (by flag, else highest total), then drops the internal ``_top`` scratch
    field and backfills a generic name. Raises ``ValueError`` if the unit has no real
    candidate rows (avoids a cryptic ``max()`` over an empty sequence).
    """
    party_info: dict[str, dict[str, Any]] = {}
    winner_key: str | None = None
    for candidate in candidates:
        # Skip noise rows (e.g. blank fusion/write-in lines with no votes).
        if candidate["votes"] == 0 and not candidate["name"]:
            continue
        key = resolve_candidate_party(candidate["ballot_parties"])
        # Multiple candidates can fold into one key (e.g. two independents -> others):
        # sum totals and keep the highest-polling candidate's name.
        bucket = party_info.setdefault(key, {"total": 0, "name": candidate["name"], "_top": -1})
        bucket["total"] += candidate["votes"]
        if candidate["votes"] > bucket["_top"]:
            bucket["_top"] = candidate["votes"]
            bucket["name"] = candidate["name"]
        if candidate["winner"]:
            winner_key = key
    if not party_info:
        raise ValueError(f"No candidate rows for {unit_label!r} — check the source CSV")
    # Fall back to the highest-polling party when no winner flag was set.
    if winner_key is None:
        winner_key = max(party_info.items(), key=lambda kv: kv[1]["total"])[0]
    for bucket in party_info.values():
        bucket.pop("_top", None)
        if not bucket["name"]:
            bucket["name"] = "Other"
    return party_info, winner_key


def keep_final_rcv_round(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop all but the final ranked-choice round for RCV districts.

    538 reports each RCV tabulation round as its own row (``ranked_choice_round`` = 1, 2, …).
    Summing across rounds over-counts turnout and can rank an eliminated-transfer runner-up
    above the actual winner, so for each district that has any RCV rows we keep only its
    highest-numbered round (the decisive two-candidate tally). Districts with no RCV rows
    (empty ``ranked_choice_round``) are returned unchanged.

    Args:
        rows: Already-filtered 538 rows for a single cycle.

    Returns:
        The subset of ``rows`` with every non-final RCV round removed.
    """
    max_round: dict[str, int] = {}
    for row in rows:
        raw_round = row["ranked_choice_round"].strip()
        if raw_round:
            code = district_code(row["state_abbrev"], row["office_seat_name"])
            max_round[code] = max(max_round.get(code, 0), int(raw_round))
    if not max_round:
        return rows
    kept: list[dict[str, Any]] = []
    for row in rows:
        code = district_code(row["state_abbrev"], row["office_seat_name"])
        if code not in max_round or row["ranked_choice_round"].strip() == str(max_round[code]):
            kept.append(row)
    return kept


def convert(csv_path: Path, year: str) -> dict[str, Any]:
    """Convert the 538 House CSV into the project election-JSON structure.

    Args:
        csv_path: Path to ``election_results_house.csv``.
        year: Election cycle to extract (e.g. ``"2024"``).

    Returns:
        Mapping of district code to ``{"seatInfo": {...}, "partyInfo": {...}}``.
    """
    with csv_path.open(encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["cycle"] == year
            and row["stage"] in ("general", "jungle primary")
            and row["special"] != "true"
            and row["state_abbrev"] not in NON_VOTING_STATES
        ]

    # For ranked-choice districts keep only the final round so rounds aren't summed (see
    # module docstring); non-RCV rows pass through untouched.
    rows = keep_final_rcv_round(rows)

    # Group rows by district, then by candidate (to merge fusion ballot lines).
    by_district: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {
        "name": "",
        "votes": 0,
        "ballot_parties": [],
        "winner": False,
    }))
    for row in rows:
        code = district_code(row["state_abbrev"], row["office_seat_name"])
        candidate_id = row.get("candidate_id") or row["candidate_name"]
        candidate = by_district[code][candidate_id]
        candidate["name"] = row["candidate_name"]
        votes = row["votes"].strip()
        candidate["votes"] += int(votes) if votes else 0
        if row["ballot_party"]:
            candidate["ballot_parties"].append(row["ballot_party"])
        if row["winner"] == "true":
            candidate["winner"] = True

    result: dict[str, Any] = {}
    for code, candidates in by_district.items():
        party_info, winner_key = aggregate_unit(candidates.values(), code)
        # Unopposed seat: no votes were reported, so it aggregated to zero. Represent the
        # sole winner as 100% with a nominal total (see UNOPPOSED_NOMINAL_VOTES).
        if sum(bucket["total"] for bucket in party_info.values()) == 0:
            party_info[winner_key]["total"] = UNOPPOSED_NOMINAL_VOTES
        result[code] = {"seatInfo": {"current": winner_key}, "partyInfo": party_info}

    return dict(sorted(result.items()))


def main() -> None:
    """CLI entry point: read the 538 CSV and write the project election JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path, help="Path to election_results_house.csv")
    parser.add_argument("--year", default="2024", help="Election cycle to extract")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    data = convert(args.csv, args.year)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(data)} districts to {args.out}")


if __name__ == "__main__":
    main()
