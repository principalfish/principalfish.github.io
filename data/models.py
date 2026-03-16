import enum
from datetime import date
from typing import Any, Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Enums ────────────────────────────────────────────────────────────────────


class ElectionType(enum.Enum):
    uk_general = "uk_general"
    by_election = "by_election"
    model_run = "model_run"
    model_uns = "model_uns"


# ── Tables ───────────────────────────────────────────────────────────────────


class Party(Base):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    short_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    colour: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # hex e.g. "#E4003B"

    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="party")

    def __repr__(self) -> str:
        return f"<Party {self.name}>"


class Map(Base):
    __tablename__ = "maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    regions: Mapped[list["Region"]] = relationship("Region", back_populates="map", cascade="all, delete-orphan")
    seats: Mapped[list["Seat"]] = relationship("Seat", back_populates="map", cascade="all, delete-orphan")
    elections: Mapped[list["Election"]] = relationship(
        "Election", back_populates="map", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Map {self.name}>"


class Region(Base):
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
        return f"<Region {self.name}>"


class Seat(Base):
    __tablename__ = "seats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("maps.id"), nullable=False)
    seat_name: Mapped[str] = mapped_column(String, nullable=False)
    region_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("regions.id"), nullable=True)
    electorate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    geometry: Mapped[Optional[Any]] = mapped_column(Geometry("MULTIPOLYGON", srid=4326), nullable=True)

    map: Mapped["Map"] = relationship("Map", back_populates="seats")
    region: Mapped[Optional["Region"]] = relationship("Region", back_populates="seats")
    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="seat", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Seat {self.seat_name}>"


class Election(Base):
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
        return f"<Election {self.name} ({self.year})>"


class Vote(Base):
    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    election_id: Mapped[int] = mapped_column(Integer, ForeignKey("elections.id"), nullable=False)
    seat_id: Mapped[int] = mapped_column(Integer, ForeignKey("seats.id"), nullable=False)
    party_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("parties.id"), nullable=True)  # nullable for independents
    candidate_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # empty for model_run/model_uns elections
    vote_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    election: Mapped["Election"] = relationship("Election", back_populates="votes")
    seat: Mapped["Seat"] = relationship("Seat", back_populates="votes")
    party: Mapped[Optional["Party"]] = relationship("Party", back_populates="votes")

    def __repr__(self) -> str:
        return (
            f"<Vote {self.candidate_name or '(no candidate)'} "
            f"party={self.party_id} votes={self.vote_total}>"
        )


# ── Polling tables ────────────────────────────────────────────────────────────


class Pollster(Base):
    __tablename__ = "pollsters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    identifier: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=1.0)
    regions_mapping: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    polls: Mapped[list["Poll"]] = relationship("Poll", back_populates="pollster", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Pollster {self.identifier}>"


class Poll(Base):
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
        return f"<Poll {self.pollster_id} {self.fieldwork_start}–{self.fieldwork_end}>"


class PollRow(Base):
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
        return f"<PollRow party={self.party_id} pct={self.percentage}>"
