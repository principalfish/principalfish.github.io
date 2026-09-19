"""
Database wrapper – provides a session and convenience methods for
reading / writing election map data.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Generator, Literal, Sequence, cast

from sqlalchemy import (
    ColumnElement,
    create_engine,
    delete,
    event,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from config import DatabaseConfig
from models import (
    Base,
    Election,
    ElectionType,
    Map,
    Party,
    Poll,
    PollRow,
    Pollster,
    Region,
    Seat,
    TrackedMatchup,
    TrackedMatchupSource,
    Vote,
)

# Outcome of Database.set_tracked_matchup; see its docstring.
TrackedMatchupWrite = Literal["created", "updated", "unchanged", "kept_manual"]


@dataclass(frozen=True, slots=True)
class MatchupSummary:
    """Stored polls for one matchup within one race of a map.

    Attributes:
        seat_id: Seat the polls cover, or None for the map's national polls.
        matchup: The matchup label shared by the polls.
        poll_count: Number of polls stored for this seat and matchup.
        latest_fieldwork_end: Most recent fieldwork end date among them.
    """

    seat_id: int | None
    matchup: str
    poll_count: int
    latest_fieldwork_end: date


def ensure_elections_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Ensure the unified elections/votes tables exist (no-op on the main DB).

    Mirrors the schema in ``models.py``. On the live ``elections.db`` these
    tables already exist, so this is a no-op; it only materialises them for a
    fresh database (e.g. in tests).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS elections (
            id INTEGER PRIMARY KEY,
            map_id INTEGER NOT NULL,
            year INTEGER NOT NULL,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            parent_election_id INTEGER,
            election_date TEXT
        );
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY,
            election_id INTEGER NOT NULL,
            seat_id INTEGER NOT NULL,
            party_id INTEGER,
            candidate_name TEXT,
            vote_total REAL,
            elected INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_votes_election_id ON votes(election_id);
        CREATE INDEX IF NOT EXISTS idx_elections_name ON elections(name);
    """)
    conn.commit()


