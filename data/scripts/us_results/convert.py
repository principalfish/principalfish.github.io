"""Turn parsed NYT races into the project's US election JSON.

The output is the shape every US importer already reads, one entry per seat::

    {
      "meta": {...},
      "TX-28": {
        "seatInfo": {"current": "democrat", "called": true, "pct": 99.5, ...},
        "partyInfo": {
          "democrat": {"total": 120000, "name": "Ana Garcia"},
          "republican": {"total": 100000, "name": "Ben Lopez"}
        }
      },
      ...
    }

``seatInfo`` gains these status fields beside ``current``:

- ``called``: whether a winner has been called. ``current`` is the winner's
  party key when called and ``null`` while uncalled (no one is elected yet).
- ``pct``: share of the vote counted, 0-100, or ``null`` when NYT gives none.
- ``sameParty``: the top two candidates share a party key (top-two and jungle
  races), so their votes were merged into one party line.
- ``round``: ``null`` for a plain first round, ``"rcv-final"`` for the final
  ranked-choice round, ``"runoff"`` when the votes are the deciding runoff.
- ``uncontested``: NYT marks the race unopposed.
- ``certified``: whether the result is certified; ``null`` when the file does
  not say.
- ``votesSource``: where ``partyInfo`` came from: ``"nyt"``, ``"538"`` or
  ``"nominal"`` (see below).
- Senate seats also carry ``special`` and ``senateClass``.

``meta.special_file`` names the specials file that goes with a main file, or is
``null`` when there is none, so an importer can spot a stale one left behind.

Candidates who share a party key are merged as the FiveThirtyEight converter
does: their votes are summed and the party line is named after the
highest-polling one.

NYT reports no votes for an unopposed race. Such a seat takes the real totals
from the file being replaced when that file has them and names the same
winner: a FiveThirtyEight-format file (no ``meta``), or an earlier NYT
conversion whose seat already carried them forward (``votesSource: "538"``),
so re-converting over a committed NYT file keeps them. Otherwise the sole
candidate gets the nominal total the FiveThirtyEight converter uses, so the
seat shows as a 100% hold instead of an empty seat.

The top-level ``meta`` key records the source credit, the source files, when
they were retrieved and updated, the election date, the map the seats belong
to and the source note shown under the map. Importers skip it as a seat.

A Senate special election in a state with no regular race that year fills the
state's seat in the main file. A special in a state that also holds its
regular race that year goes into a separate special file, imported as its own
election.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from scripts.us_results.fetch import FetchResult
from scripts.us_results.races import Race
from scripts.us_results.sources import Chamber, Kind, supported_years

_USA_SCRIPTS = Path(__file__).resolve().parents[2] / "old_data" / "scripts" / "usa"
if str(_USA_SCRIPTS) not in sys.path:
    sys.path.append(str(_USA_SCRIPTS))

from convert_538_house import (  # noqa: E402
    UNOPPOSED_NOMINAL_VOTES as UNOPPOSED_NOMINAL_VOTES,
)

META_KEY = "meta"
SOURCE_CREDIT = "The New York Times / AP"

# ``seatInfo.votesSource`` values.
VOTES_NYT = "nyt"
VOTES_538 = "538"
VOTES_NOMINAL = "nominal"

_BASE_NOTE = "Results: The New York Times / AP."
_PROVISIONAL_NOTE = " Provisional until certified."
# Notes for a year's main file, beyond the source credit.
_EXTRA_NOTES: dict[tuple[int, Chamber], str] = {
    # NC's 2020 districts came from a 2019 court-ordered map that no Census
    # boundary file has, so the 2012-cycle map shows NC's 2018 lines.
    (2020, "house"): " North Carolina districts shown on 2018 boundaries.",
    # NYT's files leave out California's partial-term Senate specials, won by
    # the same candidate as that year's regular race.
    (2022, "senate"): " California's partial-term special election is not included.",
    (2024, "senate"): " California's partial-term special election is not included.",
}


class ConvertError(Exception):
    """Raised when parsed races cannot be turned into an election file."""


@dataclass(frozen=True, slots=True)
class MapRef:
    """The DB map a converted election's seats belong to.

    Attributes:
        id: Fixed map id (US maps are 21 and up).
        name: Map name, used when the importer creates the map.
    """

    id: int
    name: str


HOUSE_2012_CYCLE_MAP = MapRef(25, "US House Districts 2012 cycle")
HOUSE_2024_MAP = MapRef(21, "US House Districts 2024")
HOUSE_2026_MAP = MapRef(24, "US House Districts 2026")
SENATE_MAP = MapRef(23, "US Senate 2024")

# One House map per district set.
HOUSE_MAPS: dict[int, MapRef] = {
    2018: HOUSE_2012_CYCLE_MAP,
    2020: HOUSE_2012_CYCLE_MAP,
    2022: HOUSE_2024_MAP,
    2024: HOUSE_2024_MAP,
    2026: HOUSE_2026_MAP,
}


def map_for(year: int, chamber: Chamber) -> MapRef:
    """Return the map a ``year``/``chamber`` election's seats belong to.

    Args:
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.

    Returns:
        The map reference.

    Raises:
        ConvertError: If no House map is listed for ``year``.
    """
    if chamber == "senate":
        return SENATE_MAP
    try:
        return HOUSE_MAPS[year]
    except KeyError as err:
        raise ConvertError(f"no House map listed for {year}") from err


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One NYT file a conversion read.

    Attributes:
        kind: ``"general"`` or ``"runoff"``.
        url: Where the file came from.
        retrieved_at: When it was downloaded (UTC).
        source_updated_at: The feed's own updated-at value, verbatim.
    """

    kind: Kind
    url: str
    retrieved_at: datetime
    source_updated_at: str | None

    def to_json(self) -> dict[str, str | None]:
        """Return the file's record as written into ``meta.sources``."""
        return {
            "kind": self.kind,
            "url": self.url,
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_updated_at": self.source_updated_at,
        }


