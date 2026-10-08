"""Bootstrap database allocations and explicitly classify legacy forecasts.

Dry runs open the existing database read-only. Applying the migration creates
the allocation tables, adds target metadata, and inserts missing seed rows in
one transaction; existing allocation values and forecast targets are retained.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1]
USA_DIR = DATA_DIR / "old_data" / "scripts" / "usa"
for directory in (DATA_DIR, USA_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from config import DatabaseConfig
from electoral_votes import (
    ElectoralVoteEra,
    ElectoralVoteError,
    positive_integer,
    select_era,
    validate_eras,
)
from scripts.migrate_add_us_poll_scope import (
    column_exists,
    open_database as open_database,
    schema_object_exists,
)
from us_electoral_votes import ELECTION_YEAR_INTERVALS, EV_BY_ERA, ev_map_for_year

ERAS_DDL = """
CREATE TABLE IF NOT EXISTS us_electoral_vote_eras (
    census_year INTEGER NOT NULL PRIMARY KEY,
    first_election_year INTEGER NOT NULL,
    last_election_year INTEGER NOT NULL,
    CONSTRAINT ck_us_electoral_vote_era_interval
        CHECK (first_election_year <= last_election_year)
)
"""
ALLOCATIONS_DDL = """
CREATE TABLE IF NOT EXISTS us_electoral_vote_allocations (
    era_year INTEGER NOT NULL,
    unit_name VARCHAR NOT NULL,
    electoral_votes INTEGER NOT NULL,
    PRIMARY KEY (era_year, unit_name),
    FOREIGN KEY (era_year) REFERENCES us_electoral_vote_eras (census_year),
    CONSTRAINT ck_us_electoral_vote_allocation_positive_integer
        CHECK (electoral_votes > 0 AND typeof(electoral_votes) = 'integer')
)
"""


@dataclass(frozen=True, slots=True)
class AllocationSeed:
    """One bounded era and its complete modeled tally-unit weights."""

    era: ElectoralVoteEra
    weights: dict[str, int]


def bootstrap_seeds() -> list[AllocationSeed]:
    """Validate the single offline dataset before using it as bootstrap data."""
    if EV_BY_ERA.keys() != ELECTION_YEAR_INTERVALS.keys():
        raise ElectoralVoteError("Bootstrap era tables and intervals differ")
    eras = validate_eras(
        (census, first, last)
        for census, (first, last) in ELECTION_YEAR_INTERVALS.items()
    )
    seeds = []
    for era in eras:
        weights = ev_map_for_year(era.first_election_year)
        if len(weights) != 56 or sum(weights.values()) != 538:
            raise ElectoralVoteError(
                f"Bootstrap era {era.census_year} must contain 56 units totaling 538",
            )
        for unit, weight in weights.items():
            positive_integer(weight, context=f"Bootstrap weight for {unit}")
        seeds.append(AllocationSeed(era, weights))
    return seeds


def _existing_eras(conn: sqlite3.Connection) -> list[ElectoralVoteEra]:
    if not schema_object_exists(conn, "table", "us_electoral_vote_eras"):
        return []
    return validate_eras(
        (row[0], row[1], row[2])
        for row in conn.execute(
            "SELECT census_year, first_election_year, last_election_year "
            "FROM us_electoral_vote_eras",
        )
    )


def _existing_allocations(conn: sqlite3.Connection) -> set[tuple[int, str]]:
    if not schema_object_exists(conn, "table", "us_electoral_vote_allocations"):
        return set()
    keys = set()
    for era, unit, weight in conn.execute(
        "SELECT era_year, unit_name, electoral_votes "
        "FROM us_electoral_vote_allocations",
    ):
        era_year = positive_integer(era, context="Allocation era")
        if not isinstance(unit, str) or not unit:
            raise ElectoralVoteError(f"Invalid allocation unit in era {era_year}")
        positive_integer(weight, context=f"Stored weight for {unit}, era {era_year}")
        keys.add((era_year, unit))
    return keys


def _legacy_groups(
    conn: sqlite3.Connection, *, has_target: bool,
) -> list[tuple[int, int, str | None, str | None]]:
    condition = " AND target_election_year IS NULL" if has_target else ""
    return [
        (row[0], row[1], row[2], row[3])
        for row in conn.execute(
            "SELECT map_id, COUNT(*), MIN(election_date), MAX(election_date) "
            "FROM elections WHERE type = 'us_presidential_model'"
            f"{condition} GROUP BY map_id ORDER BY map_id",
        )
    ]


def _era_rows(
    eras: Iterable[ElectoralVoteEra],
) -> Iterable[tuple[int, int, int]]:
    return (
        (era.census_year, era.first_election_year, era.last_election_year)
        for era in eras
    )


def _migrate(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    legacy_forecast_target_year: int | None,
) -> list[str]:
    if not schema_object_exists(conn, "table", "elections"):
        raise ElectoralVoteError("Database has no elections table; initialize it first")
    seeds = bootstrap_seeds()
    existing = _existing_eras(conn)
    existing_years = {era.census_year for era in existing}
    missing_eras = [
        seed.era for seed in seeds if seed.era.census_year not in existing_years
    ]
    merged = validate_eras(_era_rows([*existing, *missing_eras]))
    allocations = _existing_allocations(conn)
    merged_years = {era.census_year for era in merged}
    if any(era not in merged_years for era, _ in allocations):
        raise ElectoralVoteError("Stored allocations reference an unknown era")
    missing_allocations = [
        (seed.era.census_year, unit, weight)
        for seed in seeds
        for unit, weight in seed.weights.items()
        if (seed.era.census_year, unit) not in allocations
    ]
    has_target = column_exists(conn, "elections", "target_election_year")
    groups = _legacy_groups(conn, has_target=has_target)
    legacy_count = sum(count for _, count, _, _ in groups)
    has_maps = schema_object_exists(conn, "table", "maps")
    legacy_map_count = (
        int(conn.execute(
            "SELECT COUNT(*) FROM maps WHERE parliament = 'us_president'"
        ).fetchone()[0])
        if has_maps
        else 0
    )
    if legacy_forecast_target_year is not None:
        select_era(merged, legacy_forecast_target_year)
    if legacy_count and legacy_forecast_target_year is None and not dry_run:
        raise ElectoralVoteError(
            f"{legacy_count} legacy presidential forecasts need an explicit "
            "--legacy-forecast-target-year; inspect --dry-run before applying",
        )
    prefix = "Would" if dry_run else "Will"
    lines = [
        f"{prefix} add target_election_year column: {not has_target}",
        f"{prefix} insert {len(missing_eras)} eras and "
        f"{len(missing_allocations)} allocation rows; existing values are preserved.",
        f"Legacy presidential forecasts with NULL targets: {legacy_count}",
        f"{prefix} normalize {legacy_map_count} presidential map labels "
        "from us_president to us_presidential.",
    ]
    lines.extend(
        f"  map_id={map_id}: {count} runs, dates {first or 'unknown'} "
        f"through {last or 'unknown'}"
        for map_id, count, first, last in groups
    )
    if legacy_count:
        lines.append(
            f"{prefix} set legacy targets to {legacy_forecast_target_year}"
            if legacy_forecast_target_year is not None
            else "Applying requires --legacy-forecast-target-year; "
            "classify mixed-cycle rows before applying a single target.",
        )
    if dry_run:
        return lines

    if not has_target:
        conn.execute("ALTER TABLE elections ADD COLUMN target_election_year INTEGER")
    conn.execute(ERAS_DDL)
    conn.execute(ALLOCATIONS_DDL)
    if legacy_map_count:
        conn.execute(
            "UPDATE maps SET parliament = 'us_presidential' "
            "WHERE parliament = 'us_president'"
        )
    conn.executemany(
        "INSERT INTO us_electoral_vote_eras "
        "(census_year, first_election_year, last_election_year) VALUES (?, ?, ?)",
        _era_rows(missing_eras),
    )
    conn.executemany(
        "INSERT INTO us_electoral_vote_allocations "
        "(era_year, unit_name, electoral_votes) VALUES (?, ?, ?)",
        missing_allocations,
    )
    if legacy_count:
        conn.execute(
            "UPDATE elections SET target_election_year = ? "
            "WHERE type = 'us_presidential_model' AND target_election_year IS NULL",
            (legacy_forecast_target_year,),
        )
    return lines


def migrate(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    legacy_forecast_target_year: int | None = None,
) -> list[str]:
    """Report or apply bootstrap and backfill; applying owns one transaction."""
    if dry_run:
        return _migrate(
            conn, dry_run=True, legacy_forecast_target_year=legacy_forecast_target_year,
        )
    if conn.in_transaction:
        raise ElectoralVoteError(
            "Migration requires a connection without a transaction",
        )
    conn.execute("BEGIN")
    with conn:
        return _migrate(
            conn,
            dry_run=False,
            legacy_forecast_target_year=legacy_forecast_target_year,
        )


def main() -> int:
    """Run against the configured existing file, never create a database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--legacy-forecast-target-year", type=int)
    args = parser.parse_args()
    with closing(open_database(
        DatabaseConfig.from_env().database_path, read_only=args.dry_run,
    )) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for line in migrate(
            conn,
            dry_run=args.dry_run,
            legacy_forecast_target_year=args.legacy_forecast_target_year,
        ):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
