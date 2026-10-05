"""Narrow shared policies for polling weights and metadata ordering."""

from datetime import date


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