@dataclass(frozen=True, slots=True)
class SourceMeta:
    """Where a conversion's races came from.

    Attributes:
        election_date: Polling day of the general election.
        files: The files read, general first.
    """

    election_date: date
    files: tuple[SourceFile, ...]

    def __post_init__(self) -> None:
        """Check the general file is listed first."""
        if not self.files or self.files[0].kind != "general":
            raise ConvertError("source meta must list the general file first")

    @classmethod
    def from_fetches(
        cls,
        general: FetchResult,
        runoffs: Sequence[FetchResult] = (),
    ) -> SourceMeta:
        """Build the meta from the fetched general file and any runoff files.

        Args:
            general: The general election file.
            runoffs: Runoff files applied on top of it.

        Returns:
            The source meta.

        Raises:
            ConvertError: If ``general`` is not a general source or has no
                URL or date.
        """
        source = general.source
        if source.kind != "general" or source.election_date is None:
            raise ConvertError(f"{source.key} is not a dated general source")
        files = [_source_file(general)]
        files.extend(_source_file(result) for result in runoffs)
        return cls(election_date=source.election_date, files=tuple(files))

    @property
    def general(self) -> SourceFile:
        """The general election file."""
        return self.files[0]


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """The election files one conversion produces.

    Attributes:
        main: The ``{chamber}-{year}.json`` content.
        special: The ``senate-{year}-special.json`` content, or ``None`` when
            no special clashes with a regular race.
        warnings: Things worth a look that did not stop the conversion.
    """

    main: dict[str, Any]
    special: dict[str, Any] | None
    warnings: list[str] = field(default_factory=list)


def output_name(year: int, chamber: Chamber, *, special: bool = False) -> str:
    """Return the file name a converted election is written to.

    Args:
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.
        special: The clashing-specials file rather than the main one.

    Returns:
        ``{chamber}-{year}.json`` or ``senate-{year}-special.json``.
    """
    if special:
        return f"senate-{year}-special.json"
    return f"{chamber}-{year}.json"


def current_cycle() -> int:
    """Return the newest cycle with an NYT source: the one still being counted."""
    return max(supported_years())


