"""
Database wrapper – provides a session and convenience methods for
reading / writing election map data.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Generator, Sequence

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon, shape
from sqlalchemy import create_engine, func, select
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
    """Thin wrapper around SQLAlchemy for the election-maps schema."""

    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self.config = config or DatabaseConfig.from_env()
        self.engine = create_engine(self.config.url, echo=False)
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
        with self.session() as s:
            party = Party(
                name=name,
                short_name=short_name,
                colour=colour,
            )
            s.add(party)
            s.flush()
            party_id = party.id
        return self.get_party(party_id)

    def get_party(self, party_id: int) -> Party | None:
        with self.session() as s:
            return s.get(Party, party_id)

    def get_party_by_name(self, name: str) -> Party | None:
        with self.session() as s:
            return s.execute(select(Party).where(Party.name == name)).scalar_one_or_none()

    def get_all_parties(self) -> Sequence[Party]:
        with self.session() as s:
            return s.execute(select(Party).order_by(Party.name)).scalars().all()

    # ── maps ──────────────────────────────────────────────────────────────

    def add_map(self, name: str) -> Map:
        with self.session() as s:
            m = Map(name=name)
            s.add(m)
            s.flush()
            map_id = m.id
        return self.get_map(map_id)

    def get_map(self, map_id: int) -> Map | None:
        with self.session() as s:
            return s.get(Map, map_id)

    def get_map_by_name(self, name: str) -> Map | None:
        with self.session() as s:
            return s.execute(select(Map).where(Map.name == name)).scalar_one_or_none()

    def get_all_maps(self) -> Sequence[Map]:
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
        return self.get_region(region_id)

    def get_region(self, region_id: int) -> Region | None:
        with self.session() as s:
            return s.get(Region, region_id)

    def get_regions_for_map(self, map_id: int) -> Sequence[Region]:
        with self.session() as s:
            return (
                s.execute(
                    select(Region).where(Region.map_id == map_id).order_by(Region.name)
                )
                .scalars()
                .all()
            )

    # ── seats ─────────────────────────────────────────────────────────────

    def add_seat(
        self,
        map_id: int,
        seat_name: str,
        *,
        region_id: int | None = None,
        electorate: int | None = None,
        geometry: MultiPolygon | dict | None = None,
    ) -> Seat:
        geom_col = None
        if geometry is not None:
            if isinstance(geometry, dict):
                geometry = shape(geometry)
            geom_col = from_shape(geometry, srid=4326)

        with self.session() as s:
            seat = Seat(
                map_id=map_id,
                seat_name=seat_name,
                region_id=region_id,
                electorate=electorate,
                geometry=geom_col,
            )
            s.add(seat)
            s.flush()
            seat_id = seat.id
        return self.get_seat(seat_id)

    def get_seat(self, seat_id: int) -> Seat | None:
        with self.session() as s:
            return s.get(Seat, seat_id)

    def get_seats_for_map(self, map_id: int) -> Sequence[Seat]:
        with self.session() as s:
            return (
                s.execute(
                    select(Seat).where(Seat.map_id == map_id).order_by(Seat.seat_name)
                )
                .scalars()
                .all()
            )

    def get_seat_geometry(self, seat_id: int) -> MultiPolygon | None:
        """Return the geometry of a seat as a Shapely MultiPolygon."""
        with self.session() as s:
            seat = s.get(Seat, seat_id)
            if seat is None or seat.geometry is None:
                return None
            return to_shape(seat.geometry)

    def set_seat_electorate(self, seat_id: int, electorate: int | None) -> Seat | None:
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
    ) -> Election:
        with self.session() as s:
            e = Election(map_id=map_id, year=year, name=name, type=election_type)
            s.add(e)
            s.flush()
            election_id = e.id
        return self.get_election(election_id)

    def get_election(self, election_id: int) -> Election | None:
        with self.session() as s:
            return s.get(Election, election_id)

    def get_election_by_name(self, name: str) -> Election | None:
        with self.session() as s:
            return s.execute(
                select(Election).where(Election.name == name)
            ).scalar_one_or_none()

    def get_elections_for_map(self, map_id: int) -> Sequence[Election]:
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
        return self.get_vote(vote_id)

    def get_vote(self, vote_id: int) -> Vote | None:
        with self.session() as s:
            return s.get(Vote, vote_id)

    def get_votes_for_seat_election(
        self, election_id: int, seat_id: int
    ) -> Sequence[Vote]:
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
        votes: list[dict],
    ) -> int:
        """Insert many votes at once. Each dict should have keys matching
        Vote columns (election_id, seat_id, party_id, …). Returns count."""
        with self.session() as s:
            objs = [Vote(**v) for v in votes]
            s.add_all(objs)
            s.flush()
            return len(objs)

    def bulk_add_seats(
        self,
        seats: list[dict],
    ) -> int:
        """Insert many seats at once. Geometry values can be GeoJSON dicts
        or Shapely objects. Returns count."""
        with self.session() as s:
            for seat_data in seats:
                geom = seat_data.pop("geometry", None)
                if geom is not None:
                    if isinstance(geom, dict):
                        geom = shape(geom)
                    seat_data["geometry"] = from_shape(geom, srid=4326)
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
        return self.get_pollster(pid)

    def get_pollster(self, pollster_id: int) -> Pollster | None:
        with self.session() as s:
            return s.get(Pollster, pollster_id)

    def get_pollster_by_identifier(self, identifier: str) -> Pollster | None:
        with self.session() as s:
            return s.execute(
                select(Pollster).where(Pollster.identifier == identifier)
            ).scalar_one_or_none()

    def get_all_pollsters(self) -> Sequence[Pollster]:
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
        return self.get_poll(poll_id)

    def get_poll(self, poll_id: int) -> Poll | None:
        with self.session() as s:
            return s.get(Poll, poll_id)

    def get_polls_for_map(self, map_id: int) -> Sequence[Poll]:
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
        return self.get_poll_row(row_id)

    def get_poll_row(self, row_id: int) -> PollRow | None:
        with self.session() as s:
            return s.get(PollRow, row_id)

    def get_rows_for_poll(self, poll_id: int) -> Sequence[PollRow]:
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

    def bulk_add_poll_rows(self, rows: list[dict]) -> int:
        """Insert many poll rows at once. Returns count."""
        with self.session() as s:
            objs = [PollRow(**r) for r in rows]
            s.add_all(objs)
            s.flush()
            return len(objs)
