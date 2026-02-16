import enum

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Enums ────────────────────────────────────────────────────────────────────


class ElectionType(enum.Enum):
    uk_general = "uk_general"
    by_election = "by_election"
    model_run = "model_run"


# ── Tables ───────────────────────────────────────────────────────────────────


class Party(Base):
    __tablename__ = "parties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    short_name = Column(String, nullable=True)
    colour = Column(String, nullable=True)  # hex e.g. "#E4003B"

    votes = relationship("Vote", back_populates="party")

    def __repr__(self) -> str:
        return f"<Party {self.name}>"


class Map(Base):
    __tablename__ = "maps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)

    regions = relationship("Region", back_populates="map", cascade="all, delete-orphan")
    seats = relationship("Seat", back_populates="map", cascade="all, delete-orphan")
    elections = relationship(
        "Election", back_populates="map", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Map {self.name}>"


class Region(Base):
    __tablename__ = "regions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, ForeignKey("maps.id"), nullable=False)
    name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("regions.id"), nullable=True)
    population = Column(Integer, nullable=True)

    map = relationship("Map", back_populates="regions")
    parent = relationship("Region", remote_side=[id], backref="children")
    seats = relationship("Seat", back_populates="region")

    def __repr__(self) -> str:
        return f"<Region {self.name}>"


class Seat(Base):
    __tablename__ = "seats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, ForeignKey("maps.id"), nullable=False)
    seat_name = Column(String, nullable=False)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=True)
    geometry = Column(Geometry("MULTIPOLYGON", srid=4326), nullable=True)

    map = relationship("Map", back_populates="seats")
    region = relationship("Region", back_populates="seats")
    results = relationship("SeatResult", back_populates="seat", cascade="all, delete-orphan")
    votes = relationship("Vote", back_populates="seat", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Seat {self.seat_name}>"


class Election(Base):
    __tablename__ = "elections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, ForeignKey("maps.id"), nullable=False)
    year = Column(Integer, nullable=False)
    name = Column(String, nullable=False, unique=True)
    type = Column(Enum(ElectionType), nullable=False)

    map = relationship("Map", back_populates="elections")
    seat_results = relationship(
        "SeatResult", back_populates="election", cascade="all, delete-orphan"
    )
    votes = relationship("Vote", back_populates="election", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Election {self.name} ({self.year})>"


class SeatResult(Base):
    """Per-seat aggregates for an election (turnout, electorate size)."""

    __tablename__ = "seat_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    election_id = Column(Integer, ForeignKey("elections.id"), nullable=False)
    seat_id = Column(Integer, ForeignKey("seats.id"), nullable=False)
    electorate = Column(Integer, nullable=True)
    turnout = Column(Integer, nullable=True)

    election = relationship("Election", back_populates="seat_results")
    seat = relationship("Seat", back_populates="results")

    def __repr__(self) -> str:
        return f"<SeatResult election={self.election_id} seat={self.seat_id}>"


class Vote(Base):
    __tablename__ = "votes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    election_id = Column(Integer, ForeignKey("elections.id"), nullable=False)
    seat_id = Column(Integer, ForeignKey("seats.id"), nullable=False)
    party_id = Column(Integer, ForeignKey("parties.id"), nullable=True)  # nullable for independents
    candidate_name = Column(String, nullable=True)  # empty for model_run elections
    vote_total = Column(Float, nullable=True)
    elected = Column(Boolean, nullable=False, default=False)

    election = relationship("Election", back_populates="votes")
    seat = relationship("Seat", back_populates="votes")
    party = relationship("Party", back_populates="votes")

    def __repr__(self) -> str:
        return (
            f"<Vote {self.candidate_name or '(no candidate)'} "
            f"party={self.party_id} votes={self.vote_total}>"
        )


# ── Polling tables ────────────────────────────────────────────────────────────


class Pollster(Base):
    __tablename__ = "pollsters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    identifier = Column(String, nullable=False, unique=True)
    weight = Column(Float, nullable=True, default=1.0)
    regions_mapping = Column(Text, nullable=True)

    polls = relationship("Poll", back_populates="pollster", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Pollster {self.identifier}>"


class Poll(Base):
    __tablename__ = "polls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pollster_id = Column(Integer, ForeignKey("pollsters.id"), nullable=False)
    map_id = Column(Integer, ForeignKey("maps.id"), nullable=False)
    fieldwork_start = Column(Date, nullable=False)
    fieldwork_end = Column(Date, nullable=False)
    sample_size = Column(Integer, nullable=True)

    pollster = relationship("Pollster", back_populates="polls")
    map = relationship("Map")
    rows = relationship("PollRow", back_populates="poll", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Poll {self.pollster_id} {self.fieldwork_start}–{self.fieldwork_end}>"


class PollRow(Base):
    __tablename__ = "poll_rows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    poll_id = Column(Integer, ForeignKey("polls.id"), nullable=False)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=True)  # null = national
    party_id = Column(Integer, ForeignKey("parties.id"), nullable=False)
    percentage = Column(Float, nullable=False)

    poll = relationship("Poll", back_populates="rows")
    region = relationship("Region")
    party = relationship("Party")

    def __repr__(self) -> str:
        return f"<PollRow party={self.party_id} pct={self.percentage}>"
