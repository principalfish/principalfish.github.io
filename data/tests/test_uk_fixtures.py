"""Smoke tests for the shared UK test helpers in ``tests/uk_fixtures.py``.

They pin the helpers to what the code under test looks up — the importers'
map, region and party names, the UNS model's party-id alias, the Holyrood model's
list-seat naming — so a drift shows up here rather than as a confusing failure
in a later test. Only read-only model functions are called.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Generator
from datetime import date
from pathlib import Path
from typing import Any

_MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
sys.path.insert(0, str(_MODELS_DIR / "westminster"))
sys.path.insert(0, str(_MODELS_DIR / "holyrood"))

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from config import DatabaseConfig
from console.importers_registry import IMPORTERS
from db import Database
from models import Base, ElectionType, Party
from polls.importers.holyrood import holyrood_wikipedia_import
from polls.importers.westminster import (
    find_out_now_import,
    lord_ashcroft_import,
    survation_import,
)

from run_holyrood_uns_model import (
    BASELINE_ELECTION_NAME as HOLYROOD_BASELINE_ELECTION_NAME,
    HolyroodSimulationConfig,
    _is_list_seat,
    run_holyrood_projection,
)
from run_uns_model import (
    BASELINE_ELECTION_NAME as WESTMINSTER_BASELINE_ELECTION_NAME,
    PARTY_ID_ALIASES,
    build_baseline_vote_state,
)
from tests.uk_fixtures import (
    HOLYROOD_LIST_SEATS_PER_REGION,
    WESTMINSTER_BASELINE_VOTES,
    WESTMINSTER_PARTY_NAMES,
    WESTMINSTER_REGION_NAMES,
    FakeUrlResponse,
    FakeXlrdBook,
    FakeXlrdSheet,
    WestminsterWorld,
    add_poll_with_rows,
    build_workbook,
    copy_database,
    seed_holyrood_world,
    seed_westminster_world,
    workbook_bytes,
)

# Each importer's label → canonical party-name map; most call it PARTY_NAME_MAP.
_PARTY_MAP_ATTRIBUTE = {
    "deltapoll": "PARTY_LABEL_TO_CANONICAL",
    "ipsos": "PARTY_LINE_MAP",
}
# Each importer's source-region → internal-region-name map; every importer except
# the national-only ones has at least one.
_REGION_MAP_ATTRIBUTES = (
    "SOURCE_REGION_TO_INTERNAL",
    "REGION_HEADER_TO_INTERNAL",
    "MACRO_TO_INTERNAL_REGIONS",
    "MACRO_REGION_TO_INTERNAL",
)
_NATIONAL_ONLY = frozenset({"techne"})


class TestSeedWestminsterWorld:
    """The seeded Westminster world satisfies every importer and the UNS model."""

    def test_the_map_is_the_one_every_importer_defaults_to(
        self, db: Database
    ) -> None:
        """Each registered importer's DEFAULT_MAP_NAME finds the seeded map."""
        world = seed_westminster_world(db)

        for identifier, meta in IMPORTERS.items():
            poll_map = db.get_map_by_name(meta["module"].DEFAULT_MAP_NAME)
            assert poll_map is not None, identifier
            assert poll_map.id == world.map_id

    def test_every_importer_party_exists(self, db: Database) -> None:
        """Every canonical party an importer maps to is a seeded party."""
        world = seed_westminster_world(db)

        for identifier, meta in IMPORTERS.items():
            attribute = _PARTY_MAP_ATTRIBUTE.get(identifier, "PARTY_NAME_MAP")
            party_map: dict[str, str] = getattr(meta["module"], attribute)
            assert set(party_map.values()) <= set(world.party_ids), identifier
            for name in party_map.values():
                party = db.get_party_by_name(name)
                assert party is not None and party.id == world.party_ids[name]

    def test_every_importer_region_exists_on_the_map(self, db: Database) -> None:
        """Every internal region an importer maps to is a region of the seeded map."""
        world = seed_westminster_world(db)
        seeded = {region.name for region in db.get_regions_for_map(world.map_id)}

        mapped: set[str] = set()
        for identifier, meta in IMPORTERS.items():
            region_maps = [
                getattr(meta["module"], attribute)
                for attribute in _REGION_MAP_ATTRIBUTES
                if hasattr(meta["module"], attribute)
            ]
            # A renamed region map would otherwise drop out of this check silently.
            if identifier in _NATIONAL_ONLY:
                assert not region_maps, identifier
            else:
                assert region_maps, identifier
            for region_map in region_maps:
                for internal in region_map.values():
                    if isinstance(internal, str):
                        mapped.add(internal)
                    else:
                        mapped.update(internal)

        assert mapped == seeded == set(WESTMINSTER_REGION_NAMES)
        assert seeded == set(world.region_ids)

    def test_party_ids_line_up_with_the_model_alias(self, db: Database) -> None:
        """"Other" → "Others" is exactly the model's PARTY_ID_ALIASES."""
        world = seed_westminster_world(db)

        other, others = world.party_ids["Other"], world.party_ids["Others"]
        assert PARTY_ID_ALIASES == {other: others}

    def test_a_second_seed_trips_the_id_assertion(self, db: Database) -> None:
        """A non-empty parties table fails loudly rather than shifting the ids."""
        db.add_party("Already here")

        with pytest.raises(AssertionError, match="expected 1"):
            seed_westminster_world(db)

    def test_the_baseline_is_the_model_default_with_merged_others(
        self, db: Database
    ) -> None:
        """The model's default baseline holds the seeded votes.

        ``build_baseline_vote_state`` folds "Other" into "Others".
        """
        world = seed_westminster_world(db)
        baseline = db.get_election_by_name(WESTMINSTER_BASELINE_ELECTION_NAME)
        assert baseline is not None
        assert baseline.id == world.baseline_election_id
        assert baseline.type == ElectionType.uk_general
        assert baseline.map_id == world.map_id

        region_by_seat_id: dict[int, int | None] = {
            seat.id: seat.region_id for seat in db.get_seats_for_map(world.map_id)
        }
        seat_totals, national_totals, _, _ = build_baseline_vote_state(
            db, world.baseline_election_id, region_by_seat_id
        )

        others = world.party_ids["Others"]
        assert world.party_ids["Other"] not in national_totals
        assert national_totals[others] == 3000.0
        assert set(seat_totals) == set(world.seat_ids.values())
        assert sum(national_totals.values()) == sum(
            sum(votes.values()) for _, votes in WESTMINSTER_BASELINE_VOTES.values()
        )

    def test_the_largest_party_in_each_seat_is_elected(self, db: Database) -> None:
        """Exactly one winner per seat, and Hexham is the Conservative hold."""
        world = seed_westminster_world(db)

        elected = [
            (vote.seat_id, vote.party_id)
            for vote in db.get_votes_for_election(world.baseline_election_id)
            if vote.elected
        ]
        winners = dict(elected)

        assert len(elected) == len(world.seat_ids)
        assert set(winners) == set(world.seat_ids.values())
        assert winners[world.seat_ids["Hexham"]] == world.party_ids["Conservative"]
        assert winners[world.seat_ids["Glasgow North"]] == world.party_ids["Labour"]


