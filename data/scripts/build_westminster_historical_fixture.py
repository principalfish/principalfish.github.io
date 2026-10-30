"""Build the frozen Westminster historical-transitions fixture from downloads.

Parses, maps and audits two same-boundary transitions and writes
``data/models/westminster/historical-transitions-v1.json`` (loaded by
``model_support.westminster_historical_transitions``):

- **1997→2001** on the 1997 boundaries (659 seats each), from the UK
  Parliament's "UK General Elections 1832-2019" site. Its data file
  (``db/data/download.csv`` of ``ukparliament/uk-general-elections`` at a
  pinned commit) is what the site serves; the downloaded election index pages
  and a sample of election pages are checked against it.
- **2005n→2010** on the 2010 boundaries (650 seats): the official 2005
  notional results for England, Wales and Northern Ireland (591 seats,
  ``electionresults.parliament.uk``) plus the actual 2005 result for Scotland's
  59 seats, whose boundaries were already the 2010 ones. The 2010 result is
  the database's; this fixture carries the database map's seat names and
  regions so the two match by name and region.

Constituency codes, nations and English regions come from the Parliament
election results database (``ukparliament/psephology-datasette`` at a pinned
commit). The database is opened read-only, for the 2010 map's seat names and
regions and the party rows the keys refer to.

Rules: Labour Co-op counts as Labour. The Speaker's votes are kept out of the
party totals and the seat is flagged. The 2005 notional file's unnamed
minor-party aggregates stay one ``unknown_other`` total per seat (never split
or zeroed). A country or region outside the four nations is rejected, never
defaulted. A party name that resembles a mapped party but is not one must be
listed in ``OTHER_LOOKALIKES``. Every check that fails stops the build.

The source directory holds the downloads, unmodified, at the file names in
``SOURCES``; this script fetches nothing.

Usage:
    python -I data/scripts/build_westminster_historical_fixture.py SOURCE_DIR --dry-run
    python -I data/scripts/build_westminster_historical_fixture.py SOURCE_DIR
    python -I data/scripts/build_westminster_historical_fixture.py SOURCE_DIR \
        --output /tmp/historical-transitions.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sqlite3
import sys
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
for _path in (DATA_DIR, DATA_DIR / "models" / "westminster"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from config import DatabaseConfig
from model_support.io import write_text_files
from model_support.monte_carlo import SimulationFileError
from model_support.westminster_historical_transitions import (
    DATABASE_2010,
    DATABASE_MAP,
    EXPECTED_ELECTIONS,
    EXPECTED_SEATS,
    EXPECTED_TRANSITIONS,
    HISTORICAL_PATH,
    HISTORICAL_SCHEMA,
    PARTY_DB_NAMES,
    SPEAKER,
    UNKNOWN_OTHER,
    check_nation_region,
    load_historical_transitions,
)
from model_support.westminster_validation_inputs import content_hash
from scripts.calibrate_uncertainty import (
    DB_PARTY_NAMES,
    PARTY_ID_ALIASES,
    SPEAKER_SEATS,
)
from scripts.migrate_add_us_poll_scope import open_database

RETRIEVED_ON = "2026-10-30"
BUILDER = "data/scripts/build_westminster_historical_fixture.py"

API_SITE = "https://api.parliament.uk/uk-general-elections"
SEED_COMMIT = "645fa5010154db1ee8d9c0ec4f4a32013ea2624c"
PSEPHOLOGY_COMMIT = "cc94141c14f2132f74bb7c616221f35ab179c2ba"
NOTIONAL_FILE = "2005n_candidacies.csv"
SEED_FILE = "uk-general-elections-download.csv"
PSEPHOLOGY_FILE = "psephology.db"


@dataclass(frozen=True, slots=True)
class Source:
    """A downloaded file: its name in the source directory and where it came from."""

    name: str
    file: str
    url: str
    used_for: str


# The api site's general election index pages: fixture key -> (file, number).
INDEX_PAGES: Mapping[str, tuple[str, int]] = {
    "1997": ("ge42.html", 42),
    "2001": ("ge43.html", 43),
    "2005": ("ge44.html", 44),
}
# Election pages compared row by row with the site's data file:
# (general election, seat, page id).
SPOT_CHECKS: tuple[tuple[str, str, int], ...] = (
    ("1997", "Aberavon", 23133),
    ("1997", "West Bromwich West", 23757),
    ("1997", "Tatton", 23707),
    ("1997", "Belfast West", 23170),
    ("2001", "Glasgow Springburn", 24047),
    ("2001", "Wyre Forest", 24447),
    ("2001", "Aberavon", 23792),
    ("2005", "Glasgow North East", 24692),
    ("2005", "Orkney and Shetland", 24885),
    ("2005", "Edinburgh South", 24659),
)
SOURCES: tuple[Source, ...] = (
    Source(
        "2005 notional results, England, Wales and Northern Ireland on 2010 boundaries",
        NOTIONAL_FILE,
        "https://electionresults.parliament.uk/general-elections/7/candidacies.csv",
        "2005n: 591 seats' codes, regions, candidate votes and valid votes",
    ),
    *(
        Source(
            f"{year} General Election index page",
            file,
            f"{API_SITE}/general-elections/{number}",
            f"checks the seat list of the {year} data file",
        )
        for year, (file, number) in INDEX_PAGES.items()
    ),
    Source(
        "UK General Elections 1832-2019 site data file (pinned commit)",
        SEED_FILE,
        "https://raw.githubusercontent.com/ukparliament/uk-general-elections/"
        f"{SEED_COMMIT}/db/data/download.csv",
        "1997, 2001 and 2005 Scotland candidate votes",
    ),
    Source(
        "Parliament election results database (pinned commit)",
        PSEPHOLOGY_FILE,
        "https://raw.githubusercontent.com/ukparliament/psephology-datasette/"
        f"{PSEPHOLOGY_COMMIT}/psephology.db",
        "constituency codes, nations and English regions on the 1997 and 2010 "
        "boundaries",
    ),
    *(
        Source(
            f"{year} General Election - {seat} election page",
            f"election-pages/{page}.html",
            f"{API_SITE}/elections/{page}",
            "spot check of the data file against the site",
        )
        for year, seat, page in SPOT_CHECKS
    ),
)
UNAVAILABLE_SOURCES: tuple[dict[str, str], ...] = (
    {
        "name": "House of Commons Library CBP-8647 (boundary documentation)",
        "url": "https://commonslibrary.parliament.uk/research-briefings/cbp-8647/",
        "reason": f"HTTP 403 bot challenge on {RETRIEVED_ON}; not retrieved, so "
                  "its per-seat and national totals are not checked",
    },
)

# api site party name -> party key. Labour Co-op is Labour; the Referendum
# Party has no key of its own and counts as "other".
API_PARTY_KEYS: Mapping[str, str] = {
    "Labour": "lab",
    "Labour Co-op": "lab",
    "Conservative": "con",
    "NI Conservatives": "con",
    "Liberal Democrats": "ld",
    "Greens": "green",
    "Scottish Greens": "green",
    "Greens (NI)": "green",
    "UKIP": "ukip",
    "SNP": "snp",
    "Plaid Cymru": "plaid",
    "DUP": "dup",
    "UUP": "uup",
    "SDLP": "sdlp",
    "Sinn Féin": "sf",
    "Alliance": "alliance",
    "Referendum": "other",
    "Speaker": SPEAKER,
}
# 2005 notional file party name -> party key; independents are "other".
NOTIONAL_PARTY_KEYS: Mapping[str, str] = {
    "Labour": "lab",
    "Conservative": "con",
    "Liberal Democrat": "ld",
    "Green Party": "green",
    "UK Independence Party": "ukip",
    "Plaid Cymru": "plaid",
    "Democratic Unionist Party": "dup",
    "Ulster Unionist Party": "uup",
    "Social Democratic & Labour Party": "sdlp",
    "Sinn Féin": "sf",
    "British National Party": "other",
    "Respect Party": "other",
    "Independent Kidderminster Hospital and Health Concern": "other",
}
# Names that look like a mapped party but are minor parties ("other").
_LOOKALIKE = re.compile(
    r"(?i)labour|conserv|tory|liberal|unionist|green|plaid|snp|scottish national|"
    r"sinn|alliance|ukip|independence|referendum|speaker|sdlp|dup|uup|"
    r"social democratic"
)
OTHER_LOOKALIKES = frozenset({
    "Alliance for Change", "Alliance for Green Socialism", "Anti Sleaze Labour",
    "Croydon Pensions Alliance", "Conservatory", "Democratic Labour",
    "Democratic Socialist Alliance",
    "Democratic Socialist Alliance - People Before Profit",
    "English Independence Party",
    "Green Referendum Lawless Naturally Street Party", "Independent Conservative",
    "Independent English Conservative and Referendum", "Independent Green",
    "Independent Green Voice", "Independent Labour",
    "Independent Progressive Labour", "Leeds Left Alliance", "Liberal",
    "Liberal Democrat Top Choice for Parliament", "Loyal Conservative",
    "Multi-Racial Anti-Corruption Alliance", "NI Unionist", "New Labour",
    "People's Labour", "Pro-Euro Conservative", "ProLife Alliance",
    "Scottish Freedom Referendum Party", "Scottish Independence Party",
    "Scottish Socialist Alliance", "Scottish Unionist", "Social Democratic",
    "Socialist Alliance", "Socialist Labour",
    "Sportsman's Alliance: Anything but Mellow",
    "Stratford First Democratic Conservative", "Teddy Bear Alliance", "UK Unionist",
})

# Source English region -> database region. The nations outside England are
# their own region. Anything else is rejected.
ENGLISH_REGION_NAMES: Mapping[str, str] = {
    "East Midlands": "East Midlands",
    "East of England": "East of England",
    "London": "London",
    "North East": "North East England",
    "North West": "North West England",
    "South East": "South East England",
    "South West": "South West England",
    "West Midlands": "West Midlands",
    "Yorkshire and The Humber": "Yorkshire and The Humber",
}
# The database's named-independents row, which ``PARTY_ID_ALIASES`` folds
# into "Others" (the "other" key).
ALIASED_OTHER = "Other"
NATIONS_WITHOUT_REGIONS = frozenset({"Wales", "Scotland", "Northern Ireland"})

# api site name -> the results database's name, where the api site puts the
# compass point first.
NAME_ALIASES: Mapping[str, str] = {
    "Central Newcastle upon Tyne": "Newcastle upon Tyne Central",
    "East Newcastle upon Tyne and Wallsend": "Newcastle upon Tyne East and Wallsend",
    "North Newcastle upon Tyne": "Newcastle upon Tyne North",
    "Central Stoke-on-Trent": "Stoke-on-Trent Central",
    "North Stoke-on-Trent": "Stoke-on-Trent North",
    "South Stoke-on-Trent": "Stoke-on-Trent South",
    "East Kingston upon Hull": "Kingston upon Hull East",
    "North Kingston upon Hull": "Kingston upon Hull North",
    "West Kingston upon Hull and Hessle": "Kingston upon Hull West and Hessle",
    "North East Wolverhampton": "Wolverhampton North East",
    "South East Wolverhampton": "Wolverhampton South East",
    "South West Wolverhampton": "Wolverhampton South West",
    "South Middlesbrough and East Cleveland": "Middlesbrough South and East Cleveland",
    "South West Milton Keynes": "Milton Keynes South West",
}

# The Speaker at each api-site election: (seat, candidate).
SPEAKERS: Mapping[str, tuple[str, str]] = {
    "1997": ("West Bromwich West", "Boothroyd, Betty"),
    "2001": ("Glasgow Springburn", "Martin, Michael"),
    "2005": ("Glasgow North East", "Martin, Michael"),
}

# How each election's boundaries are described in the fixture.
BOUNDARY_NOTES: Mapping[str, str] = {
    "1997": "1997 boundaries (659 seats)",
    "2001": "1997 boundaries (659 seats)",
    "2005n": "2010 boundaries (650 seats): England, Wales and Northern Ireland "
             "notional, Scotland actual",
}

# Results database boundary sets.
BOUNDARY_SETS_1997 = {9: "Northern Ireland", 10: "England", 11: "Scotland", 12: "Wales"}
BOUNDARY_SETS_2010 = {5: "Northern Ireland", 6: "England", 7: "Wales", 8: "Scotland"}
SCOTLAND_2005_SET = 8
GENERAL_ELECTION_2010 = 1

OPEN_QUESTIONS: tuple[str, ...] = (
    "No independent published total was retrieved for 1997, 2001 or 2005 "
    "Scotland: the api site publishes candidate votes and turnout but no valid "
    "vote count, and the Commons Library data (CBP-8647) was blocked. Those "
    "seats have valid_votes null and their national shares are unverified.",
    "The 2005 notional national total (24,814,623) is published in the same "
    "file as the seat results, so it checks internal consistency only; no "
    "independent notional party totals exist in the retrieved sources.",
    "2005n names only Con, Lab, LD, UKIP, Green, BNP, Plaid, the four big NI "
    "parties, Respect and Health Concern. Alliance (NI) and every other minor "
    "party are inside unknown_other, so 2005n 'alliance' is absent and 'green' "
    "is named in 190 seats only.",
    "The Referendum Party (1997) is counted as 'other' rather than as UKIP's "
    "precursor.",
    "NI Conservatives count as 'con' and Greens (NI) and Scottish Greens as "
    "'green'.",
    "The 1997-boundary Scotland and Northern Ireland codes (S1997…, N1997…) are "
    "the results database's own identifiers, not ONS codes.",
    "The 1997, 2001 and 2005 Scotland results come from the api site's data "
    "file at a pinned commit; only 10 election pages and the three index pages "
    "were compared with it.",
    "Downloaded HTML pages carry per-request tokens, so a fresh download of "
    "them has a different SHA-256; the pinned-commit files are reproducible.",
)


class FixtureBuildError(Exception):
    """A source is missing or malformed, or an audit check failed."""


@dataclass(frozen=True, slots=True)
class Candidate:
    """One candidate line: party key, votes and the source's party name."""

    key: str
    votes: int
    party: str
    name: str


