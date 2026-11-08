#!/usr/bin/env python3
"""US Senate forecast runner: national uniform swing over the next cycle's Senate class.

Only ~a third of Senate seats are contested each cycle. The next election (2028) is
the Class-3 field, last contested in 2022 — so the projection swings the **2022 US
Senate** result by the national two-party average and picks an FPTP winner per
contested state.

The contested field is pinned to the states that currently hold a seat of the class
the shell's ``senateClassCycle`` puts up in ``nextElectionYear`` (read from
``senate-current.json``, the same snapshot the front end uses). This excludes states
whose baseline race was an off-class special, which projecting the raw baseline would
wrongly include. When the snapshot is unavailable the allowlist is ``None`` and every
baseline-contested seat is projected.

Two shell keys move individual seats off the field's baseline, so the model and the
map cannot disagree: ``senateSpecialElections`` (special elections the next cycle
holds, added to the allowlist with their own baseline election) and
``senateBaselineOverrides`` (seats whose last contest was a special since the regular
class voted — Florida and Ohio swing from their 2026 specials, not from 2022). Both
come from ``map-modes-shell.json``, the same hand-authored file the front end's map
modes are generated from.

Persists a ``us_senate_model`` election (the projected contested seats) and appends
a poll-tracker trend entry. The front end's SenatePredict merges these projected
winners into the full 100-member chamber (``senate-current.json``) for its
"Full Senate" view.

Usage:
    python data/models/us/run_us_senate_model.py --dry-run
    python data/models/us/run_us_senate_model.py --as-of-date 2027-06-01
    python data/models/us/run_us_senate_model.py --start-date 2027-01-01 --end-date 2027-06-01
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from model_support.trends import default_trend_path
from models.us._common import UsModelSpec, main_for_spec

# The export's parser is the one rule for which shell specials count, so the model
# reuses it rather than a copy.
from scripts.export.manifest import (
    senate_baseline_overrides,
    senate_class_up_in,
    senate_next_election_year,
    senate_specials_for_year,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
US_DATA_DIR = REPO_ROOT / "uselectionmaps" / "data"
RESULTS_DIR = US_DATA_DIR / "results"
SENATE_CURRENT_JSON = RESULTS_DIR / "senate-current.json"
# The hand-authored source of truth behind the generated ``map-modes.json``.
MAP_MODES_SHELL_JSON = US_DATA_DIR / "map-modes-shell.json"

# ``mapModes`` is keyed by database map id; 23 is "US Senate 2024".
SENATE_MAP_MODE_KEY = "23"


@dataclass(frozen=True, slots=True)
class SenateSpecial:
    """One special Senate election held alongside a regular cycle.

    Attributes:
        seat: Seat name as the Senate map spells it (a state, e.g. ``"Ohio"``).
        seat_class: Class of the seat being filled early.
            The model does not use it (it projects seats, not classes); the front
            end needs it to know which sitting member the projection replaces.
        year: Cycle the special is held in.
        baseline_election_id: Manifest id of the election this seat swings from,
            rather than the baseline the rest of the field uses.
    """

    seat: str
    seat_class: int
    year: int
    baseline_election_id: str


def _senate_shell(shell_path: Path) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The shell payload and its Senate map mode, or ``None`` when either is unusable."""
    try:
        payload = json.loads(shell_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    map_modes = payload.get("mapModes")
    senate_mode = map_modes.get(SENATE_MAP_MODE_KEY) if isinstance(map_modes, dict) else None
    if not isinstance(senate_mode, dict):
        return None
    return payload, senate_mode


def next_senate_class(shell_path: Path = MAP_MODES_SHELL_JSON) -> int | None:
    """The Senate class the shell's next election contests, or ``None`` if unknowable.

    Resolves ``senateClassCycle`` against ``parliamentFeatures.us_senate.nextElectionYear``
    through the export's own resolver (:func:`~scripts.export.manifest.senate_class_up_in`):
    the class whose cycle lands exactly on that year. ``None`` when the shell, the cycle or
    the year is missing or malformed, or when no class lands on the year.
    """
    loaded = _senate_shell(shell_path)
    if loaded is None:
        return None
    payload, senate_mode = loaded
    next_year = senate_next_election_year(payload.get("parliamentFeatures"))
    if next_year is None:
        return None
    return senate_class_up_in(senate_mode.get("senateClassCycle"), next_year)


def class_state_allowlist(
    seat_class: int | None, snapshot_path: Path = SENATE_CURRENT_JSON
) -> frozenset[str] | None:
    """Return the set of state names holding a seat of ``seat_class``.

    Reads ``senate-current.json``; returns ``None`` when the class is unknown or the
    file is absent or malformed, signalling "project every baseline-contested seat".
    """
    if seat_class is None or not snapshot_path.exists():
        return None
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    states = {
        str(seat.get("n") or "")
        for seat in data.get("seats", [])
        if any(int(member.get("class", 0) or 0) == seat_class for member in seat.get("members", []))
    }
    states.discard("")
    return frozenset(states) or None


def senate_special_elections(
    shell_path: Path = MAP_MODES_SHELL_JSON,
    *,
    current_year: int | None = None,
) -> tuple[SenateSpecial, ...]:
    """Return the special elections the *next* Senate cycle holds.

    Reads ``senateSpecialElections`` from the Senate map mode and keeps only the
    entries whose ``year`` equals ``parliamentFeatures.us_senate.nextElectionYear``
    — one shell key decides the cycle, so a special that has been held (or is not
    yet due) drops out of the model the moment that year moves.

    A next-election year that has already passed keeps nothing, so a shell nobody
    has bumped since the cycle ended cannot leave the model projecting a finished
    race. The export applies the same test before it ships the key, so the model
    and the exported manifest cannot disagree about the field.

    A missing file, unreadable JSON or a missing key all mean "no specials": the
    cycle's regular class field still projects, exactly as it did before specials existed.
    Malformed entries are skipped for the same reason — a half-written shell entry
    must not take the whole runner down at import time — by the export's own
    :func:`~scripts.export.manifest.senate_specials_for_year`, so the model and the
    exported manifest always agree on the field.

    Args:
        shell_path: The hand-authored map-modes shell to read.
        current_year: The year to judge the cycle against; defaults to today's.
            Injectable so a test need not depend on the calendar.
    """
    year_now = date.today().year if current_year is None else current_year
    loaded = _senate_shell(shell_path)
    if loaded is None:
        return ()
    payload, senate_mode = loaded
    next_year = senate_next_election_year(payload.get("parliamentFeatures"))
    if next_year is None or next_year < year_now:
        return ()

    return tuple(
        SenateSpecial(
            seat=entry["seat"],
            seat_class=entry["class"],
            year=next_year,
            baseline_election_id=entry["baselineElectionId"],
        )
        for entry in senate_specials_for_year(
            senate_mode.get("senateSpecialElections"), next_year
        )
    )


def senate_seat_baseline_overrides(
    shell_path: Path = MAP_MODES_SHELL_JSON,
) -> dict[str, str]:
    """Seat name → baseline manifest id, from ``senateBaselineOverrides``.

    An override applies only while its ``class`` is the class the next election contests
    (:func:`next_senate_class`): a state also holds seats of other classes, which swing from
    their own last contest. Otherwise it is not tied to a cycle (a seat keeps swinging from
    its last special until its class next votes there), so there is no year test. Entries are
    parsed by the export's :func:`~scripts.export.manifest.senate_baseline_overrides`, so the
    model and the exported manifest agree on them. A missing or unreadable shell, or an
    unknown contested class, means no overrides.
    """
    loaded = _senate_shell(shell_path)
    contested_class = next_senate_class(shell_path)
    if loaded is None or contested_class is None:
        return {}
    _payload, senate_mode = loaded
    return {
        entry["seat"]: entry["baselineElectionId"]
        for entry in senate_baseline_overrides(senate_mode.get("senateBaselineOverrides"))
        if entry["class"] == contested_class
    }


def senate_seat_baselines(
    specials: Sequence[SenateSpecial], overrides: Mapping[str, str]
) -> dict[str, str]:
    """Every seat that swings from its own baseline: specials first, overrides on top."""
    baselines = {special.seat: special.baseline_election_id for special in specials}
    baselines.update(overrides)
    return baselines


def senate_field_allowlist(
    class_states: frozenset[str] | None, specials: Sequence[SenateSpecial]
) -> frozenset[str] | None:
    """The contested field: every state holding the cycle's class, plus each special's seat.

    ``None`` in means "the class snapshot was unreadable, project every seat the
    baseline contested" — and stays ``None``, because narrowing an unknown field
    down to the specials would project a handful of seats.
    """
    if class_states is None:
        return None
    return class_states | {special.seat for special in specials}


SENATE_SPECIALS: tuple[SenateSpecial, ...] = senate_special_elections()
SENATE_BASELINE_OVERRIDES: dict[str, str] = senate_seat_baseline_overrides()

SPEC = UsModelSpec(
    map_name="US Senate 2024",
    baseline_election_name="2022 US Senate Election",
    election_type="us_senate_model",
    election_name_prefix="US Senate UNS",
    trend_cache_json=default_trend_path("us-senate"),
    trend_cache_meta_json=RESULTS_DIR / "us-senate-trends_meta.json",
    seat_name_allowlist=senate_field_allowlist(
        class_state_allowlist(next_senate_class()), SENATE_SPECIALS
    ),
    # The Senate has no national series of its own: its national swing is the
    # House generic ballot, polled once and stored on the House map.
    national_poll_map_name="US House Districts 2024",
    # Seats off the field's baseline: specials the cycle holds, and seats last filled at a
    # special since the class voted (Florida and Ohio swing from their 2026 specials).
    seat_baseline_overrides=senate_seat_baselines(SENATE_SPECIALS, SENATE_BASELINE_OVERRIDES),
)


def main() -> int:
    """CLI entry point — see module docstring; returns a process exit code."""
    return main_for_spec(SPEC)


if __name__ == "__main__":
    sys.exit(main())
