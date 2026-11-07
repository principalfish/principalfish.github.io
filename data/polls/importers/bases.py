"""Voting-intention table bases: parse, apportion and store them.

A poll's headline shares are percentages of some base: the respondents who
named a party, a turnout-filtered sample, or everyone interviewed. Each
Westminster importer reads the base its VI table prints per column (national
plus every region column it already reads shares from) into ``ParsedBase``
values on its ``ParsedPoll``. :func:`plan_poll_bases` turns them into one
``PlannedPollBase`` per internal region, apportioning a column that covers
several regions by population, and :func:`replace_poll_bases` writes them in
the importer's commit session alongside the poll rows.

Base extraction is tolerant by design: a source that prints no usable base
(or whose counts fail the importer's checks) gives no ``ParsedBase`` — the n
resolver in ``model_support.poll_bases`` then falls back to
``polls.sample_size`` or a population estimate — and the shares import as
before. Only an inconsistent result (a base naming a region the map lacks, or
two bases for one column) raises, as a missing region in the shares would.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete
from sqlalchemy.orm import Session

from models import PollBase, PollBaseKind, Region

# A printed count cell: digits, optionally with thousands separators.
_COUNT_TEXT = re.compile(r"^\d{1,3}(?:,\d{3})*$|^\d+$")
# A printed weighted base: a count as above, optionally with decimals.
_WEIGHTED_TEXT = re.compile(r"^(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?$")


class _HasN(BaseModel):
    """Requires at least one of ``unweighted_n`` and ``weighted_n``."""

    unweighted_n: int | None = Field(default=None, gt=0)
    weighted_n: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _has_a_count(self) -> Self:
        if self.unweighted_n is None and self.weighted_n is None:
            raise ValueError("a base needs an unweighted or a weighted count")
        return self

    @property
    def weighted_only(self) -> bool:
        """True when only the weighted base was printed."""
        return self.unweighted_n is None


class ParsedBase(_HasN):
    """One column base as printed in a poll's voting-intention table.

    Attributes:
        regions: Internal region names the column covers; empty for the
            national column. Several names mean the column is a macro
            region (e.g. YouGov's "North") to be apportioned by population.
        unweighted_n: Respondents interviewed in the column, or None when the
            pollster prints only a weighted base.
        weighted_n: The column's weighted base, rounded, when printed.
        kind: What the base counts (see ``models.PollBaseKind``).
    """

    regions: tuple[str, ...] = ()
    kind: PollBaseKind


class PlannedPollBase(_HasN):
    """One ``poll_bases`` row planned for insertion.

    Attributes:
        region_id: The internal region's id, or None for national.
        region_name: The region's name (``"National"`` for national), for
            previews.
        unweighted_n: Respondents behind the region's shares (> 0), or None
            for a weighted-only base.
        weighted_n: The weighted base, or None.
        kind: What the base counts.
        split_from_macro: True when apportioned from a macro-region column.
    """

    region_id: int | None
    region_name: str
    kind: PollBaseKind
    split_from_macro: bool = False


class ParsedPollWithBases(Protocol):
    """What every Westminster importer's ``ParsedPoll`` exposes about itself.

    The backfill (``scripts/backfill_poll_bases.py``) checks a re-parsed source
    against the stored poll through this interface, whichever importer parsed
    it.
    """

    @property
    def sample_size(self) -> int: ...

    @property
    def fieldwork_start(self) -> date: ...

    @property
    def fieldwork_end(self) -> date: ...

    @property
    def bases(self) -> list[ParsedBase]: ...

    def national_shares(self) -> dict[str, float]:
        """Return canonical party name -> national share; empty if none."""
        ...


@runtime_checkable
class HasRegionalShares(Protocol):
    """A ``ParsedPoll`` that can say what regional shares an import would store.

    Implemented by importers whose sources have no national shares (YouGov),
    so the backfill can still check a re-parsed source against the stored
    poll.
    """

    def regional_shares(self) -> dict[str, dict[str, float]]:
        """Return internal region name -> canonical party -> share."""
        ...


@dataclass(frozen=True, slots=True)
class RegionRef:
    """The fields of a ``regions`` row the apportioning needs.

    Attributes:
        id: ``regions.id``.
        name: ``regions.name``.
        population: ``regions.population``, or None when unknown.
    """

    id: int
    name: str
    population: int | None

    @classmethod
    def of(cls, region: Region) -> RegionRef:
        """Return the reference for an ORM ``Region``."""
        return cls(region.id, region.name, region.population)


def region_refs(regions: Iterable[Region | RegionRef]) -> dict[str, RegionRef]:
    """Index a map's regions by name for :func:`plan_poll_bases`.

    Args:
        regions: ORM regions or ready-made references.

    Returns:
        Region name -> reference.
    """
    refs = (r if isinstance(r, RegionRef) else RegionRef.of(r) for r in regions)
    return {ref.name: ref for ref in refs}


def count_value(value: object) -> int | None:
    """Read a printed respondent count, or None if the cell holds none.

    Accepts an integer, an integral float (spreadsheets store counts as
    floats) or digit text such as ``"1,491"``. Anything else — blank, a dash,
    a fractional number, a negative or a value with other text in it — is not
    a count. A count of 0 is returned as None too: an empty column has no
    base.

    Args:
        value: A raw cell value.

    Returns:
        The positive count, or None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value) if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not _COUNT_TEXT.fullmatch(text):
            return None
        number = int(text.replace(",", ""))
        return number if number > 0 else None
    return None