def source_note(
    year: int,
    chamber: Chamber,
    races: Sequence[Race],
    *,
    main: bool,
) -> str:
    """Return the source line shown under the map for an election.

    Only the current cycle (:func:`current_cycle`) is ever provisional, and
    only while one of its races is uncalled or uncertified. Past cycles are
    settled even where NYT's archived file still flags a race uncertified.

    Args:
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.
        races: The election's races.
        main: The main file (which carries the year's extra notes) rather than
            a specials file.

    Returns:
        The note, e.g. ``"Results: The New York Times / AP. Provisional until
        certified."``.
    """
    note = _BASE_NOTE
    unsettled = any(not race.called or race.certified is False for race in races)
    if year == current_cycle() and unsettled:
        note += _PROVISIONAL_NOTE
    if main:
        note += _EXTRA_NOTES.get((year, chamber), "")
    return note


def convert_races(
    races: Sequence[Race],
    *,
    year: int,
    chamber: Chamber,
    source_meta: SourceMeta,
    previous: Mapping[str, Any] | None = None,
    previous_special: Mapping[str, Any] | None = None,
) -> ConversionResult:
    """Convert one cycle's parsed races into the project election JSON.

    Args:
        races: The cycle's races for ``chamber``, runoffs already applied.
        year: Election cycle year.
        chamber: ``"house"`` or ``"senate"``.
        source_meta: Where the races came from.
        previous: The main file this conversion replaces, if any; unopposed
            seats take their real totals from it (see the module docstring).
        previous_special: The same for the specials file.

    Returns:
        The main file, the clashing-specials file when there is one, and any
        warnings.

    Raises:
        ConvertError: If a race belongs to another year or chamber, has no
            candidates, or a seat is contested twice in one file.
    """
    warnings: list[str] = []
    main_races, special_races = _split_races(
        races, year=year, chamber=chamber, warnings=warnings,
    )
    map_ref = map_for(year, chamber)
    main = _election_file(
        main_races,
        year=year,
        chamber=chamber,
        map_ref=map_ref,
        source_meta=source_meta,
        special=False,
        previous=previous,
        warnings=warnings,
        special_file=(
            output_name(year, chamber, special=True) if special_races else None
        ),
    )
    special = None
    if special_races:
        special = _election_file(
            special_races,
            year=year,
            chamber=chamber,
            map_ref=map_ref,
            source_meta=source_meta,
            special=True,
            previous=previous_special,
            warnings=warnings,
        )
    return ConversionResult(main=main, special=special, warnings=warnings)


def _source_file(result: FetchResult) -> SourceFile:
    url = result.source.url
    if url is None:
        raise ConvertError(f"{result.source.key} has no URL")
    return SourceFile(
        kind=result.source.kind,
        url=url,
        retrieved_at=result.retrieved_at,
        source_updated_at=result.source_updated_at,
    )


def _split_races(
    races: Sequence[Race],
    *,
    year: int,
    chamber: Chamber,
    warnings: list[str],
) -> tuple[list[Race], list[Race]]:
    """Split races into the main file's and the clashing specials'."""
    for race in races:
        if race.year != year or race.chamber != chamber:
            raise ConvertError(
                f"{race.nyt_race_id} is a {race.year} {race.chamber} race, "
                f"not {year} {chamber}",
            )
    regular = [race for race in races if not race.is_special]
    specials = [race for race in races if race.is_special]
    if chamber == "house":
        # Every House seat holds its regular race each cycle, so a same-day
        # special would contest a seat twice; the FiveThirtyEight converter
        # drops them too.
        for race in specials:
            warnings.append(
                f"{race.seat_key}: House special {race.nyt_race_id} dropped",
            )
        return regular, []

    regular_states = {race.seat_key for race in regular}
    main = list(regular)
    clashing: list[Race] = []
    for race in specials:
        (clashing if race.seat_key in regular_states else main).append(race)
    return main, clashing