@dataclass(frozen=True, slots=True)
class Area:
    """A constituency area in the results database."""

    code: str
    name: str
    nation: str
    region: str


# ── Mapping ──────────────────────────────────────────────────────────────────


def normalise_name(name: str) -> str:
    """Fold case, accents, punctuation and "&" so spelling variants compare equal."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    folded = re.sub(r"[^a-z0-9]+", " ", folded.lower().replace("&", " and "))
    return " ".join(folded.split())


def map_region(country: str | None, english_region: str | None) -> tuple[str, str]:
    """Return the ``(nation, database region)`` of a source country and region.

    Raises:
        FixtureBuildError: The country is not one of the four nations, an
            English seat has an unknown or missing region, or a seat outside
            England has one.
    """
    region = english_region or ""
    if country in NATIONS_WITHOUT_REGIONS:
        if region:
            raise FixtureBuildError(f"{country} seat has English region {region!r}")
        nation, mapped = str(country), str(country)
    elif country == "England":
        if region not in ENGLISH_REGION_NAMES:
            raise FixtureBuildError(f"unsupported English region {region!r}")
        nation, mapped = "England", ENGLISH_REGION_NAMES[region]
    else:
        raise FixtureBuildError(f"unsupported nation {country!r}")
    try:
        return check_nation_region(nation, mapped)
    except SimulationFileError as error:
        raise FixtureBuildError(str(error)) from error


def api_party_key(party: str) -> str:
    """Return the party key of an api-site party name.

    Raises:
        FixtureBuildError: The name resembles a mapped party but is neither
            mapped nor listed in ``OTHER_LOOKALIKES``.
    """
    if party in API_PARTY_KEYS:
        return API_PARTY_KEYS[party]
    if _LOOKALIKE.search(party) and party not in OTHER_LOOKALIKES:
        raise FixtureBuildError(f"party {party!r} needs an explicit mapping")
    return "other"


# ── Reading the sources ──────────────────────────────────────────────────────


def file_digest(path: Path) -> tuple[str, int]:
    """Return a file's SHA-256 and size."""
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest(), len(content)