class Database:
    """Thin wrapper around SQLAlchemy for the electionmaps schema."""

    def __init__(self, config: DatabaseConfig | None = None) -> None:
        """Initialise the database connection from config.

        Args:
            config: Database configuration. If None, loaded from environment
                variables via DatabaseConfig.from_env().
        """
        self.config = config or DatabaseConfig.from_env()
        # check_same_thread=False so the Flask dev server's threads can share the
        # engine's pooled connections.
        self.engine = create_engine(
            self.config.url,
            echo=False,
            hide_parameters=True,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            """Enforce foreign keys and use WAL journaling on every connection."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        self._session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False
        )

    # ── lifecycle ─────────────────────────────────────────────────────────

    def create_tables(self) -> None:
        """Create all tables (idempotent)."""
        Base.metadata.create_all(self.engine)

    def drop_tables(self) -> None:
        """Drop all tables (destructive!)."""
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Context-managed session with automatic commit / rollback."""
        s = self._session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ── parties ───────────────────────────────────────────────────────────

    def add_party(
        self,
        name: str,
        *,
        short_name: str | None = None,
        colour: str | None = None,
    ) -> Party:
        """Insert a new Party row and return it.

        Args:
            name: Full party name.
            short_name: Optional abbreviated name.
            colour: Optional hex colour string (e.g. '#ff0000').

        Returns:
            The newly created Party instance.
        """
        with self.session() as s:
            party = Party(
                name=name,
                short_name=short_name,
                colour=colour,
            )
            s.add(party)
            s.flush()
            party_id = party.id
        result = self.get_party(party_id)
        assert result is not None
        return result

    def get_party(self, party_id: int) -> Party | None:
        """Return the Party with the given primary key, or None if not found.

        Args:
            party_id: Primary key of the Party row.

        Returns:
            Matching Party instance, or None.
        """
        with self.session() as s:
            return s.get(Party, party_id)

    def get_party_by_name(self, name: str) -> Party | None:
        """Return the Party whose name matches exactly, or None.

        Args:
            name: Exact party name to look up.

        Returns:
            Matching Party instance, or None.
        """
        with self.session() as s:
            return s.execute(select(Party).where(Party.name == name)).scalar_one_or_none()

    def get_all_parties(self) -> Sequence[Party]:
        """Return all Party rows ordered by name.

        Returns:
            Sequence of Party instances.
        """
        with self.session() as s:
            return s.execute(select(Party).order_by(Party.name)).scalars().all()

    # ── maps ──────────────────────────────────────────────────────────────

    def add_map(self, name: str, *, parliament: str = "westminster") -> Map:
        """Insert a new Map row and return it.

        Args:
            name: Map name (e.g. 'uk-constituencies-2024').
            parliament: Which parliament this map covers. One of
                ``"westminster"`` (default) or ``"holyrood"``.

        Returns:
            The newly created Map instance.
        """
        with self.session() as s:
            m = Map(name=name, parliament=parliament)
            s.add(m)
            s.flush()
            map_id = m.id
        result = self.get_map(map_id)
        assert result is not None
        return result

    def get_map(self, map_id: int) -> Map | None:
        """Return the Map with the given primary key, or None if not found.

        Args:
            map_id: Primary key of the Map row.

        Returns:
            Matching Map instance, or None.
        """
        with self.session() as s:
            return s.get(Map, map_id)

    def get_map_by_name(self, name: str) -> Map | None:
        """Return the Map whose name matches exactly, or None.

        Args:
            name: Exact map name to look up.

        Returns:
            Matching Map instance, or None.
        """
        with self.session() as s:
            return s.execute(select(Map).where(Map.name == name)).scalar_one_or_none()

    def get_all_maps(self) -> Sequence[Map]:
        """Return all Map rows ordered by name.

        Returns:
            Sequence of Map instances.
        """
        with self.session() as s:
            return s.execute(select(Map).order_by(Map.name)).scalars().all()

    # ── regions ───────────────────────────────────────────────────────────

    def add_region(
        self,
        map_id: int,
        name: str,
        *,
        parent_id: int | None = None,
        population: int | None = None,
    ) -> Region:
        """Insert a new Region row and return it.

        Args:
            map_id: Primary key of the parent Map.
            name: Region name.
            parent_id: Optional primary key of a parent Region for hierarchical
                grouping (e.g. country → county → constituency).
            population: Optional population count for the region.

        Returns:
            The newly created Region instance.
        """
        with self.session() as s:
            r = Region(
                map_id=map_id,
                name=name,
                parent_id=parent_id,
                population=population,
            )
            s.add(r)
            s.flush()
            region_id = r.id
        result = self.get_region(region_id)
        assert result is not None
        return result

    def get_region(self, region_id: int) -> Region | None:
        """Return the Region with the given primary key, or None if not found.

        Args:
            region_id: Primary key of the Region row.

        Returns:
            Matching Region instance, or None.
        """
        with self.session() as s:
            return s.get(Region, region_id)

    def get_regions_for_map(self, map_id: int) -> Sequence[Region]:
        """Return all Region rows for the given map, ordered by name.

        Args:
            map_id: Primary key of the Map.

        Returns:
            Sequence of Region instances.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(Region).where(Region.map_id == map_id).order_by(Region.name)
                )
                .scalars()
                .all()
            )

    def get_or_create_region(
        self,
        map_id: int,
        name: str,
        *,
        parent_id: int | None = None,
        population: int | None = None,
    ) -> Region:
        """Return the Region with the given map and name, creating it if absent.

        Args:
            map_id: Primary key of the parent Map.
            name: Name of the region.
            parent_id: Optional primary key of a parent Region.
            population: Optional population count for the region.

        Returns:
            The existing or newly created Region instance.
        """
        with self.session() as s:
            region = s.execute(
                select(Region).where(Region.map_id == map_id, Region.name == name)
            ).scalars().first()
            if region is not None:
                return region
        return self.add_region(
            map_id, name, parent_id=parent_id, population=population
        )

    # ── seats ─────────────────────────────────────────────────────────────

    def add_seat(
        self,
        map_id: int,
        seat_name: str,
        *,
        region_id: int | None = None,
        electorate: int | None = None,
        electoral_votes: int | None = None,
    ) -> Seat:
        """Insert a new Seat row and return it.

        Args:
            map_id: Primary key of the parent Map.
            seat_name: Name of the constituency or seat.
            region_id: Optional primary key of the Region the seat belongs to.
            electorate: Optional registered electorate count.
            electoral_votes: Optional number of US Electoral College votes
                (US states only; omit or pass None for UK constituencies).

        Returns:
            The newly created Seat instance.
        """
        with self.session() as s:
            seat = Seat(
                map_id=map_id,
                seat_name=seat_name,
                region_id=region_id,
                electorate=electorate,
                electoral_votes=electoral_votes,
            )
            s.add(seat)
            s.flush()
            seat_id = seat.id
        result = self.get_seat(seat_id)
        assert result is not None
        return result

    def get_seat(self, seat_id: int) -> Seat | None:
        """Return the Seat with the given primary key, or None if not found.

        Args:
            seat_id: Primary key of the Seat row.

        Returns:
            Matching Seat instance, or None.
        """
        with self.session() as s:
            return s.get(Seat, seat_id)

    def get_seats_for_map(self, map_id: int) -> Sequence[Seat]:
        """Return all Seat rows for the given map, ordered by seat name.

        Args:
            map_id: Primary key of the Map.

        Returns:
            Sequence of Seat instances.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(Seat).where(Seat.map_id == map_id).order_by(Seat.seat_name)
                )
                .scalars()
                .all()
            )

    def get_or_create_seat(
        self,
        map_id: int,
        seat_name: str,
        *,
        region_id: int | None = None,
        electorate: int | None = None,
        electoral_votes: int | None = None,
    ) -> Seat:
        """Return the Seat with the given map and name, creating it if absent.

        Args:
            map_id: Primary key of the parent Map.
            seat_name: Name of the constituency or seat.
            region_id: Optional primary key of the Region the seat belongs to.
            electorate: Optional registered electorate count.
            electoral_votes: Optional number of US Electoral College votes
                (US states only; omit or pass None for UK constituencies).

        Returns:
            The existing or newly created Seat instance.
        """
        with self.session() as s:
            seat = s.execute(
                select(Seat).where(
                    Seat.map_id == map_id, Seat.seat_name == seat_name
                )
            ).scalars().first()
            if seat is not None:
                return seat
        return self.add_seat(
            map_id,
            seat_name,
            region_id=region_id,
            electorate=electorate,
            electoral_votes=electoral_votes,
        )

    def set_seat_electorate(self, seat_id: int, electorate: int | None) -> Seat | None:
        """Update the electorate count for a seat.

        Args:
            seat_id: Primary key of the Seat row.
            electorate: New electorate value, or None to clear it.

        Returns:
            Updated Seat instance, or None if the seat does not exist.
        """
        with self.session() as s:
            seat = s.get(Seat, seat_id)
            if seat is None:
                return None
            seat.electorate = electorate
            s.flush()
            return seat

    # ── elections ──────────────────────────────────────────────────────────

    def add_election(
        self,
        map_id: int,
        year: int,
        name: str,
        election_type: ElectionType,
        *,
        parent_election_id: int | None = None,
        election_date: "date | None" = None,
    ) -> Election:
        """Insert a new Election row and return it.

        Args:
            map_id: Primary key of the parent Map.
            year: Calendar year the election took place.
            name: Unique election name (e.g. 'uk-ge-2024').
            election_type: Election type enum value.
            parent_election_id: Optional primary key of a parent Election, used
                for by-elections or run-off relationships.
            election_date: Optional exact date of the election.

        Returns:
            The newly created Election instance.
        """
        with self.session() as s:
            e = Election(
                map_id=map_id,
                year=year,
                name=name,
                type=election_type,
                parent_election_id=parent_election_id,
                election_date=election_date,
            )
            s.add(e)
            s.flush()
            election_id = e.id
        result = self.get_election(election_id)
        assert result is not None
        return result

    def get_election(self, election_id: int) -> Election | None:
        """Return the Election with the given primary key, or None if not found.

        Args:
            election_id: Primary key of the Election row.

        Returns:
            Matching Election instance, or None.
        """
        with self.session() as s:
            return s.get(Election, election_id)

    def get_election_by_name(self, name: str) -> Election | None:
        """Return the Election whose name matches exactly, or None.

        Args:
            name: Exact election name to look up (e.g. 'uk-ge-2024').

        Returns:
            Matching Election instance, or None.
        """
        with self.session() as s:
            return s.execute(
                select(Election).where(Election.name == name)
            ).scalar_one_or_none()

    def get_elections_for_map(self, map_id: int) -> Sequence[Election]:
        """Return all Election rows for the given map, ordered by year.

        Args:
            map_id: Primary key of the Map.

        Returns:
            Sequence of Election instances.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(Election)
                    .where(Election.map_id == map_id)
                    .order_by(Election.year)
                )
                .scalars()
                .all()
            )

    # ── votes ─────────────────────────────────────────────────────────────

    def add_vote(
        self,
        election_id: int,
        seat_id: int,
        *,
        party_id: int | None = None,
        candidate_name: str | None = None,
        vote_total: float | None = None,
        elected: bool = False,
    ) -> Vote:
        """Insert a new Vote row and return it.

        Args:
            election_id: Primary key of the parent Election.
            seat_id: Primary key of the Seat this vote record belongs to.
            party_id: Optional primary key of the Party for this candidate.
            candidate_name: Optional name of the candidate.
            vote_total: Optional raw vote count or share.
            elected: Whether this candidate was elected. Defaults to False.

        Returns:
            The newly created Vote instance.
        """
        with self.session() as s:
            v = Vote(
                election_id=election_id,
                seat_id=seat_id,
                party_id=party_id,
                candidate_name=candidate_name,
                vote_total=vote_total,
                elected=elected,
            )
            s.add(v)
            s.flush()
            vote_id = v.id
        result = self.get_vote(vote_id)
        assert result is not None
        return result

    def get_vote(self, vote_id: int) -> Vote | None:
        """Return the Vote with the given primary key, or None if not found.

        Args:
            vote_id: Primary key of the Vote row.

        Returns:
            Matching Vote instance, or None.
        """
        with self.session() as s:
            return s.get(Vote, vote_id)

    def get_votes_for_seat_election(
        self, election_id: int, seat_id: int
    ) -> Sequence[Vote]:
        """Return all Vote rows for a seat in a given election.

        Results are ordered by vote total descending, with nulls last.

        Args:
            election_id: Primary key of the Election.
            seat_id: Primary key of the Seat.

        Returns:
            Sequence of Vote instances, highest vote total first.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(Vote)
                    .where(Vote.election_id == election_id, Vote.seat_id == seat_id)
                    .order_by(Vote.vote_total.desc().nullslast())
                )
                .scalars()
                .all()
            )

    def get_votes_for_election(self, election_id: int) -> Sequence[Vote]:
        """Return all Vote rows for an election across all seats.

        Results are ordered by seat ID then vote total descending within each
        seat, with nulls last.

        Args:
            election_id: Primary key of the Election.

        Returns:
            Sequence of Vote instances grouped by seat, highest vote total
            first within each seat.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(Vote)
                    .where(Vote.election_id == election_id)
                    .order_by(Vote.seat_id, Vote.vote_total.desc().nullslast())
                )
                .scalars()
                .all()
            )

    def get_turnout_for_seat_election(self, election_id: int, seat_id: int) -> float | None:
        """Return the total votes cast in a seat for a given election.

        Sums all Vote.vote_total values for the seat/election combination.

        Args:
            election_id: Primary key of the Election.
            seat_id: Primary key of the Seat.

        Returns:
            Sum of vote totals as a float, or None if no votes are recorded.
        """
        with self.session() as s:
            turnout = s.execute(
                select(func.sum(Vote.vote_total)).where(
                    Vote.election_id == election_id,
                    Vote.seat_id == seat_id,
                )
            ).scalar_one()
            if turnout is None:
                return None
            return float(turnout)

    def get_winner_for_seat(
        self, election_id: int, seat_id: int
    ) -> Vote | None:
        """Return the elected candidate for a seat, if any."""
        with self.session() as s:
            return s.execute(
                select(Vote).where(
                    Vote.election_id == election_id,
                    Vote.seat_id == seat_id,
                    Vote.elected == True,  # noqa: E712
                )
            ).scalar_one_or_none()

    # ── bulk helpers ──────────────────────────────────────────────────────

    def bulk_add_votes(
        self,
        votes: list[dict[str, Any]],
    ) -> int:
        """Insert many Vote rows in a single session.

        Args:
            votes: List of dicts with keys matching Vote column names
                (election_id, seat_id, party_id, candidate_name, vote_total,
                elected).

        Returns:
            Number of rows inserted.
        """
        with self.session() as s:
            objs = [Vote(**v) for v in votes]
            s.add_all(objs)
            s.flush()
            return len(objs)

    def clear_votes_for_election(self, election_id: int) -> int:
        """Delete all Vote rows for a single election.

        Removes only the Vote rows for the given election; the Election row
        itself and its ``parent_election_id`` links are left intact.

        Args:
            election_id: Primary key of the Election whose votes to delete.

        Returns:
            Number of Vote rows deleted.
        """
        with self.session() as s:
            result = s.execute(
                delete(Vote).where(Vote.election_id == election_id)
            )
            return cast("CursorResult[Any]", result).rowcount

    def bulk_add_seats(
        self,
        seats: list[dict[str, Any]],
    ) -> int:
        """Insert many Seat rows in a single session.

        Args:
            seats: List of dicts with keys matching Seat column names. The
                optional 'electoral_votes' value is an integer Electoral
                College vote count (US states only).

        Returns:
            Number of rows inserted.
        """
        with self.session() as s:
            for seat_data in seats:
                s.add(Seat(**seat_data))
            s.flush()
            return len(seats)

    # ── pollsters ─────────────────────────────────────────────────────────

    def add_pollster(
        self,
        name: str,
        identifier: str,
        *,
        weight: float | None = 1.0,
        regions_mapping: str | None = None,
    ) -> Pollster:
        """Insert a new Pollster row and return it.

        Args:
            name: Display name of the polling organisation.
            identifier: Unique slug used to identify the pollster in imports
                (e.g. 'yougov').
            weight: Weighting factor applied to this pollster's polls when
                computing averages. Defaults to 1.0.
            regions_mapping: Optional JSON string mapping region names used by
                this pollster to canonical region identifiers.

        Returns:
            The newly created Pollster instance.
        """
        with self.session() as s:
            p = Pollster(
                name=name,
                identifier=identifier,
                weight=weight,
                regions_mapping=regions_mapping,
            )
            s.add(p)
            s.flush()
            pid = p.id
        result = self.get_pollster(pid)
        assert result is not None
        return result

    def get_pollster(self, pollster_id: int) -> Pollster | None:
        """Return the Pollster with the given primary key, or None if not found.

        Args:
            pollster_id: Primary key of the Pollster row.

        Returns:
            Matching Pollster instance, or None.
        """
        with self.session() as s:
            return s.get(Pollster, pollster_id)

    def get_pollster_by_identifier(self, identifier: str) -> Pollster | None:
        """Return the Pollster whose identifier matches exactly, or None.

        Args:
            identifier: Unique pollster slug (e.g. 'yougov').

        Returns:
            Matching Pollster instance, or None.
        """
        with self.session() as s:
            return s.execute(
                select(Pollster).where(Pollster.identifier == identifier)
            ).scalar_one_or_none()

    def get_all_pollsters(self) -> Sequence[Pollster]:
        """Return all Pollster rows ordered by name.

        Returns:
            Sequence of Pollster instances.
        """
        with self.session() as s:
            return s.execute(select(Pollster).order_by(Pollster.name)).scalars().all()

    # ── polls ─────────────────────────────────────────────────────────────

    def add_poll(
        self,
        pollster_id: int,
        map_id: int,
        fieldwork_start: "date",
        fieldwork_end: "date",
        *,
        sample_size: int | None = None,
        source_url: str | None = None,
        matchup: str | None = None,
        seat_id: int | None = None,
    ) -> Poll:
        """Insert a new Poll row and return it.

        Args:
            pollster_id: Primary key of the conducting Pollster.
            map_id: Primary key of the Map this poll covers.
            fieldwork_start: First date of fieldwork (inclusive).
            fieldwork_end: Last date of fieldwork (inclusive).
            sample_size: Optional number of respondents.
            source_url: Optional URL of the published poll tables.
            matchup: Optional candidate-pairing label (e.g.
                'Vance (R) vs Newsom (D)'); None for party-only polls.
            seat_id: Optional primary key of the Seat a state or district poll
                covers; None for a national poll.

        Returns:
            The newly created Poll instance.

        Raises:
            ValueError: If seat_id is given and names no seat, or names a seat
                on a different map.
        """
        with self.session() as s:
            if seat_id is not None:
                self._require_seat_on_map(s, map_id, seat_id)
            poll = Poll(
                pollster_id=pollster_id,
                map_id=map_id,
                fieldwork_start=fieldwork_start,
                fieldwork_end=fieldwork_end,
                sample_size=sample_size,
                source_url=source_url,
                matchup=matchup,
                seat_id=seat_id,
            )
            s.add(poll)
            s.flush()
            poll_id = poll.id
        result = self.get_poll(poll_id)
        assert result is not None
        return result

    def get_poll(self, poll_id: int) -> Poll | None:
        """Return the Poll with the given primary key, or None if not found.

        Args:
            poll_id: Primary key of the Poll row.

        Returns:
            Matching Poll instance, or None.
        """
        with self.session() as s:
            return s.get(Poll, poll_id)

    def get_polls_for_map(self, map_id: int) -> Sequence[Poll]:
        """Return all Poll rows for the given map, most recent first.

        Results are ordered by fieldwork end date descending.

        Args:
            map_id: Primary key of the Map.

        Returns:
            Sequence of Poll instances.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(Poll)
                    .where(Poll.map_id == map_id)
                    .order_by(Poll.fieldwork_end.desc())
                )
                .scalars()
                .all()
            )

    def get_polls_by_pollster(self, pollster_id: int) -> Sequence[Poll]:
        """Return all Poll rows for the given pollster, most recent first.

        Results are ordered by fieldwork end date descending.

        Args:
            pollster_id: Primary key of the Pollster.

        Returns:
            Sequence of Poll instances.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(Poll)
                    .where(Poll.pollster_id == pollster_id)
                    .order_by(Poll.fieldwork_end.desc())
                )
                .scalars()
                .all()
            )

    # ── poll rows ─────────────────────────────────────────────────────────

    def add_poll_row(
        self,
        poll_id: int,
        party_id: int,
        percentage: float,
        *,
        region_id: int | None = None,
        candidate_name: str | None = None,
    ) -> PollRow:
        """Insert a new PollRow and return it.

        Args:
            poll_id: Primary key of the parent Poll.
            party_id: Primary key of the Party this row records a figure for.
            percentage: Vote-share percentage for the party (0–100).
            region_id: Optional primary key of the Region if this is a
                sub-national breakdown row.
            candidate_name: Optional name of the candidate the figure is for
                (US candidate polls).

        Returns:
            The newly created PollRow instance.
        """
        with self.session() as s:
            row = PollRow(
                poll_id=poll_id,
                party_id=party_id,
                percentage=percentage,
                region_id=region_id,
                candidate_name=candidate_name,
            )
            s.add(row)
            s.flush()
            row_id = row.id
        result = self.get_poll_row(row_id)
        assert result is not None
        return result

    def get_poll_row(self, row_id: int) -> PollRow | None:
        """Return the PollRow with the given primary key, or None if not found.

        Args:
            row_id: Primary key of the PollRow.

        Returns:
            Matching PollRow instance, or None.
        """
        with self.session() as s:
            return s.get(PollRow, row_id)

    def get_rows_for_poll(self, poll_id: int) -> Sequence[PollRow]:
        """Return all PollRow rows for the given poll, ordered by percentage descending.

        Args:
            poll_id: Primary key of the Poll.

        Returns:
            Sequence of PollRow instances, highest percentage first.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(PollRow)
                    .where(PollRow.poll_id == poll_id)
                    .order_by(PollRow.percentage.desc())
                )
                .scalars()
                .all()
            )

    def bulk_add_poll_rows(self, rows: list[dict[str, Any]]) -> int:
        """Insert many PollRow rows in a single session.

        Args:
            rows: List of dicts with keys matching PollRow column names
                (poll_id, party_id, percentage, and the optional region_id
                and candidate_name).

        Returns:
            Number of rows inserted.
        """
        with self.session() as s:
            objs = [PollRow(**r) for r in rows]
            s.add_all(objs)
            s.flush()
            return len(objs)

    # ── poll lookups by scope ─────────────────────────────────────────────

    def get_poll_keys_for_map(
        self,
        map_id: int,
        identifiers: set[str],
    ) -> set[tuple[str, date, date, str | None, int | None]]:
        """Return the identity of every poll stored on a map for these pollsters.

        Identity is ``(pollster identifier, fieldwork start, fieldwork end,
        matchup, seat id)``, so the same pollster and dates count as a
        different poll when the matchup or the seat differs.

        Args:
            map_id: Only polls on this map are returned.
            identifiers: Pollster slugs to look up. Slugs with no pollster row
                simply match nothing.

        Returns:
            Set of ``(identifier, fieldwork_start, fieldwork_end, matchup,
            seat_id)`` tuples; empty when ``identifiers`` is empty.
        """
        if not identifiers:
            return set()
        with self.session() as s:
            rows = s.execute(
                select(
                    Pollster.identifier,
                    Poll.fieldwork_start,
                    Poll.fieldwork_end,
                    Poll.matchup,
                    Poll.seat_id,
                )
                .join(Pollster, Poll.pollster_id == Pollster.id)
                .where(Poll.map_id == map_id, Pollster.identifier.in_(identifiers))
            ).tuples()
            return set(rows)

    def get_latest_poll_end_by_scope(
        self, map_id: int
    ) -> dict[tuple[int | None, str | None], date]:
        """Return the latest fieldwork end date per (seat, matchup) on a map.

        Args:
            map_id: Primary key of the Map.

        Returns:
            Mapping of ``(seat_id, matchup)`` to the most recent
            ``fieldwork_end`` among that scope's polls. National polls have a
            ``None`` seat id and party-only polls a ``None`` matchup. Scopes
            with no polls are absent.
        """
        with self.session() as s:
            rows = s.execute(
                select(Poll.seat_id, Poll.matchup, func.max(Poll.fieldwork_end))
                .where(Poll.map_id == map_id)
                .group_by(Poll.seat_id, Poll.matchup)
            ).tuples()
            return {(seat_id, matchup): latest for seat_id, matchup, latest in rows}

    def get_matchup_summaries(self, map_id: int) -> list[MatchupSummary]:
        """Summarise the stored polls of each matchup in each race of a map.

        Polls without a matchup are left out.

        Args:
            map_id: Primary key of the Map.

        Returns:
            One MatchupSummary per ``(seat_id, matchup)``, ordered by seat id
            (national first), then most polls first, then matchup label.
        """
        poll_count = func.count(Poll.id).label("poll_count")
        with self.session() as s:
            rows = s.execute(
                select(
                    Poll.seat_id,
                    Poll.matchup,
                    poll_count,
                    func.max(Poll.fieldwork_end),
                )
                .where(Poll.map_id == map_id, Poll.matchup.is_not(None))
                .group_by(Poll.seat_id, Poll.matchup)
                .order_by(
                    Poll.seat_id.asc().nullsfirst(),
                    poll_count.desc(),
                    Poll.matchup,
                )
            ).tuples()
            return [
                MatchupSummary(
                    seat_id=seat_id,
                    matchup=matchup,
                    poll_count=count,
                    latest_fieldwork_end=latest,
                )
                for seat_id, matchup, count, latest in rows
                if matchup is not None  # narrows the type; WHERE already drops NULL
            ]

    # ── tracked matchups ──────────────────────────────────────────────────

    def get_tracked_matchup(
        self, map_id: int, seat_id: int | None = None
    ) -> TrackedMatchup | None:
        """Return the tracked-matchup row for one race, or None if unset.

        Args:
            map_id: Primary key of the Map.
            seat_id: Primary key of the race's Seat, or None for the map's
                national race.

        Returns:
            Matching TrackedMatchup instance, or None.
        """
        with self.session() as s:
            return self._find_tracked_matchup(s, map_id, seat_id)

    def get_tracked_matchups_for_map(self, map_id: int) -> Sequence[TrackedMatchup]:
        """Return every tracked-matchup row on a map, national race first.

        Args:
            map_id: Primary key of the Map.

        Returns:
            Sequence of TrackedMatchup instances ordered by seat id.
        """
        with self.session() as s:
            return (
                s.execute(
                    select(TrackedMatchup)
                    .where(TrackedMatchup.map_id == map_id)
                    .order_by(TrackedMatchup.seat_id.asc().nullsfirst())
                )
                .scalars()
                .all()
            )

    def set_tracked_matchup(
        self,
        map_id: int,
        seat_id: int | None,
        matchup: str | None,
        *,
        source: TrackedMatchupSource,
    ) -> TrackedMatchupWrite:
        """Record the matchup the model should follow for one race.

        An ``"auto"`` write (from the importer) always stores ``matchup`` as
        the row's ``auto_matchup``, but only changes the effective ``matchup``
        of a new row or a row whose source is still ``"auto"``: a manual
        override is never overwritten. A ``"manual"`` write sets ``matchup``
        and marks the row ``"manual"``, leaving ``auto_matchup`` as it was
        (None on a new row).

        Every decision about an existing row is made by the UPDATE statement
        itself, never by the preceding SELECT: pysqlite defers ``BEGIN`` to the
        first write, so that SELECT runs outside the transaction and an
        override committed by another connection in between would otherwise be
        silently overwritten. The insert path is guarded instead by the unique
        scope index, so a race there raises ``IntegrityError`` rather than
        storing a second row for the same race; callers that may collide
        should retry.

        Args:
            map_id: Primary key of the Map.
            seat_id: Primary key of the race's Seat, or None for the map's
                national race.
            matchup: The matchup label to follow. None means the race's polls
                are ignored, which only a manual write may ask for.
            source: ``"auto"`` for the importer, ``"manual"`` for a user
                override.

        Returns:
            ``"created"`` if no row existed for the race; ``"kept_manual"`` for
            an auto write to a manual row (the override stands, although
            ``auto_matchup`` may have changed); ``"unchanged"`` if the row
            already held these values; otherwise ``"updated"``.

        Raises:
            ValueError: If source is ``"auto"`` and matchup is None, which
                would silence a race the importer has no way to judge; if
                seat_id names no seat, or a seat on a different map.
            IntegrityError: If another connection created the race's row
                between this call's lookup and its insert.
        """
        if source == "auto" and matchup is None:
            raise ValueError(
                "an automatic write cannot clear a race's matchup;"
                " only a manual override may ignore a race"
            )
        with self.session() as s:
            if seat_id is not None:
                self._require_seat_on_map(s, map_id, seat_id)
            row = self._find_tracked_matchup(s, map_id, seat_id)
            if row is None:
                s.add(
                    TrackedMatchup(
                        map_id=map_id,
                        seat_id=seat_id,
                        matchup=matchup,
                        source=source,
                        auto_matchup=matchup if source == "auto" else None,
                    )
                )
                return "created"

            if source == "manual":
                # Writes unless the row is already a manual override holding
                # this very matchup, so rowcount alone tells the two apart.
                needs_write = or_(
                    TrackedMatchup.source != "manual",
                    TrackedMatchup.matchup.is_distinct_from(matchup),
                )
                applied = self._update_tracked_matchup(
                    s,
                    row.id,
                    needs_write,
                    matchup=matchup,
                    source="manual",
                )
                return "updated" if applied else "unchanged"

            auto_changed = self._update_tracked_matchup(
                s,
                row.id,
                TrackedMatchup.auto_matchup.is_distinct_from(matchup),
                auto_matchup=matchup,
            )
            matchup_changed = self._update_tracked_matchup(
                s,
                row.id,
                TrackedMatchup.source == "auto",
                TrackedMatchup.matchup.is_distinct_from(matchup),
                matchup=matchup,
            )
            if matchup_changed:
                return "updated"
            # The effective matchup did not move: either a manual override
            # blocked it or the row already held this value. Re-read the source
            # rather than trusting the lookup above.
            source_now = s.execute(
                select(TrackedMatchup.source).where(TrackedMatchup.id == row.id)
            ).scalar_one_or_none()
            if source_now == "manual":
                return "kept_manual"
            return "updated" if auto_changed else "unchanged"

    def clear_tracked_matchup_override(self, map_id: int, seat_id: int | None) -> bool:
        """Drop a race's manual override so it follows the importer again.

        Sets the row's ``matchup`` to its ``auto_matchup`` and its source to
        ``"auto"``. A row the importer never set therefore ends up with a
        None matchup until the next import sets one.

        One statement does the whole job, copying ``auto_matchup`` inside the
        UPDATE, so a value written by another connection since this call
        started cannot be rolled back to a stale one.

        Args:
            map_id: Primary key of the Map.
            seat_id: Primary key of the race's Seat, or None for the map's
                national race.

        Returns:
            True if the race has a tracked row (now following the importer),
            False if it has none and nothing was changed.
        """
        with self.session() as s:
            result = s.execute(
                update(TrackedMatchup)
                .where(
                    TrackedMatchup.map_id == map_id,
                    # IS rather than =, so a None seat matches the national row.
                    TrackedMatchup.seat_id.is_not_distinct_from(seat_id),
                )
                .values(matchup=TrackedMatchup.auto_matchup, source="auto"),
                # Nothing reads the ORM objects afterwards, and neither the
                # criteria nor the column-to-column SET is evaluatable in
                # Python, so skip the session-synchronising SELECT.
                execution_options={"synchronize_session": False},
            )
            return bool(cast("CursorResult[Any]", result).rowcount)

    def delete_tracked_matchup(self, map_id: int, seat_id: int | None) -> bool:
        """Delete the tracked-matchup row for one race.

        Args:
            map_id: Primary key of the Map.
            seat_id: Primary key of the race's Seat, or None for the map's
                national race.

        Returns:
            True if a row was deleted, False if the race had none.
        """
        with self.session() as s:
            row = self._find_tracked_matchup(s, map_id, seat_id)
            if row is None:
                return False
            s.delete(row)
            return True

    @staticmethod
    def _find_tracked_matchup(
        s: Session, map_id: int, seat_id: int | None
    ) -> TrackedMatchup | None:
        """Return one race's tracked-matchup row within an open session.

        Args:
            s: Open session to query with.
            map_id: Primary key of the Map.
            seat_id: Primary key of the race's Seat, or None for the map's
                national race.

        Returns:
            Matching TrackedMatchup instance, or None.
        """
        return s.execute(
            select(TrackedMatchup).where(
                TrackedMatchup.map_id == map_id,
                # IS rather than =, so a None seat matches the national row.
                TrackedMatchup.seat_id.is_not_distinct_from(seat_id),
            )
        ).scalar_one_or_none()

    @staticmethod
    def _update_tracked_matchup(
        s: Session,
        tracked_id: int,
        *conditions: ColumnElement[bool],
        **values: str | None,
    ) -> bool:
        """Conditionally update one tracked-matchup row, reporting whether it ran.

        Args:
            s: Open session to write with.
            tracked_id: Primary key of the TrackedMatchup row.
            *conditions: Extra criteria the row must still satisfy; they are
                evaluated by SQLite as part of the write, so the caller need
                not have read a consistent row first.
            **values: Columns to set.

        Returns:
            True if the row matched every condition and was written.
        """
        result = s.execute(
            update(TrackedMatchup)
            .where(TrackedMatchup.id == tracked_id, *conditions)
            .values(**values),
            # See clear_tracked_matchup_override for why synchronisation is off.
            execution_options={"synchronize_session": False},
        )
        return bool(cast("CursorResult[Any]", result).rowcount)

    @staticmethod
    def _require_seat_on_map(s: Session, map_id: int, seat_id: int) -> None:
        """Raise unless *seat_id* names a seat belonging to *map_id*.

        The foreign key only proves the seat exists; a seat from another map
        would be stored happily and then never be found by anything that looks
        the race up by map.

        Args:
            s: Open session to query with.
            map_id: Primary key of the Map the seat must belong to.
            seat_id: Primary key of the Seat to check.

        Raises:
            ValueError: If no such seat exists, or it belongs to another map.
        """
        owner_map_id = s.execute(
            select(Seat.map_id).where(Seat.id == seat_id)
        ).scalar_one_or_none()
        if owner_map_id is None:
            raise ValueError(f"seat {seat_id} does not exist")
        if owner_map_id != map_id:
            raise ValueError(
                f"seat {seat_id} belongs to map {owner_map_id}, not map {map_id}"
            )
