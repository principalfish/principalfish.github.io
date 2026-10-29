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
    HouseAnchorCatalogue,
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


@dataclass(frozen=True, slots=True)
class SenateAnchorPolicy:
    catalogue: HouseAnchorCatalogue
    accepted_years: tuple[int, ...]
    unavailable_years: tuple[int, ...]
    policies: Mapping[str, str]

    def resolve(self, baseline_by_seat: Mapping[int, str]) -> dict[int, SenateHouseAnchor]:
        years = {
            item.year: item
            for item in self.catalogue.for_baselines(baseline_by_seat.values())
        }
        if unavailable := sorted(set(years) - set(self.accepted_years)):
            raise HouseAnchorError(
                f"House anchors unavailable for Senate baseline years {unavailable}; "
                "configure an approved reference before computing this run",
            )
        return {
            seat_id: SenateHouseAnchor(
                baseline_id, years[int(baseline_id[:4])], self.catalogue.sha256,
                SENATE_ANCHOR_POLICY_SHA256,
            )
            for seat_id, baseline_id in baseline_by_seat.items()
        }

    def unavailable_reason(self, baseline_id: str) -> str | None:
        evidence, = self.catalogue.for_baselines((baseline_id,))
        if evidence.year in self.accepted_years:
            return None
        return self.policies.get(
            str(evidence.year), evidence.view("published-general").qualification,
        )


def load_senate_anchor_policy(
    *,
    policy_path: Path = SENATE_ANCHOR_POLICY_PATH,
    catalogue_path: Path = CATALOGUE_PATH,
) -> SenateAnchorPolicy:
    """Verify pinned policy and catalogue bytes once before resolving races."""
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
    ):
        raise HouseAnchorError("Invalid accepted Senate House anchor policy")
    catalogue = inspect_house_anchor_catalogue(catalogue_path)
    if catalogue.sha256 != policy.get("candidate_sha256"):
        raise HouseAnchorError("Accepted Senate policy names another candidate digest")
    return SenateAnchorPolicy(
        catalogue, tuple(policy["accepted_years"]),
        tuple(policy["unavailable_years"]), cast(dict[str, str], policy["policies"]),
    )


def resolve_senate_house_anchors(
    baseline_by_seat: Mapping[int, str],
    *,
    policy_path: Path = SENATE_ANCHOR_POLICY_PATH,
    catalogue_path: Path = CATALOGUE_PATH,
) -> dict[int, SenateHouseAnchor]:
    """Resolve actual race baselines; missing/unapproved references fail closed."""
    return load_senate_anchor_policy(
        policy_path=policy_path, catalogue_path=catalogue_path,
    ).resolve(baseline_by_seat)