def read_api_rows(path: Path) -> dict[str, dict[str, list[Candidate]]]:
    """Read the api site's data file: election year -> seat -> candidates."""
    wanted = {f"{year} General Election": year for year in INDEX_PAGES}
    elections: dict[str, dict[str, list[Candidate]]] = {
        year: {} for year in INDEX_PAGES
    }
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if not row or row[0].strip() not in wanted:
                continue
            if len(row) != 10:
                raise FixtureBuildError(f"data file row has {len(row)} fields: {row}")
            year = wanted[row[0].strip()]
            if row[1].strip() != year or row[7].strip() != "0":
                raise FixtureBuildError(f"unexpected year or uncontested flag: {row}")
            votes = row[8].strip()
            if not votes.isdigit():
                raise FixtureBuildError(f"missing vote count: {row}")
            party = row[5].strip()
            candidate = Candidate(
                api_party_key(party), int(votes), party, row[6].strip()
            )
            elections[year].setdefault(row[4].strip(), []).append(candidate)
    return elections


def read_index_page(path: Path) -> list[str]:
    """Return the seat names an api-site general election page lists."""
    text = path.read_text(encoding="utf-8")
    names = [
        html.unescape(name)
        for name in re.findall(
            rf'href="{re.escape(API_SITE)}/elections/\d+">([^<]+)</a>', text
        )
    ]
    stated = re.search(r"There are (\d+) elections\.", text)
    if stated is None or int(stated.group(1)) != len(names):
        raise FixtureBuildError(f"{path.name}: listed seats do not match its count")
    return names


