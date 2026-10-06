"""Shared polling observations, weights, metadata and endpoint selection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Generic, TypeVar

from sqlalchemy import select

from db import Database
from model_support.cli import validate_date_window
from models import Poll, PollRow, Pollster


def effective_pollster_weight(weight: float | None) -> float:
    """Default an unspecified weight to one and exclude nonpositive weights."""
    if weight is None:
        return 1.0
    return max(0.0, weight)


def latest_poll_key(
    fieldwork_end: date, fieldwork_start: date, poll_id: int
) -> tuple[date, date, int]:
    """Order polls deterministically, including exact fieldwork-date ties."""
    return fieldwork_end, fieldwork_start, poll_id


@dataclass(frozen=True, slots=True)
class PollContributor:
    """A poll that supplied at least one observation to a weighted average."""

    poll_id: int
    pollster: str
    fieldwork_start: date
    fieldwork_end: date

    @property
    def latest_key(self) -> tuple[date, date, int]:
        return latest_poll_key(self.fieldwork_end, self.fieldwork_start, self.poll_id)


ObservationKey = TypeVar("ObservationKey")
WindowResult = TypeVar("WindowResult")


@dataclass(frozen=True, slots=True)
class PollSource:
    """A reusable snapshot, avoiding repeated history reads while selecting caps."""

    polls: tuple[Poll, ...]
    rows: dict[int, tuple[PollRow, ...]]
    pollsters: tuple[Pollster, ...]

    @classmethod
    def load(cls, db: Database, map_ids: Iterable[int], upper: date) -> PollSource:
        ids = tuple(set(map_ids))
        with db.session() as session:
            polls = tuple(
                session.scalars(
                    select(Poll)
                    .where(Poll.map_id.in_(ids), Poll.fieldwork_end <= upper)
                    .order_by(Poll.fieldwork_end.desc())
                ).all()
            )
            raw_rows = session.scalars(
                select(PollRow)
                .join(Poll)
                .where(Poll.map_id.in_(ids), Poll.fieldwork_end <= upper)
            ).all()
            pollsters = tuple(session.scalars(select(Pollster)).all())
        rows: dict[int, list[PollRow]] = defaultdict(list)
        for row in raw_rows:
            rows[row.poll_id].append(row)
        return cls(polls, {key: tuple(value) for key, value in rows.items()}, pollsters)


def candidate_since(endpoint: date, duration: timedelta) -> date:
    """Preserve duration, bounded by the representable calendar."""
    return endpoint - min(duration, endpoint - date.min)


def select_poll_endpoint(
    raw_endpoints: Iterable[date],
    requested: date,
    since: date,
    collect: Callable[[date, date], WindowResult],
    contributes: Callable[[WindowResult, date], bool],
    *,
    include_earliest: bool = False,
) -> tuple[date | None, date | None, WindowResult | None]:
    """Admit each endpoint in its own preserved-length polling window.

    Raw candidates must be retained: candidate completeness can change as
    reference evidence leaves a window. No candidate after the request is used.
    """
    validate_date_window(since, requested)
    duration = requested - since
    latest: date | None = None
    earliest: date | None = None
    latest_result: WindowResult | None = None
    for endpoint in sorted(
        {day for day in raw_endpoints if day <= requested}, reverse=True
    ):
        result = collect(candidate_since(endpoint, duration), endpoint)
        if not contributes(result, endpoint):
            continue
        earliest = endpoint
        if latest is None:
            latest = endpoint
            latest_result = result
        if not include_earliest:
            break
    return earliest, latest, latest_result


@dataclass(frozen=True, slots=True)
class PollAggregation(Generic[ObservationKey]):
    """Weighted observations and the polls admitted to the same calculation."""

    weighted_sums: dict[ObservationKey, float]
    total_weights: dict[ObservationKey, float]
    contributors: tuple[PollContributor, ...]

    @property
    def latest(self) -> PollContributor | None:
        return max(self.contributors, key=lambda poll: poll.latest_key, default=None)

    @property
    def averages(self) -> dict[ObservationKey, float]:
        return {
            key: value / self.total_weights[key]
            for key, value in self.weighted_sums.items()
            if self.total_weights[key] > 0
        }
