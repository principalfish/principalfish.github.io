import enum
from datetime import date
from typing import Any, Literal, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapped, Mapper, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy ORM models in this project."""

    pass


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

    Represents a single parliamentary constituency within a map. Seats are
    linked to votes within an election.

    Attributes:
        id: Auto-incrementing primary key.
        map_id: Foreign key to the owning ``Map``.
        seat_name: Name of the constituency.
        region_id: Optional foreign key to the ``Region`` this seat belongs to.
        electorate: Optional registered electorate size.
        electoral_votes: Optional number of US Electoral College votes for this
            seat (US states only; ``None`` for UK constituencies).
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

    map: Mapped["Map"] = relationship("Map", back_populates="seats")
    region: Mapped[Optional["Region"]] = relationship("Region", back_populates="seats")
    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="seat", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        """Return a debug string representation of the Seat."""
        return f"<Seat {self.seat_name}>"


class USElectoralVoteEra(Base):
    """Supported election-year interval for one census allocation."""

    __tablename__ = "us_electoral_vote_eras"
    __table_args__ = (
        CheckConstraint(
            "first_election_year <= last_election_year",
            name="ck_us_electoral_vote_era_interval",
        ),
    )

    census_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_election_year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_election_year: Mapped[int] = mapped_column(Integer, nullable=False)


class USElectoralVoteAllocation(Base):
    """Electoral votes for a stable presidential tally-unit name in an era."""

    __tablename__ = "us_electoral_vote_allocations"
    __table_args__ = (
        CheckConstraint(
            "electoral_votes > 0 AND typeof(electoral_votes) = 'integer'",
            name="ck_us_electoral_vote_allocation_positive_integer",
        ),
    )

    era_year: Mapped[int] = mapped_column(
        Integer, ForeignKey("us_electoral_vote_eras.census_year"), primary_key=True,
    )
    unit_name: Mapped[str] = mapped_column(String, primary_key=True)
    electoral_votes: Mapped[int] = mapped_column(Integer, nullable=False)


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
        target_election_year: Target cycle for presidential model runs.
        map: The owning ``Map`` instance.
        parent_election: Optional parent ``Election`` instance.
        votes: All ``Vote`` records belonging to this election.
        uncertainty: The run's ``ModelUncertainty`` summary, if one is stored.
    """

    __tablename__ = "elections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("maps.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    type: Mapped[ElectionType] = mapped_column(Enum(ElectionType), nullable=False)
    parent_election_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("elections.id"), nullable=True)
    election_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    target_election_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    map: Mapped["Map"] = relationship("Map", back_populates="elections")
    parent_election: Mapped[Optional["Election"]] = relationship("Election", remote_side="Election.id")
    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="election", cascade="all, delete-orphan")
    # passive_deletes: deleting an election must not SELECT this table, which
    # a database migrated before migrate_add_model_uncertainty.py lacks; the
    # foreign key's ON DELETE CASCADE removes the row where it exists.
    uncertainty: Mapped[Optional["ModelUncertainty"]] = relationship(
        "ModelUncertainty",
        back_populates="election",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

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


class ModelUncertainty(Base):
    """ORM model for the ``model_uncertainty`` table.

    The Monte Carlo summary of one model run, keyed by that run's election.
    Each block is compact JSON (sorted keys, no whitespace), so the same
    summary always stores the same text. ``schema`` names the blocks' format
    (``model_support.persistence.UNCERTAINTY_SCHEMA``), so a reader can tell a
    row written in an older format from the current one.

    The national block is kept for every run. The region and seat blocks are
    kept only on the latest-dated run of a scope (model type, map and name
    prefix): every ``model_support.persistence.replace_output`` sets every
    other run's to NULL, whatever order the runs were saved in.

    Rows are deleted with their election (ORM cascade, and ``ON DELETE
    CASCADE`` for raw SQL with foreign keys enforced; the persistence module's
    raw ``sqlite3`` writers do not enforce them and delete the rows
    explicitly).

    Attributes:
        election_id: Primary key and foreign key to the model run's
            ``Election``.
        schema: Format of the JSON blocks, e.g. "pf-model-uncertainty-v1".
        national_json: National outcomes, intervals and error-budget inputs.
        regions_json: Per-region outcomes and error budget; NULL once pruned.
        seats_json: Per-seat win probabilities and share intervals; NULL once
            pruned.
        election: The owning ``Election`` instance.
    """

    __tablename__ = "model_uncertainty"

    election_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("elections.id", ondelete="CASCADE"), primary_key=True
    )
    schema: Mapped[str] = mapped_column(Text, nullable=False)
    national_json: Mapped[str] = mapped_column(Text, nullable=False)
    regions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    seats_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    election: Mapped["Election"] = relationship(
        "Election", back_populates="uncertainty"
    )

    def __repr__(self) -> str:
        """Return a debug string representation of the ModelUncertainty."""
        return (
            f"<ModelUncertainty election={self.election_id} schema={self.schema} "
            f"detail={self.regions_json is not None or self.seats_json is not None}>"
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

    A poll that asks several head-to-head questions (US polls) is stored as
    one ``Poll`` per question, told apart by ``matchup``. A US state or
    district poll is attached to its ``Seat``; its rows keep ``region_id``
    NULL.

    Like ``map``, the ``seat`` relationship is only usable while the loading
    session is open: every ``Database`` accessor closes its session, so
    ``db.get_poll(id).seat`` raises ``DetachedInstanceError``. Callers that
    need the seat either work inside ``db.session()`` or look the seat up by
    ``seat_id``.

    Attributes:
        id: Auto-incrementing primary key.
        pollster_id: Foreign key to the conducting ``Pollster``.
        map_id: Foreign key to the ``Map`` this poll applies to.
        fieldwork_start: Start date of the fieldwork period.
        fieldwork_end: End date of the fieldwork period.
        sample_size: Optional number of respondents.
        source_url: Optional URL to the published poll source.
        matchup: Optional label of the candidate pairing polled (e.g.
            ``"Vance (R) vs Newsom (D)"``). ``None`` for party-only polls.
        seat_id: Optional foreign key to the ``Seat`` a state or district poll
            covers. ``None`` for a national poll.
        pollster: The conducting ``Pollster`` instance.
        map: The associated ``Map`` instance.
        seat: The ``Seat`` this poll covers, if any.
        rows: All ``PollRow`` records belonging to this poll.
        bases: The ``PollBase`` respondent counts its tables printed, if
            captured.
    """

    __tablename__ = "polls"
    __table_args__ = (
        # Serves the per-seat poll lookups of the US model and review queue.
        Index("ix_polls_map_seat", "map_id", "seat_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pollster_id: Mapped[int] = mapped_column(Integer, ForeignKey("pollsters.id"), nullable=False)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("maps.id"), nullable=False)
    fieldwork_start: Mapped[date] = mapped_column(Date, nullable=False)
    fieldwork_end: Mapped[date] = mapped_column(Date, nullable=False)
    sample_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    matchup: Mapped[str | None] = mapped_column(String, nullable=True)
    seat_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("seats.id"), nullable=True)  # null = national

    pollster: Mapped["Pollster"] = relationship("Pollster", back_populates="polls")
    map: Mapped["Map"] = relationship("Map")
    seat: Mapped["Seat | None"] = relationship("Seat")
    rows: Mapped[list["PollRow"]] = relationship("PollRow", back_populates="poll", cascade="all, delete-orphan")
    bases: Mapped[list["PollBase"]] = relationship(
        "PollBase", back_populates="poll", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        """Return a debug string representation of the Poll."""
        return f"<Poll {self.pollster_id} {self.fieldwork_start}–{self.fieldwork_end}>"


@event.listens_for(Poll, "before_insert")
@event.listens_for(Poll, "before_update")
def _reject_inverted_fieldwork(
    _mapper: Mapper[Any], _connection: Connection, target: Poll
) -> None:
    """Refuse to flush a poll whose fieldwork starts after it ends.

    Covers every ORM write path (importers, the console, ``db.py``). Core
    ``update()``/``insert()`` statements and raw ``sqlite3`` bypass mapper
    events, so they are not covered.

    Args:
        _mapper: The ``Poll`` mapper (unused).
        _connection: The flush's connection (unused).
        target: The poll being inserted or updated.

    Raises:
        ValueError: If ``fieldwork_start`` is after ``fieldwork_end``.
    """
    start, end = target.fieldwork_start, target.fieldwork_end
    if start is not None and end is not None and start > end:
        raise ValueError(
            f"Poll fieldwork_start {start} is after fieldwork_end {end}"
            f" (pollster_id={target.pollster_id})"
        )


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
        candidate_name: Optional name of the candidate the figure is for (US
            candidate polls). ``None`` for party-only polls.
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
    candidate_name: Mapped[str | None] = mapped_column(String, nullable=True)

    poll: Mapped["Poll"] = relationship("Poll", back_populates="rows")
    region: Mapped[Optional["Region"]] = relationship("Region")
    party: Mapped["Party"] = relationship("Party")

    def __repr__(self) -> str:
        """Return a debug string representation of the PollRow."""
        return f"<PollRow party={self.party_id} pct={self.percentage}>"


# What a PollBase counts; mirrors the poll_bases.kind CHECK constraint.
#   decided       — respondents who named a party: don't-knows, refusals and
#                   won't-votes removed, so the base is the shares' denominator.
#   likely_voters — a turnout-filtered base that still includes undecided or
#                   refusing respondents, so it overstates the shares' n a little.
#   all_adults    — everyone interviewed in the column, before any turnout or
#                   don't-know filter.
PollBaseKind = Literal["decided", "likely_voters", "all_adults"]
POLL_BASE_KINDS: tuple[PollBaseKind, ...] = ("decided", "likely_voters", "all_adults")


class PollBase(Base):
    """ORM model for the ``poll_bases`` table.

    The number of respondents behind one column of a poll's voting-intention
    table, as the pollster printed it: national (``region_id`` NULL) or one
    region. At most one row exists per ``(poll_id, region_id)``, enforced by a
    unique index over ``IFNULL(region_id, 0)`` because a plain UNIQUE
    constraint treats every NULL ``region_id`` as distinct.

    A source column that covers several internal regions (YouGov's "North",
    say) is apportioned across them in proportion to ``regions.population``,
    keeping whole numbers that sum to the printed base; those rows carry
    ``split_from_macro`` so a reader knows the per-region n is an estimate of
    that region's share of the column.

    A pollster that prints only a weighted count of the respondents behind
    its shares (Find Out Now's "Filtered N") is stored weighted-only:
    ``unweighted_n`` NULL, ``weighted_n`` set. At least one is always present.

    Rows are deleted with their poll (ORM cascade, and ``ON DELETE CASCADE``
    for raw SQL with foreign keys enforced).

    Attributes:
        id: Auto-incrementing primary key.
        poll_id: Foreign key to the owning ``Poll``.
        region_id: Optional foreign key to the ``Region`` the column covers.
            ``None`` means the national ("Total"/"All") column.
        unweighted_n: Respondents actually interviewed in the column; > 0, or
            None when the pollster prints only a weighted base.
        weighted_n: The column's weighted base, when printed; > 0 or None.
        kind: What the base counts. See ``PollBaseKind``.
        split_from_macro: True when the counts were apportioned from a wider
            source column by population rather than printed for the region.
        poll: The owning ``Poll`` instance.
        region: The ``Region`` this base covers, if any.
    """

    __tablename__ = "poll_bases"
    __table_args__ = (
        CheckConstraint(
            "unweighted_n IS NULL OR unweighted_n > 0",
            name="ck_poll_bases_unweighted_n",
        ),
        CheckConstraint(
            "unweighted_n IS NOT NULL OR weighted_n IS NOT NULL",
            name="ck_poll_bases_has_n",
        ),
        CheckConstraint(
            "weighted_n IS NULL OR weighted_n > 0", name="ck_poll_bases_weighted_n"
        ),
        CheckConstraint(
            "kind IN ('decided', 'likely_voters', 'all_adults')",
            name="ck_poll_bases_kind",
        ),
        # One base per poll column; IFNULL folds the national column (NULL
        # region) onto 0, which no autoincrement region id can take.
        Index(
            "ux_poll_bases_scope",
            "poll_id",
            text("IFNULL(region_id, 0)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("polls.id", ondelete="CASCADE"), nullable=False
    )
    region_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("regions.id"), nullable=True
    )  # null = national
    unweighted_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weighted_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[PollBaseKind] = mapped_column(String, nullable=False)
    split_from_macro: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    poll: Mapped["Poll"] = relationship("Poll", back_populates="bases")
    region: Mapped[Optional["Region"]] = relationship("Region")

    def __repr__(self) -> str:
        """Return a debug string representation of the PollBase."""
        return (
            f"<PollBase poll={self.poll_id} region={self.region_id} "
            f"n={self.unweighted_n} kind={self.kind}>"
        )


# Who set a TrackedMatchup; mirrors the tracked_matchups.source CHECK constraint.
TrackedMatchupSource = Literal["auto", "manual"]


class TrackedMatchup(Base):
    """ORM model for the ``tracked_matchups`` table.

    Records which ``Poll.matchup`` the US forecast model follows for one race:
    a whole map (national, ``seat_id`` NULL) or a single seat. At most one row
    exists per ``(map_id, seat_id)``, enforced by a unique index over
    ``IFNULL(seat_id, 0)`` because a plain UNIQUE constraint treats every
    NULL ``seat_id`` as distinct.

    Rows written by the importer have ``source`` ``"auto"``; a user override
    has ``"manual"`` and is never overwritten by the importer, which still
    records its latest choice in ``auto_matchup`` so the override can be
    cleared back to it.

    Attributes:
        id: Auto-incrementing primary key.
        map_id: Foreign key to the ``Map`` the race belongs to.
        seat_id: Optional foreign key to the race's ``Seat``. ``None`` means
            the map's national race.
        matchup: The matchup label the model follows. ``None`` means the
            race's polls are ignored.
        source: ``"auto"`` (set by the importer) or ``"manual"`` (a user
            override). See ``TrackedMatchupSource``.
        auto_matchup: The matchup the importer last chose for the race, kept
            even while a manual override is in force. ``None`` if the importer
            has never set one.
    """

    __tablename__ = "tracked_matchups"
    __table_args__ = (
        CheckConstraint("source IN ('auto', 'manual')", name="ck_tracked_matchups_source"),
        # One row per race; IFNULL folds the national race (NULL seat) onto 0,
        # which no autoincrement seat id can take.
        Index(
            "ux_tracked_matchups_scope",
            "map_id",
            text("IFNULL(seat_id, 0)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("maps.id"), nullable=False)
    seat_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("seats.id"), nullable=True)  # null = national
    matchup: Mapped[str | None] = mapped_column(String, nullable=True)  # null = ignore the race's polls
    source: Mapped[TrackedMatchupSource] = mapped_column(String, nullable=False)
    auto_matchup: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        """Return a debug string representation of the TrackedMatchup."""
        return (
            f"<TrackedMatchup map={self.map_id} seat={self.seat_id} "
            f"matchup={self.matchup!r} source={self.source}>"
        )