def weighted_value(value: object) -> int | None:
    """Read a printed weighted base, rounded to a whole number, or None.

    Weighted bases are often fractional (``1367.843``). A value that is not a
    finite positive number, or that rounds to 0, gives None.

    Args:
        value: A raw cell value.

    Returns:
        The rounded positive base, or None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not _WEIGHTED_TEXT.fullmatch(text):
            return None
        number = float(text.replace(",", ""))
    else:
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number)
    return rounded if rounded > 0 else None


def make_weighted_base(
    regions: Sequence[str], weighted: object, kind: PollBaseKind
) -> ParsedBase | None:
    """Build a weighted-only ``ParsedBase``, or None if there is no base.

    For pollsters that print no unweighted count behind their shares.

    Args:
        regions: Internal region names the column covers (empty = national).
        weighted: The raw weighted-base cell.
        kind: What the base counts.

    Returns:
        The base, or None when *weighted* is not a positive number.
    """
    weighted_n = weighted_value(weighted)
    if weighted_n is None:
        return None
    return ParsedBase(regions=tuple(regions), weighted_n=weighted_n, kind=kind)


def national_shares_from(
    percentages: Mapping[str, Mapping[str, float]], national_key: str
) -> dict[str, float]:
    """Return party -> national share from a party -> column -> share mapping.

    Args:
        percentages: An importer's party -> column (region, macro region or
            *national_key*) -> share mapping.
        national_key: The column key holding the national share.

    Returns:
        Each party's national share; parties without one are left out.
    """
    return {
        party: columns[national_key]
        for party, columns in percentages.items()
        if national_key in columns
    }


def make_base(
    regions: Sequence[str],
    unweighted: object,
    weighted: object,
    kind: PollBaseKind,
) -> ParsedBase | None:
    """Build a ``ParsedBase`` from raw cells, or None if there is no count.

    Args:
        regions: Internal region names the column covers (empty = national).
        unweighted: The raw unweighted-count cell.
        weighted: The raw weighted-base cell, or None when not printed.
        kind: What the base counts.

    Returns:
        The base, or None when *unweighted* is not a positive count.
    """
    unweighted_n = count_value(unweighted)
    if unweighted_n is None:
        return None
    return ParsedBase(
        regions=tuple(regions),
        unweighted_n=unweighted_n,
        weighted_n=weighted_value(weighted),
        kind=kind,
    )


def apportion(total: int, weights: Sequence[int]) -> list[int]:
    """Split *total* into whole parts proportional to *weights*.

    Largest-remainder (Hamilton) method on exact fractions: each part gets the
    floor of its quota, then the parts with the largest remainders get one
    more until the parts sum to *total*. Equal remainders go to the earlier
    weight, so the result is deterministic.

    Args:
        total: The whole number to split; >= 0.
        weights: Positive weights, one per part.

    Returns:
        One part per weight, summing to *total*.

    Raises:
        ValueError: If *weights* is empty or holds a non-positive weight, or
            *total* is negative.
    """
    if not weights or any(weight <= 0 for weight in weights):
        raise ValueError(f"apportion needs positive weights, got {list(weights)}")
    if total < 0:
        raise ValueError(f"apportion needs a non-negative total, got {total}")
    weight_sum = sum(weights)
    quotas = [Fraction(total * weight, weight_sum) for weight in weights]
    parts = [math.floor(quota) for quota in quotas]
    remaining = total - sum(parts)
    by_remainder = sorted(
        range(len(weights)), key=lambda i: (-(quotas[i] - parts[i]), i)
    )
    for index in by_remainder[:remaining]:
        parts[index] += 1
    return parts


def _apportion_or_none(total: int | None, weights: Sequence[int]) -> list[int | None]:
    """Apportion *total* by *weights*, or give one None per weight."""
    if total is None:
        return [None] * len(weights)
    return list(apportion(total, weights))


def plan_poll_bases(
    bases: Sequence[ParsedBase],
    regions: Mapping[str, RegionRef],
) -> list[PlannedPollBase]:
    """Resolve parsed bases to one planned row per national/internal region.

    A base covering one region maps straight onto it. A base covering several
    is apportioned across them by population (unweighted and weighted counts
    each with :func:`apportion`), flagged ``split_from_macro``; a region whose
    shares round to 0 gets no row. When any region of a macro column has no
    population the column is skipped: the resolver's estimate covers it.

    Args:
        bases: The importer's parsed bases.
        regions: The poll map's regions by name.

    Returns:
        Planned rows, national first then in input order.

    Raises:
        ValueError: If a base names a region the map lacks, or two bases cover
            the same region (or both are national).
    """
    planned: list[PlannedPollBase] = []
    seen: set[str | None] = set()
    for base in bases:
        covered: list[str | None] = list(base.regions) if base.regions else [None]
        duplicates = seen.intersection(covered)
        if duplicates:
            names = sorted("National" if name is None else name for name in duplicates)
            raise ValueError(f"Two bases cover the same column: {names}")
        seen.update(covered)

        if not base.regions:
            planned.append(
                PlannedPollBase(
                    region_id=None,
                    region_name="National",
                    unweighted_n=base.unweighted_n,
                    weighted_n=base.weighted_n,
                    kind=base.kind,
                )
            )
            continue

        missing = [name for name in base.regions if name not in regions]
        if missing:
            raise ValueError(f"Base names regions not on the map: {missing}")
        refs = [regions[name] for name in base.regions]

        if len(refs) == 1:
            planned.append(
                PlannedPollBase(
                    region_id=refs[0].id,
                    region_name=refs[0].name,
                    unweighted_n=base.unweighted_n,
                    weighted_n=base.weighted_n,
                    kind=base.kind,
                )
            )
            continue

        populations = [ref.population for ref in refs]
        if any(population is None or population <= 0 for population in populations):
            continue
        weights = [int(population) for population in populations if population]
        unweighted_parts = _apportion_or_none(base.unweighted_n, weights)
        weighted_parts = _apportion_or_none(base.weighted_n, weights)
        for ref, unweighted_n, weighted_n in zip(
            refs, unweighted_parts, weighted_parts, strict=True
        ):
            # The count n is read from (unweighted, else weighted) must be > 0.
            primary = unweighted_n if base.unweighted_n is not None else weighted_n
            if not primary:
                continue
            planned.append(
                PlannedPollBase(
                    region_id=ref.id,
                    region_name=ref.name,
                    unweighted_n=unweighted_n or None,
                    weighted_n=weighted_n or None,
                    kind=base.kind,
                    split_from_macro=True,
                )
            )

    planned.sort(key=lambda row: row.region_id is not None)
    return planned


def replace_poll_bases(
    session: Session, poll_id: int, bases: Sequence[PlannedPollBase]
) -> int:
    """Replace a poll's stored bases with *bases*, in the caller's session.

    Args:
        session: The importer's commit session (its transaction holds the
            poll rows written alongside).
        poll_id: The poll's id.
        bases: The planned rows.

    Returns:
        The number of rows inserted.
    """
    session.execute(delete(PollBase).where(PollBase.poll_id == poll_id))
    for base in bases:
        session.add(
            PollBase(
                poll_id=poll_id,
                region_id=base.region_id,
                unweighted_n=base.unweighted_n,
                weighted_n=base.weighted_n,
                kind=base.kind,
                split_from_macro=base.split_from_macro,
            )
        )
    return len(bases)
