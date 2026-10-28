"""Read-only presentation of stored Westminster simulation summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from db import Database
from model_support.persistence import StoredUncertainty, load_uncertainty, westminster_uns_scope
from models import Election, ElectionType, Map, Party, Region, Seat, Vote

WESTMINSTER_MAP = "UK Constituencies post 2022"


def mapping(value: object) -> dict[str, Any]:
    """Treat absent optional JSON blocks as empty, including older summaries."""
    return value if isinstance(value, dict) else {}


def ordered_parties(parties: object, seats: dict[str, int]) -> list[tuple[str, Any]]:
    """Show parties with the most centrally projected seats first."""
    return sorted(mapping(parties).items(), key=lambda item: seats.get(item[0], 0),
                  reverse=True)


def projected_seat_totals(db: Database, election_ids: list[int]) -> dict[int, dict[str, int]]:
    """Count saved winners for history without loading every constituency vote."""
    if not election_ids:
        return {}
    with db.session() as session:
        rows = session.execute(
            select(Vote.election_id, Vote.party_id, func.count())
            .where(Vote.election_id.in_(election_ids), Vote.elected.is_(True))
            .group_by(Vote.election_id, Vote.party_id)
        ).all()
    totals: dict[int, dict[str, int]] = {}
    for election_id, party, count in rows:
        totals.setdefault(election_id, {})[str(party)] = count
    return totals


@dataclass(frozen=True)
class RunRecord:
    election_id: int
    name: str
    as_of: date
    stored: StoredUncertainty


def stored_runs(db: Database) -> list[RunRecord]:
    """Return scoped runs newest first, including pruned national-only runs."""
    with db.session() as session:
        elections = session.execute(
            select(Election.id, Election.name, Election.map_id)
            .join(Map).where(Map.name == WESTMINSTER_MAP,
                             Election.type == ElectionType.model_uns)
        ).all()
    runs = []
    for election_id, name, map_id in elections:
        day = westminster_uns_scope(map_id).date_from_name(name)
        if day is None:
            continue
        stored = load_uncertainty(Path(db.config.database_path), election_id)
        if stored is not None:
            runs.append(RunRecord(election_id, name, day, stored))
    return sorted(runs, key=lambda run: (run.as_of, run.election_id), reverse=True)


def names(db: Database) -> dict[str, Any]:
    """Resolve display labels without using current data to reconstruct a run."""
    with db.session() as session:
        return {
            "parties": {str(i): name for i, name in session.execute(
                select(Party.id, Party.name))},
            "regions": {str(i): name for i, name in session.execute(
                select(Region.id, Region.name))},
            "seats": {str(i): {"name": name, "region": str(region)}
                      for i, name, region in session.execute(
                          select(Seat.id, Seat.seat_name, Seat.region_id))},
        }


def central_results(db: Database, election_id: int) -> dict[str, Any]:
    """Read the persisted central projection, not a fresh model calculation."""
    with db.session() as session:
        rows = session.execute(
            select(Vote.seat_id, Vote.party_id, Vote.vote_total, Vote.elected,
                   Seat.region_id).join(Seat).where(Vote.election_id == election_id)
        ).all()
    seats: dict[str, Any] = {}
    totals: dict[str, int] = {}
    regional: dict[str, dict[str, int]] = {}
    votes: dict[str, float] = {}
    for seat, party, count, elected, region in rows:
        key = str(party)
        entry = seats.setdefault(str(seat), {"votes": {}, "winner": None})
        entry["votes"][key] = (entry["votes"].get(key, 0) + (count or 0))
        votes[key] = votes.get(key, 0) + (count or 0)
        if elected:
            entry["winner"] = key
            totals[key] = totals.get(key, 0) + 1
            regional.setdefault(str(region), {})[key] = (
                regional.get(str(region), {}).get(key, 0) + 1)
    for entry in seats.values():
        total = sum(entry["votes"].values())
        entry["shares"] = {p: 100 * v / total if total else 0
                           for p, v in entry["votes"].items()}
    total_vote = sum(votes.values())
    return {"seats": seats, "totals": totals, "regions": regional,
            "vote": {p: 100 * v / total_vote if total_vote else 0
                     for p, v in votes.items()}}


def competitive_seats(run: RunRecord, central: dict[str, Any],
                      labels: dict[str, Any], sort: str) -> list[dict[str, Any]]:
    """Order seats by the leading simulated win probability, lowest first."""
    rows = []
    for seat, values in (run.stored.seats or {}).items():
        parties = mapping(values)
        ordered = sorted(parties, key=lambda p: mapping(parties[p]).get("w", 0),
                         reverse=True)
        projected = central["seats"].get(seat, {}).get("winner")
        winner = ordered[0] if ordered else None
        runner = ordered[1] if len(ordered) > 1 else None
        rows.append({"id": seat, "name": labels["seats"].get(seat, {}).get("name", seat),
                     "central": projected, "leader": winner, "runner": runner,
                     "probability": mapping(parties.get(winner or "")).get("w", 0),
                     "runner_probability": mapping(parties.get(runner or "")).get("w", 0),
                     "parties": parties})
    return sorted(rows, key=lambda row: row["name"] if sort == "name" else (
        -row["probability"] if sort == "safe" else row["probability"], row["name"]))


def budget_groups(run: RunRecord, labels: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep every stored diagnostic field, with explicit absent/ineligible cells."""
    groups: list[dict[str, Any]] = [{"id": "national", "name": "National",
               "parties": mapping(mapping(run.stored.national.get("budget")).get("parties"))}]
    for region, block in (run.stored.regions or {}).items():
        groups.append({"id": region, "name": labels["regions"].get(region, region),
                       "parties": mapping(mapping(block).get("budget"))})
    # A new party with no baseline anywhere remains eligible, as in the engine.
    established = {p for group in groups[1:] for p, cell in group["parties"].items()
                   if mapping(cell).get("baseline_share", 0) > 0}
    for group in groups:
        group["cells"] = []
        for party, raw in group["parties"].items():
            cell = mapping(raw)
            ineligible = (group["id"] != "national" and party in established
                          and cell.get("baseline_share", 0) <= 0)
            group["cells"].append({"party": party, "values": cell,
                                   "ineligible": ineligible})
    return groups
