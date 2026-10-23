"""Explicit, fail-closed US uncertainty release decisions, separate from maths."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from model_support.us_simulation import US_KERNEL_VERSION, UsErrorConfig

HOUSE_RELEASE = Path(__file__).resolve().parents[1] / "models/us/house-release.json"


class UsReleaseError(ValueError):
    """A model is not approved for operational uncertainty calculations."""


def house_target_cycle(as_of: date) -> int:
    """Use the next even-year field, rolling forward each odd-year January."""
    return as_of.year + as_of.year % 2


@dataclass(frozen=True, slots=True)
class HouseRelease:
    state: Mapping[str, Any]
    calibration: Mapping[str, Any]
    blockers: tuple[str, ...]

    @property
    def enabled(self) -> bool:
        return not self.blockers

    def require_enabled(self) -> None:
        if self.blockers:
            raise UsReleaseError(
                "House uncertainty is gated: " + "; ".join(self.blockers)
                + ". Use --no-uncertainty for the central projection.",
            )

    def error_config(self) -> UsErrorConfig:
        self.require_enabled()
        config = self.calibration
        return UsErrorConfig(
            assumption_label=str(config["assumption_label"]),
            state_scale=float(config["state_scale"]),
            race_scale=float(config["race_scale"]),
            local_scale=float(config["local_scale"]),
            design_effect=float(config["design_effect"]),
            n_eff_cap=float(config["n_eff_cap"]),
            lv_fraction=float(config["lv_fraction"]),
            rv_fraction=float(config["rv_fraction"]),
            adult_fraction=float(config["adult_fraction"]),
            unknown_fraction=float(config["unknown_fraction"]),
        )


def house_release(path: Path = HOUSE_RELEASE) -> HouseRelease:
    """Read release and accepted calibration; never upgrade a candidate implicitly."""
    try:
        state = json.loads(path.read_text())
        if (
            not isinstance(state, dict)
            or state.get("schema") != "pf-us-house-release-v1"
        ):
            raise ValueError("unsupported House release format")
        calibration_path = path.parent / state["calibration_file"]
        config = json.loads(calibration_path.read_text())
        if not isinstance(config, dict):
            raise ValueError("calibration must be an object")
        if (
            config.get("schema") != "pf-us-house-calibration-candidate-v1"
            or config.get("model") != "us-house"
        ):
            raise ValueError("unsupported House calibration format or model")
        if (
            type(state.get("target_cycle")) is not int
            or state["target_cycle"] <= 0
            or state["target_cycle"] % 2
        ):
            raise ValueError("release requires a positive even-year target cycle")
        for name in ("map_name", "baseline_name", "comparability_id"):
            if not isinstance(state.get(name), str) or not state[name].strip():
                raise ValueError(f"release requires {name}")
        messages = state.get("blockers")
        if not isinstance(messages, list) or any(
            not isinstance(message, str) or not message.strip() for message in messages
        ):
            raise ValueError("release blockers must be an explicit list of messages")
        blockers = []
        if state.get("enabled") is not True:
            blockers.append("production release is disabled")
        if state.get("geography_validated") is not True:
            blockers.append("target-cycle House geography/baseline is unresolved")
        if config.get("accepted") is not True:
            blockers.append("calibration and labelled assumptions are unaccepted")
        if config.get("kernel_version") != US_KERNEL_VERSION:
            blockers.append("calibration kernel version differs from the runner")
        if not isinstance(config.get("assumption_label"), str):
            blockers.append("accepted calibration needs its explicit assumption label")
        blockers.extend(messages)
        blockers.extend(str(message) for message in config.get("release_blockers", []))
        release = HouseRelease(state, config, tuple(blockers))
        if release.enabled:
            release.error_config()
            sigma = config.get("national_margin_sigma")
            if (
                isinstance(sigma, bool)
                or not isinstance(sigma, (int, float))
                or not math.isfinite(sigma)
                or sigma < 0
            ):
                raise ValueError(
                    "accepted calibration needs finite non-negative "
                    "national margin sigma",
                )
        return release
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UsReleaseError(f"Cannot load House release settings: {exc}") from exc
