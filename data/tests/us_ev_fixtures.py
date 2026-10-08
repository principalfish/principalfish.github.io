"""Explicit presidential allocations for consumer tests, never an autouse seed."""

from db import Database
from models import Election, USElectoralVoteAllocation, USElectoralVoteEra
from scripts.migrate_us_electoral_votes import bootstrap_seeds


def seed_allocations(
    db: Database,
    weights: dict[str, int],
    *,
    census_year: int = 2020,
    first_election_year: int = 2024,
    last_election_year: int = 2028,
) -> None:
    with db.session() as session:
        session.add(
            USElectoralVoteEra(
                census_year=census_year,
                first_election_year=first_election_year,
                last_election_year=last_election_year,
            )
        )
        session.flush()
        session.add_all(
            USElectoralVoteAllocation(
                era_year=census_year, unit_name=name, electoral_votes=weight
            )
            for name, weight in weights.items()
        )


def seed_canonical_allocations(db: Database) -> None:
    for seed in bootstrap_seeds():
        seed_allocations(
            db,
            seed.weights,
            census_year=seed.era.census_year,
            first_election_year=seed.era.first_election_year,
            last_election_year=seed.era.last_election_year,
        )


def set_model_target(db: Database, election_id: int, target: int = 2028) -> None:
    with db.session() as session:
        election = session.get(Election, election_id)
        assert election is not None
        election.target_election_year = target
