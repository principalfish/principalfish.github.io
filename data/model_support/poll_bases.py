"""Resolve the respondent count (n) behind a poll's shares, per region.

Pollsters print the base of each voting-intention column, captured into
``poll_bases`` by the importers (see ``polls.importers.bases``). Where none was
captured, n falls back in a fixed order. :func:`resolve_poll_n` returns, for
one poll and one region (``None`` = national):

1. the captured base — source ``"base:<kind>"``, e.g. ``"base:decided"`` or
   ``"base:all_adults"`` (see ``models.PollBaseKind``): its unweighted count,
   or for a weighted-only base (Find Out Now) its weighted count, labelled
   ``"base:<kind>(weighted)"``;
2. nationally, ``polls.sample_size`` — source ``"sample_size"``;
3. regionally, a population estimate: the poll's n (its national base if
   captured, else ``sample_size``) times the region's population over the
   total population of the regions the poll reported. Its source names that
   poll n's basis: ``"estimate:<kind>"`` (the national base's kind, with
   ``(weighted)`` for a weighted-only base) or ``"estimate:sample_size"``. An
   estimate below 1 is unresolved.

A region counts as reported when the poll has at least one row there with a
share above zero (:func:`reported_region_id`, :data:`REPORTED_ROW_SQL`): several
importers store all-zero placeholder rows for regions they do not publish
(Northern Ireland, mostly), and counting those would shrink every other
region's estimate.

For bulk use (the model resolves every poll x region in a window) build a
:class:`PollNIndex` once — from rows already in memory, or with
:meth:`PollNIndex.load` — and call :meth:`PollNIndex.resolve` per key.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from models import Poll, PollBase, PollBaseKind, PollRow, Region

# A poll_rows row (alias ``r``) that makes its region "reported": a regional
# row with a share above zero. The SQL twin of :func:`reported_region_id`.
REPORTED_ROW_SQL = "r.region_id IS NOT NULL AND r.percentage > 0"


def reported_region_id(region_id: int | None, percentage: float) -> int | None:
    """Return the region a poll row reports, or None if it reports none.

    A national row reports no region, and nor does a zero share: importers
    store all-zero placeholder rows for regions they do not publish.
    """
    return region_id if region_id is not None and percentage > 0 else None


# Per-pollster coverage of captured bases on one map, shared by the console's
# coverage page and the backfill report. Runs on SQLAlchemy ``text()`` and on
# a raw ``sqlite3`` connection alike (named ``:map_id`` parameter).
COVERAGE_SQL = f"""\
WITH reported AS (
    SELECT DISTINCT r.poll_id, r.region_id
    FROM poll_rows AS r
    JOIN polls AS p ON p.id = r.poll_id
    WHERE p.map_id = :map_id AND {REPORTED_ROW_SQL}
)
SELECT
    ps.identifier,
    ps.name,
    COUNT(*) AS polls,
    SUM(EXISTS (
        SELECT 1 FROM poll_bases AS b
        WHERE b.poll_id = p.id AND b.region_id IS NULL
    )) AS polls_with_national_base,
    (
        SELECT COUNT(*) FROM reported AS c
        JOIN polls AS cp ON cp.id = c.poll_id
        WHERE cp.pollster_id = ps.id
    ) AS crossbreaks,
    (
        SELECT COUNT(*) FROM reported AS c
        JOIN polls AS cp ON cp.id = c.poll_id
        JOIN poll_bases AS b ON b.poll_id = c.poll_id AND b.region_id = c.region_id
        WHERE cp.pollster_id = ps.id
    ) AS crossbreaks_with_base
