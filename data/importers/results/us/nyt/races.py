"""The one in-memory shape every NYT results schema is normalised into.

A :class:`Race` is a single House district or Senate seat contest as one NYT
file reports it: its candidates with their votes, who (if anyone) has been
called the winner, and how much of the vote is counted. The adapters in
:mod:`importers.results.us.nyt.adapters` build these from each schema family; later
steps turn them into the project's election JSON.

Seat keys follow the project's US maps: House districts are ``ST-NN``
(zero-padded, at-large states as ``ST-01``) and Senate seats are the full state
name, e.g. ``Arizona``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from core.us_geography import STATE_POSTAL
from importers.results.us.nyt.sources import Chamber, Kind

PartyKey = Literal[
    "democrat",
    "republican",
    "libertarian",
    "usgreen",
    "independent",
    "others",
]

# ``"rcv-final"``: the votes are the last ranked-choice tabulation round.
# ``"runoff"``: the race is the runoff that settled the seat.
DecisiveRound = Literal["rcv-final", "runoff"]

# Postal code -> full state name, the Senate seat key.
STATE_NAMES: dict[str, str] = {postal: name for name, postal in STATE_POSTAL.items()}

# Party codes as NYT writes them, upper-cased: the A-family codes (``DEM``,
# ``GOP``, ...) and the B/C-family slugs (``democrat``, ``green``, ...). Every
# other code, including minor parties, write-ins and no-party lines, is
# ``others``.
_PARTY_KEYS: dict[str, PartyKey] = {
    "DEM": "democrat",
    "DFL": "democrat",
    "DEMOCRAT": "democrat",
    "GOP": "republican",
    "REP": "republican",
    "REPUBLICAN": "republican",
    "LIB": "libertarian",
    "LIBERTARIAN": "libertarian",
    "GRN": "usgreen",
    "GRE": "usgreen",
    "GREEN": "usgreen",
    # Green Party state affiliates running under their own names: Oregon's
    # Pacific Green Party and West Virginia's Mountain Party.
    "PAG": "usgreen",
    "PACIFIC-GREEN": "usgreen",
    "MOUNTAIN": "usgreen",
    "IND": "independent",
    "INDEPENDENT": "independent",
    # Candidates on no party's line, under each state's label for it: No Party
    # Affiliation (FL), Unaffiliated (NC, CO, UT), No Party / Nonpartisan (AK,
    # NE, HI), No Party Preference (CA), Unenrolled (MA, ME). Nevada's "None of
    # these candidates" ballot line also comes as NPD / no-party-designation, so
    # those two stay ``others``, as do named parties with "independent" in the
    # name (Independence, Independent American, Alaskan Independence).
    "NPA": "independent",
    "NO-PARTY-AFFILIATION": "independent",
    "UNA": "independent",
    "NP": "independent",
    "NPT": "independent",
    "NONPARTISAN": "independent",
    "NPP": "independent",
    "UNR": "independent",
}


def party_key(code: str) -> PartyKey:
    """Map an NYT party code to the project's party key.

    Args:
        code: The party code from any schema (``"DEM"``, ``"republican"``, ...);
            case does not matter.

    Returns:
        The project party key, ``"others"`` for anything not mapped.
    """
    return _PARTY_KEYS.get(code.strip().upper(), "others")


def senate_class_for_year(year: int) -> int:
    """Return the Senate class whose regular election falls in ``year``.

    Classes rotate every two years: 2018 was class 1, 2020 class 2, 2022
    class 3, and so on.

    Args:
        year: An even election year.

    Returns:
        1, 2 or 3.
    """
    return (year - 2018) // 2 % 3 + 1


@dataclass(frozen=True, slots=True)
class Candidate:
    """One candidate's line in a race.

    Attributes:
        nyt_id: NYT's id for the candidate within the file, e.g. ``moody-a``.
        name: Display name, e.g. ``"Ashley Moody"``.
        nyt_party: The party code exactly as NYT gives it.
        party_key: The project party key ``nyt_party`` maps to.
        votes: Votes in the race's stored round (see ``Race.decisive_round``).
    """

    nyt_id: str
    name: str
    nyt_party: str
    party_key: PartyKey
    votes: int


@dataclass(frozen=True, slots=True)
class Race:
    """One House district or Senate seat contest from one NYT file.

    Attributes:
        year: Election cycle year (a January runoff belongs to the cycle
            before it).
        chamber: ``"house"`` or ``"senate"``.
        kind: Kind of the source file the race came from, ``"general"`` or
            ``"runoff"``. A general file can already carry a seat's runoff;
            ``decisive_round`` says which round the votes are.
        state: Two-letter postal code.
        district: House district number (1 for at-large); ``None`` for Senate.
        nyt_race_id: NYT's id for the race, e.g. ``TX-G-H-28-2026-11-03``.
        election_date: The race's own polling day.
        candidates: Candidates, most votes first.
        winners: ``nyt_id`` of each called winner; empty while uncalled
            (including a first round that sent the seat to a runoff).
        pct_counted: Share of the vote counted, 0-100, when the file gives one.
            Schemas A and B report NYT's estimated share of the expected vote;
            schema C has only the share of precincts reporting.
        is_special: A special election for a vacated seat.
        senate_class: The seat's Senate class when known; always ``None`` for
            House races.
        uncontested: NYT marks the race as unopposed. Such races often carry
            no vote totals at all.
        certified: Whether the result is certified, when the file says;
            ``None`` when it does not (schemas B and C).
        decisive_round: ``None`` for a plain first round, ``"rcv-final"`` when
            the votes are the final ranked-choice round, ``"runoff"`` when the
            race is a runoff.
    """

    year: int
    chamber: Chamber
    kind: Kind
    state: str
    district: int | None
    nyt_race_id: str
    election_date: date
    candidates: tuple[Candidate, ...]
    winners: tuple[str, ...]
    pct_counted: float | None
    is_special: bool
    senate_class: int | None
    uncontested: bool
    certified: bool | None
    decisive_round: DecisiveRound | None

    def __post_init__(self) -> None:
        """Check the fields agree with each other.

        Raises:
            ValueError: If the state is unknown, the district or Senate class
                does not fit the chamber, the candidates are not in vote order
                or repeat, a winner is not a candidate, or ``pct_counted`` is
                outside 0-100.
        """
        if self.state not in STATE_NAMES:
            raise ValueError(f"{self.nyt_race_id}: unknown state {self.state!r}")
        if self.chamber == "house":
            if self.district is None or self.district < 1:
                raise ValueError(
                    f"{self.nyt_race_id}: House race needs a district >= 1, "
                    f"got {self.district!r}",
                )
            if self.senate_class is not None:
                raise ValueError(f"{self.nyt_race_id}: House race has a Senate class")
        elif self.district is not None:
            raise ValueError(f"{self.nyt_race_id}: Senate race has a district")
        if self.senate_class is not None and self.senate_class not in (1, 2, 3):
            raise ValueError(
                f"{self.nyt_race_id}: bad Senate class {self.senate_class!r}",
            )
        votes = [candidate.votes for candidate in self.candidates]
        if votes != sorted(votes, reverse=True):
            raise ValueError(f"{self.nyt_race_id}: candidates are not in vote order")
        ids = [candidate.nyt_id for candidate in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.nyt_race_id}: a candidate appears twice")
        unknown = set(self.winners) - set(ids)
        if unknown:
            raise ValueError(
                f"{self.nyt_race_id}: winner(s) {sorted(unknown)} are not candidates",
            )
        if self.pct_counted is not None and not 0 <= self.pct_counted <= 100:
            raise ValueError(
                f"{self.nyt_race_id}: pct_counted {self.pct_counted} is not 0-100",
            )

    @property
    def seat_key(self) -> str:
        """The map seat: ``ST-NN`` for House, the state's name for Senate."""
        if self.district is None:
            return STATE_NAMES[self.state]
        return f"{self.state}-{self.district:02d}"

    @property
    def called(self) -> bool:
        """Whether a winner has been called."""
        return bool(self.winners)

    @property
    def same_party(self) -> bool:
        """Whether the top two candidates share a project party key.

        True for top-two contests such as a California Democrat-vs-Democrat
        general, a Louisiana jungle race led by two Republicans, or two
        minor-party candidates who both fold into ``others``.
        """
        if len(self.candidates) < 2:
            return False
        first, second = self.candidates[0], self.candidates[1]
        return first.party_key == second.party_key


def sort_candidates(candidates: list[Candidate]) -> tuple[Candidate, ...]:
    """Order candidates most votes first, ties by ``nyt_id`` for stability."""
    return tuple(sorted(candidates, key=lambda c: (-c.votes, c.nyt_id)))
