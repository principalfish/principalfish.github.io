"""Narrow shared policies for polling weights and metadata ordering."""

from dataclasses import dataclass
from datetime import date
from typing import Generic, TypeVar


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
