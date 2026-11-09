"""Read each NYT results schema family into :class:`~importers.results.us.nyt.races.Race`.

One parser per layout (see :mod:`importers.results.us.nyt.sources` for which cycles
use which):

- :func:`parse_a` reads schema A (2024 on) and A2 (2022). Both keep races in a
  top-level ``races`` list, with candidate names and parties in
  ``candidate_metadata`` and district totals on the reporting unit whose
  ``level`` is ``"state"``. They differ on ranked-choice voting: A gives the
  tabulation ``rounds`` (matched to candidates by ``nyt_candidate_race_id``);
  A2 gives each candidate's ``votes_round1`` beside a ``total`` that is
  already the final round.
- :func:`parse_b` reads schema B (2020): ``data.races``, one flat ``candidates``
  list per race with ``winner`` flags.
- :func:`parse_c` reads schema C (2018): top-level ``races`` covering every
  office, otherwise shaped like B, with ranked-choice tallies in a candidate's
  ``ranked_choice`` list and no estimated share of the vote counted.

Every parser keeps only the requested chamber, and drops the nationwide
popular-vote race and the non-voting delegates (DC and the territories). Other
offices on the same file (governors, ballot measures, a Public Service
Commission runoff) are skipped. The ``blank_votes`` line NYT adds to some
ranked-choice races is not a candidate and is dropped too.

Any structure a parser does not expect raises :class:`AdapterError` naming the
race, rather than a bare ``KeyError`` or ``TypeError``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date

from importers.results.us.nyt.races import (
    Candidate,
    DecisiveRound,
    Race,
    party_key,
    senate_class_for_year,
    sort_candidates,
)
from importers.results.us.nyt.sources import Chamber, Kind, Source

logger = logging.getLogger(__name__)

# Nationwide popular-vote "race" and the non-voting delegates' jurisdictions.
DROPPED_STATES = frozenset({"US", "DC", "PR", "GU", "VI", "AS", "MP"})

# NYT pseudo-candidates that are not votes for anyone.
_PSEUDO_CANDIDATES = frozenset({"blank_votes"})

_A_OFFICE_IDS: dict[Chamber, str] = {"house": "H", "senate": "S"}

# Ceiling for any vote or precinct count: far above any US contest, low enough
# that arithmetic on it cannot overflow a float.
MAX_COUNT = 10**9

# Schemas B and C do not say which Senate class a special fills, so the
# specials those cycles held are listed here. A special missing from this
# table is an error rather than a silent ``None``.
_BC_SPECIAL_SENATE_CLASSES: dict[tuple[int, str], int] = {
    (2018, "MN"): 2,
    (2018, "MS"): 2,
    (2020, "AZ"): 3,
    (2020, "GA"): 3,
}

# A race's place on the map: two races with the same slot contest one seat.
_SeatSlot = tuple[int, Chamber, str, bool]


class AdapterError(Exception):
    """Raised when an NYT file is not shaped as its schema should be."""


class _Fields:
    """Typed reads from one JSON object; errors name where it came from."""

    def __init__(self, value: object, where: str) -> None:
        if not isinstance(value, Mapping):
            raise AdapterError(f"{where}: expected an object, got {_kind(value)}")
        self._data: Mapping[str, object] = value
        self.where = where

    def at(self, where: str) -> _Fields:
        """The same object, with errors naming ``where`` instead."""
        return _Fields(self._data, where)

    def get(self, key: str) -> object:
        return self._data.get(key)

    def _require(self, key: str) -> object:
        if key not in self._data:
            raise AdapterError(f"{self.where}: missing {key!r}")
        return self._data[key]

    def text(self, key: str) -> str:
        value = self._require(key)
        if not isinstance(value, str):
            raise AdapterError(f"{self.where}: {key!r} should be text, got {value!r}")
        return value

    def optional_text(self, key: str) -> str | None:
        value = self.get(key)
        if value is None or isinstance(value, str):
            return value
        raise AdapterError(f"{self.where}: {key!r} should be text, got {value!r}")

    def integer(self, key: str) -> int:
        value = self._require(key)
        number = _as_int(value)
        if number is None:
            raise AdapterError(
                f"{self.where}: {key!r} should be a whole number, got {value!r}",
            )
        return number

    def optional_integer(self, key: str) -> int | None:
        value = self.get(key)
        if value is None:
            return None
        return self.integer(key)

    def count(self, key: str) -> int:
        """A vote or precinct count, 0 to ``MAX_COUNT``."""
        value = self.integer(key)
        if not 0 <= value <= MAX_COUNT:
            raise AdapterError(
                f"{self.where}: {key!r} should be 0 to {MAX_COUNT}, got {value}",
            )
        return value

    def optional_count(self, key: str) -> int | None:
        if self.get(key) is None:
            return None
        return self.count(key)

    def optional_pct(self, key: str) -> float | None:
        """A percentage, 0 to 100, checked before ``float()`` can overflow."""
        value = self.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise AdapterError(
                f"{self.where}: {key!r} should be a number, got {value!r}",
            )
        # Also false for NaN, so NaN is refused with out-of-range values.
        if not 0 <= value <= 100:
            raise AdapterError(f"{self.where}: {key!r} should be 0 to 100")
        return float(value)

    def optional_flag(self, key: str) -> bool | None:
        value = self.get(key)
        if value is None or isinstance(value, bool):
            return value
        raise AdapterError(
            f"{self.where}: {key!r} should be true or false, got {value!r}",
        )

    def flag(self, key: str, *, default: bool | None = None) -> bool:
        value = self.get(key)
        if value is None:
            if default is None:
                raise AdapterError(f"{self.where}: missing {key!r}")
            return default
        if not isinstance(value, bool):
            raise AdapterError(
                f"{self.where}: {key!r} should be true or false, got {value!r}",
            )
        return value

    def items(self, key: str) -> list[object]:
        value = self._require(key)
        if not isinstance(value, list):
            raise AdapterError(f"{self.where}: {key!r} should be a list")
        return value

    def child(self, key: str) -> _Fields:
        return _Fields(self._require(key), f"{self.where}: {key!r}")

    def children(self, key: str) -> list[_Fields]:
        return [
            _Fields(value, f"{self.where}: {key}[{index}]")
            for index, value in enumerate(self.items(key))
        ]

    def entries(self) -> Iterator[tuple[str, _Fields]]:
        for key, value in self._data.items():
            yield key, _Fields(value, f"{self.where}: {key!r}")


# Reads a race's share of the vote counted (0-100), or None when unknown.
_PctReader = Callable[[_Fields], float | None]


def parse(source: Source, data: Mapping[str, object]) -> list[Race]:
    """Parse a fetched NYT file with the parser its source's schema calls for.

    Args:
        source: The source the file was fetched for.
        data: The file's parsed top-level JSON object.

    Returns:
        The source chamber's races, in file order.

    Raises:
        AdapterError: If the file is not shaped as its schema should be.
    """
    if source.schema in ("A", "A2"):
        parser = parse_a
    elif source.schema == "B":
        parser = parse_b
    else:
        parser = parse_c
    return parser(data, year=source.year, chamber=source.chamber, kind=source.kind)


def parse_a(
    data: Mapping[str, object],
    *,
    year: int,
    chamber: Chamber,
    kind: Kind,
) -> list[Race]:
    """Parse a schema A or A2 file (2022 onwards).

    Args:
        data: The file's top-level JSON object.
        year: Election cycle year.
        chamber: Chamber to keep; races for other offices are skipped.
        kind: Kind of the source file (``"runoff"`` marks every race a runoff).

    Returns:
        The chamber's races, in file order.

    Raises:
        AdapterError: If the file or a race is not shaped as expected.
    """
    races: list[Race] = []
    for race in _Fields(data, "file").children("races"):
        race_id = race.text("nyt_id")
        with _errors_name(race_id):
            parsed = _parse_a_race(
                race.at(race_id),
                race_id,
                year=year,
                chamber=chamber,
                kind=kind,
            )
        if parsed is not None:
            races.append(parsed)
    _check_one_race_per_seat(races)
    return races


def parse_b(
    data: Mapping[str, object],
    *,
    year: int,
    chamber: Chamber,
    kind: Kind,
) -> list[Race]:
    """Parse a schema B file (2020 and its runoffs).

    ``pct_counted`` is NYT's estimated share of the expected vote, or the share
    of precincts reporting where the race has no estimate. B's
    ``edison_certified`` is not read as certification: the final 2020 Senate
    snapshot, taken long after every state certified, has it false for 34 of
    35 races, so ``certified`` is ``None``.

    Args:
        data: The file's top-level JSON object.
        year: Election cycle year.
        chamber: Chamber to keep; races for other offices are skipped.
        kind: Kind of the source file (``"runoff"`` marks every race a runoff).

    Returns:
        The chamber's races, in file order.

    Raises:
        AdapterError: If the file or a race is not shaped as expected.
    """
    container = _Fields(data, "file").child("data")
    return _parse_bc(
        container.children("races"),
        year=year,
        chamber=chamber,
        kind=kind,
        pct_counted=_eevp_or_precincts_pct,
    )


def parse_c(
    data: Mapping[str, object],
    *,
    year: int,
    chamber: Chamber,
    kind: Kind,
) -> list[Race]:
    """Parse a schema C file (2018 and its runoff).

    Schema C has no estimated share of the expected vote, so ``pct_counted``
    is the share of precincts reporting. It has no certification field, so
    ``certified`` is ``None``.

    Args:
        data: The file's top-level JSON object.
        year: Election cycle year.
        chamber: Chamber to keep; races for other offices are skipped.
        kind: Kind of the source file (``"runoff"`` marks every race a runoff).

    Returns:
        The chamber's races, in file order.

    Raises:
        AdapterError: If the file or a race is not shaped as expected.
    """
    return _parse_bc(
        _Fields(data, "file").children("races"),
        year=year,
        chamber=chamber,
        kind=kind,
        pct_counted=_precincts_reporting_pct,
    )


def apply_runoffs(general: Sequence[Race], runoffs: Sequence[Race]) -> list[Race]:
    """Replace each first-round race with the runoff that settled its seat.

    A runoff matches the general race for the same cycle, chamber and seat,
    special or regular alike, so a state's regular and special Senate races
    each take their own runoff. A general file that already carries a seat's
    runoff is fine: the runoff simply replaces it.

    Args:
        general: Races parsed from the general files.
        runoffs: Races parsed from the runoff files.

    Returns:
        ``general`` in its order, with every matched race swapped for its
        runoff; unmatched races are unchanged.

    Raises:
        AdapterError: If a runoff is not marked as one, two races share a seat
            on either side, or a runoff matches no general race.
    """
    position: dict[_SeatSlot, int] = {}
    for index, race in enumerate(general):
        slot = _slot(race)
        if slot in position:
            other = general[position[slot]].nyt_race_id
            raise AdapterError(
                f"{race.nyt_race_id} and {other} both contest {race.seat_key}",
            )
        position[slot] = index

    result = list(general)
    replaced: dict[_SeatSlot, str] = {}
    unmatched: list[str] = []
    for runoff in runoffs:
        if runoff.decisive_round != "runoff":
            raise AdapterError(f"{runoff.nyt_race_id} is not a runoff race")
        slot = _slot(runoff)
        if slot in replaced:
            raise AdapterError(
                f"{runoff.nyt_race_id} and {replaced[slot]} are both runoffs "
                f"for {runoff.seat_key}",
            )
        replaced[slot] = runoff.nyt_race_id
        if slot not in position:
            unmatched.append(runoff.nyt_race_id)
            continue
        result[position[slot]] = runoff
    if unmatched:
        raise AdapterError(
            f"runoff(s) with no general race to replace: {', '.join(unmatched)}",
        )
    return result


def _parse_a_race(
    race: _Fields,
    race_id: str,
    *,
    year: int,
    chamber: Chamber,
    kind: Kind,
) -> Race | None:
    if race.text("office_id") != _A_OFFICE_IDS[chamber]:
        logger.debug("skipping %s: not a %s race", race_id, chamber)
        return None
    state = race_id.split("-", 1)[0]
    if state in DROPPED_STATES:
        logger.debug("skipping %s: national or non-voting", race_id)
        return None

    race_type = race.text("type").lower()
    is_special = "special" in race_type
    is_runoff = kind == "runoff" or "runoff" in race_type
    seat_number = race.optional_integer("seat_number")
    if chamber == "house":
        if seat_number is None:
            raise AdapterError(f"{race_id}: House race has no seat_number")
        district: int | None = seat_number
        senate_class = None
    else:
        district = None
        if not is_special:
            senate_class = senate_class_for_year(year)
        elif seat_number is None:
            raise AdapterError(f"{race_id}: special has no seat_number (Senate class)")
        else:
            senate_class = seat_number

    metadata = dict(race.child("candidate_metadata").entries())
    unit = _state_unit(race)
    votes, rcv = _a_votes(race, unit, metadata)
    candidates = [
        _a_candidate(candidate_id, metadata, count)
        for candidate_id, count in votes.items()
    ]
    winners = _texts(race.child("outcome"), "won")

    decisive: DecisiveRound | None = "runoff" if is_runoff else rcv
    return Race(
        year=year,
        chamber=chamber,
        kind=kind,
        state=state,
        district=district,
        nyt_race_id=race_id,
        election_date=date.fromisoformat(race.text("election_date")),
        candidates=sort_candidates(candidates),
        winners=winners,
        pct_counted=unit.optional_pct("eevp"),
        is_special=is_special,
        senate_class=senate_class,
        uncontested=race.flag("uncontested"),
        certified=race.optional_flag("certified"),
        decisive_round=decisive,
    )


def _state_unit(race: _Fields) -> _Fields:
    units = [
        unit
        for unit in race.children("reporting_units")
        if unit.get("level") == "state"
    ]
    if len(units) != 1:
        raise AdapterError(
            f"{race.where}: expected one state-level reporting unit, "
            f"found {len(units)}",
        )
    return units[0]


def _a_votes(
    race: _Fields,
    unit: _Fields,
    metadata: Mapping[str, _Fields],
) -> tuple[dict[str, int], DecisiveRound | None]:
    """Return each counted candidate's votes and whether they are an RCV tally.

    Schema A's ``rounds`` win when present: the highest-``order`` round is the
    decisive tally, and candidates eliminated before it are left out. Without
    rounds, the state unit's totals are used; in A2 a ranked-choice race shows
    ``votes_round1`` beside a final-round ``total``, and a candidate eliminated
    in tabulation has a ``total`` of zero.
    """
    rounds = race.get("rounds")
    if isinstance(rounds, list) and rounds:
        return _a_final_round(race, metadata), "rcv-final"
    if rounds is not None and not isinstance(rounds, list):
        raise AdapterError(f"{race.where}: 'rounds' should be a list")

    totals: dict[str, int] = {}
    first_round: dict[str, int] = {}
    for candidate in unit.children("candidates"):
        candidate_id = candidate.text("nyt_id")
        if candidate_id in _PSEUDO_CANDIDATES:
            continue
        counts = candidate.child("votes")
        if candidate_id in totals:
            raise AdapterError(f"{unit.where}: candidate {candidate_id!r} twice")
        totals[candidate_id] = counts.count("total")
        round1 = counts.optional_count("votes_round1")
        if round1 is not None:
            first_round[candidate_id] = round1
    if not any(first_round.get(cid, total) != total for cid, total in totals.items()):
        return totals, None
    eliminated = {
        cid for cid, total in totals.items() if total == 0 and first_round.get(cid, 0)
    }
    return {
        cid: total for cid, total in totals.items() if cid not in eliminated
    }, "rcv-final"


def _a_final_round(race: _Fields, metadata: Mapping[str, _Fields]) -> dict[str, int]:
    by_race_id: dict[int, str] = {}
    for metadata_id, meta in metadata.items():
        shared_id = meta.integer("nyt_candidate_race_id")
        if shared_id in by_race_id:
            raise AdapterError(
                f"{race.where}: nyt_candidate_race_id {shared_id} is "
                f"shared by {by_race_id[shared_id]!r} and {metadata_id!r}",
            )
        by_race_id[shared_id] = metadata_id
    rounds = race.children("rounds")
    final = max(rounds, key=lambda tally: tally.integer("order"))
    votes: dict[str, int] = {}
    for candidate in final.children("candidates"):
        if candidate.flag("previously_eliminated", default=False):
            continue
        race_candidate_id = candidate.integer("nyt_candidate_race_id")
        candidate_id = by_race_id.get(race_candidate_id)
        if candidate_id is None:
            raise AdapterError(
                f"{race.where}: final round lists nyt_candidate_race_id "
                f"{race_candidate_id}, which no candidate has",
            )
        if candidate_id in _PSEUDO_CANDIDATES:
            continue
        if candidate_id in votes:
            raise AdapterError(
                f"{final.where}: candidate {candidate_id!r} twice in the final round",
            )
        votes[candidate_id] = candidate.count("total")
    return votes


def _a_candidate(
    candidate_id: str,
    metadata: Mapping[str, _Fields],
    votes: int,
) -> Candidate:
    meta = metadata.get(candidate_id)
    if meta is None:
        raise AdapterError(f"candidate {candidate_id!r} has no candidate_metadata")
    party = meta.get("party")
    code = _Fields(party, meta.where).optional_text("nyt_id") if party else None
    return _candidate(
        candidate_id,
        _join_name(meta, fallback=candidate_id),
        code or "",
        votes,
    )


def _parse_bc(
    races: list[_Fields],
    *,
    year: int,
    chamber: Chamber,
    kind: Kind,
    pct_counted: _PctReader,
) -> list[Race]:
    parsed: list[Race] = []
    for race in races:
        race_id = race.text("race_id")
        with _errors_name(race_id):
            result = _parse_bc_race(
                race.at(race_id),
                race_id,
                year=year,
                chamber=chamber,
                kind=kind,
                pct_counted=pct_counted,
            )
        if result is not None:
            parsed.append(result)
    _check_one_race_per_seat(parsed)
    return parsed


def _parse_bc_race(
    race: _Fields,
    race_id: str,
    *,
    year: int,
    chamber: Chamber,
    kind: Kind,
    pct_counted: _PctReader,
) -> Race | None:
    if race.text("race_type") != chamber:
        logger.debug("skipping %s: not a %s race", race_id, chamber)
        return None
    state = race.text("state_id").upper()
    if state in DROPPED_STATES:
        logger.debug("skipping %s: national or non-voting", race_id)
        return None

    election_type = race.text("election_type")
    is_special = election_type.startswith("special")
    is_runoff = (
        kind == "runoff"
        or "runoff" in election_type
        or race.flag("runoff", default=False)
    )
    if chamber == "house":
        district: int | None = race.integer("seat")
        senate_class = None
    else:
        district = None
        senate_class = (
            _bc_special_class(race_id, year, state)
            if is_special
            else senate_class_for_year(year)
        )

    candidates, rcv = _bc_candidates(race)
    winners = tuple(
        candidate.text("candidate_id")
        for candidate in race.children("candidates")
        if candidate.flag("winner", default=False)
    )

    decisive: DecisiveRound | None = "runoff" if is_runoff else rcv
    return Race(
        year=year,
        chamber=chamber,
        kind=kind,
        state=state,
        district=district,
        nyt_race_id=race_id,
        election_date=date.fromisoformat(race.text("election_date")),
        candidates=sort_candidates(candidates),
        winners=winners,
        pct_counted=pct_counted(race),
        is_special=is_special,
        senate_class=senate_class,
        uncontested=race.flag("uncontested", default=False),
        certified=None,
        decisive_round=decisive,
    )


def _bc_candidates(race: _Fields) -> tuple[list[Candidate], DecisiveRound | None]:
    """Build a B/C race's candidates, using the final RCV round if it has one.

    A ranked-choice race lists each tabulation round after the first in every
    candidate's ``ranked_choice``; its last entry is the decisive tally, and a
    candidate eliminated during tabulation has zero votes there.
    """
    rows = race.children("candidates")
    tallies = [row.get("ranked_choice") for row in rows]
    has_rcv = [isinstance(tally, list) and bool(tally) for tally in tallies]
    if any(has_rcv) and not all(has_rcv):
        raise AdapterError(
            f"{race.where}: only some candidates have ranked_choice rounds",
        )

    candidates: list[Candidate] = []
    for row in rows:
        candidate_id = row.text("candidate_id")
        if candidate_id in _PSEUDO_CANDIDATES:
            continue
        votes = row.count("votes")
        if any(has_rcv):
            first_round = votes
            final = row.children("ranked_choice")[-1]
            votes = final.count("votes")
            if votes == 0 and first_round > 0:
                continue
        name = _join_name(row, fallback=row.optional_text("name_display") or "")
        candidates.append(
            _candidate(candidate_id, name or candidate_id, row.text("party_id"), votes),
        )
    return candidates, ("rcv-final" if any(has_rcv) else None)


def _bc_special_class(race_id: str, year: int, state: str) -> int:
    try:
        return _BC_SPECIAL_SENATE_CLASSES[(year, state)]
    except KeyError as err:
        raise AdapterError(
            f"{race_id}: no Senate class listed for the {year} {state} special",
        ) from err


def _eevp_or_precincts_pct(race: _Fields) -> float | None:
    eevp = race.optional_pct("eevp")
    return eevp if eevp is not None else _precincts_reporting_pct(race)


def _precincts_reporting_pct(race: _Fields) -> float | None:
    reporting = race.optional_count("precincts_reporting")
    total = race.optional_count("precincts_total")
    if reporting is None or total is None or total <= 0:
        return None
    return 100 * reporting / total


def _candidate(candidate_id: str, name: str, code: str, votes: int) -> Candidate:
    return Candidate(
        nyt_id=candidate_id,
        name=name,
        nyt_party=code,
        party_key=party_key(code),
        votes=votes,
    )


def _join_name(fields: _Fields, *, fallback: str) -> str:
    parts = [fields.optional_text("first_name"), fields.optional_text("last_name")]
    name = " ".join(part.strip() for part in parts if part and part.strip())
    return name or fallback


def _texts(fields: _Fields, key: str) -> tuple[str, ...]:
    values = fields.items(key)
    if not all(isinstance(value, str) for value in values):
        raise AdapterError(f"{fields.where}: {key!r} should list text ids")
    return tuple(str(value) for value in values)


def _slot(race: Race) -> _SeatSlot:
    return (race.year, race.chamber, race.seat_key, race.is_special)


def _check_one_race_per_seat(races: Sequence[Race]) -> None:
    seen: dict[_SeatSlot, str] = {}
    for race in races:
        slot = _slot(race)
        if slot in seen:
            raise AdapterError(
                f"{race.nyt_race_id} and {seen[slot]} both contest {race.seat_key}",
            )
        seen[slot] = race.nyt_race_id


@contextmanager
def _errors_name(race_id: str) -> Iterator[None]:
    """Re-raise anything unexpected while parsing a race as an AdapterError."""
    try:
        yield
    except AdapterError as err:
        message = str(err)
        if race_id in message:
            raise
        raise AdapterError(f"{race_id}: {message}") from err
    except (KeyError, TypeError, ValueError, OverflowError) as err:
        raise AdapterError(f"{race_id}: {type(err).__name__}: {err}") from err


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    # ASCII digits only: ``str.isdigit`` also accepts "²" and "①", which
    # ``int()`` then rejects, and ``int()`` accepts other scripts' digits.
    text = value.strip() if isinstance(value, str) else ""
    if text.isascii() and text.isdecimal():
        return int(text)
    return None


def _kind(value: object) -> str:
    return "nothing" if value is None else type(value).__name__
