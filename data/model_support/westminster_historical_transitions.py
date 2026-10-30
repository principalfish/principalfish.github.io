"""Frozen Westminster seat results for two historical same-boundary transitions.

The fixture holds three elections, each keyed by constituency code:

- ``1997`` and ``2001``: the 659 seats of the 1997 boundaries, so 1997→2001
  matches seat for seat by code.
- ``2005n``: the 650 seats of the 2010 boundaries. England, Wales and Northern
  Ireland (591) are the official 2005 notional results; Scotland (59) is the
  actual 2005 result, already on the boundaries used in 2010. Each seat carries
  the seat name and region of the database map "UK Constituencies pre 2019", so
  2005n→2010 matches the database's 2010 result by name and region.

Each seat's ``votes`` hold only the named parties that stood and polled at
least one vote (five 2005 notional lines of 0 are omitted). In ``2005n`` the
notional results do not name every party: the unnamed minor-party remainder is
``unknown_other`` and is never split, so a party missing from a notional seat
may be inside it. The Speaker's votes are kept out of the party totals as
``speaker_votes``. ``valid_votes`` is the published per-seat valid vote where
the source publishes one and ``None`` otherwise (never zero).

``build_westminster_historical_fixture.py`` builds the file from the
downloaded sources and records its audit inside it. Loading needs no
database; the schema, content hash and every seat are checked first, and
every audit figure the seats determine is recomputed. The audit's
``party_mapping``, ``spot_checks``, ``mapping.index_pages`` and
``open_questions`` record source-side checks the seats cannot reproduce; they
are informational and not validated.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any

from model_support.monte_carlo import SimulationFileError
from model_support.westminster_validation_inputs import content_hash

HISTORICAL_PATH = (
    Path(__file__).resolve().parents[1]
    / "models/westminster/historical-transitions-v1.json"
)
HISTORICAL_SCHEMA = "westminster-historical-transitions-v1"

# Party key -> database party name. The shared keys match
# ``calibrate_uncertainty.DB_PARTY_NAMES``; "other" (named minor parties and
# independents) is the "Others" row that ``PARTY_ID_ALIASES`` folds "Other" into.
PARTY_DB_NAMES: Mapping[str, str] = MappingProxyType({
    "con": "Conservative",
    "lab": "Labour",
    "ld": "Liberal Democrats",
    "green": "Green",
    "ukip": "UK Independence Party",
    "snp": "Scottish National Party",
    "plaid": "Plaid Cymru",
    "dup": "Democratic Unionist Party",
    "uup": "Ulster Unionist Party",
    "sdlp": "SDLP",
    "sf": "Sinn Féin",
    "alliance": "Alliance",
    "other": "Others",
})
SPEAKER = "speaker"
UNKNOWN_OTHER = "unknown_other"

ENGLISH_REGIONS = frozenset({
    "East Midlands", "East of England", "London", "North East England",
    "North West England", "South East England", "South West England",
    "West Midlands", "Yorkshire and The Humber",
})
# Nation -> the database region names allowed in it, and its code prefix.
NATION_REGIONS: Mapping[str, frozenset[str]] = MappingProxyType({
    "England": ENGLISH_REGIONS,
    "Wales": frozenset({"Wales"}),
    "Scotland": frozenset({"Scotland"}),
    "Northern Ireland": frozenset({"Northern Ireland"}),
})
NATION_CODE_PREFIX: Mapping[str, str] = MappingProxyType({
    "England": "E", "Wales": "W", "Scotland": "S", "Northern Ireland": "N",
})
EXPECTED_SEATS: Mapping[str, int] = MappingProxyType(
    {"1997": 659, "2001": 659, "2005n": 650}
)
DATABASE_MAP = "UK Constituencies pre 2019"
DATABASE_2010 = "2010 General Election"
# Transition label -> the Speaker's seats it must exclude, in code order:
# Betty Boothroyd (West Bromwich West, 1997) and Michael Martin (Glasgow
# Springburn, 2001); Martin again (Glasgow North East, 2005) and John Bercow
# (Buckingham, 2010). Bercow's 2010 result is the database's, not this
# fixture's, so his seat is named here rather than derived from a flag.
EXPECTED_EXCLUSIONS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "1997→2001": ("E14000498", "S19970045"),
    "2005n→2010": ("E14000608", "S14000032"),
})
# Election key -> its Speaker's seat: Boothroyd (West Bromwich West) in 1997,
# Martin (Glasgow Springburn) in 2001 and (Glasgow North East) in 2005.
EXPECTED_SPEAKER_SEATS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "1997": ("E14000498",),
    "2001": ("S19970045",),
    "2005n": ("S14000032",),
})
# Transition label -> (earlier, later fixture election, later database
# election); a database successor is always on ``DATABASE_MAP``.
EXPECTED_TRANSITIONS: Mapping[str, tuple[str, str | None, str | None]] = (
    MappingProxyType({
        "1997→2001": ("1997", "2001", None),
        "2005n→2010": ("2005n", None, DATABASE_2010),
    })
)
# Election key -> (label, polling date, notional).
EXPECTED_ELECTIONS: Mapping[str, tuple[str, str, bool]] = MappingProxyType({
    "1997": ("1997 General Election", "1997-05-01", False),
    "2001": ("2001 General Election", "2001-06-07", False),
    "2005n": ("2005 General Election on 2010 boundaries", "2005-05-05", True),
})
_SHA256 = re.compile(r"[0-9a-f]{64}")

_TOP_FIELDS = {
    "schema", "provenance", "parties", "elections", "transitions", "audit",
    "content_sha256",
}
_PROVENANCE_FIELDS = {
    "retrieved_on", "sources", "unavailable_sources", "builder",
    "database_crosswalk",
}
_SOURCE_FIELDS = {"name", "url", "file", "sha256", "bytes", "used_for"}
_UNAVAILABLE_FIELDS = {"name", "url", "reason"}
_ELECTION_FIELDS = {"label", "polling_date", "notional", "boundaries", "seats"}
_SEAT_FIELDS = {
    "name", "nation", "region", "speaker", "votes", "unknown_other",
    "speaker_votes", "valid_votes", "winner",
}
_TRANSITION_FIELDS = {
    "label", "earlier", "later_fixture_election", "later_database_election",
    "later_database_map", "excluded_seat_codes",
}
_AUDIT_FIELDS = {
    "elections", "mapping", "party_mapping", "spot_checks", "open_questions",
}
_PUBLISHED_CHECK_FIELDS = {"independent", "note"}
_NOTIONAL_CHECK_FIELDS = _PUBLISHED_CHECK_FIELDS | {
    "scope", "published_total_valid_votes", "sum_of_seat_valid_votes", "difference",
}
_MAPPING_FIELDS = {
    "index_pages", "boundaries_1997", "scotland_2005", "database",
    "notional_2005_seats_matched_to_2010_by_code_and_region",
}
_BOUNDARIES_1997_FIELDS = {
    "seats_matched_to_1997_boundary_areas", "name_aliases", "codes_not_issued_by_ons",
}
_SCOTLAND_2005_FIELDS = {
    "scottish_seats_matched_to_2005_boundaries", "other_2005_seats_on_1997_boundaries",
}
_DATABASE_FIELDS = {
    "map", "seats_matched_by_name_and_region", "names_taken_from_database",
}


@dataclass(frozen=True, slots=True)
class HistoricalSeat:
    """One seat's result.

    Attributes:
        code: Constituency code (ONS where issued; the Parliament database's
            own ``S1997…``/``N1997…`` codes for 1997 Scotland and NI).
        name: Seat name (the database map's name for ``2005n``).
        nation: England, Wales, Scotland or Northern Ireland.
        region: Database region name (the nation outside England).
        speaker: The Speaker stood here; ``speaker_votes`` are the Speaker's.
        votes: Party key -> votes (positive), for the named parties that stood.
        unknown_other: Unnamed minor-party votes (notional results only).
        speaker_votes: The Speaker's votes, kept out of ``votes``.
        valid_votes: Published valid votes, or ``None`` if not published.
        winner: Party key, or ``"speaker"``.
    """

    code: str
    name: str
    nation: str
    region: str
    speaker: bool
    votes: Mapping[str, int]
    unknown_other: int
    speaker_votes: int
    valid_votes: int | None
    winner: str

    @property
    def total_votes(self) -> int:
        """Every vote in the seat: parties, unknown remainder and Speaker."""
        return sum(self.votes.values()) + self.unknown_other + self.speaker_votes


@dataclass(frozen=True, slots=True)
class HistoricalElection:
    """One election's seats, keyed by constituency code."""

    key: str
    label: str
    polling_date: date
    notional: bool
    boundaries: str
    seats: Mapping[str, HistoricalSeat]


@dataclass(frozen=True, slots=True)
class HistoricalTransition:
    """A same-boundary pair: an earlier fixture election and its successor.

    Exactly one of ``later_fixture_election`` (another fixture election) or
    ``later_database_election`` (an election on ``later_database_map``) is set.
    ``excluded_seat_codes`` are the Speaker's seats at either election.
    """

    label: str
    earlier: str
    later_fixture_election: str | None
    later_database_election: str | None
    later_database_map: str | None
    excluded_seat_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoricalTransitions:
    """The verified fixture and the SHA-256 of its file."""

    elections: Mapping[str, HistoricalElection]
    transitions: tuple[HistoricalTransition, ...]
    parties: Mapping[str, str]
    provenance: Mapping[str, Any]
    audit: Mapping[str, Any]
    fixture_sha256: str


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SimulationFileError(f"Historical {name} must be a non-negative integer")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SimulationFileError(f"Historical {name} must be a nonempty string")
    return value


def _sha256(value: object, name: str) -> str:
    text = _text(value, name)
    if not _SHA256.fullmatch(text):
        raise SimulationFileError(f"Historical {name} is not a SHA-256 digest")
    return text


def _freeze(value: Any) -> Any:
    """Return *value* with every object read-only and every array a tuple."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _iso_date(value: object, name: str) -> date:
    try:
        return date.fromisoformat(_text(value, name))
    except ValueError as error:
        raise SimulationFileError(f"Historical {name} is not an ISO date") from error


def _fields(raw: object, expected: set[str], name: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != expected:
        raise SimulationFileError(f"Historical {name} schema is incomplete")
    return raw


def check_nation_region(nation: object, region: object) -> tuple[str, str]:
    """Return ``(nation, region)`` if the region belongs to a supported nation.

    Raises:
        SimulationFileError: The nation is not one of the four, or the region
            is not one of its database regions.
    """
    if not isinstance(nation, str) or nation not in NATION_REGIONS:
        raise SimulationFileError(f"Unsupported historical nation {nation!r}")
    if not isinstance(region, str) or region not in NATION_REGIONS[nation]:
        raise SimulationFileError(f"Unsupported region {region!r} for {nation}")
    return nation, region


def _seat(code: str, raw: object) -> HistoricalSeat:
    seat = _fields(raw, _SEAT_FIELDS, f"seat {code}")
    nation, region = check_nation_region(seat["nation"], seat["region"])
    if not code.startswith(NATION_CODE_PREFIX[nation]):
        raise SimulationFileError(
            f"Historical seat {code} code does not match {nation}"
        )
    raw_votes = seat["votes"]
    if (not isinstance(raw_votes, dict) or not raw_votes
            or not set(raw_votes) <= set(PARTY_DB_NAMES)):
        raise SimulationFileError(f"Historical seat {code} has unknown party keys")
    votes = {
        party: _count(value, f"{code} {party} votes")
        for party, value in sorted(raw_votes.items())
    }
    if not all(votes.values()):
        raise SimulationFileError(f"Historical seat {code} has a party with 0 votes")
    speaker = seat["speaker"]
    if not isinstance(speaker, bool):
        raise SimulationFileError(f"Historical seat {code} speaker flag is invalid")
    speaker_votes = _count(seat["speaker_votes"], f"{code} speaker votes")
    if speaker != (speaker_votes > 0):
        raise SimulationFileError(
            f"Historical seat {code} speaker votes contradict flag"
        )
    unknown = _count(seat["unknown_other"], f"{code} unknown_other")
    valid = seat["valid_votes"]
    total = sum(votes.values()) + unknown + speaker_votes
    if valid is not None and _count(valid, f"{code} valid votes") != total:
        raise SimulationFileError(
            f"Historical seat {code} votes do not sum to its valid votes"
        )
    totals = dict(votes)
    if speaker:
        totals[SPEAKER] = speaker_votes
    winner = _text(seat["winner"], f"{code} winner")
    top = max(totals.values())
    leaders = [party for party, value in totals.items() if value == top]
    if leaders != [winner] or unknown >= top:
        raise SimulationFileError(
            f"Historical seat {code} winner is not its sole leader"
        )
    return HistoricalSeat(
        code=code,
        name=_text(seat["name"], f"{code} name"),
        nation=nation,
        region=region,
        speaker=speaker,
        votes=MappingProxyType(votes),
        unknown_other=unknown,
        speaker_votes=speaker_votes,
        valid_votes=valid,
        winner=winner,
    )


def _election(key: str, raw: object) -> HistoricalElection:
    election = _fields(raw, _ELECTION_FIELDS, f"election {key}")
    seats_raw = election["seats"]
    if not isinstance(seats_raw, dict) or len(seats_raw) != EXPECTED_SEATS[key]:
        raise SimulationFileError(
            f"Historical election {key} must have {EXPECTED_SEATS[key]} seats"
        )
    seats = {code: _seat(_text(code, "seat code"), value)
             for code, value in sorted(seats_raw.items())}
    if len({(seat.name, seat.region) for seat in seats.values()}) != len(seats):
        raise SimulationFileError(f"Historical election {key} repeats a seat name")
    notional = election["notional"]
    if not isinstance(notional, bool):
        raise SimulationFileError(f"Historical election {key} notional flag is invalid")
    label, polling_date, expected_notional = EXPECTED_ELECTIONS[key]
    if notional != expected_notional:
        raise SimulationFileError(f"Historical election {key} notional flag differs")
    if not notional and any(seat.unknown_other for seat in seats.values()):
        raise SimulationFileError(f"Historical election {key} has unnamed votes")
    # Scotland's 2005 seats are actual results, which name every candidate.
    if any(seat.unknown_other for seat in seats.values() if seat.nation == "Scotland"):
        raise SimulationFileError(
            f"Historical election {key} has unnamed Scottish votes"
        )
    speakers = tuple(code for code, seat in seats.items() if seat.speaker)
    if speakers != EXPECTED_SPEAKER_SEATS[key]:
        raise SimulationFileError(f"Historical election {key} Speaker seats differ")
    if election["label"] != label:
        raise SimulationFileError(f"Historical election {key} label differs")
    if _iso_date(election["polling_date"], f"{key} date") != date.fromisoformat(
        polling_date
    ):
        raise SimulationFileError(f"Historical election {key} polling date differs")
    return HistoricalElection(
        key=key,
        label=label,
        polling_date=date.fromisoformat(polling_date),
        notional=notional,
        boundaries=_text(election["boundaries"], f"{key} boundaries"),
        seats=MappingProxyType(seats),
    )


def _transition(
    raw: object, elections: Mapping[str, HistoricalElection]
) -> HistoricalTransition:
    item = _fields(raw, _TRANSITION_FIELDS, "transition")
    earlier = item["earlier"]
    if earlier not in elections:
        raise SimulationFileError(f"Historical transition from unknown {earlier!r}")
    later = item["later_fixture_election"]
    database = item["later_database_election"]
    database_map = item["later_database_map"]
    if later is not None:
        if later not in elections or database is not None or database_map is not None:
            raise SimulationFileError("Historical transition has an invalid successor")
        if set(elections[later].seats) != set(elections[earlier].seats):
            raise SimulationFileError("Historical transition seats differ by code")
        for code, seat in elections[earlier].seats.items():
            after = elections[later].seats[code]
            if (seat.nation, seat.region) != (after.nation, after.region):
                raise SimulationFileError(f"Historical seat {code} changes region")
    elif (database, database_map) != (DATABASE_2010, DATABASE_MAP):
        raise SimulationFileError("Historical transition has an invalid successor")
    codes = item["excluded_seat_codes"]
    if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
        raise SimulationFileError("Historical transition exclusions must be codes")
    if any(code not in elections[earlier].seats for code in codes):
        raise SimulationFileError("Historical transition excludes unknown seats")
    if len(set(codes)) != len(codes):
        raise SimulationFileError("Historical transition repeats an excluded seat")
    speakers = {code for code, seat in elections[earlier].seats.items() if seat.speaker}
    if later is not None:
        speakers |= {code for code, seat in elections[later].seats.items()
                     if seat.speaker}
    if not speakers <= set(codes):
        raise SimulationFileError("Historical transition keeps a Speaker's seat")
    label = _text(item["label"], "transition label")
    if tuple(codes) != EXPECTED_EXCLUSIONS.get(label):
        raise SimulationFileError(
            f"Historical transition {label} does not exclude exactly its Speakers"
        )
    if (earlier, later, database) != EXPECTED_TRANSITIONS[label]:
        raise SimulationFileError(
            f"Historical transition {label} links the wrong elections"
        )
    return HistoricalTransition(
        label=label,
        earlier=earlier,
        later_fixture_election=later,
        later_database_election=database,
        later_database_map=database_map,
        excluded_seat_codes=tuple(codes),
    )


def _check_provenance(raw: object) -> dict[str, Any]:
    source = _fields(raw, _PROVENANCE_FIELDS, "provenance")
    _iso_date(source["retrieved_on"], "retrieval date")
    _text(source["builder"], "builder")
    if not isinstance(source["sources"], list) or not source["sources"]:
        raise SimulationFileError("Historical provenance lists no sources")
    for item in source["sources"]:
        entry = _fields(item, _SOURCE_FIELDS, "source")
        for name in ("name", "url", "file", "used_for"):
            _text(entry[name], f"source {name}")
        _sha256(entry["sha256"], "source sha256")
        _count(entry["bytes"], "source bytes")
    if not isinstance(source["unavailable_sources"], list):
        raise SimulationFileError("Historical unavailable sources must be a list")
    for item in source["unavailable_sources"]:
        entry = _fields(item, _UNAVAILABLE_FIELDS, "unavailable source")
        for name in _UNAVAILABLE_FIELDS:
            _text(entry[name], f"unavailable source {name}")
    if not isinstance(source["database_crosswalk"], dict):
        raise SimulationFileError("Historical database crosswalk must be an object")
    return source


def _check_audit(
    raw: object, elections: Mapping[str, HistoricalElection]
) -> dict[str, Any]:
    audit = _fields(raw, _AUDIT_FIELDS, "audit")
    recorded = audit["elections"]
    if not isinstance(recorded, dict) or set(recorded) != set(elections):
        raise SimulationFileError("Historical audit does not cover every election")
    _check_mapping(audit["mapping"], elections)
    for key, election in elections.items():
        seats = list(election.seats.values())
        extra = {"published_total_check"}
        if key == "2005n":
            extra.add("scotland_actual_2005")
        _check_derived(recorded[key], seats, key, extra)
        _check_published_total(recorded[key]["published_total_check"], seats, key)
    scotland = [seat for seat in elections["2005n"].seats.values()
                if seat.nation == "Scotland"]
    _check_derived(recorded["2005n"]["scotland_actual_2005"], scotland,
                   "2005n Scotland", set())
    if not isinstance(audit["open_questions"], list):
        raise SimulationFileError("Historical audit open questions must be a list")
    return audit


def _derived_audit(seats: list[HistoricalSeat]) -> dict[str, Any]:
    """Every audit figure the seats determine, computed as the builder does."""
    party_totals: dict[str, int] = {}
    winners: dict[str, int] = {}
    for seat in seats:
        for party, votes in seat.votes.items():
            party_totals[party] = party_totals.get(party, 0) + votes
        winners[seat.winner] = winners.get(seat.winner, 0) + 1
    unknown = sum(seat.unknown_other for seat in seats)
    speaker = sum(seat.speaker_votes for seat in seats)
    total = sum(party_totals.values()) + unknown + speaker
    shares = {**party_totals, UNKNOWN_OTHER: unknown, SPEAKER: speaker}
    published = [seat for seat in seats if seat.valid_votes is not None]
    return {
        "seat_count": len(seats),
        "total_votes": total,
        "party_totals": dict(sorted(party_totals.items())),
        "unknown_other_total": unknown,
        "unknown_other_seats": sum(1 for seat in seats if seat.unknown_other),
        "speaker_votes": speaker,
        "speaker_seats": sorted(seat.code for seat in seats if seat.speaker),
        "national_shares": {
            key: round(value / total, 6) for key, value in sorted(shares.items())
        },
        "winners": dict(sorted(winners.items())),
        "valid_vote_reconciliation": {
            "seats_with_published_valid_votes": len(published),
            "seats_without_published_valid_votes": len(seats) - len(published),
            # Each published seat already equals its votes, so the gap is 0.
            "max_abs_difference": 0 if published else None,
        },
    }


def _same(recorded: object, actual: object) -> bool:
    """Equal and of the same JSON types throughout, so False or 0.0 is not 0."""
    if type(recorded) is not type(actual):
        return False
    if isinstance(recorded, dict) and isinstance(actual, dict):
        return recorded.keys() == actual.keys() and all(
            _same(recorded[key], value) for key, value in actual.items()
        )
    if isinstance(recorded, list) and isinstance(actual, list):
        return len(recorded) == len(actual) and all(
            _same(item, value) for item, value in zip(recorded, actual)
        )
    return recorded == actual


def _check_derived(
    entry: object, seats: list[HistoricalSeat], name: str, extra: set[str]
) -> None:
    """Check an audit entry's exact fields and every figure the seats determine.

    ``extra`` names the fields checked elsewhere (published totals, sub-audits).
    """
    actual = _derived_audit(seats)
    checked = _fields(entry, set(actual) | {"candidate_rows"} | extra,
                      f"audit for {name}")
    _count(checked["candidate_rows"], f"audit {name} candidate rows")
    for field, value in actual.items():
        if not _same(checked[field], value):
            raise SimulationFileError(f"Historical audit {name} {field} differs")


def _check_published_total(raw: object, seats: list[HistoricalSeat], key: str) -> None:
    """Check the shape of a published-total check, and its sums where it has any.

    No retrieved total is independent of the seat data, so none may claim to be.
    """
    notional = key == "2005n"
    fields = _NOTIONAL_CHECK_FIELDS if notional else _PUBLISHED_CHECK_FIELDS
    entry = _fields(raw, fields, f"audit {key} published total")
    _text(entry["note"], f"audit {key} published total note")
    if entry["independent"] is not False:
        raise SimulationFileError(f"Historical audit {key} claims an independent total")
    if not notional:
        return
    _text(entry["scope"], "audit 2005n published total scope")
    published = _count(entry["published_total_valid_votes"], "2005n published total")
    seat_sum = sum(seat.valid_votes or 0 for seat in seats)
    if (not _same(entry["sum_of_seat_valid_votes"], seat_sum)
            or not _same(entry["difference"], seat_sum - published)
            or seat_sum != published):
        raise SimulationFileError("Historical audit 2005n published total differs")


def _check_mapping(raw: object, elections: Mapping[str, HistoricalElection]) -> None:
    """Recompute the recorded mapping counts that the seats themselves imply.

    On 2010 boundaries the notional seats (matched to 2010 by code and region)
    are those outside Scotland, each with published valid votes; Scotland's
    actual 2005 seats (matched by name) have none.
    """
    mapping = _fields(raw, _MAPPING_FIELDS, "audit mapping")
    boundaries = _fields(mapping["boundaries_1997"], _BOUNDARIES_1997_FIELDS,
                         "audit mapping boundaries_1997")
    scotland_2005 = _fields(mapping["scotland_2005"], _SCOTLAND_2005_FIELDS,
                            "audit mapping scotland_2005")
    database = _fields(mapping["database"], _DATABASE_FIELDS, "audit mapping database")
    if database["map"] != DATABASE_MAP:
        raise SimulationFileError("Historical audit mapping names the wrong map")
    seats = elections["2005n"].seats.values()
    scotland = [seat for seat in seats if seat.nation == "Scotland"]
    notional = [seat for seat in seats if seat.nation != "Scotland"]
    if (any(seat.valid_votes is not None for seat in scotland)
            or any(seat.valid_votes is None for seat in notional)):
        raise SimulationFileError("Historical 2005n valid votes do not follow nation")
    checks = {
        "boundaries_1997": (boundaries["seats_matched_to_1997_boundary_areas"],
                            len(elections["1997"].seats)),
        "codes_not_issued_by_ons": (
            boundaries["codes_not_issued_by_ons"],
            sum(1 for code in elections["1997"].seats if code[1:5] == "1997"),
        ),
        "notional": (mapping["notional_2005_seats_matched_to_2010_by_code_and_region"],
                     len(notional)),
        "scotland": (scotland_2005["scottish_seats_matched_to_2005_boundaries"],
                     len(scotland)),
        "database": (database["seats_matched_by_name_and_region"],
                     len(elections["2005n"].seats)),
    }
    for name, (recorded, actual) in checks.items():
        if not _same(recorded, actual):
            raise SimulationFileError(f"Historical audit mapping {name} count differs")


def load_historical_transitions(path: Path = HISTORICAL_PATH) -> HistoricalTransitions:
    """Fail closed on incomplete schemas, tampering and inconsistent seats."""
    try:
        content = path.read_bytes()
        raw = json.loads(content)
        if not isinstance(raw, dict) or set(raw) != _TOP_FIELDS:
            raise SimulationFileError("Historical transitions schema is incomplete")
        if raw["schema"] != HISTORICAL_SCHEMA:
            raise SimulationFileError("Unknown historical transitions schema")
        body = {key: value for key, value in raw.items() if key != "content_sha256"}
        if raw["content_sha256"] != content_hash(body):
            raise SimulationFileError("Historical transitions content hash differs")
        if raw["parties"] != dict(PARTY_DB_NAMES):
            raise SimulationFileError("Historical party keys differ from the loader's")
        provenance = _check_provenance(raw["provenance"])
        elections_raw = raw["elections"]
        if (not isinstance(elections_raw, dict)
                or set(elections_raw) != set(EXPECTED_SEATS)):
            raise SimulationFileError("Historical elections schema is incomplete")
        elections = {key: _election(key, elections_raw[key]) for key in EXPECTED_SEATS}
        transitions_raw = raw["transitions"]
        if not isinstance(transitions_raw, list) or not transitions_raw:
            raise SimulationFileError("Historical transitions list is empty")
        transitions = tuple(_transition(item, elections) for item in transitions_raw)
        if [item.label for item in transitions] != list(EXPECTED_EXCLUSIONS):
            raise SimulationFileError(
                "Historical transitions are not the expected pair"
            )
        audit = _check_audit(raw["audit"], elections)
        return HistoricalTransitions(
            elections=MappingProxyType(elections),
            transitions=transitions,
            parties=PARTY_DB_NAMES,
            provenance=_freeze(provenance),
            audit=_freeze(audit),
            fixture_sha256=hashlib.sha256(content).hexdigest(),
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise SimulationFileError(
            f"Cannot read historical transitions: {error}"
        ) from error
