import enum
from datetime import date
from typing import Any, Optional, cast

from shapely import wkb as shapely_wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    types,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy ORM models in this project."""

    pass


class GeometryWKB(types.TypeDecorator[bytes]):
    """Store a Shapely geometry as plain WKB bytes in a BLOB column.

    Replaces the former PostGIS/geoalchemy2 ``Geometry`` type so the schema is
    portable to SQLite. The code never runs spatial SQL — geometries are only
    stored and loaded whole — so a WKB blob is sufficient. Bind values may be a
    Shapely geometry (stored as WKB); result values are loaded back into Shapely
    geometries. SRID 4326 is assumed by convention and not encoded in the blob.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> bytes | None:
        """Serialise a Shapely geometry (or raw WKB bytes) to WKB bytes."""
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, BaseGeometry):
            return cast(bytes, shapely_wkb.dumps(value))
        raise TypeError(f"Unsupported geometry value: {type(value)!r}")

    def process_result_value(self, value: Any, dialect: Any) -> BaseGeometry | None:
        """Load WKB bytes from the database back into a Shapely geometry."""
        if value is None:
            return None
        return shapely_wkb.loads(bytes(value))


# ── Enums ────────────────────────────────────────────────────────────────────


class ElectionType(enum.Enum):
    """Enumeration of election types stored in the elections table.

    Attributes:
        uk_general: A UK general election.
        by_election: A UK by-election for a single seat.
        model_run: A modelled/projected election result (e.g. UNS projection).
        model_uns: A uniform national swing model run.
        holyrood_uns: A Holyrood uniform national swing model run.
        holyrood_general: A Scottish Parliament (Holyrood) general election
            covering FPTP constituency seats.
        holyrood_list: Regional list seat allocations for a Scottish Parliament
            election, computed via d'Hondt from the regional list vote.
        us_house: A US House of Representatives election.
        us_senate: A US Senate election.
        us_presidential: A US presidential election.
        us_house_model: A modelled/projected US House of Representatives election.
        us_senate_model: A modelled/projected US Senate election.
        us_presidential_model: A modelled/projected US presidential election.
    """

    uk_general = "uk_general"
    by_election = "by_election"
    model_run = "model_run"
    model_uns = "model_uns"
    holyrood_uns = "holyrood_uns"
    holyrood_general = "holyrood_general"
    holyrood_list = "holyrood_list"
    us_house = "us_house"
    us_senate = "us_senate"
    us_presidential = "us_presidential"
    us_house_model = "us_house_model"
    us_senate_model = "us_senate_model"
    us_presidential_model = "us_presidential_model"


# ── Tables ───────────────────────────────────────────────────────────────────


class Party(Base):
    """ORM model for the ``parties`` table.

    Stores political parties referenced by votes and poll rows.

    Attributes:
        id: Auto-incrementing primary key.
        name: Full party name. Unique and non-nullable.
        short_name: Optional abbreviated party name (e.g. "Lab", "Con").
        colour: Optional hex colour string for the party (e.g. ``"#E4003B"``).
        votes: All ``Vote`` records associated with this party.
    """

    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    short_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    colour: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # hex e.g. "#E4003B"

    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="party")

    def __repr__(self) -> str:
        """Return a debug string representation of the Party."""
        return f"<Party {self.name}>"


