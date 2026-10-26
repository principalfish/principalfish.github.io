"""Accepted, immutable House references for the Senate's generic-ballot swing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from model_support.us_house_anchors import (
    CATALOGUE_PATH,
    AnchorYear,
    HouseAnchorError,
    inspect_house_anchor_catalogue,
)

SENATE_CENTRAL_ESTIMATOR = "senate-house-anchor-v1"
SENATE_ANCHOR_POLICY_PATH = CATALOGUE_PATH.with_name("accepted-v1.json")
SENATE_ANCHOR_POLICY_SHA256 = (
    "6568de70b4317be92b58a7450de2d4d262e397989e5364cb8a8447bfbd4e71ee"
)


@dataclass(frozen=True, slots=True)
class SenateHouseAnchor:
    baseline_id: str
    evidence: AnchorYear
    catalogue_sha256: str
    policy_sha256: str

    @property
    def shares(self) -> dict[str, float]:
        counts = self.evidence.view("published-general").counts
        if counts is None:
            raise HouseAnchorError("Accepted House anchor has no counts")
        return {
            "Democratic": counts.democratic / counts.denominator * 100.0,
            "Republican": counts.republican / counts.denominator * 100.0,
            "Others": counts.other / counts.denominator * 100.0,
        }

    def snapshot(self) -> dict[str, object]:
        """Freeze evidence and approval, without consulting it during replay."""
        return {
            "baseline_id": self.baseline_id,
            "estimator": SENATE_CENTRAL_ESTIMATOR,
            "policy_sha256": self.policy_sha256,
            "catalogue_sha256": self.catalogue_sha256,
            "selected_view": "published-general",
            "shares": self.shares,
            "evidence": asdict(self.evidence),
        }


def resolve_senate_house_anchors(
    baseline_by_seat: Mapping[int, str],
    *,
    policy_path: Path = SENATE_ANCHOR_POLICY_PATH,
    catalogue_path: Path = CATALOGUE_PATH,
) -> dict[int, SenateHouseAnchor]:
    """Resolve actual race baselines; missing/unapproved references fail closed."""
    try:
        raw = policy_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SENATE_ANCHOR_POLICY_SHA256:
            raise HouseAnchorError("Senate House anchor policy bytes differ")
        policy = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise HouseAnchorError(
            f"Cannot read Senate House anchor policy: {exc}",
        ) from exc
    if (
        not isinstance(policy, dict)
        or policy.get("schema") != "pf-us-senate-house-anchor-policy-v1"
        or policy.get("estimator") != SENATE_CENTRAL_ESTIMATOR
        or policy.get("selected_view") != "published-general"
        or policy.get("accepted_years") != [2016, 2020, 2022]
        or policy.get("unavailable_years") != [2018, 2024]
    ):
        raise HouseAnchorError("Invalid accepted Senate House anchor policy")
    catalogue = inspect_house_anchor_catalogue(catalogue_path)
    if catalogue.sha256 != policy.get("candidate_sha256"):
        raise HouseAnchorError("Accepted Senate policy names another candidate digest")
    years = {
        item.year: item
        for item in catalogue.for_baselines(baseline_by_seat.values())
    }
    accepted = cast(list[int], policy["accepted_years"])
    if unavailable := sorted(set(years) - set(accepted)):
        raise HouseAnchorError(
            f"House anchors unavailable for Senate baseline years {unavailable}; "
            "configure an approved reference before computing this run",
        )
    result: dict[int, SenateHouseAnchor] = {}
    for seat_id, baseline_id in baseline_by_seat.items():
        year = int(baseline_id[:4])
        result[seat_id] = SenateHouseAnchor(
            baseline_id, years[year], catalogue.sha256,
            SENATE_ANCHOR_POLICY_SHA256,
        )
    return result
