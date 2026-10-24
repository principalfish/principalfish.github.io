"""Immutable, date-resolved Senate fields and optional chamber counting context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_SENATE_CYCLES = (
    Path(__file__).resolve().parents[1] / "models/us/senate_cycles/cycles-v1.json"
)
_CLASS_COUNTS = {1: 33, 2: 33, 3: 34}


@dataclass(frozen=True, slots=True)
class SenateRace:
    state: str
    seat_class: int
    baseline_id: str
    special: bool = False


@dataclass(frozen=True, slots=True)
class SenateMember:
    state: str
    seat_class: int
    candidate: str
    party: str


@dataclass(frozen=True, slots=True)
class SenateControlPolicy:
    identity: str
    vice_president: str | None
    alignments: tuple[tuple[str, str, str | None], ...]
    provenance: str


@dataclass(frozen=True, slots=True)
class SenateCycle:
    identity: str
    target_year: int
    effective_from: date
    effective_until: date
    regular_class: int
    baseline_name: str
    baseline_id: str
    election_date: str
    races: tuple[SenateRace, ...]
    roster: tuple[SenateMember, ...] | None
    roster_path: Path | None
    roster_sha256: str | None
    control_policy: SenateControlPolicy | None
    provenance: str
    source_json: str
    roster_json: str | None

    @property
    def states(self) -> frozenset[str]:
        return frozenset(race.state for race in self.races)

    @property
    def baseline_overrides(self) -> dict[str, str]:
        return {race.state: race.baseline_id for race in self.races if race.special}

    def require_chamber(self) -> tuple[SenateMember, ...]:
        """Never substitute a current roster for unknown future holdovers."""
        if self.roster is None or self.control_policy is None:
            raise ValueError(
                f"Senate {self.target_year} full chamber requires a verified "
                "post-election roster and explicit control policy; only the "
                "contested field is configured"
            )
        return self.roster

    def log_description(self) -> str:
        return (
            f"SENATE_CYCLE target={self.target_year} config={self.identity} "
            f"regular_class={self.regular_class} contested={len(self.races)} "
            f"baseline={self.baseline_id} specials="
            + ",".join(f"{r.state}:{r.baseline_id}" for r in self.races if r.special)
        )


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError(f"Senate config {label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Senate config {label} must be nonempty text")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"Senate config {label} must be an integer")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"Senate config {label} must be an array")
    return value


def _read_object(path: Path) -> dict[str, Any]:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read pinned Senate config {path}: {exc}") from exc


def _roster(
    value: object, directory: Path, roster_text: str | None = None
) -> tuple[tuple[SenateMember, ...] | None, Path | None, str | None]:
    if value is None:
        return None, None, None
    source = _object(value, "roster")
    _text(source.get("provenance"), "roster provenance")
    filename = _text(source.get("file"), "roster file")
    if Path(filename).name != filename:
        raise ValueError("Pinned Senate roster must be beside its cycle configuration")
    path = directory / filename
    expected_hash = _text(source.get("sha256"), "roster sha256")
    try:
        raw = path.read_bytes() if roster_text is None else roster_text.encode("utf-8")
        actual_hash = hashlib.sha256(raw).hexdigest()
    except OSError as exc:
        raise ValueError(f"Cannot read pinned Senate roster {path}: {exc}") from exc
    if actual_hash != expected_hash:
        raise ValueError(f"Pinned Senate roster checksum mismatch: {path}")
    payload = _object(json.loads(raw), "roster")
    members: list[SenateMember] = []
    states: set[str] = set()
    for raw_state in _list(payload.get("seats"), "roster seats"):
        state = _object(raw_state, "roster state")
        name = _text(state.get("n"), "state name")
        if name in states:
            raise ValueError(f"Duplicate Senate roster state: {name}")
        states.add(name)
        state_members = _list(state.get("members"), "state members")
        if len(state_members) != 2:
            raise ValueError(f"Senate roster needs two slots for {name}")
        classes: set[int] = set()
        for raw_member in state_members:
            member = _object(raw_member, "member")
            seat_class = _integer(member.get("class"), "member class")
            if seat_class not in _CLASS_COUNTS or seat_class in classes:
                raise ValueError(
                    f"Invalid or duplicate Senate slot: {name}/{seat_class}"
                )
            classes.add(seat_class)
            members.append(SenateMember(
                name, seat_class, _text(member.get("name"), "candidate"),
                _text(member.get("party"), "party"),
            ))
    if len(states) != 50 or any(
        sum(member.seat_class == cls for member in members) != count
        for cls, count in _CLASS_COUNTS.items()
    ):
        raise ValueError("Pinned Senate roster must contain all 100 valid class slots")
    return tuple(members), path, actual_hash


def _policy(value: object) -> SenateControlPolicy | None:
    if value is None:
        return None
    source = _object(value, "control policy")
    if "vice_president" not in source:
        raise ValueError("Senate control policy needs an explicit VP scenario")
    vp = source["vice_president"]
    if vp is not None and (
        not isinstance(vp, str) or vp not in {"democrat", "republican"}
    ):
        raise ValueError("Unsupported Senate vice-presidential caucus")
    alignments: list[tuple[str, str, str | None]] = []
    identities: set[tuple[str, str]] = set()
    for raw in _list(source.get("alignments"), "alignments"):
        alignment = _object(raw, "alignment")
        state = _text(alignment.get("state"), "alignment state")
        candidate = _text(alignment.get("candidate"), "alignment candidate")
        caucus = alignment.get("caucus")
        if caucus is not None and (
            not isinstance(caucus, str) or caucus not in {"democrat", "republican"}
        ):
            raise ValueError("Unsupported Senate independent alignment")
        if (state, candidate) in identities:
            raise ValueError("Duplicate Senate candidate alignment")
        identities.add((state, candidate))
        alignments.append((state, candidate, caucus))
    return SenateControlPolicy(
        _text(source.get("id"), "policy id"), vp, tuple(alignments),
        _text(source.get("provenance"), "policy provenance"),
    )


def _cycle(
    source: dict[str, Any], directory: Path, roster_text: str | None = None
) -> SenateCycle:
    year = _integer(source.get("target_year"), "target year")
    regular_class = _integer(source.get("regular_class"), "regular class")
    if (
        regular_class not in _CLASS_COUNTS or year % 2
        or regular_class != ((year - 2018) // 2) % 3 + 1
    ):
        raise ValueError("Invalid Senate target year or regular class")
    start = date.fromisoformat(_text(source.get("effective_from"), "effective from"))
    end = date.fromisoformat(_text(source.get("effective_until"), "effective until"))
    if start >= end or not start <= date(year, 11, 1) < end:
        raise ValueError("Invalid Senate cycle effective dates")
    baseline_id = _text(source.get("baseline_id"), "baseline id")
    states = [_text(state, "regular state") for state in _list(
        source.get("regular_states"), "regular states"
    )]
    if len(states) != _CLASS_COUNTS[regular_class] or len(set(states)) != len(states):
        raise ValueError("Senate regular field has missing or duplicate states")
    races = [SenateRace(state, regular_class, baseline_id) for state in states]
    for raw in _list(source.get("specials"), "specials"):
        special = _object(raw, "special")
        state = _text(special.get("state"), "special state")
        seat_class = _integer(special.get("seat_class"), "special class")
        if seat_class not in _CLASS_COUNTS:
            raise ValueError("Invalid Senate special class")
        if any(race.state == state for race in races):
            raise ValueError(f"Unsupported multiple Senate contests in {state}")
        races.append(SenateRace(
            state, seat_class,
            _text(special.get("baseline_id"), "special baseline"), True,
        ))
    roster, roster_path, roster_hash = _roster(
        source.get("roster"), directory, roster_text
    )
    policy = _policy(source.get("control_policy"))
    if (roster is None) != (policy is None):
        raise ValueError(
            "Senate chamber roster and control policy must be configured together"
        )
    if roster is not None:
        slots = {(member.state, member.seat_class) for member in roster}
        if any((race.state, race.seat_class) not in slots for race in races):
            raise ValueError("Senate contested field does not match its pinned roster")
        if set(states) != {
            member.state for member in roster if member.seat_class == regular_class
        }:
            raise ValueError(
                "Senate regular field differs from its pinned roster class"
            )
    return SenateCycle(
        _text(source.get("id"), "cycle id"), year, start, end, regular_class,
        _text(source.get("baseline_name"), "baseline name"), baseline_id,
        _text(source.get("election_date"), "election date"), tuple(races),
        roster, roster_path, roster_hash, policy,
        _text(source.get("provenance"), "cycle provenance"),
        json.dumps(source, sort_keys=True, separators=(",", ":")),
        (roster_text if roster_text is not None
         else roster_path.read_text(encoding="utf-8"))
        if roster_path is not None else None,
    )


def resolve_senate_cycle(
    as_of: date, config_path: Path = DEFAULT_SENATE_CYCLES
) -> SenateCycle:
    """Resolve only explicitly configured dates; never consult today's roster."""
    payload = _read_object(config_path)
    if type(payload.get("schema")) is not int or payload["schema"] != 1:
        raise ValueError("Unsupported Senate cycle configuration schema")
    cycles = sorted(
        (_cycle(_object(raw, "cycle"), config_path.parent)
         for raw in _list(payload.get("cycles"), "cycles")),
        key=lambda cycle: cycle.effective_from,
    )
    if len({cycle.identity for cycle in cycles}) != len(cycles):
        raise ValueError("Duplicate Senate cycle configuration identity")
    if any(
        left.effective_until > right.effective_from
        for left, right in zip(cycles, cycles[1:])
    ):
        raise ValueError("Overlapping Senate cycle effective dates")
    for cycle in cycles:
        if cycle.effective_from <= as_of < cycle.effective_until:
            return cycle
    raise ValueError(
        f"No pinned Senate cycle configured for {as_of}; "
        "configure this period before running"
    )


