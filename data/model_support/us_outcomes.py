"""Model and history boundaries for US held-today uncertainty summaries.

These contracts do not calculate outcomes or change the deployed projections.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from hashlib import sha256
import json
from typing import Literal

UsModel = Literal["house", "senate", "president"]
MetricName = Literal[
    "house_seats",
    "senate_contested_seats",
    "senate_full_seats",
    "electoral_votes",
    "national_vote_share",
]


class UsContractError(ValueError):
    """An uncertainty document cannot be interpreted under its declared contract."""


def positive_integer(value: object, name: str) -> int:
    """Reject booleans and fractional numbers at count/identity boundaries."""
    if type(value) is not int or value <= 0:
        raise UsContractError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class UsRunContext:
    """References to frozen inputs, not lookups against today's mutable records.

    Every referenced snapshot must be saved with the run before persistence is
    enabled. Public documents retain fingerprints, never the private snapshots.
    ``comparability_id`` changes when a configuration revision changes the
    interpretation of a series; ordinary numerical recalibration remains recorded
    separately by the run's configuration hash.
    """

    model: UsModel
    map_id: int
    as_of_date: str
    target_cycle: int
    matchup_id: str
    field_id: str
    baseline_id: str
    central_options_id: str
    comparability_id: str
    roster_id: str | None = None
    control_policy_id: str | None = None
    electoral_allocation_id: str | None = None

    def __post_init__(self) -> None:
        if self.model not in ("house", "senate", "president"):
            raise UsContractError("unsupported US model")
        positive_integer(self.map_id, "map_id")
        positive_integer(self.target_cycle, "target_cycle")
        try:
            parsed = date.fromisoformat(self.as_of_date)
        except (TypeError, ValueError) as exc:
            raise UsContractError("as_of_date must be an ISO calendar date") from exc
        if parsed.isoformat() != self.as_of_date:
            raise UsContractError("as_of_date must use YYYY-MM-DD")
        for name in (
            "matchup_id", "field_id", "baseline_id", "central_options_id",
            "comparability_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise UsContractError(f"{name} must identify a frozen input")
        for name, required in (
            ("roster_id", self.model == "senate"),
            ("control_policy_id", self.model == "senate"),
            ("electoral_allocation_id", self.model == "president"),
        ):
            value = getattr(self, name)
            if required and (not isinstance(value, str) or not value.strip()):
                raise UsContractError(f"{name} is required for {self.model}")
            if not required and value is not None:
                raise UsContractError(f"{name} is not applicable to {self.model}")

    @property
    def segment_id(self) -> str:
        """Stable across dates/seeds, distinct across changes of meaning."""
        comparable = {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "as_of_date"
        }
        encoded = json.dumps(comparable, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class UsOutcomeSpec:
    model: UsModel
    primary_metric: MetricName
    total: int
    majority: int
    outcome_domain: Literal["party", "caucus"]
    success_event: str
    residual_event: str


OUTCOME_SPECS: dict[UsModel, UsOutcomeSpec] = {
    "house": UsOutcomeSpec(
        "house", "house_seats", 435, 218, "party", "majority", "no_majority",
    ),
    "senate": UsOutcomeSpec(
        "senate", "senate_full_seats", 100, 51, "caucus", "control",
        "unresolved_control",
    ),
    "president": UsOutcomeSpec(
        "president", "electoral_votes", 538, 270, "party", "electoral_majority",
        "no_electoral_majority",
    ),
}
