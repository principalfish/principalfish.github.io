"""Validate curated House facts and prepare an explicitly reviewed DB correction."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import cast

# The archive importer owns the canonical seeded US party names.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "old_data/scripts/usa"))
from us_import import PARTY_KEY_TO_NAME as PARTY_KEY_TO_NAME  # noqa: E402

PROPOSAL = Path(__file__).resolve().parents[1] / "models/us/baseline_audit/house-2024.json"
SOURCE_URL = "https://clerk.house.gov/member_info/electionInfo/2024/statistics2024.pdf"
SOURCE_SHA256 = "d4ba800fc17135b616ae556c92861080ab962d95916ace34b546c5aac824aec4"
EXCLUDED = {
    "Blank", "Blanks", "Void", "Over Votes", "Under Votes", "Blank Votes",
    "Continuing Ballots", "Exhausted Ballots",
}
ALIASES = {
    ("CO-05", "Joseph Gaye"): "independent",
    ("IA-02", "Jody Madlom Puffett"): "independent",
    ("NJ-01", "Austin Johnson"): "independent",
    ("OH-07", "Dennis Kucinich"): "others",
    ("OH-10", "Michael Harbaugh"): "others",
    ("OH-11", "Sean Freeman"): "others",
}
NOMINAL = {"FL-20": "democrat", "OK-03": "republican"}


class BaselineError(ValueError):
    """A proposal or DB differs from the reviewed, complete baseline."""


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise BaselineError("Expected an object with string keys")
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise BaselineError("Expected an array")
    return cast(list[object], value)


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise BaselineError("Vote counts must be nonnegative integers")
    return value


def _votes(value: object) -> dict[str, int]:
    result = {k: _count(v) for k, v in _object(value).items()}
    if not result or not set(result) <= PARTY_KEY_TO_NAME.keys():
        raise BaselineError("Unknown or empty party aggregate")
    return result


def digest(value: object) -> str:
    """Fingerprint complete structured data, rejecting non-finite JSON values."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def load_proposal(path: Path = PROPOSAL) -> dict[str, object]:
    """Check coverage, provenance and candidate-to-party reconciliation."""
    proposal = _object(json.loads(path.read_text()))
    if (
        type(proposal.get("schema")) is not int or proposal["schema"] != 1
        or proposal.get("source_url") != SOURCE_URL
        or proposal.get("source_sha256") != SOURCE_SHA256
    ):
        raise BaselineError("Unsupported proposal or source provenance")
    baseline = _object(proposal.get("baseline"))
    proposed = _object(proposal.get("proposed"))
    if len(baseline) != 435 or baseline.keys() != proposed.keys():
        raise BaselineError("Both baselines must cover the same 435 districts")
    totals: dict[str, Counter[str]] = {d: Counter() for d in baseline}
    nominal: set[str] = set()
    aliases: dict[tuple[str, str], str] = {}
    for value in _array(proposal.get("aliases")):
        row = _object(value)
        key = (str(row.get("district")), str(row.get("name")))
        if key in aliases:
            raise BaselineError("Duplicate alias")
        aliases[key] = str(row.get("party"))
    if aliases != ALIASES:
        raise BaselineError("The six reviewed minor-party aliases must be explicit")
    seen_aliases: set[tuple[str, str]] = set()
    for value in _array(proposal.get("candidates")):
        row = _object(value)
        district, party = str(row.get("district")), str(row.get("mapped_party"))
        if district not in totals or party not in PARTY_KEY_TO_NAME:
            raise BaselineError("Candidate outside scope or unknown party")
        page = _count(row.get("pdf_page"))
        if not 3 <= page <= 81:
            raise BaselineError("Missing or invalid candidate PDF page")
        count = _count(row.get("votes"))
        lines = _array(row.get("ballot_lines"))
        if row.get("nominal") is True:
            if district in nominal or NOMINAL.get(district) != party or count != 100 or lines:
                raise BaselineError("Only FL20 and OK03 may have nominal 100 votes")
            nominal.add(district)
        else:
            if not lines:
                raise BaselineError("Recorded candidate lacks ballot-line provenance")
            total = 0
            for line_value in lines:
                line = _object(line_value)
                if line.get("district") != district or line.get("label") in EXCLUDED:
                    raise BaselineError("Invalid ballot category in candidate votes")
                if not 3 <= _count(line.get("pdf_page")) <= 81:
                    raise BaselineError("Invalid ballot-line PDF page")
                total += _count(line.get("votes"))
            if total != count:
                raise BaselineError("Candidate count differs from its fusion ballot lines")
        key = (district, str(row.get("name")))
        if key in ALIASES:
            if party != ALIASES[key]:
                raise BaselineError("Minor-party alias differs from reviewed convention")
            seen_aliases.add(key)
        totals[district][party] += count
    if nominal != set(NOMINAL) or seen_aliases != ALIASES.keys():
        raise BaselineError("Incomplete nominal or alias evidence")
    for value in _array(proposal.get("excluded")):
        row = _object(value)
        if row.get("district") not in totals or row.get("label") not in EXCLUDED:
            raise BaselineError("Unknown excluded ballot category")
        _count(row.get("votes"))
        if not 3 <= _count(row.get("pdf_page")) <= 81:
            raise BaselineError("Invalid exclusion PDF page")
    changed = 0
    for district, value in baseline.items():
        old = _object(value)
        before, after = _votes(old.get("votes")), _votes(proposed[district])
        if district == "AK-01":
            if after != before:
                raise BaselineError("Alaska must retain the existing final-round baseline")
        elif dict(totals[district]) != after:
            raise BaselineError(f"Candidate reconciliation failed in {district}")
        if old.get("winner") not in after or max(after, key=lambda k: after[k]) != old.get("winner"):
            raise BaselineError(f"Party winner changed in {district}")
        if not after.keys() <= before.keys():
            raise BaselineError("New party rows require a separate reviewed proposal")
        changed += before != after
    if changed != 51:
        raise BaselineError("Expected exactly 51 changed district aggregates")
    return proposal