def senate_cycle_snapshot(cycle: SenateCycle) -> dict[str, object]:
    """Freeze the field, policy and complete roster alongside deterministic votes."""
    payload: dict[str, object] = {
        "schema": "pf-senate-cycle-v1", "cycle": json.loads(cycle.source_json),
        "roster_json": cycle.roster_json,
    }
    payload["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload


def senate_cycle_from_snapshot(value: object, as_of: date) -> SenateCycle:
    """Read saved counting inputs without touching current configuration files."""
    snapshot = _object(value, "saved context")
    if (
        set(snapshot) != {"schema", "cycle", "roster_json", "sha256"}
        or snapshot["schema"] != "pf-senate-cycle-v1"
    ):
        raise ValueError("Unsupported saved Senate context")
    payload = {key: item for key, item in snapshot.items() if key != "sha256"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if snapshot["sha256"] != digest:
        raise ValueError("Saved Senate context checksum mismatch")
    source = _object(snapshot["cycle"], "saved cycle")
    roster_text = snapshot["roster_json"]
    if source.get("roster") is not None and not isinstance(roster_text, str):
        raise ValueError("Saved Senate context is missing its frozen roster")
    if source.get("roster") is None and roster_text is not None:
        raise ValueError("Unexpected saved Senate roster")
    cycle = _cycle(source, Path("."), roster_text)
    if not cycle.effective_from <= as_of < cycle.effective_until:
        raise ValueError("Saved Senate cycle does not match the run date")
    return cycle