def read_election_page(path: Path) -> tuple[str, list[tuple[str, str, int]]]:
    """Return an election page's title and (candidate, party, votes) rows."""
    text = path.read_text(encoding="utf-8")
    title = re.search(r"<h1>([^<]+)</h1>", text)
    rows = re.findall(
        r'candidate-names/\d+">([^<]+)</a></td>\s*<td><a href="[^"]*/parties/\d+">'
        r'([^<]+)</a></td>\s*<td class="number">([\d,]+)</td>',
        text,
    )
    if title is None or not rows:
        raise FixtureBuildError(f"{path.name}: no title or result rows")
    return html.unescape(title.group(1)), [
        (html.unescape(name), html.unescape(party), int(votes.replace(",", "")))
        for name, party, votes in rows
    ]


def read_areas(
    conn: sqlite3.Connection, sets: Mapping[int, str]
) -> dict[int, list[Area]]:
    """Read the constituency areas of each boundary set, with mapped regions."""
    query = """
        SELECT a.geographic_code, a.name, c.name, r.name
        FROM constituency_areas a
        JOIN countries c ON c.id = a.country_id
        LEFT JOIN english_regions r ON r.id = a.english_region_id
        WHERE a.boundary_set_id = ?
        ORDER BY a.geographic_code
    """
    areas: dict[int, list[Area]] = {}
    for boundary_set, country in sets.items():
        rows = conn.execute(query, (boundary_set,)).fetchall()
        found = []
        for code, name, area_country, english_region in rows:
            if area_country != country:
                raise FixtureBuildError(
                    f"area {code} is in {area_country}, not {country}"
                )
            nation, region = map_region(area_country, english_region)
            found.append(Area(str(code), str(name), nation, region))
        areas[boundary_set] = found
    codes = [area.code for found in areas.values() for area in found]
    if len(codes) != len(set(codes)):
        raise FixtureBuildError("constituency codes repeat across boundary sets")
    return areas


def _by_name(areas: Iterable[Area]) -> dict[str, Area]:
    index: dict[str, Area] = {}
    for area in areas:
        key = normalise_name(area.name)
        if key in index:
            raise FixtureBuildError(f"two areas normalise to {key!r}")
        index[key] = area
    return index


# ── Seat results ─────────────────────────────────────────────────────────────


def seat_result(
    area: Area,
    name: str,
    candidates: Sequence[Candidate],
    valid_votes: int | None,
    declared_winner: Candidate | None = None,
) -> dict[str, Any]:
    """Aggregate one seat's candidates into the fixture's seat record.

    The winner is the top candidate; it must also lead the party totals.

    Raises:
        FixtureBuildError: A tie for first, a declared winner that is not the
            top candidate, an unnamed aggregate on top, a party-total leader
            other than the winning candidate's party, or votes that do not
            sum to the published valid votes.
    """
    ranked = sorted(candidates, key=lambda candidate: -candidate.votes)
    if len(ranked) > 1 and ranked[0].votes == ranked[1].votes:
        raise FixtureBuildError(f"{name}: tie for first place")
    top = ranked[0]
    if declared_winner is not None and declared_winner != top:
        raise FixtureBuildError(f"{name}: declared winner is not the top candidate")
    if top.key == UNKNOWN_OTHER:
        raise FixtureBuildError(f"{name}: unnamed aggregate is the top line")
    votes: Counter[str] = Counter()
    for candidate in candidates:
        votes[candidate.key] += candidate.votes
    unknown = votes.pop(UNKNOWN_OTHER, 0)
    speaker_votes = votes.pop(SPEAKER, 0)
    totals = dict(votes)
    if speaker_votes:
        totals[SPEAKER] = speaker_votes
    leaders = [key for key, value in totals.items() if value == max(totals.values())]
    if leaders != [top.key]:
        raise FixtureBuildError(
            f"{name}: party totals are led by {leaders}, not {top.key}"
        )
    total = sum(candidate.votes for candidate in candidates)
    if valid_votes is not None and valid_votes != total:
        raise FixtureBuildError(f"{name}: {total} candidate votes, {valid_votes} valid")
    return {
        "name": name,
        "nation": area.nation,
        "region": area.region,
        "speaker": speaker_votes > 0,
        # A party line with no votes (five 2005 notional lines) is dropped, so
        # ``votes`` holds positive totals only.
        "votes": {key: value for key, value in sorted(votes.items()) if value > 0},
        "unknown_other": unknown,
        "speaker_votes": speaker_votes,
        "valid_votes": valid_votes,
        "winner": top.key,
    }


def _check_speaker(year: str, seats: Mapping[str, Sequence[Candidate]]) -> None:
    found = [
        (seat, candidate.name)
        for seat, candidates in seats.items()
        for candidate in candidates
        if candidate.key == SPEAKER
    ]
    if found != [SPEAKERS[year]]:
        raise FixtureBuildError(
            f"{year}: Speaker lines {found}, expected {SPEAKERS[year]}"
        )


