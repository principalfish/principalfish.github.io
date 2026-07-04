"""
Database wrapper – provides a session and convenience methods for
reading / writing election map data.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, Generator, Sequence, cast

from sqlalchemy import create_engine, delete, event, func, select
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
    Vote,
)


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
    ) -> Poll:
        """Insert a new Poll row and return it.

        Args:
            pollster_id: Primary key of the conducting Pollster.
            map_id: Primary key of the Map this poll covers.
            fieldwork_start: First date of fieldwork (inclusive).
            fieldwork_end: Last date of fieldwork (inclusive).
            sample_size: Optional number of respondents.
            source_url: Optional URL of the published poll tables.

        Returns:
            The newly created Poll instance.
        """
        with self.session() as s:
            poll = Poll(
                pollster_id=pollster_id,
                map_id=map_id,
                fieldwork_start=fieldwork_start,
                fieldwork_end=fieldwork_end,
                sample_size=sample_size,
                source_url=source_url,
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
    ) -> PollRow:
        """Insert a new PollRow and return it.

        Args:
            poll_id: Primary key of the parent Poll.
            party_id: Primary key of the Party this row records a figure for.
            percentage: Vote-share percentage for the party (0–100).
            region_id: Optional primary key of the Region if this is a
                sub-national breakdown row.

        Returns:
            The newly created PollRow instance.
        """
        with self.session() as s:
            row = PollRow(
                poll_id=poll_id,
                party_id=party_id,
                percentage=percentage,
                region_id=region_id,
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
                (poll_id, party_id, percentage, region_id).

        Returns:
            Number of rows inserted.
        """
        with self.session() as s:
            objs = [PollRow(**r) for r in rows]
            s.add_all(objs)
            s.flush()
            return len(objs)
