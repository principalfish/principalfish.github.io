"""Derive the "Current Senate" composition from the 2026 Senate results.

``uselectionmaps/data/results/senate-current.json`` is a ``pf-senate-current-v1``
snapshot of all 100 seats: per state, its two senators (``party``, ``name``,
Senate ``class`` 1/2/3), and ``w``, the pair's combined party or ``"split"``.
``archive/convert_senate_current.py`` first built it from the
congress-legislators list. Once the 2026 Senate election is imported, this
module rebuilds it from that election instead:

- the *holdovers*: every senator of the snapshot whose seat the election did not
  contest, 65 of them (the class 1 and class 3 senators, minus the Florida and
  Ohio class 3 seats filled at the 2026 specials);
- the *winners*: the 33 class 2 regular winners and the Florida and Ohio
  special winners, taking the winning party row's candidate name.

The output has the converter's shape exactly, so every consumer (the export,
the Senate model's class allowlist, the front end) keeps working.

Idempotence: a seat is replaced by *what the election contested*, never by who
sat there before, so the result does not depend on which snapshot goes in. The
input may be the pre-2026 snapshot from the converter or this module's own
earlier output; both give identical bytes for the same election. The snapshot's
class 2 members and the Florida and Ohio class 3 members are dropped either way
and replaced by the election's winners. No marker is stored in the payload.

An uncalled race has no winner yet, so its member is *unknown*, not the party
currently leading::

    {"party": null, "name": null, "class": 2, "c": false, "pc": 62.5}

``c: false`` follows the results payload's uncalled flag; ``pc`` is the percent
of the vote counted to one decimal place and is left out when unknown (or when
the election's count is not a number from 0 to 100, with a warning).

The state's own record follows its *called* members. With both called, ``w`` is
their shared party or ``"split"``. With one called and one uncalled, ``w`` is the
called member's party and the state carries no ``c``: it is drawn in that colour,
solid, until the other race is called, and only the member keeps ``c: false``.
With both uncalled the state carries ``c: false`` and no ``w``, which the front
end draws in the neutral no-result colour.
"""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from importers.results.us.us_import import PARTY_KEY_TO_NAME, STATE_NAMES

DATA_DIR = Path(__file__).resolve().parents[4]
REPO_ROOT = DATA_DIR.parent

SCHEMA = "pf-senate-current-v1"
META_KEY = "meta"

DEFAULT_SNAPSHOT = (
    REPO_ROOT / "uselectionmaps" / "data" / "results" / "senate-current.json"
)
DEFAULT_ELECTION_FILE = DATA_DIR / "inputs" / "us" / "senate-2026.json"

# The class contested at the regular 2026 election, and the standalone specials
# of that cycle (state -> class of the seat filled). The election must hold all
# of them, or the chamber would silently keep the senators they replace.
REGULAR_CLASS = 2
EXPECTED_SPECIALS_2026: Mapping[str, int] = {"Florida": 3, "Ohio": 3}
ELECTION_YEAR = 2026

SEATS_PER_STATE = 2
CHAMBER_SIZE = 100
CLASSES = (1, 2, 3)
# Mode given to a file written where none existed (the temp file starts 0o600).
NEW_FILE_MODE = 0o644

_STATES = frozenset(STATE_NAMES.values())


class CurrentSenateError(ValueError):
    """Raised when the snapshot or election cannot make a valid Current Senate."""