def build_1997_boundaries(
    api: Mapping[str, Mapping[str, Sequence[Candidate]]],
    areas_1997: Iterable[Area],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    """Build the 1997 and 2001 seat records, keyed by 1997-boundary code."""
    index = _by_name(areas_1997)
    elections: dict[str, dict[str, dict[str, Any]]] = {}
    for year in ("1997", "2001"):
        _check_speaker(year, api[year])
        seats: dict[str, dict[str, Any]] = {}
        for name, candidates in sorted(api[year].items()):
            area = index.get(normalise_name(NAME_ALIASES.get(name, name)))
            if area is None:
                raise FixtureBuildError(
                    f"{year}: {name!r} matches no 1997-boundary area"
                )
            if area.code in seats:
                raise FixtureBuildError(f"{year}: two seats match area {area.code}")
            seats[area.code] = seat_result(area, name, candidates, None)
        if len(seats) != len(index):
            raise FixtureBuildError(
                f"{year}: {len(seats)} seats for {len(index)} areas"
            )
        elections[year] = seats
    if any(elections["1997"][code]["name"] != elections["2001"][code]["name"]
           for code in elections["1997"]):
        raise FixtureBuildError("1997 and 2001 seat names differ for a code")
    mapping = {
        "seats_matched_to_1997_boundary_areas": len(elections["1997"]),
        "name_aliases": dict(sorted(NAME_ALIASES.items())),
        "codes_not_issued_by_ons": sum(
            1 for code in elections["1997"] if code[1:5] == "1997"
        ),
    }
    return elections, mapping


def build_scotland_2005(
    api_2005: Mapping[str, Sequence[Candidate]],
    areas_scotland: Iterable[Area],
    areas_1997: Iterable[Area],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Build Scotland's 59 actual 2005 seats; every other 2005 seat must be on
    the unchanged 1997 boundaries outside Scotland."""
    _check_speaker("2005", api_2005)
    scotland = _by_name(areas_scotland)
    old_outside = _by_name(area for area in areas_1997 if area.nation != "Scotland")
    seats: dict[str, dict[str, Any]] = {}
    matched: set[str] = set()
    for name, candidates in sorted(api_2005.items()):
        key = normalise_name(NAME_ALIASES.get(name, name))
        if key in matched:
            raise FixtureBuildError(f"2005: two seats match {key!r}")
        matched.add(key)
        if key in scotland:
            area = scotland[key]
            seats[area.code] = seat_result(area, name, candidates, None)
        elif key not in old_outside:
            raise FixtureBuildError(f"2005: {name!r} matches no area")
    outside = matched - set(scotland)
    if set(seats) != {area.code for area in scotland.values()} or outside != set(
        old_outside
    ):
        raise FixtureBuildError(
            f"2005: {len(seats)} Scottish and {len(outside)} other seats matched"
        )
    return seats, {
        "scottish_seats_matched_to_2005_boundaries": len(seats),
        "other_2005_seats_on_1997_boundaries": len(outside),
    }


# The notional file's columns this build reads, and those that must be counts.
NOTIONAL_COUNT_COLUMNS = frozenset({
    "Total valid votes in general election", "Election valid vote count",
    "Candidate vote count", "Candidate result position",
})
NOTIONAL_COLUMNS = NOTIONAL_COUNT_COLUMNS | {
    "General election is notional", "Election is by-election",
    "Candidate is standing as Commons Speaker", "Constituency geographic code",
    "Constituency name", "Country name", "English region name",
    "Boundary set start date", "Election URL", "Main party name",
    "Candidate is notional political party aggregate",
    "Candidate is standing as independent", "Candidate given name",
    "Candidate family name",
}


def read_notional(
    path: Path, areas_2010: Iterable[Area], codes_2010_election: set[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Build the 591 notional seats; each must match a 2010 area by code and region."""
    by_code = {area.code: area for area in areas_2010}
    rows_by_seat: dict[str, list[dict[str, str]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = NOTIONAL_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise FixtureBuildError(f"notional file lacks columns {sorted(missing)}")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise FixtureBuildError(
                    f"notional file line {reader.line_num} has the wrong field count"
                )
            for column in NOTIONAL_COUNT_COLUMNS:
                if not row[column].isdigit():
                    raise FixtureBuildError(
                        f"notional file line {reader.line_num}: {column} is not a count"
                    )
            if (row["General election is notional"] != "true"
                    or row["Election is by-election"] != "false"
                    or row["Candidate is standing as Commons Speaker"] != "false"):
                raise FixtureBuildError(
                    f"unexpected notional row: {row['Election URL']}"
                )
            rows_by_seat.setdefault(row["Constituency geographic code"], []).append(row)
    published_totals = {row["Total valid votes in general election"]
                        for rows in rows_by_seat.values() for row in rows}
    if len(published_totals) != 1:
        raise FixtureBuildError("notional rows disagree on the national total")
    seats: dict[str, dict[str, Any]] = {}
    party_votes: Counter[str] = Counter()
    for code, rows in sorted(rows_by_seat.items()):
        seat_fields = {
            (row["Constituency name"], row["Country name"], row["English region name"],
             row["Election valid vote count"], row["Boundary set start date"])
            for row in rows
        }
        if len(seat_fields) != 1:
            raise FixtureBuildError(f"notional seat {code} rows disagree")
        name, country, english_region, valid, boundary_start = seat_fields.pop()
        area = by_code.get(code)
        if (area is None or boundary_start != "2010-04-13"
                or (area.nation, area.region) != map_region(country, english_region)
                or code not in codes_2010_election):
            raise FixtureBuildError(f"notional seat {code} matches no 2010 seat")
        if normalise_name(name) != normalise_name(area.name):
            raise FixtureBuildError(f"notional seat {code} name {name!r} differs")
        candidates: list[Candidate] = []
        declared: list[Candidate] = []
        for row in rows:
            party = row["Main party name"]
            aggregate = row["Candidate is notional political party aggregate"] == "true"
            independent = row["Candidate is standing as independent"] == "true"
            if aggregate and not party and not independent:
                key = UNKNOWN_OTHER
            elif independent and not party and not aggregate:
                key = "other"
            elif party in NOTIONAL_PARTY_KEYS and not aggregate and not independent:
                key = NOTIONAL_PARTY_KEYS[party]
            else:
                raise FixtureBuildError(
                    f"notional seat {code}: unmapped line {party!r}"
                )
            label = party or ("unnamed aggregate" if aggregate else "Independent")
            candidate = Candidate(key, int(row["Candidate vote count"]), label,
                                  f"{row['Candidate given name']} "
                                  f"{row['Candidate family name']}".strip())
            candidates.append(candidate)
            party_votes[label] += candidate.votes
            if row["Candidate result position"] == "1":
                declared.append(candidate)
        if len(declared) != 1:
            raise FixtureBuildError(f"notional seat {code} has {len(declared)} winners")
        seats[code] = seat_result(area, name, candidates, int(valid), declared[0])
    published = int(published_totals.pop())
    return seats, {
        "candidate_rows": sum(len(rows) for rows in rows_by_seat.values()),
        "published_total_valid_votes": published,
        "party_votes": dict(sorted(party_votes.items())),
    }


# ── The database crosswalk ───────────────────────────────────────────────────


def database_crosswalk(
    conn: sqlite3.Connection, seats: Mapping[str, Mapping[str, Any]],
    areas_2010: Mapping[str, Area],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Return 2010 code -> database seat name, checking names and regions.

    Also checks that each party key's database row exists and that "other"
    is the row ``PARTY_ID_ALIASES`` folds "Other" into.
    """
    row = conn.execute("SELECT id FROM maps WHERE name = ?", (DATABASE_MAP,)).fetchone()
    if row is None:
        raise FixtureBuildError(f"no map named {DATABASE_MAP!r}")
    map_id = row[0]
    if conn.execute(
        "SELECT 1 FROM elections WHERE name = ? AND map_id = ?", (DATABASE_2010, map_id)
    ).fetchone() is None:
        raise FixtureBuildError(f"{DATABASE_2010} is not on {DATABASE_MAP}")
    party_ids = {str(name): int(party_id)
                 for party_id, name in conn.execute("SELECT id, name FROM parties")}
    missing = sorted({*PARTY_DB_NAMES.values(), ALIASED_OTHER} - set(party_ids))
    if missing:
        raise FixtureBuildError(f"database lacks parties {missing}")
    if (PARTY_ID_ALIASES.get(party_ids[ALIASED_OTHER])
            != party_ids[PARTY_DB_NAMES["other"]]):
        raise FixtureBuildError("'other' is not the alias target of 'Other'")
    database: dict[str, tuple[str, str]] = {}
    query = """
        SELECT s.seat_name, r.name FROM seats s JOIN regions r ON r.id = s.region_id
        WHERE s.map_id = ?
    """
    for seat_name, region in conn.execute(query, (map_id,)):
        key = normalise_name(seat_name)
        if key in database:
            raise FixtureBuildError(f"database seats repeat {key!r}")
        database[key] = (str(seat_name), str(region))
    names: dict[str, str] = {}
    renamed = []
    for code, seat in seats.items():
        match = database.get(normalise_name(areas_2010[code].name))
        if match is None or match[1] != seat["region"]:
            raise FixtureBuildError(
                f"2010 seat {code} matches no database seat and region"
            )
        names[code] = match[0]
        if match[0] != seat["name"]:
            renamed.append({"code": code, "source": seat["name"], "database": match[0]})
    if len(set(names.values())) != len(database) or len(names) != len(database):
        raise FixtureBuildError("2010 seats and database seats are not one-to-one")
    return names, {
        "map": DATABASE_MAP,
        "seats_matched_by_name_and_region": len(names),
        "names_taken_from_database": renamed,
    }


# ── Audit and assembly ───────────────────────────────────────────────────────


def election_audit(
    seats: Mapping[str, Mapping[str, Any]], candidate_rows: int
) -> dict[str, Any]:
    """Totals, shares, winners and the valid-vote reconciliation of one election."""
    party_totals: Counter[str] = Counter()
    for seat in seats.values():
        party_totals.update(seat["votes"])
    unknown = sum(seat["unknown_other"] for seat in seats.values())
    speaker = sum(seat["speaker_votes"] for seat in seats.values())
    total = sum(party_totals.values()) + unknown + speaker
    published = [seat for seat in seats.values() if seat["valid_votes"] is not None]
    differences = [
        abs(seat["valid_votes"] - sum(seat["votes"].values()) - seat["unknown_other"]
            - seat["speaker_votes"])
        for seat in published
    ]
    return {
        "seat_count": len(seats),
        "candidate_rows": candidate_rows,
        "total_votes": total,
        "party_totals": dict(sorted(party_totals.items())),
        "unknown_other_total": unknown,
        "unknown_other_seats": sum(
            1 for seat in seats.values() if seat["unknown_other"]
        ),
        "speaker_votes": speaker,
        "speaker_seats": sorted(
            code for code, seat in seats.items() if seat["speaker"]
        ),
        "national_shares": {
            key: round(value / total, 6)
            for key, value in sorted({**party_totals, UNKNOWN_OTHER: unknown,
                                      SPEAKER: speaker}.items())
        },
        "winners": dict(
            sorted(Counter(seat["winner"] for seat in seats.values()).items())
        ),
        "valid_vote_reconciliation": {
            "seats_with_published_valid_votes": len(published),
            "seats_without_published_valid_votes": len(seats) - len(published),
            "max_abs_difference": max(differences) if differences else None,
        },
    }


@dataclass(frozen=True, slots=True)
class Build:
    """The assembled fixture body and the lines to print."""

    body: dict[str, Any]
    lines: list[str]


def build(source_dir: Path, conn: sqlite3.Connection) -> Build:
    """Parse, map and audit every source into the fixture body (unsealed)."""
    digests = {}
    for source in SOURCES:
        path = source_dir / source.file
        if not path.is_file():
            raise FixtureBuildError(f"missing source {path}")
        digests[source.file] = file_digest(path)
    api = read_api_rows(source_dir / SEED_FILE)
    index_check = {}
    for year, (file, _number) in INDEX_PAGES.items():
        listed = read_index_page(source_dir / file)
        if sorted(listed) != sorted(api[year]):
            raise FixtureBuildError(f"{file} lists different seats from the data file")
        index_check[year] = {"listed": len(listed), "data_file": len(api[year])}
    spot_checks = []
    for year, seat_name, page in SPOT_CHECKS:
        page_path = source_dir / f"election-pages/{page}.html"
        title, page_rows = read_election_page(page_path)
        expected = sorted((c.name, c.party, c.votes) for c in api[year][seat_name])
        if (title != f"{year} General Election - {seat_name}"
                or sorted(page_rows) != expected):
            raise FixtureBuildError(f"election page {page} differs from the data file")
        spot_checks.append({"election": year, "seat": seat_name, "page": page,
                            "rows": len(page_rows), "matches_data_file": True})
    with closing(sqlite3.connect(
        f"{(source_dir / PSEPHOLOGY_FILE).resolve().as_uri()}?mode=ro&immutable=1",
        uri=True,
    )) as results_db:
        areas_1997 = read_areas(results_db, BOUNDARY_SETS_1997)
        areas_2010 = read_areas(results_db, BOUNDARY_SETS_2010)
        codes_2010_election = {
            str(code) for (code,) in results_db.execute(
                """
                SELECT a.geographic_code FROM elections e
                JOIN constituency_groups g ON g.id = e.constituency_group_id
                JOIN constituency_areas a ON a.id = g.constituency_area_id
                WHERE e.general_election_id = ?
                """,
                (GENERAL_ELECTION_2010,),
            )
        }
    all_1997 = [area for found in areas_1997.values() for area in found]
    all_2010 = [area for found in areas_2010.values() for area in found]
    if (len(all_1997) != EXPECTED_SEATS["1997"]
            or len(all_2010) != EXPECTED_SEATS["2005n"]):
        raise FixtureBuildError("boundary sets do not have 659 and 650 seats")
    if codes_2010_election != {area.code for area in all_2010}:
        raise FixtureBuildError("the 2010 election's areas differ from the 2010 sets")
    old, old_mapping = build_1997_boundaries(api, all_1997)
    scotland, scotland_mapping = build_scotland_2005(
        api["2005"], areas_2010[SCOTLAND_2005_SET], all_1997
    )
    notional, notional_source = read_notional(
        source_dir / NOTIONAL_FILE,
        [area for boundary_set, found in areas_2010.items()
         if boundary_set != SCOTLAND_2005_SET for area in found],
        codes_2010_election,
    )
    scotland_rows = sum(len(api["2005"][seat["name"]]) for seat in scotland.values())
    combined = dict(sorted({**notional, **scotland}.items()))
    by_code_2010 = {area.code: area for area in all_2010}
    database_names, database_mapping = database_crosswalk(conn, combined, by_code_2010)
    for code, seat in combined.items():
        seat["name"] = database_names[code]
    buckingham = [code for code, name in database_names.items()
                  if name == SPEAKER_SEATS[DATABASE_2010]]
    if len(buckingham) != 1:
        raise FixtureBuildError("the 2010 Speaker's seat is not on the 2010 map")
    seats_by_election = {"1997": old["1997"], "2001": old["2001"], "2005n": combined}
    rows = {
        "1997": sum(len(c) for c in api["1997"].values()),
        "2001": sum(len(c) for c in api["2001"].values()),
        "2005n": notional_source["candidate_rows"] + scotland_rows,
    }
    audits = {key: election_audit(seats, rows[key])
              for key, seats in seats_by_election.items()}
    notional_total = sum(seat["valid_votes"] for seat in notional.values())
    audits["2005n"]["published_total_check"] = {
        "scope": "England, Wales and Northern Ireland notional (591 seats)",
        "published_total_valid_votes": notional_source["published_total_valid_votes"],
        "sum_of_seat_valid_votes": notional_total,
        "difference": notional_total - notional_source["published_total_valid_votes"],
        "independent": False,
        "note": "the published total is in the same file as the seat results",
    }
    audits["2005n"]["scotland_actual_2005"] = election_audit(scotland, scotland_rows)
    for key in ("1997", "2001"):
        audits[key]["published_total_check"] = {
            "independent": False,
            "note": "no published valid-vote or national party totals in the "
                    "retrieved sources",
        }
    party_mapping = {
        "api_site": {
            party: {"key": api_party_key(party), "votes": {
                year: sum(c.votes for cs in api[year].values() for c in cs
                          if c.party == party)
                for year in INDEX_PAGES
            }}
            for party in sorted({c.party for year in api for cs in api[year].values()
                                 for c in cs})
        },
        "notional_2005": {
            label: {"key": _notional_key(label), "votes": votes}
            for label, votes in notional_source["party_votes"].items()
        },
    }
    speaker_codes = {key: audits[key]["speaker_seats"] for key in seats_by_election}
    transitions = []
    for label, (earlier, later, database) in EXPECTED_TRANSITIONS.items():
        excluded = set(speaker_codes[earlier])
        # A database successor's Speaker seat is on the 2010 map, not flagged here.
        excluded |= set(speaker_codes[later]) if later is not None else set(buckingham)
        transitions.append({
            "label": label,
            "earlier": earlier,
            "later_fixture_election": later,
            "later_database_election": database,
            "later_database_map": None if database is None else DATABASE_MAP,
            "excluded_seat_codes": sorted(excluded),
        })
    body: dict[str, Any] = {
        "schema": HISTORICAL_SCHEMA,
        "provenance": {
            "retrieved_on": RETRIEVED_ON,
            "builder": BUILDER,
            "sources": [
                {"name": s.name, "url": s.url, "file": s.file,
                 "sha256": digests[s.file][0], "bytes": digests[s.file][1],
                 "used_for": s.used_for}
                for s in SOURCES
            ],
            "unavailable_sources": list(UNAVAILABLE_SOURCES),
            "database_crosswalk": {
                "map": DATABASE_MAP,
                "later_election": DATABASE_2010,
                "opened": "read-only",
                "seat_names_and_regions_from_database": True,
            },
        },
        "parties": dict(PARTY_DB_NAMES),
        "elections": {
            key: {"label": label, "polling_date": polling_date, "notional": notional,
                  "boundaries": BOUNDARY_NOTES[key], "seats": seats_by_election[key]}
            for key, (label, polling_date, notional) in EXPECTED_ELECTIONS.items()
        },
        "transitions": transitions,
        "audit": {
            "elections": audits,
            "mapping": {
                "index_pages": index_check,
                "boundaries_1997": old_mapping,
                "scotland_2005": scotland_mapping,
                "notional_2005_seats_matched_to_2010_by_code_and_region": len(notional),
                "database": database_mapping,
            },
            "party_mapping": party_mapping,
            "spot_checks": spot_checks,
            "open_questions": list(OPEN_QUESTIONS),
        },
    }
    return Build(body, summary_lines(body))


def _notional_key(label: str) -> str:
    if label == "unnamed aggregate":
        return UNKNOWN_OTHER
    if label == "Independent":
        return "other"
    return NOTIONAL_PARTY_KEYS[label]


def summary_lines(body: Mapping[str, Any]) -> list[str]:
    """The audit as printable lines."""
    lines = []
    for key, audit in body["audit"]["elections"].items():
        reconciliation = audit["valid_vote_reconciliation"]
        lines += [
            f"{key}: {audit['seat_count']} seats, {audit['candidate_rows']} lines, "
            f"{audit['total_votes']:,} votes",
            f"    unknown_other {audit['unknown_other_total']:,} in "
            f"{audit['unknown_other_seats']} seats; Speaker {audit['speaker_votes']:,} "
            f"({', '.join(audit['speaker_seats']) or 'none'})",
            f"    valid votes: {reconciliation['seats_with_published_valid_votes']} "
            f"seats checked, max |diff| {reconciliation['max_abs_difference']}, "
            f"{reconciliation['seats_without_published_valid_votes']} unpublished",
            "    shares: " + ", ".join(
                f"{party} {share:.2%}"
                for party, share in audit["national_shares"].items()
            ),
            "    winners: "
            + ", ".join(f"{p} {n}" for p, n in audit["winners"].items()),
        ]
    check = body["audit"]["elections"]["2005n"]["published_total_check"]
    lines.append(
        f"2005n notional total {check['sum_of_seat_valid_votes']:,} vs published "
        f"{check['published_total_valid_votes']:,} (difference {check['difference']})"
    )
    for transition in body["transitions"]:
        lines.append(
            f"{transition['label']}: excluded "
            f"{', '.join(transition['excluded_seat_codes'])}"
        )
    return lines


def seal(body: Mapping[str, Any]) -> str:
    """Add the content hash and return the file text."""
    sealed = {**body, "content_sha256": content_hash(body)}
    return json.dumps(sealed, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def verify(text: str) -> None:
    """Load the sealed text through the fixture loader before it is written."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.json"
        path.write_text(text, encoding="utf-8")
        load_historical_transitions(path)


def main(argv: Sequence[str] | None = None) -> int:
    """Build, audit and write the fixture (unless ``--dry-run``).

    Returns:
        Process exit code: 0 on success, 1 if a source is missing or an audit
        check fails.
    """
    parser = argparse.ArgumentParser(
        description="Build the Westminster historical-transitions fixture.",
    )
    parser.add_argument(
        "source_dir", type=Path, help="Directory of downloaded sources."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the audit; write nothing."
    )
    parser.add_argument(
        "--output", type=Path, default=HISTORICAL_PATH, help="Fixture to write."
    )
    args = parser.parse_args(argv)
    if any(PARTY_DB_NAMES[key] != name for key, name in DB_PARTY_NAMES.items()
           if key in PARTY_DB_NAMES):
        print("error: party keys disagree with calibrate_uncertainty.DB_PARTY_NAMES")
        return 1
    db_path = DatabaseConfig.from_env().database_path
    print(f"database (read-only): {db_path}")
    try:
        with closing(open_database(db_path, read_only=True)) as conn:
            result = build(args.source_dir, conn)
        text = seal(result.body)
        verify(text)
    except (FixtureBuildError, SimulationFileError, sqlite3.Error, OSError,
            KeyError, TypeError, ValueError) as err:
        print(f"error: {err}")
        return 1
    for line in result.lines:
        print(line)
    if args.dry_run:
        print("\nDry-run complete. Nothing written.")
        return 0
    try:
        write_text_files({args.output: text})
    except OSError as err:
        print(f"error: {err}")
        return 1
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