@pytest.fixture()
def empty_database(tmp_path: Path) -> Generator[Database, None, None]:
    """A second fresh database beside ``db``, with tables and no rows."""
    config = DatabaseConfig.model_construct(database_path=str(tmp_path / "second.db"))
    database = Database(config)
    database.create_tables()
    yield database
    database.engine.dispose()


def _every_table(database: Database) -> dict[str, list[tuple[Any, ...]]]:
    """Return every table's rows, in primary-key order, keyed by table name."""
    with database.engine.connect() as connection:
        return {
            table.name: [
                tuple(row)
                for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                )
            ]
            for table in Base.metadata.sorted_tables
        }


def _party_count(database: Database) -> int:
    """Return how many parties ``database`` holds."""
    with database.session() as session:
        count: int = session.execute(
            select(func.count()).select_from(Party)
        ).scalar_one()
    return count


class TestWestminsterWorldFixture:
    """``westminster_world`` restores the session template into each test's ``db``."""

    def test_restored_world_matches_a_fresh_seed(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        empty_database: Database,
    ) -> None:
        """Same ids and every table row for row as seeding ``db`` directly."""
        fresh_world = seed_westminster_world(empty_database)

        restored, fresh = _every_table(db), _every_table(empty_database)

        assert westminster_world == fresh_world
        for name in ("maps", "regions", "parties", "seats", "elections", "votes"):
            assert restored[name], name
        assert restored == fresh

    def test_orm_writes_continue_the_seeded_ids(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        """Reads find the seeded rows; new rows take the next free ids."""
        labour = db.get_party_by_name("Labour")
        party = db.add_party("New Party")
        pollster = db.add_pollster("New Pollster", "new_pollster")
        poll = db.add_poll(
            pollster.id, westminster_world.map_id, date(2026, 6, 1), date(2026, 6, 3)
        )
        db.add_poll_row(poll.id, party.id, 12.0)

        assert labour is not None
        assert labour.id == westminster_world.party_ids["Labour"]
        next_party_id = len(WESTMINSTER_PARTY_NAMES) + 1
        assert party.id == next_party_id
        assert (pollster.id, poll.id) == (1, 1)
        assert [row.party_id for row in db.get_rows_for_poll(poll.id)] == [
            next_party_id
        ]

    def test_the_copy_is_in_the_database_file(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
    ) -> None:
        """A new raw connection to ``db``'s file reads the whole world."""
        connection = sqlite3.connect(only_the_test_database)
        try:
            parties = connection.execute("SELECT count(*) FROM parties").fetchone()
            votes = connection.execute("SELECT count(*) FROM votes").fetchone()
        finally:
            connection.close()

        seeded_votes = sum(
            len(seat_votes) for _, seat_votes in WESTMINSTER_BASELINE_VOTES.values()
        )
        assert parties == (len(WESTMINSTER_PARTY_NAMES),)
        assert votes == (seeded_votes,)

    def test_the_restore_passes_the_raw_connect_guard(
        self,
        db: Database,
        only_the_test_database: Path,
        _westminster_template: tuple[Database, WestminsterWorld],
    ) -> None:
        """Both engines open fresh connections while ``sqlite3.connect`` is guarded.

        The template is a file other than ``db``'s, so the guard would refuse it
        if the restore connected through ``sqlite3.connect``.
        """
        template, _ = _westminster_template
        template.engine.dispose()
        db.engine.dispose()

        copy_database(template, db)

        assert _party_count(db) == len(WESTMINSTER_PARTY_NAMES)

    def test_the_template_refuses_writes(
        self, _westminster_template: tuple[Database, WestminsterWorld]
    ) -> None:
        """A stray write to the template fails instead of reaching later restores."""
        template, _ = _westminster_template
        seeded = _party_count(template)

        with pytest.raises(OperationalError, match="attempt to write a readonly"):
            template.add_party("Leaked Party")

        assert _party_count(template) == seeded

    def test_writes_stay_in_the_tests_own_copy(
        self,
        db: Database,
        westminster_world: WestminsterWorld,
        _westminster_template: tuple[Database, WestminsterWorld],
        empty_database: Database,
    ) -> None:
        """A write to ``db`` reaches neither the template nor the next restore."""
        template, _ = _westminster_template
        seeded = _party_count(template)
        db.add_party("Only In This Test")

        copy_database(template, empty_database)

        assert _party_count(db) == seeded + 1
        assert _party_count(template) == seeded
        assert _party_count(empty_database) == seeded
        assert empty_database.get_party_by_name("Only In This Test") is None

    def test_a_connection_open_across_the_copy_reads_it(
        self,
        _westminster_template: tuple[Database, WestminsterWorld],
        empty_database: Database,
    ) -> None:
        """A pooled connection that read the empty tables is not left stale."""
        template, _ = _westminster_template
        count_parties = select(func.count()).select_from(Party)

        with empty_database.engine.connect() as held:
            before = held.execute(count_parties).scalar_one()
            copy_database(template, empty_database)
            after = held.execute(count_parties).scalar_one()

        assert before == 0
        assert after == len(WESTMINSTER_PARTY_NAMES)

    def test_a_target_with_rows_is_refused(
        self,
        db: Database,
        _westminster_template: tuple[Database, WestminsterWorld],
    ) -> None:
        """Copying over rows would discard them, so it fails and changes nothing."""
        template, _ = _westminster_template
        db.add_party("Already here")

        with pytest.raises(
            RuntimeError, match=r"^copy would discard rows in \['parties'\]$"
        ):
            copy_database(template, db)

        with db.session() as session:
            names = session.scalars(select(Party.name)).all()
        assert names == ["Already here"]

    def test_rows_in_a_table_outside_the_orm_are_refused(
        self,
        db: Database,
        _westminster_template: tuple[Database, WestminsterWorld],
    ) -> None:
        """The guard reads the target's own tables, not just the ORM metadata."""
        template, _ = _westminster_template
        with db.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE scratch (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO scratch VALUES (1)")

        with pytest.raises(
            RuntimeError, match=r"^copy would discard rows in \['scratch'\]$"
        ):
            copy_database(template, db)

        assert _party_count(db) == 0


class TestSeedHolyroodWorld:
    """The seeded Holyrood world satisfies the model's naming conventions."""

    def test_list_seats_are_recognised_and_constituencies_are_not(
        self, db: Database
    ) -> None:
        """List seats satisfy _is_list_seat; constituency seats do not."""
        world = seed_holyrood_world(db)

        assert all(_is_list_seat(name) for name in world.list_seat_ids)
        assert not any(_is_list_seat(name) for name in world.constituency_seat_ids)
        assert len(world.list_seat_ids) == HOLYROOD_LIST_SEATS_PER_REGION * len(
            world.region_ids
        )

    def test_the_elections_are_linked_and_named_as_the_code_expects(
        self, db: Database
    ) -> None:
        """The baseline is the model default; the list election is its child."""
        world = seed_holyrood_world(db)

        assert world.map_name == holyrood_wikipedia_import.DEFAULT_MAP_NAME
        assert world.constituency_election_name == HOLYROOD_BASELINE_ELECTION_NAME
        list_election = db.get_election(world.list_election_id)
        assert list_election is not None
        assert list_election.type == ElectionType.holyrood_list
        assert list_election.parent_election_id == world.constituency_election_id

    def test_every_wikipedia_importer_party_exists(self, db: Database) -> None:
        """Every party the Holyrood Wikipedia importer maps a column to is seeded."""
        world = seed_holyrood_world(db)

        assert set(holyrood_wikipedia_import.PARTY_COLUMN_MAP.values()) == set(
            world.party_ids
        )

    def test_zero_swing_projection_matches_the_documented_dhondt(
        self, db: Database
    ) -> None:
        """Each region: SNP and Labour hold one seat each; list goes SNP, Con, SNP."""
        world = seed_holyrood_world(db)
        cfg = HolyroodSimulationConfig(
            constituency_election_name=world.constituency_election_name
        )

        _, _, summary = run_holyrood_projection(db, cfg)

        regions = len(world.region_ids)
        assert summary["Scottish National Party"] == {
            "constituency": regions,
            "list": 2 * regions,
            "total": 3 * regions,
        }
        assert summary["Labour"] == {
            "constituency": regions,
            "list": 0,
            "total": regions,
        }
        assert summary["Conservative"] == {
            "constituency": 0,
            "list": regions,
            "total": regions,
        }

    def test_it_reuses_parties_seeded_for_westminster(self, db: Database) -> None:
        """Both worlds fit in one database; shared party names keep one id."""
        westminster = seed_westminster_world(db)
        holyrood = seed_holyrood_world(db)

        assert holyrood.party_ids["Labour"] == westminster.party_ids["Labour"]
        assert holyrood.party_ids["Others"] == westminster.party_ids["Others"]


class TestAddPollWithRows:
    """add_poll_with_rows creates the pollster once and writes all the rows."""

    def test_rows_and_pollster(self, db: Database) -> None:
        """National rows have no region, regional rows carry theirs, pollster reused."""
        world = seed_westminster_world(db)
        labour, reform = world.party_ids["Labour"], world.party_ids["Reform UK"]
        london = world.region_ids["London"]

        first = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="yougov",
            pollster_name="YouGov",
            pollster_weight=None,
            fieldwork_end=date(2026, 6, 10),
            national={labour: 25.0, reform: 30.0},
            regional={london: {labour: 35.0}},
        )
        second = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="yougov",
            pollster_name="ignored",
            fieldwork_start=date(2026, 6, 12),
            fieldwork_end=date(2026, 6, 13),
            national={labour: 26.0},
        )

        rows = [
            (row.region_id, row.party_id, row.percentage)
            for row in db.get_rows_for_poll(first.id)
        ]
        assert len(rows) == 3
        assert set(rows) == {
            (None, labour, 25.0),
            (None, reform, 30.0),
            (london, labour, 35.0),
        }
        assert [row.percentage for row in db.get_rows_for_poll(second.id)] == [26.0]
        assert first.fieldwork_start == date(2026, 6, 8)
        assert (second.fieldwork_start, second.fieldwork_end) == (
            date(2026, 6, 12),
            date(2026, 6, 13),
        )
        assert first.pollster_id == second.pollster_id
        pollster = db.get_pollster(first.pollster_id)
        assert pollster is not None
        assert (pollster.name, pollster.weight) == ("YouGov", None)