class Map(Base):
    """ORM model for the ``maps`` table.

    A map groups a set of seats and regions under a named boundary configuration
    (e.g. a specific constituency boundary revision). Elections and polls are
    associated with a map to constrain which seats are in scope.

    Attributes:
        id: Auto-incrementing primary key.
        name: Unique human-readable map name.
        parliament: Which parliament this map covers. One of ``"westminster"``
            (UK Parliament) or ``"holyrood"`` (Scottish Parliament). Defaults
            to ``"westminster"``.
        regions: All ``Region`` records belonging to this map.
        seats: All ``Seat`` records belonging to this map.
        elections: All ``Election`` records associated with this map.
    """

    __tablename__ = "maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    parliament: Mapped[str] = mapped_column(String, nullable=False, server_default="westminster")

    regions: Mapped[list["Region"]] = relationship("Region", back_populates="map", cascade="all, delete-orphan")
    seats: Mapped[list["Seat"]] = relationship("Seat", back_populates="map", cascade="all, delete-orphan")
    elections: Mapped[list["Election"]] = relationship(
        "Election", back_populates="map", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        """Return a debug string representation of the Map."""
        return f"<Map {self.name}>"


class Region(Base):
    """ORM model for the ``regions`` table.

    Regions are named geographic subdivisions within a map (e.g. countries,
    English regions). They can be nested via ``parent_id`` to form a hierarchy,
    and are used to group seats and to attach sub-national poll rows.

    Attributes:
        id: Auto-incrementing primary key.
        map_id: Foreign key to the owning ``Map``.
        name: Region name (e.g. "Scotland", "North West").
        parent_id: Optional foreign key to a parent ``Region``, enabling
            hierarchical region structures.
        population: Optional population count for the region.
        map: The owning ``Map`` instance.
        parent: Optional parent ``Region`` instance.
        seats: All ``Seat`` records within this region.
    """

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("maps.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("regions.id"), nullable=True)
    population: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    map: Mapped["Map"] = relationship("Map", back_populates="regions")
    parent: Mapped[Optional["Region"]] = relationship("Region", remote_side="Region.id", backref="children")
    seats: Mapped[list["Seat"]] = relationship("Seat", back_populates="region")

    def __repr__(self) -> str:
        """Return a debug string representation of the Region."""
        return f"<Region {self.name}>"


class Seat(Base):
    """ORM model for the ``seats`` table.

    Represents a single parliamentary constituency within a map. Seats hold
    optional geographic geometry for map rendering, and are linked to votes
    within an election.

    Attributes:
        id: Auto-incrementing primary key.
        map_id: Foreign key to the owning ``Map``.
        seat_name: Name of the constituency.
        region_id: Optional foreign key to the ``Region`` this seat belongs to.
        electorate: Optional registered electorate size.
        electoral_votes: Optional number of US Electoral College votes for this
            seat (US states only; ``None`` for UK constituencies).
        geometry: Optional MULTIPOLYGON geometry (SRID 4326) for the seat boundary.
        map: The owning ``Map`` instance.
        region: The ``Region`` this seat belongs to, if any.
        votes: All ``Vote`` records for this seat across elections.
    """

    __tablename__ = "seats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("maps.id"), nullable=False)
    seat_name: Mapped[str] = mapped_column(String, nullable=False)
    region_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("regions.id"), nullable=True)
    electorate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    electoral_votes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    geometry: Mapped[Optional[Any]] = mapped_column(GeometryWKB, nullable=True)

    map: Mapped["Map"] = relationship("Map", back_populates="seats")
    region: Mapped[Optional["Region"]] = relationship("Region", back_populates="seats")
    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="seat", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Return a debug string representation of the Seat."""
        return f"<Seat {self.seat_name}>"


class Election(Base):
    """ORM model for the ``elections`` table.

    An election represents a single contest across some or all seats within a
    map. This includes real elections (``uk_general``, ``by_election``) and
    synthetic model runs (``model_run``, ``model_uns``, ``holyrood_uns``). Model runs typically
    reference a real parent election via ``parent_election_id``.

    Attributes:
        id: Auto-incrementing primary key.
        map_id: Foreign key to the owning ``Map``.
        year: Calendar year of the election.
        name: Unique human-readable label (e.g. "GE 2024").
        type: ``ElectionType`` enum value classifying this election.
        parent_election_id: Optional foreign key to a parent ``Election``.
            Used by model runs to reference the real election they are derived
            from.
        election_date: Optional specific date of the election.
        map: The owning ``Map`` instance.
        parent_election: Optional parent ``Election`` instance.
        votes: All ``Vote`` records belonging to this election.
    """

    __tablename__ = "elections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("maps.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    type: Mapped[ElectionType] = mapped_column(Enum(ElectionType), nullable=False)
    parent_election_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("elections.id"), nullable=True)
    election_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    map: Mapped["Map"] = relationship("Map", back_populates="elections")
    parent_election: Mapped[Optional["Election"]] = relationship("Election", remote_side="Election.id")
    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="election", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Return a debug string representation of the Election."""
        return f"<Election {self.name} ({self.year})>"


class Vote(Base):
    """ORM model for the ``votes`` table.

    Records a single candidate's vote total in a specific seat within an
    election. For model runs and UNS projections, ``candidate_name`` is
    typically ``None`` and ``party_id`` drives the result. Independents have
    a ``None`` ``party_id``.

    Attributes:
        id: Auto-incrementing primary key.
        election_id: Foreign key to the owning ``Election``.
        seat_id: Foreign key to the ``Seat`` this vote was cast in.
        party_id: Optional foreign key to the ``Party``. ``None`` for
            independent candidates.
        candidate_name: Optional candidate name. Empty for model run and
            model UNS elections.
        vote_total: Optional raw vote count or modelled vote share.
        elected: Whether this candidate was elected. Defaults to ``False``.
        election: The owning ``Election`` instance.
        seat: The ``Seat`` this vote belongs to.
        party: The ``Party`` associated with this vote, if any.
    """

    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    election_id: Mapped[int] = mapped_column(Integer, ForeignKey("elections.id"), nullable=False)
    seat_id: Mapped[int] = mapped_column(Integer, ForeignKey("seats.id"), nullable=False)
    party_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("parties.id"), nullable=True)  # nullable for independents
    candidate_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # empty for model_run/model_uns/holyrood_uns elections
    vote_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    election: Mapped["Election"] = relationship("Election", back_populates="votes")
    seat: Mapped["Seat"] = relationship("Seat", back_populates="votes")
    party: Mapped[Optional["Party"]] = relationship("Party", back_populates="votes")

    def __repr__(self) -> str:
        """Return a debug string representation of the Vote."""
        return (
            f"<Vote {self.candidate_name or '(no candidate)'} "
            f"party={self.party_id} votes={self.vote_total}>"
        )


