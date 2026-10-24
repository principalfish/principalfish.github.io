#!/usr/bin/env python3
"""US Senate nowcast runner with an immutable field resolved from the run date.

The 2026 Class-2 field uses 2020 results, with Florida/Ohio Class-3 specials
using 2022. From 2027-01-01 the configured target is the 2028 Class-3 field,
using 2022 results. A verified post-2026 roster is required for its full chamber.
The live frontend roster and shell do not determine model inputs.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import UsModelSpec, main_for_spec
from model_support.trends import default_trend_path
from model_support.senate_cycles import DEFAULT_SENATE_CYCLES
from model_support.us_release import SENATE_RELEASE

# Importable because ``_common`` (above) puts ``data/`` on the path, as it does for
# its own ``scripts.export.naming`` import. The export's parser is the one rule
# for which shell specials count, so the model reuses it rather than a copy.
from scripts.export.manifest import senate_next_election_year, senate_specials_for_year

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
        seat_class: Class of the seat being filled early — 3 for the 2026 specials.
            The model does not use it (it projects seats, not classes); the front
            end needs it to know which sitting member the projection replaces.
        year: Cycle the special is held in.
        baseline_election_id: Manifest id of the election this seat swings from
            (``"2022-us-senate"``), not the 2020 race the rest of the field uses.
    """

    seat: str
    seat_class: int
    year: int
    baseline_election_id: str


def class2_state_allowlist(snapshot_path: Path = SENATE_CURRENT_JSON) -> frozenset[str] | None:
    """Return the set of state names holding a Class-2 seat (the 2026 field).

    Reads ``senate-current.json``; returns ``None`` when the file is absent or
    malformed, signalling "project every 2020-contested seat".
    """
    if not snapshot_path.exists():
        return None
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    states = {
        str(seat.get("n") or "")
        for seat in data.get("seats", [])
        if any(int(member.get("class", 0) or 0) == 2 for member in seat.get("members", []))
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
    regular Class-2 field still projects, exactly as it did before specials existed.
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
    try:
        payload = json.loads(shell_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    if not isinstance(payload, dict):
        return ()

    map_modes = payload.get("mapModes")
    senate_mode = map_modes.get(SENATE_MAP_MODE_KEY) if isinstance(map_modes, dict) else None
    next_year = senate_next_election_year(payload.get("parliamentFeatures"))
    if not isinstance(senate_mode, dict) or next_year is None or next_year < year_now:
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


def senate_field_allowlist(
    class2_states: frozenset[str] | None, specials: Sequence[SenateSpecial]
) -> frozenset[str] | None:
    """The contested field: every Class-2 state, plus each special's seat.

    ``None`` in means "the Class-2 snapshot was unreadable, project every seat the
    baseline contested" — and stays ``None``, because narrowing an unknown field
    down to the two specials would project a two-seat Senate.
    """
    if class2_states is None:
        return None
    return class2_states | {special.seat for special in specials}


SPEC = UsModelSpec(
    map_name="US Senate 2024",
    baseline_election_name="2020 US Senate Election",
    election_type="us_senate_model",
    election_name_prefix="US Senate UNS",
    trend_cache_json=default_trend_path("us-senate"),
    trend_cache_meta_json=RESULTS_DIR / "us-senate-trends_meta.json",
    senate_cycle_config=DEFAULT_SENATE_CYCLES,
    uncertainty_release_config=SENATE_RELEASE,
    # The Senate has no national series of its own: its national swing is the
    # House generic ballot, polled once and stored on the House map.
    national_poll_map_name="US House Districts 2024",

)


def main() -> int:
    """CLI entry point — see module docstring; returns a process exit code."""
    # int(): _common is imported by bare module name, so it is untyped here.
    return int(main_for_spec(SPEC))


if __name__ == "__main__":
    sys.exit(main())