class TestWorkbookHelpers:
    """Workbook bytes served through FakeUrlResponse load in the real importers."""

    @staticmethod
    def _payload() -> bytes:
        return workbook_bytes(
            build_workbook(
                {
                    "Cover": [["Fieldwork", "1-3 June 2026"], ["Sample", 2000]],
                    "Tables": [["", "Total", "London"], ["Labour", 0.25, None]],
                }
            )
        )

    def test_served_as_a_context_manager(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Survation reads ``with urlopen(...) as response``; order and cells kept."""
        payload = self._payload()
        monkeypatch.setattr(
            survation_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(payload),
        )

        workbook = survation_import.extract_workbook("https://example.test/x.xlsx")

        assert payload.startswith(b"PK")
        assert workbook.sheetnames == ["Cover", "Tables"]
        assert workbook["Cover"]["B2"].value == 2000
        row = [cell.value for cell in workbook["Tables"][2]]
        assert row == ["Labour", 0.25, None]

    def test_served_bare(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Find Out Now calls ``urlopen(...).read()`` without a ``with``."""
        payload = self._payload()
        monkeypatch.setattr(
            find_out_now_import,
            "urlopen",
            lambda *_a, **_k: FakeUrlResponse(payload),
        )

        workbook = find_out_now_import.extract_workbook("https://example.test/x.xlsx")

        assert workbook.sheetnames == ["Cover", "Tables"]


class TestFakeXlrd:
    """The fake xlrd book answers every call lord_ashcroft_import makes."""

    def test_cell_access_pads_short_rows_and_rejects_out_of_range(self) -> None:
        """Short rows read "" up to ncols; outside the sheet raises IndexError."""
        sheet = FakeXlrdSheet("Sheet1", [["a", 1.0, "c"], ["b"]])

        assert (sheet.nrows, sheet.ncols) == (2, 3)
        assert sheet.cell_value(1, 2) == ""
        with pytest.raises(IndexError):
            sheet.cell_value(2, 0)
        with pytest.raises(IndexError):
            sheet.cell_value(0, 3)

    def test_the_importer_helpers_read_it(self) -> None:
        """Fieldwork, sample size and a values sheet are found through the fake."""
        cover = FakeXlrdSheet(
            "Cover",
            [
                ["Lord Ashcroft Polls"],
                ["Fieldwork: 1-3 June 2026"],
                ["Sample size: 2,000"],
            ],
        )
        # The importer only takes a header row naming at least eight regions.
        headers = [
            "North East",
            "North West",
            "Yorkshire and the Humber",
            "East Midlands",
            "West Midlands",
            "East of England",
            "London",
            "South East",
        ]
        values = FakeXlrdSheet("Values", [["", *headers]])
        book = FakeXlrdBook([cover, values])

        assert book.nsheets == 2
        assert book.sheet_by_index(1).name == "Values"
        assert lord_ashcroft_import._find_sample_size(cover) == 2000
        assert lord_ashcroft_import._find_fieldwork(cover) == (
            date(2026, 6, 1),
            date(2026, 6, 3),
        )
        assert lord_ashcroft_import._find_region_columns(values) == {
            1: "North East England",
            2: "North West England",
            3: "Yorkshire and The Humber",
            4: "East Midlands",
            5: "West Midlands",
            6: "East of England",
            7: "London",
            8: "South East England",
        }
