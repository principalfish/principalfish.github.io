"""Shared seed data and fakes for the UK (Westminster + Holyrood) tests.

Import as ``from tests.uk_fixtures import ...``. Every helper writes through the
``db`` fixture's :class:`Database`, so nothing here touches the live database.
The module-level tables are read-only (tuples and ``MappingProxyType``) because
every test shares them.

- :func:`seed_westminster_world`: the map, regions, parties, seats and 2024
  baseline that the Westminster importers and UNS model look up by name.
- :func:`seed_holyrood_world`: a two-region Holyrood map with constituency and
  list seats plus the linked constituency and list elections.
- :func:`add_poll_with_rows`: one poll (and its pollster, if new) with national
  and optional regional rows.
- :func:`build_workbook` / :func:`workbook_bytes` / :class:`FakeUrlResponse`: an
  in-memory XLSX served to a monkeypatched ``urlopen``.
- :class:`FakeXlrdSheet` / :class:`FakeXlrdBook`: duck-typed ``xlrd`` stand-ins
  for the Lord Ashcroft importer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from types import MappingProxyType, TracebackType

from openpyxl import Workbook
from sqlalchemy import update

from db import Database
from models import ElectionType, Poll, Pollster

# ── Westminster ───────────────────────────────────────────────────────────────

WESTMINSTER_MAP_NAME = "UK Constituencies post 2022"
WESTMINSTER_BASELINE_NAME = "2024 General Election"

# The internal region names every importer's region mapping resolves to.
WESTMINSTER_REGION_NAMES: tuple[str, ...] = (
    "East Midlands",
    "East of England",
    "London",
    "North East England",
    "North West England",
    "Northern Ireland",
    "Scotland",
    "South East England",
    "South West England",
    "Wales",
    "West Midlands",
    "Yorkshire and The Humber",
)

# Seeded in this order into an empty parties table, so each party's id is its
# 1-based position. "Other" lands on id 7 and "Others" on id 15, the pair the
# UNS model's ``PARTY_ID_ALIASES`` merges.
WESTMINSTER_PARTY_NAMES: tuple[str, ...] = (
    "Conservative",
    "Labour",
    "Liberal Democrats",
    "Scottish National Party",
    "Plaid Cymru",
    "Green",
    "Other",
    "Reform UK",
    "Democratic Unionist Party",
    "Sinn Féin",
    "SDLP",
    "Ulster Unionist Party",
    "Alliance",
    "Alba Party",
    "Others",
)

# seat name → (region name, party name → 2024 vote total). The highest total in
# each seat is marked elected. Hexham is the one Conservative hold, so a swing
# to Labour flips it; "Other" and "Others" each poll in one seat so the alias
# merge has something to merge.
WESTMINSTER_BASELINE_VOTES: Mapping[str, tuple[str, Mapping[str, float]]] = (
    MappingProxyType(
        {
            "Holborn and St Pancras": (
                "London",
                MappingProxyType(
                    {
                        "Labour": 20000.0,
                        "Conservative": 6000.0,
                        "Green": 5000.0,
                        "Liberal Democrats": 4000.0,
                        "Reform UK": 3000.0,
                        "Others": 2000.0,
                    }
                ),
            ),
            "Glasgow North": (
                "Scotland",
                MappingProxyType(
                    {
                        "Labour": 15000.0,
                        "Scottish National Party": 12000.0,
                        "Green": 2500.0,
                        "Conservative": 2000.0,
                        "Reform UK": 2000.0,
                        "Liberal Democrats": 1500.0,
                    }
                ),
            ),
            "Cardiff East": (
                "Wales",
                MappingProxyType(
                    {
                        "Labour": 14000.0,
                        "Reform UK": 6000.0,
                        "Plaid Cymru": 5000.0,
                        "Conservative": 4000.0,
                        "Liberal Democrats": 3000.0,
                        "Green": 2000.0,
                    }
                ),
            ),
            "Hexham": (
                "North East England",
                MappingProxyType(
                    {
                        "Conservative": 20000.0,
                        "Labour": 18000.0,
                        "Reform UK": 7000.0,
                        "Liberal Democrats": 3000.0,
                        "Green": 2000.0,
                        "Other": 1000.0,
                    }
                ),
            ),
        }
    )
)


@dataclass(frozen=True, slots=True)
class WestminsterWorld:
    """Ids of a seeded Westminster map, keyed by the names they were seeded with."""

    map_id: int
    map_name: str
    baseline_election_id: int
    baseline_election_name: str
    party_ids: Mapping[str, int]
    region_ids: Mapping[str, int]
    seat_ids: Mapping[str, int]


def seed_westminster_world(db: Database) -> WestminsterWorld:
    """Build the Westminster map the importers and the UNS model look up by name.

    Seeds every internal region, every party any importer maps to (plus the
    NI parties and "Others"), four seats in London, Scotland, Wales and the
    North East, and a ``uk_general`` baseline with ``WESTMINSTER_BASELINE_VOTES``.

    Must run on an empty parties table: party ids are asserted to match their
    position in ``WESTMINSTER_PARTY_NAMES`` so the model's id alias lines up.
    """
    westminster_map = db.add_map(WESTMINSTER_MAP_NAME, parliament="westminster")
    region_ids = {
        name: db.add_region(westminster_map.id, name).id
        for name in WESTMINSTER_REGION_NAMES
    }

    party_ids: dict[str, int] = {}
    for position, name in enumerate(WESTMINSTER_PARTY_NAMES, start=1):
        party_id = db.add_party(name).id
        assert party_id == position, f"{name!r} got id {party_id}, expected {position}"
        party_ids[name] = party_id

    baseline = db.add_election(
        westminster_map.id,
        2024,
        WESTMINSTER_BASELINE_NAME,
        ElectionType.uk_general,
        election_date=date(2024, 7, 4),
    )
    seat_ids: dict[str, int] = {}
    for seat_name, (region_name, votes) in WESTMINSTER_BASELINE_VOTES.items():
        seat = db.add_seat(
            westminster_map.id, seat_name, region_id=region_ids[region_name]
        )
        seat_ids[seat_name] = seat.id
        winner = max(votes, key=votes.__getitem__)
        for party_name, total in votes.items():
            db.add_vote(
                baseline.id,
                seat.id,
                party_id=party_ids[party_name],
                vote_total=total,
                elected=party_name == winner,
            )

    return WestminsterWorld(
        map_id=westminster_map.id,
        map_name=westminster_map.name,
        baseline_election_id=baseline.id,
        baseline_election_name=baseline.name,
        party_ids=MappingProxyType(party_ids),
        region_ids=MappingProxyType(region_ids),
        seat_ids=MappingProxyType(seat_ids),
    )


# ── Holyrood ──────────────────────────────────────────────────────────────────

HOLYROOD_MAP_NAME = "Scottish Parliament Constituencies 2026"
HOLYROOD_CONSTITUENCY_ELECTION_NAME = "2026 Scottish Parliament Election"
HOLYROOD_LIST_ELECTION_NAME = f"{HOLYROOD_CONSTITUENCY_ELECTION_NAME} (List)"

# Every party the Holyrood Wikipedia importer maps a column to.
HOLYROOD_PARTY_NAMES: tuple[str, ...] = (
    "Scottish National Party",
    "Labour",
    "Conservative",
    "Liberal Democrats",
    "Scottish Greens",
    "Alba Party",
    "Reform UK",
    "Others",
)

# region → (first constituency, second constituency). In every region the SNP
# wins the first 20000–10000 over Labour and Labour wins the second by the same.
HOLYROOD_CONSTITUENCIES: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "Glasgow": ("Glasgow Kelvin and Maryhill", "Glasgow Southside"),
        "North East Scotland": (
            "Aberdeen Central",
            "Aberdeen Deeside and North Kincardine",
        ),
    }
)
HOLYROOD_LIST_SEATS_PER_REGION = 3

# Regional list votes, identical on every list seat in a region (as the real data
# stores them). With one constituency win each for SNP and Labour, D'Hondt for
# three seats gives SNP (1200/2), Conservative (500/1), SNP (1200/3).
HOLYROOD_LIST_VOTES: Mapping[str, float] = MappingProxyType(
    {
        "Scottish National Party": 1200.0,
        "Labour": 700.0,
        "Conservative": 500.0,
    }
)


@dataclass(frozen=True, slots=True)
class HolyroodWorld:
    """Ids of a seeded Holyrood map, keyed by the names they were seeded with."""

    map_id: int
    map_name: str
    constituency_election_id: int
    constituency_election_name: str
    list_election_id: int
    list_election_name: str
    party_ids: Mapping[str, int]
    region_ids: Mapping[str, int]
    constituency_seat_ids: Mapping[str, int]
    list_seat_ids: Mapping[str, int]


def seed_holyrood_world(db: Database) -> HolyroodWorld:
    """Build a two-region Holyrood map with linked constituency and list elections.

    Each region in ``HOLYROOD_CONSTITUENCIES`` gets its two constituency seats and
    ``HOLYROOD_LIST_SEATS_PER_REGION`` list seats named ``"<Region> List <N>"``.
    The constituency election uses the Holyrood model's default baseline name;
    the list election is its ``holyrood_list`` child. Parties that already exist
    (e.g. from :func:`seed_westminster_world`) are reused by name.
    """
    holyrood_map = db.add_map(HOLYROOD_MAP_NAME, parliament="holyrood")

    party_ids: dict[str, int] = {}
    for name in HOLYROOD_PARTY_NAMES:
        existing = db.get_party_by_name(name)
        party_ids[name] = existing.id if existing is not None else db.add_party(name).id
    snp = party_ids["Scottish National Party"]
    labour = party_ids["Labour"]

    constituency_election = db.add_election(
        holyrood_map.id,
        2026,
        HOLYROOD_CONSTITUENCY_ELECTION_NAME,
        ElectionType.holyrood_general,
        election_date=date(2026, 5, 7),
    )
    list_election = db.add_election(
        holyrood_map.id,
        2026,
        HOLYROOD_LIST_ELECTION_NAME,
        ElectionType.holyrood_list,
        parent_election_id=constituency_election.id,
        election_date=date(2026, 5, 7),
    )

    region_ids: dict[str, int] = {}
    constituency_seat_ids: dict[str, int] = {}
    list_seat_ids: dict[str, int] = {}
    for region_name, constituencies in HOLYROOD_CONSTITUENCIES.items():
        region_id = db.add_region(holyrood_map.id, region_name).id
        region_ids[region_name] = region_id

        for seat_name, (winner, runner_up) in zip(
            constituencies, ((snp, labour), (labour, snp))
        ):
            seat_id = db.add_seat(holyrood_map.id, seat_name, region_id=region_id).id
            constituency_seat_ids[seat_name] = seat_id
            db.add_vote(
                constituency_election.id,
                seat_id,
                party_id=winner,
                vote_total=20000.0,
                elected=True,
            )
            db.add_vote(
                constituency_election.id,
                seat_id,
                party_id=runner_up,
                vote_total=10000.0,
            )

        for number in range(1, HOLYROOD_LIST_SEATS_PER_REGION + 1):
            seat_name = f"{region_name} List {number}"
            seat_id = db.add_seat(holyrood_map.id, seat_name, region_id=region_id).id
            list_seat_ids[seat_name] = seat_id
            for party_name, total in HOLYROOD_LIST_VOTES.items():
                db.add_vote(
                    list_election.id,
                    seat_id,
                    party_id=party_ids[party_name],
                    vote_total=total,
                )

    return HolyroodWorld(
        map_id=holyrood_map.id,
        map_name=holyrood_map.name,
        constituency_election_id=constituency_election.id,
        constituency_election_name=constituency_election.name,
        list_election_id=list_election.id,
        list_election_name=list_election.name,
        party_ids=MappingProxyType(party_ids),
        region_ids=MappingProxyType(region_ids),
        constituency_seat_ids=MappingProxyType(constituency_seat_ids),
        list_seat_ids=MappingProxyType(list_seat_ids),
    )


# ── Polls ─────────────────────────────────────────────────────────────────────


def add_poll_with_rows(
    db: Database,
    *,
    map_id: int,
    pollster_identifier: str,
    fieldwork_end: date,
    national: Mapping[int, float],
    regional: Mapping[int, Mapping[int, float]] | None = None,
    fieldwork_start: date | None = None,
    pollster_name: str | None = None,
    pollster_weight: float | None = 1.0,
    sample_size: int | None = 1000,
    source_url: str | None = None,
) -> Poll:
    """Add one poll with a national row per party and optional regional rows.

    The pollster is looked up by ``pollster_identifier`` and created (named
    ``pollster_name``, default the identifier, weighted ``pollster_weight``) only
    if missing; an existing pollster keeps its name and weight. A ``None`` weight
    is stored as NULL.

    Args:
        national: party id → national percentage (rows with ``region_id=None``).
        regional: region id → party id → percentage.
        fieldwork_start: Defaults to two days before ``fieldwork_end``.
    """
    pollster = db.get_pollster_by_identifier(pollster_identifier)
    if pollster is None:
        pollster = db.add_pollster(
            pollster_name or pollster_identifier,
            pollster_identifier,
            weight=pollster_weight,
        )
        if pollster_weight is None:
            # The column's Python-side default turns an explicit None into 1.0
            # on insert; the model's "unweighted pollster" branch needs NULL.
            with db.session() as session:
                session.execute(
                    update(Pollster)
                    .where(Pollster.id == pollster.id)
                    .values(weight=None)
                )
    poll = db.add_poll(
        pollster.id,
        map_id,
        fieldwork_start or fieldwork_end - timedelta(days=2),
        fieldwork_end,
        sample_size=sample_size,
        source_url=source_url,
    )
    for party_id, percentage in national.items():
        db.add_poll_row(poll.id, party_id, percentage)
    for region_id, shares in (regional or {}).items():
        for party_id, percentage in shares.items():
            db.add_poll_row(poll.id, party_id, percentage, region_id=region_id)
    return poll


# ── Workbooks served over a fake urlopen ──────────────────────────────────────


def build_workbook(sheets: Mapping[str, Sequence[Sequence[object]]]) -> Workbook:
    """Return an openpyxl workbook with one sheet per key, rows appended in order.

    Sheets keep the mapping's order; openpyxl's default empty sheet is removed.
    """
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(list(row))
    return workbook


def workbook_bytes(workbook: Workbook) -> bytes:
    """Serialise ``workbook`` to XLSX bytes (a ZIP payload starting ``PK``)."""
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class FakeUrlResponse:
    """A ``urlopen`` result serving fixed bytes, used bare or as a context manager.

    Monkeypatch an importer's ``urlopen`` with
    ``lambda *_a, **_k: FakeUrlResponse(payload)``.
    """

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        """Return the whole payload."""
        return self._payload

    def __enter__(self) -> FakeUrlResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


# ── Fake xlrd workbook (Lord Ashcroft .xls) ───────────────────────────────────


class FakeXlrdSheet:
    """Duck-typed ``xlrd.sheet.Sheet``: ``name``, ``nrows``, ``ncols``, ``cell_value``.

    Like xlrd's default (non-ragged) sheets, short rows read as ``""`` up to
    ``ncols``; a cell outside the sheet raises ``IndexError``.
    """

    def __init__(self, name: str, rows: Sequence[Sequence[object]]) -> None:
        self.name = name
        self._rows = [list(row) for row in rows]
        self.nrows = len(self._rows)
        self.ncols = max((len(row) for row in self._rows), default=0)

    def cell_value(self, rowx: int, colx: int) -> object:
        """Return the value at ``(rowx, colx)``, ``""`` for a padded cell."""
        if not (0 <= rowx < self.nrows and 0 <= colx < self.ncols):
            raise IndexError(f"cell ({rowx}, {colx}) outside {self.nrows}x{self.ncols}")
        row = self._rows[rowx]
        return row[colx] if colx < len(row) else ""


class FakeXlrdBook:
    """Duck-typed ``xlrd.book.Book``: ``nsheets`` and ``sheet_by_index``."""

    def __init__(self, sheets: Sequence[FakeXlrdSheet]) -> None:
        self._sheets = list(sheets)
        self.nsheets = len(self._sheets)

    def sheet_by_index(self, sheetx: int) -> FakeXlrdSheet:
        """Return the sheet at ``sheetx``."""
        return self._sheets[sheetx]