FROM polls AS p
JOIN pollsters AS ps ON ps.id = p.pollster_id
WHERE p.map_id = :map_id
GROUP BY ps.id
ORDER BY ps.name, ps.id
"""


@dataclass(frozen=True, slots=True)
class PollsterCoverage:
    """How much of one pollster's polling has captured bases.

    Attributes:
        identifier: ``pollsters.identifier``.
        name: ``pollsters.name``.
        polls: Polls on the map.
        polls_with_national_base: Polls with a national base.
        crossbreaks: (poll, region) pairs the polls reported.
        crossbreaks_with_base: Those with a regional base.
    """

    identifier: str
    name: str
    polls: int
    polls_with_national_base: int
    crossbreaks: int
    crossbreaks_with_base: int

    @property
    def national_pct(self) -> float | None:
        """Percentage of polls with a national base, or None for no polls."""
        return _pct(self.polls_with_national_base, self.polls)

    @property
    def crossbreak_pct(self) -> float | None:
        """Percentage of crossbreaks with a base, or None for no crossbreaks."""
        return _pct(self.crossbreaks_with_base, self.crossbreaks)


def _pct(part: int, whole: int) -> float | None:
    return 100.0 * part / whole if whole else None


CoverageRow = tuple[str, str, int, int | None, int | None, int | None]


def coverage_from_rows(rows: Iterable[CoverageRow]) -> list[PollsterCoverage]:
    """Build coverage records from :data:`COVERAGE_SQL` result rows."""
    return [
        PollsterCoverage(
            identifier=identifier,
            name=name,
            polls=polls,
            polls_with_national_base=national or 0,
            crossbreaks=crossbreaks or 0,
            crossbreaks_with_base=with_base or 0,
        )
        for identifier, name, polls, national, crossbreaks, with_base in rows
    ]


def poll_base_coverage(session: Session, map_id: int) -> list[PollsterCoverage]:
    """Return per-pollster base coverage for the polls on one map.

    Args:
        session: An open session.
        map_id: The map whose polls to count.

    Returns:
        One record per pollster with polls on the map, by name.
    """
    result = session.execute(text(COVERAGE_SQL), {"map_id": map_id})
    return coverage_from_rows(result.tuples().all())


@dataclass(frozen=True, slots=True)
class CapturedBase:
    """A stored ``poll_bases`` row, reduced to what resolving needs.

    Attributes:
        unweighted_n: The column's unweighted count, or None for a
            weighted-only base.
        weighted_n: Its weighted base, or None.
        kind: What it counts.
        split_from_macro: Whether it was apportioned from a macro column.
    """

    unweighted_n: int | None
    weighted_n: int | None
    kind: PollBaseKind
    split_from_macro: bool = False

    @property
    def n(self) -> int | None:
        """The count n is read from: unweighted, else (weighted-only) weighted."""
        return self.unweighted_n if self.unweighted_n is not None else self.weighted_n

    @property
    def is_weighted_only(self) -> bool:
        """True when only a weighted base was printed, so n is that count."""
        return self.unweighted_n is None


# Where a resolved n came from: a captured base, the poll's sample size, or a
# population estimate.
NOrigin = Literal["base", "sample_size", "estimate"]
# What the count behind a resolved n counts: a base's kind, or the poll's
# total sample (which includes don't-knows). An estimate's basis is that of
# the poll n it was scaled from.
NBasis = PollBaseKind | Literal["sample_size"]


@dataclass(frozen=True, slots=True)
class ResolvedN:
    """The n behind one poll's shares in one region, and where it came from.

    *origin*, *basis* and *weighted_only* are the structured description that
    code reads (``model_support.uncertainty`` adjusts n by its basis);
    :attr:`source` renders them as the label the console shows.

    Attributes:
        n: The respondent count, or None when nothing resolves (no base, no
            sample size, or a region the poll did not report).
        origin: Where *n* came from; None when *n* is None.
        basis: What the count behind *n* counts; None when *n* is None. For an
            estimate, the basis of the poll n it was scaled from.
        weighted_only: True when that count is a weighted-only base (the
            pollster printed no unweighted count).
        weighted_n: The captured weighted base, when the origin is a base.
        split_from_macro: True when the base was apportioned by population.
    """

    n: int | None
    origin: NOrigin | None
    basis: NBasis | None
    weighted_only: bool = False
    weighted_n: int | None = None
    split_from_macro: bool = False

    def __post_init__(self) -> None:
        """Reject a description that contradicts itself.

        Raises:
            ValueError: *n*, *origin* and *basis* are not all set or all None;
                a sample-size origin has another basis; or a base claims a
                sample-size basis (an estimate may be scaled from either).
        """
        present = {self.n is not None, self.origin is not None, self.basis is not None}
        if len(present) != 1:
            raise ValueError(
                "n, origin and basis must be all set or all None, got "
                f"{self.n!r}, {self.origin!r}, {self.basis!r}"
            )
        if (self.origin == "sample_size" and self.basis != "sample_size") or (
            self.origin == "base" and self.basis == "sample_size"
        ):
            raise ValueError(
                f"origin {self.origin!r} cannot have basis {self.basis!r}"
            )

    @property
    def source(self) -> str | None:
        """The console label: ``"base:<kind>"``, ``"sample_size"``, etc.

        ``"base:<kind>"`` (``"base:<kind>(weighted)"`` when only a weighted
        base was printed), ``"sample_size"``, ``"estimate:<kind>"`` (likewise
        suffixed) or ``"estimate:sample_size"``; None when unresolved.
        """
        if self.origin is None or self.basis is None:
            return None
        if self.origin == "sample_size":
            return "sample_size"
        suffix = "(weighted)" if self.weighted_only else ""
        return f"{self.origin}:{self.basis}{suffix}"


UNRESOLVED = ResolvedN(None, None, None)


@dataclass(frozen=True, slots=True)
class PollNInputs:
    """Everything about one poll that resolving its n needs.

    Attributes:
        sample_size: ``polls.sample_size``.
        bases: Captured bases by region id (None = national).
        reported_region_ids: Regions the poll reported a non-zero share in.
    """

    sample_size: int | None
    bases: Mapping[int | None, CapturedBase] = field(default_factory=dict)
    reported_region_ids: frozenset[int] = frozenset()


def resolve_poll_n(
    inputs: PollNInputs,
    region_id: int | None,
    populations: Mapping[int, int | None],
) -> ResolvedN:
    """Resolve the n behind a poll's shares in one region (see module doc).

    Args:
        inputs: The poll's sample size, bases and reported regions.
        region_id: The region, or None for national.
        populations: Region id -> population (None when unknown).

    Returns:
        The resolved n and its source, or :data:`UNRESOLVED`.
    """
    base = inputs.bases.get(region_id)
    if base is not None and base.n is not None:
        return ResolvedN(
            base.n,
            "base",
            base.kind,
            weighted_only=base.is_weighted_only,
            weighted_n=base.weighted_n,
            split_from_macro=base.split_from_macro,
        )
    if region_id is None:
        if inputs.sample_size is None or inputs.sample_size <= 0:
            return UNRESOLVED
        return ResolvedN(inputs.sample_size, "sample_size", "sample_size")

    if region_id not in inputs.reported_region_ids:
        return UNRESOLVED
    national = inputs.bases.get(None)
    basis: NBasis
    if national is not None and national.n is not None:
        poll_n: int | None = national.n
        basis, weighted_only = national.kind, national.is_weighted_only
    else:
        poll_n, basis, weighted_only = inputs.sample_size, "sample_size", False
    if poll_n is None or poll_n <= 0:
        return UNRESOLVED
    population = populations.get(region_id)
    reported = [populations.get(rid) for rid in inputs.reported_region_ids]
    if not population or any(not pop for pop in reported):
        return UNRESOLVED
    total = sum(pop for pop in reported if pop)
    estimate = round(poll_n * population / total)
    if estimate < 1:
        return UNRESOLVED
    return ResolvedN(estimate, "estimate", basis, weighted_only=weighted_only)


@dataclass(frozen=True, slots=True)
class PollNIndex:
    """Resolution inputs for many polls, for repeated :meth:`resolve` calls.

    Attributes:
        inputs: Poll id -> its inputs.
        populations: Region id -> population.
    """

    inputs: Mapping[int, PollNInputs]
    populations: Mapping[int, int | None]

    @classmethod
    def from_records(
        cls,
        polls: Iterable[Poll],
        rows: Iterable[PollRow],
        bases: Iterable[PollBase],
        regions: Iterable[Region],
    ) -> PollNIndex:
        """Build the index from ORM objects already loaded.

        Args:
            polls: The polls to index (rows/bases of other polls are ignored).
            rows: Their poll rows.
            bases: Their captured bases.
            regions: The regions their rows and bases name.

        Returns:
            The index.
        """
        poll_list = list(polls)
        poll_ids = {poll.id for poll in poll_list}
        reported: dict[int, set[int]] = defaultdict(set)
        for row in rows:
            region_id = reported_region_id(row.region_id, row.percentage)
            if row.poll_id in poll_ids and region_id is not None:
                reported[row.poll_id].add(region_id)
        captured: dict[int, dict[int | None, CapturedBase]] = defaultdict(dict)
        for base in bases:
            if base.poll_id in poll_ids:
                captured[base.poll_id][base.region_id] = CapturedBase(
                    base.unweighted_n, base.weighted_n, base.kind, base.split_from_macro
                )
        inputs = {
            poll.id: PollNInputs(
                sample_size=poll.sample_size,
                bases=captured.get(poll.id, {}),
                reported_region_ids=frozenset(reported.get(poll.id, ())),
            )
            for poll in poll_list
        }
        return cls(inputs, {region.id: region.population for region in regions})

    @classmethod
    def load(cls, session: Session, poll_ids: Iterable[int]) -> PollNIndex:
        """Load the index for *poll_ids* in four queries.

        Args:
            session: An open session.
            poll_ids: The polls to index.

        Returns:
            The index (polls that do not exist are left out).
        """
        ids = sorted(set(poll_ids))
        if not ids:
            return cls({}, {})
        polls = session.scalars(select(Poll).where(Poll.id.in_(ids))).all()
        rows = session.scalars(
            select(PollRow).where(
                PollRow.poll_id.in_(ids), PollRow.region_id.is_not(None)
            )
        ).all()
        bases = session.scalars(select(PollBase).where(PollBase.poll_id.in_(ids))).all()
        map_ids = {poll.map_id for poll in polls}
        regions = session.scalars(
            select(Region).where(Region.map_id.in_(map_ids))
        ).all()
        return cls.from_records(polls, rows, bases, regions)

    def resolve(self, poll_id: int, region_id: int | None) -> ResolvedN:
        """Resolve one poll's n in one region; unknown polls are unresolved."""
        inputs = self.inputs.get(poll_id)
        if inputs is None:
            return UNRESOLVED
        return resolve_poll_n(inputs, region_id, self.populations)
