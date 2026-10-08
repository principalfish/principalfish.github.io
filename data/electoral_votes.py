"""Read presidential allocations from the caller's database transaction."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from models import ElectionType, USElectoralVoteAllocation, USElectoralVoteEra


class ElectoralVoteError(ValueError):
    """Missing or invalid presidential allocation data."""


@dataclass(frozen=True, slots=True)
class ElectoralVoteEra:
    """Validated database era with inclusive election-year bounds."""

    census_year: int
    first_election_year: int
    last_election_year: int


def positive_integer(value: object, *, context: str) -> int:
    """Reject coercible strings, fractions, booleans and non-positive values."""
    if type(value) is not int or value <= 0:
        raise ElectoralVoteError(f"{context} must be a positive integer; got {value!r}")
    return value


def validate_eras(
    rows: Iterable[tuple[object, object, object]],
) -> list[ElectoralVoteEra]:
    """Validate all intervals, including ambiguity outside a requested year."""
    eras = []
    seen: set[int] = set()
    for census_year, first, last in rows:
        era = ElectoralVoteEra(
            positive_integer(census_year, context="Census year"),
            positive_integer(first, context=f"Era {census_year} first election year"),
            positive_integer(last, context=f"Era {census_year} last election year"),
        )
        if era.census_year in seen or era.first_election_year > era.last_election_year:
            raise ElectoralVoteError(f"Invalid electoral-vote era {era.census_year}")
        seen.add(era.census_year)
        eras.append(era)
    eras.sort(key=lambda era: era.first_election_year)
    for previous, current in zip(eras, eras[1:]):
        if current.first_election_year <= previous.last_election_year:
            raise ElectoralVoteError(
                f"Overlapping electoral-vote eras {previous.census_year} "
                f"and {current.census_year}",
            )
    return eras


def select_era(eras: Iterable[ElectoralVoteEra], year: int) -> ElectoralVoteEra:
    """Find the sole supported era for an allocation year."""
    positive_integer(year, context="Allocation year")
    matches = [
        era for era in eras
        if era.first_election_year <= year <= era.last_election_year
    ]
    if len(matches) != 1:
        raise ElectoralVoteError(
            f"Expected one electoral-vote era for year {year}; found {len(matches)}. "
            "Run scripts/migrate_us_electoral_votes.py or add the required era.",
        )
    return matches[0]


def allocation_year(
    election_type: ElectionType | str,
    year: int,
    target_election_year: int | None = None,
) -> int:
    """Actual elections use their year; presidential forecasts require a target."""
    kind = (
        election_type.value if isinstance(election_type, ElectionType) else election_type
    )
    if kind == ElectionType.us_presidential.value:
        return positive_integer(year, context="Presidential election year")
    if kind == ElectionType.us_presidential_model.value:
        if target_election_year is None:
            raise ElectoralVoteError(
                "Presidential model is missing target_election_year; run "
                "scripts/migrate_us_electoral_votes.py with an explicit legacy target.",
            )
        return positive_integer(
            target_election_year, context="Presidential target year",
        )
    raise ElectoralVoteError(f"Election type {kind!r} has no presidential allocation")


def _weights(
    rows: Iterable[tuple[object, object]],
    required_units: Iterable[str],
    *,
    era: ElectoralVoteEra,
    year: int,
) -> dict[str, int]:
    available: dict[str, int] = {}
    for name, weight in rows:
        if not isinstance(name, str) or not name:
            raise ElectoralVoteError(
                f"Invalid tally-unit name in era {era.census_year}",
            )
        available[name] = positive_integer(
            weight, context=f"EV weight for {name!r} in era {era.census_year}",
        )
    required = set(required_units)
    missing = required - available.keys()
    if missing:
        raise ElectoralVoteError(
            f"Missing electoral-vote units for year {year}, era {era.census_year}: "
            f"{', '.join(sorted(missing))}",
        )
    return {name: available[name] for name in sorted(required)}


def get_electoral_votes(
    session: Session, year: int, required_units: Iterable[str],
) -> dict[str, int]:
    """Read validated weights without seeding, caching or opening a connection."""
    try:
        eras = validate_eras(
            (row[0], row[1], row[2])
            for row in session.execute(select(
                USElectoralVoteEra.census_year,
                USElectoralVoteEra.first_election_year,
                USElectoralVoteEra.last_election_year,
            ))
        )
        era = select_era(eras, year)
        rows = session.execute(select(
            USElectoralVoteAllocation.unit_name,
            USElectoralVoteAllocation.electoral_votes,
        ).where(USElectoralVoteAllocation.era_year == era.census_year))
        return _weights(
            ((row[0], row[1]) for row in rows), required_units, era=era, year=year,
        )
    except OperationalError as error:
        if not str(error.orig).startswith(("no such table:", "no such column:")):
            raise
        raise ElectoralVoteError(
            "Electoral-vote schema is unavailable; run "
            "scripts/migrate_us_electoral_votes.py.",
        ) from error


def get_electoral_votes_sqlite(
    conn: sqlite3.Connection, year: int, required_units: Iterable[str],
) -> dict[str, int]:
    """SQLite counterpart using the same validation and transaction as the caller."""
    try:
        eras = validate_eras(
            (row[0], row[1], row[2])
            for row in conn.execute(
                "SELECT census_year, first_election_year, last_election_year "
                "FROM us_electoral_vote_eras",
            )
        )
        era = select_era(eras, year)
        rows = conn.execute(
            "SELECT unit_name, electoral_votes FROM us_electoral_vote_allocations "
            "WHERE era_year = ?", (era.census_year,),
        )
        return _weights(
            ((row[0], row[1]) for row in rows), required_units, era=era, year=year,
        )
    except sqlite3.OperationalError as error:
        if not str(error).startswith(("no such table:", "no such column:")):
            raise
        raise ElectoralVoteError(
            "Electoral-vote schema is unavailable; run "
            "scripts/migrate_us_electoral_votes.py.",
        ) from error