def _election_file(
    races: Sequence[Race],
    *,
    year: int,
    chamber: Chamber,
    map_ref: MapRef,
    source_meta: SourceMeta,
    special: bool,
    previous: Mapping[str, Any] | None,
    warnings: list[str],
    special_file: str | None = None,
) -> dict[str, Any]:
    backfill = _backfill_source(previous)
    seats: dict[str, dict[str, Any]] = {}
    for race in sorted(races, key=lambda r: r.seat_key):
        if race.seat_key in seats:
            raise ConvertError(
                f"{race.seat_key} is contested twice ({race.nyt_race_id})",
            )
        seats[race.seat_key] = _seat(race, backfill, warnings)
    meta: dict[str, Any] = {
        "source": SOURCE_CREDIT,
        "source_urls": [source.url for source in source_meta.files],
        "retrieved_at": source_meta.general.retrieved_at.isoformat(),
        "source_updated_at": source_meta.general.source_updated_at,
        "sources": [source.to_json() for source in source_meta.files],
        "year": year,
        "chamber": chamber,
        "special": special,
        "map_id": map_ref.id,
        "map_name": map_ref.name,
        "special_file": special_file,
        "election_date": source_meta.election_date.isoformat(),
        "source_note": source_note(year, chamber, races, main=not special),
    }
    return {META_KEY: meta, **seats}


def _backfill_source(previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the seats of ``previous`` that can lend an unopposed seat real totals.

    From a FiveThirtyEight-format file (no ``meta``) every seat whose totals are
    real, not the nominal placeholder; from an NYT conversion only the seats it
    had already backfilled.
    """
    if previous is None:
        return {}
    is_538 = META_KEY not in previous
    usable: dict[str, Any] = {}
    for key, seat in previous.items():
        if key == META_KEY or not isinstance(seat, dict):
            continue
        seat_info = seat.get("seatInfo", {})
        party_info = seat.get("partyInfo", {})
        if is_538 and _has_real_totals(party_info):
            usable[key] = seat
        elif not is_538 and seat_info.get("votesSource") == VOTES_538:
            usable[key] = seat
    return usable


def _has_real_totals(party_info: Mapping[str, Any]) -> bool:
    totals = [info.get("total", 0) for info in party_info.values()]
    if sum(totals) <= 0:
        return False
    return not (len(totals) == 1 and totals[0] == UNOPPOSED_NOMINAL_VOTES)


def _seat(
    race: Race,
    backfill: Mapping[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    if not race.candidates:
        raise ConvertError(f"{race.nyt_race_id} has no candidates")
    party_info: dict[str, dict[str, Any]] = {}
    # Candidates come most votes first, so the first one seen names the line.
    for candidate in race.candidates:
        bucket = party_info.get(candidate.party_key)
        if bucket is None:
            party_info[candidate.party_key] = {
                "total": candidate.votes,
                "name": candidate.name,
            }
        else:
            bucket["total"] += candidate.votes

    winner_key: str | None = None
    if race.called:
        by_id = {candidate.nyt_id: candidate.party_key for candidate in race.candidates}
        winner_keys = {by_id[winner] for winner in race.winners}
        if len(winner_keys) != 1:
            raise ConvertError(
                f"{race.nyt_race_id}: winners span parties {sorted(winner_keys)}",
            )
        (winner_key,) = winner_keys

    votes_source = VOTES_NYT
    if sum(bucket["total"] for bucket in party_info.values()) == 0:
        if len(race.candidates) == 1:
            sole_key = race.candidates[0].party_key
            earlier = backfill.get(race.seat_key)
            earlier_winner = None
            if earlier is not None:
                earlier_winner = earlier["seatInfo"].get("current")
            if earlier is not None and earlier_winner == sole_key:
                party_info = {
                    party: {"total": info["total"], "name": info["name"]}
                    for party, info in earlier["partyInfo"].items()
                }
                votes_source = VOTES_538
            else:
                if earlier is not None:
                    warnings.append(
                        f"{race.seat_key}: earlier totals not used, winner was "
                        f"{earlier_winner}, now {sole_key}",
                    )
                party_info[sole_key]["total"] = UNOPPOSED_NOMINAL_VOTES
                votes_source = VOTES_NOMINAL
        else:
            warnings.append(f"{race.seat_key}: no votes counted yet")

    seat_info: dict[str, Any] = {
        "current": winner_key,
        "called": race.called,
        "pct": race.pct_counted,
        "sameParty": race.same_party,
        "round": race.decisive_round,
        "uncontested": race.uncontested,
        "certified": race.certified,
        "votesSource": votes_source,
    }
    if race.chamber == "senate":
        seat_info["special"] = race.is_special
        seat_info["senateClass"] = race.senate_class
    return {"seatInfo": seat_info, "partyInfo": party_info}
