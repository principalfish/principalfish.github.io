"""Input validation shared by CLI runners and console forms."""

import argparse
import json
import math
from datetime import date, timedelta


def validate_half_life(value: float) -> float:
    """Require a finite, strictly positive poll decay half-life."""
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("--half-life-days must be greater than zero and finite")
    return value


def validate_prior_weight(value: float) -> float:
    """Require a finite, nonnegative seat-poll prior weight."""
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("--seat-prior-weight must be finite and zero or greater")
    return value


def validate_day_count(value: int, flag: str) -> int:
    """Reject negative durations rather than silently clamping them."""
    if value < 0:
        raise ValueError(f"{flag} must be zero or greater")
    return value


def validate_date_window(since_date: date, as_of_date: date) -> None:
    """Require an ordered inclusive polling window."""
    if since_date > as_of_date:
        raise ValueError(
            "--since-days-back/--since-date must be older than or equal to as-of"
        )


def validate_date_range(start_date: date, end_date: date) -> None:
    """Require an ordered inclusive historical run range."""
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")


def single_date_window(args: argparse.Namespace, today: date) -> tuple[date, date]:
    """Resolve a single-date window after validating explicit dates and durations."""
    validate_day_count(args.as_of_days_back, "--as-of-days-back")
    validate_day_count(args.since_days_back, "--since-days-back")
    as_of_date = (
        date.fromisoformat(args.as_of_date)
        if args.as_of_date
        else today - timedelta(days=args.as_of_days_back)
    )
    since_date = (
        date.fromisoformat(args.since_date)
        if args.since_date
        else today - timedelta(days=args.since_days_back)
    )
    validate_date_window(since_date, as_of_date)
    return since_date, as_of_date


def validate_run_arguments(args: argparse.Namespace, today: date) -> None:
    """Check all shared flags before opening a database or resetting outputs."""
    validate_half_life(args.half_life_days)
    for attribute, flag in (
        ("as_of_days_back", "--as-of-days-back"),
        ("since_days_back", "--since-days-back"),
        ("lookback_days", "--lookback-days"),
    ):
        validate_day_count(getattr(args, attribute), flag)
    if hasattr(args, "seat_prior_weight"):
        validate_prior_weight(args.seat_prior_weight)
    if bool(args.start_date) != bool(args.end_date):
        raise ValueError("--start-date and --end-date must be supplied together")
    if args.start_date:
        validate_date_range(
            date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
        )
        # Single-date overrides are ignored in range mode, but malformed dates
        # still indicate an operator input error.
        for value in (args.as_of_date, args.since_date):
            if value:
                date.fromisoformat(value)
        if args.as_of_date and args.since_date:
            validate_date_window(
                date.fromisoformat(args.since_date), date.fromisoformat(args.as_of_date)
            )
    else:
        single_date_window(args, today)


def validate_manual_share(value: object) -> float:
    """Validate one partial override without imposing a complete 100% total."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.0 <= value <= 100.0
        or not math.isfinite(value)
    ):
        raise ValueError("Manual poll shares must be finite numbers from 0 to 100")
    return float(value)


def parse_manual_shares(raw: str) -> dict[str, float]:
    """Require a JSON object of party names and numeric partial share values."""
    values = json.loads(raw)
    if not isinstance(values, dict) or any(
        not isinstance(name, str) for name in values
    ):
        raise ValueError(
            "--poll-shares must be a JSON object mapping party names to shares"
        )
    return {name: validate_manual_share(value) for name, value in values.items()}