# ── Polling tables ────────────────────────────────────────────────────────────


class Pollster(Base):
    """ORM model for the ``pollsters`` table.

    Represents a polling organisation that conducts polls. Each pollster has
    a unique machine-readable identifier, an optional weighting for average
    calculations, and an optional JSON-encoded regions mapping that controls
    how the pollster's regional breakdowns are translated to ``Region`` rows.

    Attributes:
        id: Auto-incrementing primary key.
        name: Human-readable pollster name (e.g. "YouGov").
        identifier: Unique machine-readable slug (e.g. ``"yougov"``).
        weight: Optional relative weight applied when computing polling
            averages. Defaults to ``1.0``.
        regions_mapping: Optional JSON-encoded string mapping the pollster's
            own region labels to canonical ``Region`` identifiers.
        polls: All ``Poll`` records conducted by this pollster.
    """

    __tablename__ = "pollsters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    identifier: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=1.0)
    regions_mapping: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    polls: Mapped[list["Poll"]] = relationship("Poll", back_populates="pollster", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Return a debug string representation of the Pollster."""
        return f"<Pollster {self.identifier}>"


class Poll(Base):
    """ORM model for the ``polls`` table.

    Represents a single polling exercise conducted by a ``Pollster`` over a
    defined fieldwork period. Each poll is associated with a ``Map`` and
    contains one or more ``PollRow`` records holding party-level percentages,
    optionally broken down by region.

    Attributes:
        id: Auto-incrementing primary key.
        pollster_id: Foreign key to the conducting ``Pollster``.
        map_id: Foreign key to the ``Map`` this poll applies to.
        fieldwork_start: Start date of the fieldwork period.
        fieldwork_end: End date of the fieldwork period.
        sample_size: Optional number of respondents.
        source_url: Optional URL to the published poll source.
        pollster: The conducting ``Pollster`` instance.
        map: The associated ``Map`` instance.
        rows: All ``PollRow`` records belonging to this poll.
    """

    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pollster_id: Mapped[int] = mapped_column(Integer, ForeignKey("pollsters.id"), nullable=False)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("maps.id"), nullable=False)
    fieldwork_start: Mapped[date] = mapped_column(Date, nullable=False)
    fieldwork_end: Mapped[date] = mapped_column(Date, nullable=False)
    sample_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    pollster: Mapped["Pollster"] = relationship("Pollster", back_populates="polls")
    map: Mapped["Map"] = relationship("Map")
    rows: Mapped[list["PollRow"]] = relationship("PollRow", back_populates="poll", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Return a debug string representation of the Poll."""
        return f"<Poll {self.pollster_id} {self.fieldwork_start}–{self.fieldwork_end}>"


class PollRow(Base):
    """ORM model for the ``poll_rows`` table.

    Stores a single party-level voting intention percentage within a ``Poll``,
    optionally scoped to a ``Region``. A ``None`` ``region_id`` indicates a
    national-level figure.

    Attributes:
        id: Auto-incrementing primary key.
        poll_id: Foreign key to the owning ``Poll``.
        region_id: Optional foreign key to the ``Region`` this row covers.
            ``None`` indicates a national (non-regional) figure.
        party_id: Foreign key to the ``Party`` this figure is for.
        percentage: Voting intention share for the party, as a percentage
            (0–100).
        poll: The owning ``Poll`` instance.
        region: The ``Region`` this row is scoped to, if any.
        party: The ``Party`` this row refers to.
    """

    __tablename__ = "poll_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[int] = mapped_column(Integer, ForeignKey("polls.id"), nullable=False)
    region_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("regions.id"), nullable=True)  # null = national
    party_id: Mapped[int] = mapped_column(Integer, ForeignKey("parties.id"), nullable=False)
    percentage: Mapped[float] = mapped_column(Float, nullable=False)

    poll: Mapped["Poll"] = relationship("Poll", back_populates="rows")
    region: Mapped[Optional["Region"]] = relationship("Region")
    party: Mapped["Party"] = relationship("Party")

    def __repr__(self) -> str:
        """Return a debug string representation of the PollRow."""
        return f"<PollRow party={self.party_id} pct={self.percentage}>"