def _snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    election = connection.execute(
        "SELECT * FROM elections WHERE name = ?", ("2024 US House Election",),
    ).fetchone()
    map_row = connection.execute("SELECT * FROM maps WHERE id = 21").fetchone()
    if (
        election is None or election["map_id"] != 21 or election["year"] != 2024
        or election["type"] != "us_house" or election["parent_election_id"] is not None
        or map_row is None or map_row["name"] != "US House Districts 2024"
        or map_row["parliament"] != "us_house"
    ):
        raise BaselineError("Election/map identity is outside the House 2024 scope")
    seats = connection.execute("SELECT * FROM seats WHERE map_id = 21 ORDER BY id").fetchall()
    rows = connection.execute(
        "SELECT v.*, s.seat_name, s.map_id AS seat_map_id, p.name AS party_name "
        "FROM votes v LEFT JOIN seats s ON s.id = v.seat_id "
        "LEFT JOIN parties p ON p.id = v.party_id WHERE v.election_id = ? ORDER BY v.id",
        (election["id"],),
    ).fetchall()
    return {"election": dict(election), "map": dict(map_row),
            "seats": [dict(s) for s in seats], "votes": [dict(r) for r in rows]}


def _project(snapshot: dict[str, object], proposal: dict[str, object]) -> dict[str, object]:
    baseline = _object(proposal["baseline"])
    proposed = _object(proposal["proposed"])
    names = {v: k for k, v in PARTY_KEY_TO_NAME.items()}
    seats = [_object(value) for value in _array(snapshot["seats"])]
    if len(seats) != 435 or {str(s.get("seat_name")) for s in seats} != baseline.keys():
        raise BaselineError("Map must contain exactly the 435 reviewed districts")
    seen: dict[str, dict[str, int]] = {}
    result: list[dict[str, object]] = []
    for value in _array(snapshot["votes"]):
        row = _object(value)
        district = str(row.get("seat_name"))
        party = names.get(str(row.get("party_name")))
        if district not in baseline or party is None or row.get("seat_map_id") != 21:
            raise BaselineError("Vote row outside the exact House baseline scope")
        district_seen = seen.setdefault(district, {})
        count = row.get("vote_total")
        if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0 or count % 1:
            raise BaselineError("DB vote count must be a finite nonnegative integer")
        if party in district_seen:
            raise BaselineError("Duplicate party rows; candidate-level DB needs separate review")
        district_seen[party] = int(count)
        old = _object(baseline[district])
        if row.get("elected") != int(party == old["winner"]):
            raise BaselineError("Unexpected baseline elected flags")
        after = _votes(proposed[district])
        if party in after:
            result.append({**row, "vote_total": float(after[party])})
    if len(seen) != 435 or any(
        votes != _votes(_object(baseline[d])["votes"]) for d, votes in seen.items()
    ):
        raise BaselineError("Stale, partial or mismatched House baseline")
    return {**snapshot, "votes": result}


def audit_database(database: Path, proposal: dict[str, object]) -> dict[str, object]:
    """Read without creating a DB; return the exact pre/post state for review."""
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        before = _snapshot(connection)
        after = _project(before, proposal)
    baseline, proposed = _object(proposal["baseline"]), _object(proposal["proposed"])
    return {
        "schema": 1, "proposal_sha256": digest(proposal),
        "before_sha256": digest(before), "after_sha256": digest(after),
        "identity_sha256": digest({k: v for k, v in before.items() if k != "votes"}),
        "changes": [{"district": d, "before": _object(baseline[d])["votes"],
                     "after": proposed[d]} for d in sorted(baseline)
                    if _object(baseline[d])["votes"] != proposed[d]],
        "source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256,
    }


def apply_reviewed(database: Path, proposal: dict[str, object], report: dict[str, object]) -> bool:
    """Atomically apply an unchanged reviewed report, or return False if applied."""
    if report.get("schema") != 1 or report.get("proposal_sha256") != digest(proposal):
        raise BaselineError("Reviewed report does not match this proposal")
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=rw", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            before = _snapshot(connection)
            if digest({k: v for k, v in before.items() if k != "votes"}) != report.get("identity_sha256"):
                raise BaselineError("Election/map/seat identity changed since review")
            if digest(before) == report.get("after_sha256"):
                return False
            if digest(before) != report.get("before_sha256"):
                raise BaselineError("Vote rows changed since review; refusing partial repair")
            after = _project(before, proposal)
            if digest(after) != report.get("after_sha256"):
                raise BaselineError("Reviewed corrected state differs")
            retained = {_object(r)["id"]: _object(r) for r in _array(after["votes"])}
            for value in _array(before["votes"]):
                row = _object(value)
                if row["id"] not in retained:
                    connection.execute("DELETE FROM votes WHERE id = ?", (row["id"],))
                elif retained[row["id"]]["vote_total"] != row["vote_total"]:
                    connection.execute("UPDATE votes SET vote_total = ? WHERE id = ?",
                                       (retained[row["id"]]["vote_total"], row["id"]))
            if digest(_snapshot(connection)) != report["after_sha256"]:
                raise BaselineError("Post-write verification failed; rolling back")
    return True