@dataclass(frozen=True, slots=True)
class CurrentSenate:
    """A derived Current Senate and how it was made.

    Attributes:
        payload: The ``pf-senate-current-v1`` payload.
        holdovers: Senators carried over from the snapshot.
        winners: Called winners taken from the election.
        uncalled: Members of races not yet called.
        warnings: Things worth a look that did not stop the build.
    """

    payload: dict[str, Any]
    holdovers: int
    winners: int
    uncalled: int
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Return the counts as one line."""
        total = self.holdovers + self.winners + self.uncalled
        return (
            f"{self.holdovers} holdovers, {self.winners} winners, "
            f"{self.uncalled} uncalled ({total} members)"
        )


def build_current_senate(
    snapshot: Mapping[str, Any],
    election: Mapping[str, Any],
    *,
    special_election: Mapping[str, Any] | None = None,
    regular_class: int = REGULAR_CLASS,
    expected_specials: Mapping[str, int] = EXPECTED_SPECIALS_2026,
) -> CurrentSenate:
    """Build the 100-member Current Senate from a snapshot and a Senate election.

    Args:
        snapshot: A ``pf-senate-current-v1`` payload; the pre-2026 snapshot or
            an earlier derived one (see the module docstring).
        election: The converted Senate election file (``{key: {seatInfo,
            partyInfo}}``, plus an optional ``meta``): one seat per state, its
            ``senateClass`` and ``special`` saying which senator it replaces.
        special_election: The converted file of specials that clash with a
            regular race in the same state, if there is one.
        regular_class: The class the regular election contests; every state
            holding a senator of it must have a regular race in ``election``.
        expected_specials: State -> class of each standalone special that must
            be in ``election`` or ``special_election``.

    Returns:
        The derived payload and its counts. The payload is sorted by state, a
        state's members by class, so the same inputs always give the same bytes.

    Raises:
        CurrentSenateError: If an input is malformed, a state that should be up
            (or a required special) is missing from the election, a contested
            seat has no sitting senator to replace, a state does not end with
            two members of different classes, or the chamber is not 100 members.
    """
    by_state = _snapshot_seats(snapshot)
    contests = _contests(election, regular_class=regular_class, what="election")
    if special_election is not None:
        extra = _contests(
            special_election, regular_class=regular_class, what="special election"
        )
        for state, seat_class in sorted(extra.keys() & contests.keys()):
            raise CurrentSenateError(
                f"{state} class {seat_class} is contested in both the election "
                "and the special election"
            )
        contests.update(extra)
    _check_contests(
        by_state,
        contests,
        regular_class=regular_class,
        expected_specials=expected_specials,
    )

    warnings: list[str] = []
    holdovers = winners = uncalled = 0
    seats: list[dict[str, Any]] = []
    for state in sorted(by_state):
        old = by_state[state]
        replaced = {seat_class for (key, seat_class) in contests if key == state}
        members: list[dict[str, Any]] = []
        for member in old["members"]:
            if member["class"] in replaced:
                continue
            members.append(dict(member))
            holdovers += 1
        for (key, seat_class), contest in contests.items():
            if key != state:
                continue
            member, called = _member(state, seat_class, contest, warnings)
            members.append(member)
            if called:
                winners += 1
            else:
                uncalled += 1
        members.sort(key=lambda member: member["class"])
        _check_state(state, members)
        seats.append(_seat(state, old, members))

    total = holdovers + winners + uncalled
    if total != CHAMBER_SIZE:
        raise CurrentSenateError(
            f"derived chamber has {total} members, not {CHAMBER_SIZE}"
        )
    return CurrentSenate(
        payload={"schema": SCHEMA, "seats": seats},
        holdovers=holdovers,
        winners=winners,
        uncalled=uncalled,
        warnings=warnings,
    )


def render(payload: Mapping[str, Any]) -> str:
    """Return ``payload`` as the text the retired ``convert_senate_current.py`` wrote.

    Raises:
        CurrentSenateError: If the payload holds a NaN or infinite number, which
            is not valid JSON.
    """
    try:
        return (
            json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        )
    except ValueError as err:
        raise CurrentSenateError(f"payload is not valid JSON: {err}") from err


def rebuild_current_senate(
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT,
    election_path: Path = DEFAULT_ELECTION_FILE,
    out_path: Path | None = None,
    dry_run: bool = False,
) -> str:
    """Read the files, derive the Current Senate and write it, never touching the DB.

    The election file's ``meta.special_file``, when set, names a specials file
    in the same folder (a plain file name, no path) that is read as well; a
    missing one is an error, not skipped. ``meta.year`` must be
    :data:`ELECTION_YEAR` and ``meta.chamber`` ``"senate"`` when present.

    Args:
        snapshot_path: The input snapshot.
        election_path: The converted 2026 Senate election file.
        out_path: Where to write; defaults to ``snapshot_path``.
        dry_run: Build and report, but write nothing.

    Returns:
        A report: the counts, any warnings and what was (or would be) written.

    Raises:
        CurrentSenateError: If a file is missing or unreadable, the election is
            not the 2026 Senate, or the build is refused.
        OSError: If the output cannot be written.
    """
    target = snapshot_path if out_path is None else out_path
    snapshot = _read_json(snapshot_path, "snapshot")
    election = _read_json(election_path, "election file")
    meta = election.get(META_KEY)
    special_election = None
    if isinstance(meta, dict):
        if meta.get("chamber") not in (None, "senate") or meta.get("year") not in (
            None,
            ELECTION_YEAR,
        ):
            raise CurrentSenateError(
                f"{election_path.name} is the {meta.get('year')} "
                f"{meta.get('chamber')} election, not {ELECTION_YEAR} senate"
            )
        special_name = meta.get("special_file")
        if special_name is not None:
            special_election = _read_json(
                election_path.with_name(_plain_file_name(special_name)),
                "specials file",
            )

    built = build_current_senate(snapshot, election, special_election=special_election)
    text = render(built.payload)
    lines = [f"Current Senate: {built.summary()}"]
    lines.extend(f"warning: {warning}" for warning in built.warnings)
    changed = _read_text(target) != text
    if dry_run:
        action = "rewrite" if changed else "leave unchanged"
        lines.append(f"dry run: would {action} {target}")
    elif changed:
        _write_atomic(target, text)
        lines.append(f"wrote {target}")
    else:
        lines.append(f"unchanged {target}")
    return "\n".join(lines)


def _plain_file_name(value: Any) -> str:
    """Return ``value`` if it is a bare file name, else refuse it.

    The election file is data, so its ``special_file`` must not steer a read
    outside the folder it sits in.
    """
    if (
        not isinstance(value, str)
        or value in ("", ".", "..")
        or "/" in value
        or "\\" in value
        or "\0" in value
    ):
        raise CurrentSenateError(
            f"meta special_file must be a plain file name, not {value!r}"
        )
    return value


def _read_text(path: Path) -> str | None:
    """Return the file's text, or ``None`` when it is absent or unreadable.

    An unreadable existing output is simply "changed": the write that follows
    replaces it or reports why it cannot.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_json(path: Path, what: str) -> dict[str, Any]:
    if not path.exists():
        raise CurrentSenateError(f"{what} not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise CurrentSenateError(f"{what} {path} cannot be read: {err}") from err
    except ValueError as err:
        raise CurrentSenateError(f"{what} {path} is not valid JSON: {err}") from err
    if not isinstance(data, dict):
        raise CurrentSenateError(f"{what} {path} does not hold a JSON object")
    return data


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temp file, so a crash leaves no half file.

    The new file keeps the target's mode (:data:`NEW_FILE_MODE` for a new one),
    and its bytes are on disk before it replaces the target.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = NEW_FILE_MODE
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _snapshot_seats(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Check the snapshot's shape once and return its seats by state name.

    Every seat needs a state name ``n``, a region ``r`` and a ``members`` list;
    every member a ``party`` and ``name`` (strings, or null for an uncalled
    member) and an integer ``class`` of 1, 2 or 3.
    """
    if snapshot.get("schema") != SCHEMA:
        raise CurrentSenateError(f"snapshot schema is not {SCHEMA!r}")
    seats = snapshot.get("seats")
    if not isinstance(seats, list):
        raise CurrentSenateError("snapshot has no seats list")
    by_state: dict[str, dict[str, Any]] = {}
    for seat in seats:
        if not isinstance(seat, dict) or not isinstance(seat.get("n"), str):
            raise CurrentSenateError("snapshot has a seat without a state name 'n'")
        state = seat["n"]
        if state in by_state:
            raise CurrentSenateError(f"snapshot names {state} twice")
        if "r" not in seat:
            raise CurrentSenateError(f"snapshot seat {state} has no region 'r'")
        members = seat.get("members")
        if not isinstance(members, list):
            raise CurrentSenateError(f"snapshot seat {state} has no members list")
        for member in members:
            _check_snapshot_member(state, member)
        by_state[state] = seat
    return by_state


def _check_snapshot_member(state: str, member: Any) -> None:
    if not isinstance(member, dict):
        raise CurrentSenateError(f"snapshot member of {state} is not an object")
    for key in ("party", "name"):
        if key not in member:
            raise CurrentSenateError(f"snapshot member of {state} has no {key!r}")
        if member[key] is not None and not isinstance(member[key], str):
            raise CurrentSenateError(f"snapshot member of {state} has a bad {key!r}")
    seat_class = member.get("class")
    if (
        isinstance(seat_class, bool)
        or not isinstance(seat_class, int)
        or seat_class not in CLASSES
    ):
        raise CurrentSenateError(
            f"snapshot member of {state} has class {seat_class!r}, not 1, 2 or 3"
        )


def _contests(
    election: Mapping[str, Any],
    *,
    regular_class: int,
    what: str,
) -> dict[tuple[str, int], Mapping[str, Any]]:
    """The election's races keyed ``(state, class of the seat filled)``."""
    meta = election.get(META_KEY)
    if isinstance(meta, dict) and meta.get("chamber") not in (None, "senate"):
        raise CurrentSenateError(
            f"{what} is a {meta.get('chamber')} election, not senate"
        )
    contests: dict[tuple[str, int], Mapping[str, Any]] = {}
    for key, seat in election.items():
        if key == META_KEY:
            continue
        if key not in _STATES:
            raise CurrentSenateError(f"{what} seat {key!r} is not a state")
        if not isinstance(seat, dict) or not isinstance(seat.get("seatInfo"), dict):
            raise CurrentSenateError(f"{what} seat {key!r} has no seatInfo")
        info = seat["seatInfo"]
        seat_class = info.get("senateClass")
        if seat_class is None:
            if info.get("special"):
                raise CurrentSenateError(f"{what} special in {key} has no senateClass")
            seat_class = regular_class
        if isinstance(seat_class, bool) or seat_class not in CLASSES:
            raise CurrentSenateError(
                f"{what} seat {key} has an invalid senateClass {seat_class!r}"
            )
        contests[(key, int(seat_class))] = seat
    if not contests:
        raise CurrentSenateError(f"{what} has no seats")
    return contests


def _check_contests(
    by_state: Mapping[str, Mapping[str, Any]],
    contests: Mapping[tuple[str, int], Mapping[str, Any]],
    *,
    regular_class: int,
    expected_specials: Mapping[str, int],
) -> None:
    """Refuse an election missing a seat that is up or filling one no one holds."""
    missing_regular = sorted(
        state
        for state, seat in by_state.items()
        if any(m["class"] == regular_class for m in seat["members"])
        and (state, regular_class) not in contests
    )
    if missing_regular:
        raise CurrentSenateError(
            f"election has no class {regular_class} race for: "
            f"{', '.join(missing_regular)}"
        )
    missing_special = sorted(
        state
        for state, seat_class in expected_specials.items()
        if (state, seat_class) not in contests
    )
    if missing_special:
        raise CurrentSenateError(
            f"election has no special race for: {', '.join(missing_special)}"
        )
    for state, seat_class in sorted(contests):
        seat = by_state.get(state)
        if seat is None:
            raise CurrentSenateError(f"snapshot has no seat for {state}")
        if not any(m["class"] == seat_class for m in seat["members"]):
            raise CurrentSenateError(
                f"{state} has no sitting class {seat_class} senator for the "
                "election to replace"
            )


def _counted_pct(
    state: str, seat_class: int, pct: Any, warnings: list[str]
) -> float | None:
    """The percent counted to one decimal place, or ``None`` when unknown or invalid."""
    if pct is None:
        return None
    if (
        isinstance(pct, bool)
        or not isinstance(pct, int | float)
        or not math.isfinite(pct)
        or not 0 <= pct <= 100
    ):
        warnings.append(
            f"{state} class {seat_class}: percent counted {pct!r} is not a number "
            "from 0 to 100; left out"
        )
        return None
    return round(float(pct), 1)


def _member(
    state: str,
    seat_class: int,
    contest: Mapping[str, Any],
    warnings: list[str],
) -> tuple[dict[str, Any], bool]:
    """One member from a race: its winner, or an unknown member when uncalled."""
    info = contest["seatInfo"]
    party_info = contest.get("partyInfo")
    if not isinstance(party_info, dict):
        raise CurrentSenateError(f"{state} class {seat_class} has no partyInfo")
    if not info.get("called"):
        member: dict[str, Any] = {
            "party": None,
            "name": None,
            "class": seat_class,
            "c": False,
        }
        pct = _counted_pct(state, seat_class, info.get("pct"), warnings)
        if pct is not None:
            member["pc"] = pct
        return member, False
    party = info.get("current")
    if not isinstance(party, str) or not party:
        raise CurrentSenateError(
            f"{state} class {seat_class} is called but names no winning party"
        )
    row = party_info.get(party)
    name = row.get("name") if isinstance(row, dict) else None
    if not isinstance(name, str) or not name.strip():
        label = PARTY_KEY_TO_NAME.get(party, party)
        name = f"{label} winner"
        warnings.append(
            f"{state} class {seat_class}: winner has no name; using {name!r}"
        )
    return {"party": party, "name": name.strip(), "class": seat_class}, True


def _check_state(state: str, members: Iterable[Mapping[str, Any]]) -> None:
    classes = [member["class"] for member in members]
    if len(classes) != SEATS_PER_STATE:
        raise CurrentSenateError(
            f"{state} has {len(classes)} members, not {SEATS_PER_STATE}"
        )
    if len(set(classes)) != len(classes):
        raise CurrentSenateError(f"{state} has two members of class {classes[0]}")


def _seat(
    state: str, old: Mapping[str, Any], members: list[dict[str, Any]]
) -> dict[str, Any]:
    seat: dict[str, Any] = {"n": state, "r": old["r"]}
    parties = {member["party"] for member in members if member.get("c") is not False}
    if not parties:
        # Nobody is known yet: uncalled, no combined party and no "split".
        seat["c"] = False
    else:
        # One called member colours the state alone; two combine or split.
        seat["w"] = next(iter(parties)) if len(parties) == 1 else "split"
    seat["members"] = members
    return seat
