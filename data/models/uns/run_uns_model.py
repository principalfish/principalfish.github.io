#!/usr/bin/env python3
"""Run a regional UNS simulation and persist results as a new model_uns election."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import delete, select, text

DATA_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = DATA_DIR.parent
TREND_CACHE_CSV = REPO_ROOT / "electionmaps" / "data" / "results" / "model_output_trends.csv"
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from db import Database
from models import Election, ElectionType, Vote


@dataclass
class SimulationConfig:
    map_name: str
    baseline_election_name: str
    as_of_date: date
    since_date: date
    half_life_days: float
    output_csv: str | None
    dry_run: bool


@dataclass
class SeatRef:
    id: int
    region_id: int | None


def existing_trend_dates() -> set[date]:
    if not TREND_CACHE_CSV.exists():
        return set()

    dates: set[date] = set()
    with TREND_CACHE_CSV.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw = str(row.get("as_of_date") or "").strip()
            if not raw:
                continue
            try:
                dates.add(date.fromisoformat(raw))
            except ValueError:
                continue
    return dates


def dates_to_run_for_cfg(cfg: SimulationConfig) -> list[date]:
    if cfg.dry_run:
        return [cfg.as_of_date]

    existing = existing_trend_dates()
    previous_dates = [value for value in existing if value < cfg.as_of_date]
    if not previous_dates:
        return [cfg.as_of_date]

    previous = max(previous_dates)
    missing: list[date] = []
    current = previous + timedelta(days=1)
    while current <= cfg.as_of_date:
        if current not in existing:
            missing.append(current)
        current += timedelta(days=1)

    if missing:
        return missing
    return [cfg.as_of_date]


def parse_args() -> SimulationConfig:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map-name", default="UK Constituencies post 2022")
    parser.add_argument("--baseline-election-name", default="2024 General Election")
    parser.add_argument("--as-of-days-back", type=int, default=0)
    parser.add_argument("--since-days-back", type=int, default=30)
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--since-date", default=None)
    parser.add_argument("--half-life-days", type=float, default=30.0)
    parser.add_argument("--output-csv", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    today = date.today()
    as_of_days_back = max(0, int(args.as_of_days_back))
    since_days_back = max(0, int(args.since_days_back))

    as_of_date = (
        date.fromisoformat(args.as_of_date)
        if args.as_of_date
        else today - timedelta(days=as_of_days_back)
    )
    since_date = (
        date.fromisoformat(args.since_date)
        if args.since_date
        else today - timedelta(days=since_days_back)
    )

    if since_date > as_of_date:
        parser.error("--since-days-back/--since-date must be older than or equal to as-of")

    return SimulationConfig(
        map_name=args.map_name,
        baseline_election_name=args.baseline_election_name,
        as_of_date=as_of_date,
        since_date=since_date,
        half_life_days=args.half_life_days,
        output_csv=args.output_csv,
        dry_run=args.dry_run,
    )


def ensure_model_uns_enum_value(db: Database) -> None:
    if db.engine.dialect.name != "postgresql":
        return

    with db.session() as session:
        labels = session.execute(
            text(
                """
                SELECT e.enumlabel
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = :enum_type
                """
            ),
            {"enum_type": "electiontype"},
        ).fetchall()
        existing = {row[0] for row in labels}
        if "model_uns" not in existing:
            session.execute(text("ALTER TYPE electiontype ADD VALUE 'model_uns'"))


def unique_election_name(db: Database, base_name: str) -> str:
    if db.get_election_by_name(base_name) is None:
        return base_name

    suffix = 2
    while True:
        candidate = f"{base_name} #{suffix}"
        if db.get_election_by_name(candidate) is None:
            return candidate
        suffix += 1


def delete_model_uns_for_as_of_date(db: Database, as_of_date: date) -> tuple[int, int]:
    base_name = f"UNS {as_of_date.isoformat()}"

    with db.session() as session:
        existing_ids = session.execute(
            select(Election.id)
            .where(Election.type == ElectionType.model_uns)
            .where(Election.name.like(f"{base_name}%"))
        ).scalars().all()

        if not existing_ids:
            return 0, 0

        deleted_votes = session.execute(
            delete(Vote).where(Vote.election_id.in_(existing_ids))
        ).rowcount or 0
        deleted_elections = session.execute(
            delete(Election).where(Election.id.in_(existing_ids))
        ).rowcount or 0

    return int(deleted_elections), int(deleted_votes)


def weighted_average(weighted_sum: float, total_weight: float) -> float | None:
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def resolve_simulation_scope(db: Database, cfg: SimulationConfig):
    poll_map = db.get_map_by_name(cfg.map_name)
    if poll_map is None:
        raise ValueError(f"Map not found: {cfg.map_name}")

    baseline = db.get_election_by_name(cfg.baseline_election_name)
    if baseline is None:
        raise ValueError(f"Baseline election not found: {cfg.baseline_election_name}")
    if baseline.map_id != poll_map.id:
        raise ValueError(
            f"Baseline election map_id={baseline.map_id} does not match map '{cfg.map_name}'"
        )

    since_date = cfg.since_date
    if since_date == date(1900, 1, 1):
        since_date = date(baseline.year, 1, 1)

    return poll_map, baseline, since_date


def fetch_seat_refs(db: Database, map_id: int) -> list[SeatRef]:
    with db.session() as session:
        rows = session.execute(
            text(
                """
                SELECT id, region_id
                FROM seats
                WHERE map_id = :map_id
                ORDER BY seat_name
                """
            ),
            {"map_id": map_id},
        ).fetchall()

    return [SeatRef(id=int(row.id), region_id=row.region_id) for row in rows]


def build_reference_data(db: Database, map_id: int):
    seats = fetch_seat_refs(db, map_id)
    regions = db.get_regions_for_map(map_id)
    seat_by_id = {seat.id: seat for seat in seats}
    region_by_id = {region.id: region for region in regions}
    region_by_seat_id = {seat.id: seat.region_id for seat in seats}

    all_parties = db.get_all_parties()
    party_name_by_id = {party.id: party.name for party in all_parties}
    party_colour_by_id = {party.id: party.colour for party in all_parties}
    pollster_weight_by_id = {
        pollster.id: (pollster.weight if pollster.weight is not None else 1.0)
        for pollster in db.get_all_pollsters()
    }

    return (
        seats,
        regions,
        seat_by_id,
        region_by_id,
        region_by_seat_id,
        party_name_by_id,
        party_colour_by_id,
        pollster_weight_by_id,
    )


def build_baseline_vote_state(db: Database, baseline_election_id: int, region_by_seat_id: dict[int, int | None]):
    baseline_votes = db.get_votes_for_election(baseline_election_id)
    if not baseline_votes:
        raise ValueError("Baseline election has no votes")

    seat_party_vote_totals: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    region_party_totals: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    region_totals: dict[int, float] = defaultdict(float)
    national_party_totals: dict[int, float] = defaultdict(float)
    national_total = 0.0

    for vote in baseline_votes:
        if vote.vote_total is None or vote.party_id is None:
            continue
        seat_id = vote.seat_id
        party_id = vote.party_id
        value = float(vote.vote_total)
        seat_party_vote_totals[seat_id][party_id] += value

        national_party_totals[party_id] += value
        national_total += value

        region_id = region_by_seat_id.get(seat_id)
        if region_id is None:
            continue
        region_party_totals[region_id][party_id] += value
        region_totals[region_id] += value

    if not seat_party_vote_totals:
        raise ValueError("No baseline seat-party vote totals available")

    baseline_national_shares: dict[int, float] = {}
    if national_total > 0:
        baseline_national_shares = {
            party_id: (value / national_total) * 100.0
            for party_id, value in national_party_totals.items()
        }

    baseline_region_shares: dict[int, dict[int, float]] = defaultdict(dict)
    for region_id, totals in region_party_totals.items():
        denom = region_totals[region_id]
        if denom <= 0:
            continue
        for party_id, value in totals.items():
            baseline_region_shares[region_id][party_id] = (value / denom) * 100.0

    return (
        seat_party_vote_totals,
        national_party_totals,
        baseline_national_shares,
        baseline_region_shares,
    )


def aggregate_poll_shares(
    db: Database,
    map_id: int,
    since_date: date,
    as_of_date: date,
    half_life_days: float,
    pollster_weight_by_id: dict[int, float],
):
    polls = db.get_polls_for_map(map_id)
    weighted_sums: dict[tuple[int | None, int], float] = defaultdict(float)
    total_weights: dict[tuple[int | None, int], float] = defaultdict(float)

    decay_lambda = math.log(2.0) / max(half_life_days, 0.001)

    for poll in polls:
        if poll.fieldwork_end < since_date or poll.fieldwork_end > as_of_date:
            continue

        days_since = (as_of_date - poll.fieldwork_end).days
        if days_since < 0:
            continue

        decay_weight = math.exp(-decay_lambda * float(days_since))
        pollster_weight = float(pollster_weight_by_id.get(poll.pollster_id, 1.0) or 1.0)
        poll_weight = decay_weight * pollster_weight
        if poll_weight <= 0:
            continue

        rows = db.get_rows_for_poll(poll.id)
        if not rows:
            continue

        for row in rows:
            if row.party_id is None:
                continue
            key = (row.region_id, row.party_id)
            weighted_sums[key] += float(row.percentage) * poll_weight
            total_weights[key] += poll_weight

    return weighted_sums, total_weights


def compute_region_diffs(
    seats,
    region_by_id: dict[int, object],
    party_name_by_id: dict[int, str],
    national_party_totals: dict[int, float],
    weighted_sums: dict[tuple[int | None, int], float],
    total_weights: dict[tuple[int | None, int], float],
    baseline_national_shares: dict[int, float],
    baseline_region_shares: dict[int, dict[int, float]],
):
    party_universe: set[int] = set(national_party_totals.keys())
    party_universe.update(party_id for _, party_id in weighted_sums.keys())
    region_ids = sorted({seat.region_id for seat in seats if seat.region_id is not None})

    current_region_shares: dict[int, dict[int, float]] = defaultdict(dict)
    current_national_shares: dict[int, float] = {}

    for party_id in party_universe:
        avg = weighted_average(weighted_sums[(None, party_id)], total_weights[(None, party_id)])
        if avg is not None:
            current_national_shares[party_id] = avg

    for region_id in region_ids:
        for party_id in party_universe:
            avg = weighted_average(
                weighted_sums[(region_id, party_id)],
                total_weights[(region_id, party_id)],
            )
            if avg is None:
                avg = current_national_shares.get(party_id)
            if avg is not None:
                current_region_shares[region_id][party_id] = avg

    region_swings: dict[int, dict[int, float]] = defaultdict(dict)
    region_diff_rows: list[dict[str, object]] = []
    for region_id in region_ids:
        region_name = region_by_id[region_id].name if region_id in region_by_id else str(region_id)
        for party_id in sorted(party_universe, key=lambda party: party_name_by_id.get(party, "")):
            baseline_share = baseline_region_shares.get(region_id, {}).get(
                party_id,
                baseline_national_shares.get(party_id, 0.0),
            )
            current_share = current_region_shares.get(region_id, {}).get(party_id)
            if current_share is None:
                current_share = baseline_share
            swing = current_share - baseline_share
            region_swings[region_id][party_id] = swing
            region_diff_rows.append(
                {
                    "region_id": region_id,
                    "region_name": region_name,
                    "party_id": party_id,
                    "party_name": party_name_by_id.get(party_id, str(party_id)),
                    "baseline_share": baseline_share,
                    "weighted_share": current_share,
                    "swing": swing,
                }
            )

    return party_universe, region_swings, region_diff_rows


def project_seat_votes(
    seat_party_vote_totals: dict[int, dict[int, float]],
    region_by_seat_id: dict[int, int | None],
    party_universe: set[int],
    region_swings: dict[int, dict[int, float]],
    party_name_by_id: dict[int, str],
):
    projected_votes: list[dict[str, object]] = []
    winners_by_party: Counter = Counter()

    for seat_id, base_vote_totals in seat_party_vote_totals.items():
        seat_total = sum(base_vote_totals.values())
        if seat_total <= 0:
            continue

        base_share_by_party = {
            party_id: (value / seat_total) * 100.0
            for party_id, value in base_vote_totals.items()
        }

        region_id = region_by_seat_id.get(seat_id)
        swing_for_region = region_swings.get(region_id, {})

        projection_raw: dict[int, float] = {}
        for party_id in party_universe:
            baseline_share = base_share_by_party.get(party_id, 0.0)
            swing = swing_for_region.get(party_id, 0.0)
            projection_raw[party_id] = max(0.0, baseline_share + swing)

        projection_sum = sum(projection_raw.values())
        if projection_sum <= 0:
            projection_raw = {party_id: base_share_by_party.get(party_id, 0.0) for party_id in party_universe}
            projection_sum = sum(projection_raw.values())
            if projection_sum <= 0:
                continue

        normalized = {
            party_id: (value / projection_sum) * 100.0
            for party_id, value in projection_raw.items()
        }
        winner_party_id = max(normalized, key=normalized.get)
        winners_by_party[party_name_by_id.get(winner_party_id, str(winner_party_id))] += 1

        for party_id, pct in normalized.items():
            projected_votes.append(
                {
                    "seat_id": seat_id,
                    "party_id": party_id,
                    "vote_total": pct,
                    "elected": party_id == winner_party_id,
                }
            )

    return projected_votes, winners_by_party


def write_output_csvs(
    output_csv: str,
    projected_votes: list[dict[str, object]],
    region_diff_rows: list[dict[str, object]],
    seat_by_id: dict[int, object],
    party_name_by_id: dict[int, str],
) -> None:
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["seat_id", "seat_name", "party_id", "party_name", "predicted_pct", "elected"],
        )
        writer.writeheader()
        for row in projected_votes:
            seat_id = int(row["seat_id"])
            party_id = int(row["party_id"])
            writer.writerow(
                {
                    "seat_id": seat_id,
                    "seat_name": seat_by_id[seat_id].seat_name if seat_id in seat_by_id else "",
                    "party_id": party_id,
                    "party_name": party_name_by_id.get(party_id, ""),
                    "predicted_pct": f"{float(row['vote_total']):.4f}",
                    "elected": bool(row["elected"]),
                }
            )

    diff_output_path = output_path.with_name(output_path.stem + "_regional_diffs" + output_path.suffix)
    with diff_output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "region_id",
                "region_name",
                "party_id",
                "party_name",
                "baseline_share",
                "weighted_share",
                "swing",
            ],
        )
        writer.writeheader()
        for row in region_diff_rows:
            writer.writerow(
                {
                    "region_id": int(row["region_id"]),
                    "region_name": str(row["region_name"]),
                    "party_id": int(row["party_id"]),
                    "party_name": str(row["party_name"]),
                    "baseline_share": f"{float(row['baseline_share']):.4f}",
                    "weighted_share": f"{float(row['weighted_share']):.4f}",
                    "swing": f"{float(row['swing']):.4f}",
                }
            )


def persist_projection(
    db: Database,
    map_id: int,
    as_of_date: date,
    election_name: str,
    projected_votes: list[dict[str, object]],
    party_name_by_id: dict[int, str],
) -> tuple[str, int]:
    ensure_model_uns_enum_value(db)
    election = db.add_election(
        map_id,
        as_of_date.year,
        election_name,
        ElectionType.model_uns,
    )

    payload = [
        {
            "election_id": election.id,
            "seat_id": int(row["seat_id"]),
            "party_id": int(row["party_id"]),
            "candidate_name": party_name_by_id.get(int(row["party_id"]), ""),
            "vote_total": float(row["vote_total"]),
            "elected": bool(row["elected"]),
        }
        for row in projected_votes
    ]
    db.bulk_add_votes(payload)
    return election.name, int(election.id)


def update_trend_cache_csv(
    election_id: int,
    election_name: str,
    as_of_date: date,
    projected_votes: list[dict[str, object]],
    party_name_by_id: dict[int, str],
    party_colour_by_id: dict[int, str | None],
) -> None:
    TREND_CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)

    vote_totals_by_party: dict[int, float] = defaultdict(float)
    seats_by_party: dict[int, int] = defaultdict(int)
    for row in projected_votes:
        party_id = int(row["party_id"])
        vote_totals_by_party[party_id] += float(row["vote_total"])
        if bool(row["elected"]):
            seats_by_party[party_id] += 1

    total_votes = sum(vote_totals_by_party.values())

    existing_rows: list[dict[str, str]] = []
    if TREND_CACHE_CSV.exists():
        with TREND_CACHE_CSV.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if str(row.get("as_of_date") or "").strip() == as_of_date.isoformat():
                    continue
                if int(row.get("election_id", "0") or "0") == election_id:
                    continue
                existing_rows.append(row)

    new_rows = []
    for party_id in sorted(vote_totals_by_party.keys(), key=lambda key: party_name_by_id.get(key, "")):
        new_rows.append(
            {
                "election_id": str(election_id),
                "election_name": election_name,
                "as_of_date": as_of_date.isoformat(),
                "party_id": str(party_id),
                "party_name": party_name_by_id.get(party_id, str(party_id)),
                "party_colour": party_colour_by_id.get(party_id) or "",
                "seats_won": str(seats_by_party.get(party_id, 0)),
                "vote_total_sum": f"{vote_totals_by_party.get(party_id, 0.0):.6f}",
                "vote_pct": (
                    f"{((vote_totals_by_party.get(party_id, 0.0) / total_votes) * 100.0):.6f}"
                    if total_votes > 0
                    else "0.000000"
                ),
            }
        )

    combined = existing_rows + new_rows
    combined.sort(key=lambda row: (int(row["election_id"]), row["party_name"]))

    with TREND_CACHE_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "election_id",
                "election_name",
                "as_of_date",
                "party_id",
                "party_name",
                "party_colour",
                "seats_won",
                "vote_total_sum",
                "vote_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(combined)


def run_simulation(
    db: Database,
    cfg: SimulationConfig,
) -> tuple[str, list[dict[str, object]], list[dict[str, object]], Counter]:
    poll_map, baseline, since_date = resolve_simulation_scope(db, cfg)
    cfg.since_date = since_date

    (
        seats,
        _regions,
        seat_by_id,
        region_by_id,
        region_by_seat_id,
        party_name_by_id,
        party_colour_by_id,
        pollster_weight_by_id,
    ) = build_reference_data(db, poll_map.id)

    (
        seat_party_vote_totals,
        national_party_totals,
        baseline_national_shares,
        baseline_region_shares,
    ) = build_baseline_vote_state(db, baseline.id, region_by_seat_id)

    weighted_sums, total_weights = aggregate_poll_shares(
        db,
        poll_map.id,
        cfg.since_date,
        cfg.as_of_date,
        cfg.half_life_days,
        pollster_weight_by_id,
    )

    party_universe, region_swings, region_diff_rows = compute_region_diffs(
        seats,
        region_by_id,
        party_name_by_id,
        national_party_totals,
        weighted_sums,
        total_weights,
        baseline_national_shares,
        baseline_region_shares,
    )

    projected_votes, winners_by_party = project_seat_votes(
        seat_party_vote_totals,
        region_by_seat_id,
        party_universe,
        region_swings,
        party_name_by_id,
    )

    base_election_name = f"UNS {cfg.as_of_date.isoformat()}"
    election_name = base_election_name

    if cfg.output_csv:
        write_output_csvs(
            cfg.output_csv,
            projected_votes,
            region_diff_rows,
            seat_by_id,
            party_name_by_id,
        )

    if cfg.dry_run:
        return election_name, projected_votes, region_diff_rows, winners_by_party

    delete_model_uns_for_as_of_date(db, cfg.as_of_date)

    persisted_name, persisted_election_id = persist_projection(
        db,
        poll_map.id,
        cfg.as_of_date,
        election_name,
        projected_votes,
        party_name_by_id,
    )

    update_trend_cache_csv(
        persisted_election_id,
        persisted_name,
        cfg.as_of_date,
        projected_votes,
        party_name_by_id,
        party_colour_by_id,
    )

    return persisted_name, projected_votes, region_diff_rows, winners_by_party


def main() -> None:
    cfg = parse_args()
    db = Database()

    run_dates = dates_to_run_for_cfg(cfg)
    if len(run_dates) > 1:
        print(
            "AUTO-BACKFILL "
            f"missing_dates={len(run_dates)} "
            f"from={run_dates[0].isoformat()} "
            f"to={run_dates[-1].isoformat()}"
        )

    lookback_days = max(0, (cfg.as_of_date - cfg.since_date).days)

    for index, run_date in enumerate(run_dates, start=1):
        run_cfg = SimulationConfig(
            map_name=cfg.map_name,
            baseline_election_name=cfg.baseline_election_name,
            as_of_date=run_date,
            since_date=run_date - timedelta(days=lookback_days),
            half_life_days=cfg.half_life_days,
            output_csv=cfg.output_csv,
            dry_run=cfg.dry_run,
        )

        election_name, projected_votes, region_diff_rows, winners_by_party = run_simulation(db, run_cfg)

        seat_ids = {int(row["seat_id"]) for row in projected_votes}

        print("UNS simulation complete")
        print(f"Map: {run_cfg.map_name}")
        print(f"Baseline election: {run_cfg.baseline_election_name}")
        print(f"As-of date: {run_cfg.as_of_date.isoformat()}")
        print(f"Since date: {run_cfg.since_date.isoformat()}")
        print(f"Half-life days: {run_cfg.half_life_days}")
        print(f"Election name: {election_name}")
        print(f"Projected seats: {len(seat_ids)}")
        print(f"Projected vote rows: {len(projected_votes)}")
        if len(run_dates) > 1:
            print(f"Backfill progress: {index}/{len(run_dates)}")

        print("Top projected seat winners:")
        for party_name, seats in winners_by_party.most_common(8):
            print(f"- {party_name}: {seats}")

        print("Weighted regional diffs (swing) snapshot:")
        by_region: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in region_diff_rows:
            by_region[str(row["region_name"])].append(row)

        key_parties = {
            "Conservative",
            "Labour",
            "Liberal Democrats",
            "Reform UK",
            "Green",
            "Scottish National Party",
            "Plaid Cymru",
            "Other",
        }

        for region_name in sorted(by_region.keys()):
            rows = [
                row
                for row in by_region[region_name]
                if str(row["party_name"]) in key_parties
            ]
            rows.sort(key=lambda row: str(row["party_name"]))
            if not rows:
                continue
            summary = ", ".join(
                f"{row['party_name']}: {float(row['swing']):+.2f}"
                for row in rows
            )
            print(f"- {region_name}: {summary}")


if __name__ == "__main__":
    main()
