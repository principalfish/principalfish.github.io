"""Tests for the console polls blueprint: list, detail (party x region matrix),
CSV export, delete.

``poll_list``, ``poll_detail``, ``delete_poll`` and ``poll_detail_csv`` never call
a subprocess runner or read a trend-cache constant (unlike the Westminster/
Holyrood model blueprints), so this file needs no ``forbid_call`` tripwire and
no per-module trend-JSON isolation fixture — only ``get_db`` is patched, via one
autouse fixture. ``poll_detail`` for a US poll (seat, matchup, per-candidate
rows) is already covered by ``test_console_us.py``'s
``TestPollDetailShowsUsScope``; this file covers the Westminster multi-region
"National" grouping and the not-found redirect, which that file doesn't reach.
"""

from __future__ import annotations

import csv
import html
import io
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask

from db import Database

from tests.console_fixtures import app, flashes as _flashes
from tests.uk_fixtures import WestminsterWorld, add_poll_with_rows


@pytest.fixture(autouse=True)
def _use_temp_db(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every test's ``get_db`` to the shared temp-DB fixture."""
    monkeypatch.setattr("console.blueprints.polls.get_db", lambda: db)


def _table_headers(body: str) -> list[str]:
    """Return the ``<th>`` texts inside the page's single ``<thead>`` block."""
    match = re.search(r"<thead>(.*?)</thead>", body, re.DOTALL)
    assert match is not None, "no <thead> in body"
    headers = re.findall(r"<th>(.*?)</th>", match.group(1))
    return [html.unescape(h.strip()) for h in headers]


def _table_rows(body: str) -> list[list[str]]:
    """Return each ``<tbody>`` row's raw ``<td>`` inner-HTML, in document order."""
    tbody_match = re.search(r"<tbody>(.*?)</tbody>", body, re.DOTALL)
    assert tbody_match is not None, "no <tbody> in body"
    return [
        re.findall(r"<td>(.*?)</td>", row_html, re.DOTALL)
        for row_html in re.findall(r"<tr>(.*?)</tr>", tbody_match.group(1), re.DOTALL)
    ]


def _seed_decoys(db: Database, world: WestminsterWorld) -> None:
    """Insert two unrelated polls under their own pollster first.

    In a fresh ``westminster_world`` database, the first poll and pollster a
    test creates both get id 1 — the same as ``world.map_id`` (also always
    1) and often a low party id too. That coincidence means a mutant that
    swaps which id column feeds a lookup, or corrupts a foreign key, can go
    undetected: every plausible wrong value equals every right value. Calling
    this before seeding the poll under test pushes its id and its pollster's
    id past 1, so a test can assert the three ids are genuinely distinct.
    """
    for day in (1, 2):
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="decoy_pollster",
            fieldwork_end=date(2020, 1, day),
            national={world.party_ids["Labour"]: 1.0},
        )


class TestPollList:
    """GET /polls lists every poll, most recent fieldwork first."""

    def test_no_polls_shows_empty_message(self, app: Flask, db: Database) -> None:
        response = app.test_client().get("/polls")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "No polls have been imported yet." in body
        assert "<table>" not in body

    def test_orders_by_fieldwork_end_desc_then_id_desc_as_tiebreak(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        # Insertion order is early, late, early — the opposite of the expected
        # display order — so a plain unordered scan (ascending rowid) would
        # produce [early_first, late, early_second], not the sorted answer.
        # early_second's *only* advantage over early_first is a higher id, so
        # dropping the id-desc secondary key exposes their tie. Dropping
        # fieldwork_end-desc entirely (leaving plain id-desc) would rank
        # purely by id, putting early_second first instead of late — a
        # different wrong answer, but still one this exact-list assertion
        # catches.
        early_first = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="ordering_pollster",
            fieldwork_end=date(2026, 1, 1),
            national={labour: 30.0},
        )
        late = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="ordering_pollster",
            fieldwork_end=date(2026, 3, 1),
            national={labour: 31.0},
        )
        early_second = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="ordering_pollster",
            fieldwork_end=date(2026, 1, 1),
            national={labour: 32.0},
        )

        response = app.test_client().get("/polls")

        body = response.get_data(as_text=True)
        ids_in_order = [int(m) for m in re.findall(r">#(\d+)</a>", body)]
        assert ids_in_order == [late.id, early_second.id, early_first.id]

    def test_orders_by_fieldwork_end_not_fieldwork_start(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        # add_poll_with_rows defaults fieldwork_start to end - 2 days, so
        # every poll elsewhere in this file has start and end agreeing on
        # order — a route that sorted by fieldwork_start instead of
        # fieldwork_end would look identical. Here, long_fieldwork's start is
        # much earlier than short_fieldwork's, but its end is later, so the
        # two orderings disagree.
        long_fieldwork = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="long_fieldwork_pollster",
            fieldwork_start=date(2026, 1, 1),
            fieldwork_end=date(2026, 3, 5),
            national={labour: 20.0},
        )
        short_fieldwork = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="short_fieldwork_pollster",
            fieldwork_start=date(2026, 3, 1),
            fieldwork_end=date(2026, 3, 2),
            national={labour: 21.0},
        )

        response = app.test_client().get("/polls")

        body = response.get_data(as_text=True)
        ids_in_order = [int(m) for m in re.findall(r">#(\d+)</a>", body)]
        assert ids_in_order == [long_fieldwork.id, short_fieldwork.id]

    def test_row_count_reflects_actual_rows_including_zero(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        conservative = world.party_ids["Conservative"]
        scotland = world.region_ids["Scotland"]
        with_rows = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="counted_pollster",
            fieldwork_end=date(2026, 2, 1),
            national={labour: 40.0, conservative: 30.0},
            regional={scotland: {labour: 45.0}},
        )
        no_rows = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="empty_pollster",
            fieldwork_end=date(2026, 2, 2),
            national={},
        )

        response = app.test_client().get("/polls")

        body = response.get_data(as_text=True)
        rows = _table_rows(body)
        assert len(rows) == 2
        # no_rows sorts first: later fieldwork_end (2026-02-02).
        assert [html.unescape(row[0]) for row in rows] == [
            f'<a href="/polls/{no_rows.id}">#{no_rows.id}</a>',
            f'<a href="/polls/{with_rows.id}">#{with_rows.id}</a>',
        ]
        assert rows[0][5] == "0"
        assert rows[1][5] == "3"

    def test_source_url_rendered_as_link_and_dash_when_missing(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        with_url = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="linked_pollster",
            fieldwork_end=date(2026, 2, 5),
            national={labour: 33.0},
            source_url="https://example.com/witness-source",
        )
        without_url = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="unlinked_pollster",
            fieldwork_end=date(2026, 2, 4),
            national={labour: 34.0},
        )

        response = app.test_client().get("/polls")

        body = response.get_data(as_text=True)
        rows = _table_rows(body)
        assert f'<a href="/polls/{with_url.id}">#{with_url.id}</a>' in rows[0][0]
        assert (
            '<a href="https://example.com/witness-source" target="_blank" '
            'rel="noopener">Source</a>' in rows[0][4]
        )
        assert f'<a href="/polls/{without_url.id}">#{without_url.id}</a>' in rows[1][0]
        assert rows[1][4].strip() == "-"

    def test_headers_and_full_row_values(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        # fieldwork_start is 5 days before fieldwork_end here, not the
        # add_poll_with_rows default of 2 days, and sample_size is 777, not
        # its default of 1000 — both distinct from any fallback value, so a
        # mutant that dropped or mis-mapped either column can't pass by
        # coincidence.
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="named_pollster",
            pollster_name="Named Pollster Ltd",
            fieldwork_start=date(2026, 2, 1),
            fieldwork_end=date(2026, 2, 6),
            national={world.party_ids["Labour"]: 35.0},
            sample_size=777,
        )

        response = app.test_client().get("/polls")

        body = response.get_data(as_text=True)
        assert _table_headers(body) == [
            "Poll",
            "Pollster",
            "Fieldwork",
            "Sample",
            "Source URL",
            "Rows",
            "Actions",
        ]
        rows = _table_rows(body)
        assert len(rows) == 1
        [row] = rows
        assert [html.unescape(cell).strip() for cell in row[:6]] == [
            f'<a href="/polls/{poll.id}">#{poll.id}</a>',
            "Named Pollster Ltd (named_pollster)",
            "2026-02-01 to 2026-02-06",
            "777",
            "-",
            "1",
        ]
        assert f'action="/polls/{poll.id}/delete"' in row[6]


class TestPollDetail:
    """GET /polls/<id> shows the party x region percentage matrix."""

    def test_westminster_poll_groups_national_and_regional_columns(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        _seed_decoys(db, world)
        conservative = world.party_ids["Conservative"]
        labour = world.party_ids["Labour"]
        green = world.party_ids["Green"]
        snp = world.party_ids["Scottish National Party"]
        libdem = world.party_ids["Liberal Democrats"]
        london = world.region_ids["London"]
        scotland = world.region_ids["Scotland"]
        north_east = world.region_ids["North East England"]
        wales = world.region_ids["Wales"]
        pollster_name = "Matrix Witness Pollster"
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="matrix_pollster",
            pollster_name=pollster_name,
            fieldwork_end=date(2026, 4, 1),
            # Insertion order (Green, Conservative, SNP, Labour, Liberal
            # Democrats) is neither alphabetical nor id order, and the row
            # count (5) is large enough that removing the matrix's sorted()
            # calls fails regardless of set hash-iteration order — with only
            # 2 parties, an unsorted set can coincidentally land in the
            # right order for most PYTHONHASHSEED values.
            national={
                green: 5.0,
                conservative: 30.0,
                snp: 15.0,
                labour: 45.0,
                libdem: 8.0,
            },
            regional={
                london: {conservative: 25.0, labour: 50.0},
                # Conservative has no row in Scotland: proves a missing cell
                # renders blank rather than 0.0 or raising.
                scotland: {labour: 40.0},
                north_east: {conservative: 35.0, labour: 38.0},
                wales: {conservative: 20.0, labour: 42.0},
            },
        )
        pollster = db.get_pollster_by_identifier("matrix_pollster")
        assert pollster is not None
        assert len({poll.id, pollster.id, world.map_id}) == 3

        response = app.test_client().get(f"/polls/{poll.id}")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert f"Poll #{poll.id}" in body
        assert (
            f"<strong>Pollster:</strong> {pollster_name} (matrix_pollster)</p>"
            in body
        )
        # "National" sorts alphabetically between London and North East
        # England, not first or last, so this also proves the headers are
        # genuinely sorted rather than National being pinned to an end.
        assert _table_headers(body) == [
            "Party",
            "London",
            "National",
            "North East England",
            "Scotland",
            "Wales",
        ]
        rows = _table_rows(body)
        assert len(rows) == 5
        assert [html.unescape(cell) for cell in rows[0]] == [
            "Conservative",
            "25.0",
            "30.0",
            "35.0",
            "",
            "20.0",
        ]
        assert [html.unescape(cell) for cell in rows[1]] == [
            "Green",
            "",
            "5.0",
            "",
            "",
            "",
        ]
        assert [html.unescape(cell) for cell in rows[2]] == [
            "Labour",
            "50.0",
            "45.0",
            "38.0",
            "40.0",
            "42.0",
        ]
        assert [html.unescape(cell) for cell in rows[3]] == [
            "Liberal Democrats",
            "",
            "8.0",
            "",
            "",
            "",
        ]
        assert [html.unescape(cell) for cell in rows[4]] == [
            "Scottish National Party",
            "",
            "15.0",
            "",
            "",
            "",
        ]

    def test_not_found_redirects_with_flash(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.get("/polls/99999")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/polls")
        assert _flashes(client, response) == ["Poll #99999 not found."]


class TestDeletePoll:
    """POST /polls/<id>/delete removes a poll and its rows."""

    def test_found_deletes_and_flashes_row_count(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        scotland = world.region_ids["Scotland"]
        # A bystander poll under a different pollster, so a mutant that
        # drops delete_poll's `.where(PollRow.poll_id == poll.id)` scope
        # (deleting every poll's rows, not just the target's) still leaves
        # the flash-message row count matching — only the bystander's
        # survival proves the delete was scoped.
        bystander = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="bystander_pollster",
            fieldwork_end=date(2026, 5, 2),
            national={world.party_ids["Conservative"]: 22.0},
            regional={scotland: {world.party_ids["Conservative"]: 19.0}},
        )
        bystander_row_count = len(db.get_rows_for_poll(bystander.id))
        assert bystander_row_count == 2
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="deleted_pollster",
            fieldwork_end=date(2026, 5, 1),
            national={
                world.party_ids["Conservative"]: 20.0,
                world.party_ids["Labour"]: 44.0,
            },
            regional={scotland: {world.party_ids["Labour"]: 41.0}},
        )
        assert len(db.get_rows_for_poll(poll.id)) == 3
        client = app.test_client()

        response = client.post(f"/polls/{poll.id}/delete")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/polls")
        assert _flashes(client, response) == [
            f"Deleted poll #{poll.id} and 3 poll rows."
        ]
        assert db.get_poll(poll.id) is None
        assert db.get_rows_for_poll(poll.id) == []
        assert db.get_poll(bystander.id) is not None
        assert len(db.get_rows_for_poll(bystander.id)) == bystander_row_count

    def test_not_found_flashes(self, app: Flask, db: Database) -> None:
        client = app.test_client()

        response = client.post("/polls/99999/delete")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/polls")
        assert _flashes(client, response) == ["Poll #99999 not found."]


class TestPollDetailCsv:
    """GET /polls/<id>/csv downloads the poll's rows as a CSV attachment."""

    def test_found_returns_csv_with_national_and_regional_rows(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        _seed_decoys(db, world)
        # Liberal Democrats (id 3) sorts *after* Green (id 6) by name but
        # *before* it by id, and the regional rows below are inserted Wales
        # then London (reverse of their alphabetical order) — so a query
        # that ordered by id, or by insertion order, instead of Party.name/
        # Region.name would produce a different row sequence than the one
        # asserted below.
        green = world.party_ids["Green"]
        libdem = world.party_ids["Liberal Democrats"]
        london = world.region_ids["London"]
        wales = world.region_ids["Wales"]
        pollster_name = "CSV Witness Pollster"
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="csv_pollster",
            pollster_name=pollster_name,
            fieldwork_start=date(2026, 3, 7),
            fieldwork_end=date(2026, 3, 10),
            national={green: 12.5, libdem: 47.5},
            regional={wales: {green: 55.5}, london: {green: 33.0}},
            sample_size=1500,
            source_url="https://example.com/csv-poll",
        )
        pollster = db.get_pollster_by_identifier("csv_pollster")
        assert pollster is not None
        assert len({poll.id, pollster.id, world.map_id}) == 3

        response = app.test_client().get(f"/polls/{poll.id}/csv")

        assert response.status_code == 200
        assert response.headers["Content-Type"] == "text/csv; charset=utf-8"
        assert response.headers["Content-Disposition"] == (
            f"attachment; filename=poll_{poll.id}_rows.csv"
        )

        body = response.get_data(as_text=True)
        reader = csv.DictReader(io.StringIO(body))
        assert reader.fieldnames == [
            "poll_id",
            "pollster_id",
            "pollster_identifier",
            "pollster_name",
            "map_id",
            "fieldwork_start",
            "fieldwork_end",
            "sample_size",
            "source_url",
            "region_id",
            "region_name",
            "party_id",
            "party_name",
            "percentage",
        ]
        rows = list(reader)
        assert len(rows) == 4

        common = {
            "poll_id": str(poll.id),
            "pollster_id": str(pollster.id),
            "pollster_identifier": "csv_pollster",
            "pollster_name": pollster_name,
            "map_id": str(world.map_id),
            "fieldwork_start": "2026-03-07",
            "fieldwork_end": "2026-03-10",
            "sample_size": "1500",
            "source_url": "https://example.com/csv-poll",
        }
        assert rows[0] == {
            **common,
            "region_id": "",
            "region_name": "National",
            "party_id": str(green),
            "party_name": "Green",
            "percentage": "12.5",
        }
        assert rows[1] == {
            **common,
            "region_id": str(london),
            "region_name": "London",
            "party_id": str(green),
            "party_name": "Green",
            "percentage": "33.0",
        }
        assert rows[2] == {
            **common,
            "region_id": str(wales),
            "region_name": "Wales",
            "party_id": str(green),
            "party_name": "Green",
            "percentage": "55.5",
        }
        assert rows[3] == {
            **common,
            "region_id": "",
            "region_name": "National",
            "party_id": str(libdem),
            "party_name": "Liberal Democrats",
            "percentage": "47.5",
        }

    def test_missing_pollster_renders_blank_name_and_identifier(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        only_the_test_database: Path,
    ) -> None:
        world = westminster_world
        _seed_decoys(db, world)
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="vanishing_pollster",
            fieldwork_end=date(2026, 3, 15),
            national={world.party_ids["Labour"]: 36.0},
        )
        pollster = db.get_pollster_by_identifier("vanishing_pollster")
        assert pollster is not None
        assert len({poll.id, pollster.id, world.map_id}) == 3

        # Delete the pollster row directly, bypassing the ORM's
        # cascade="all, delete-orphan" (which would also delete the poll) to
        # reach the orphaned-poll case poll_detail_csv guards against.
        # only_the_test_database's own guard can't fire here, since we
        # connect to exactly the path it returns — what actually keeps this
        # off the live database is that this path IS the db fixture's own
        # file; requesting the fixture guards against a future edit that
        # points this connection elsewhere. A raw sqlite3 connection has
        # foreign-key enforcement off by default (only db.py's SQLAlchemy
        # engine turns it on), so the delete needs no schema-enforcement
        # bypass of its own.
        conn = sqlite3.connect(str(only_the_test_database))
        try:
            conn.execute("DELETE FROM pollsters WHERE id = ?", (pollster.id,))
            conn.commit()
        finally:
            conn.close()

        response = app.test_client().get(f"/polls/{poll.id}/csv")

        assert response.status_code == 200
        [row] = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        assert row["pollster_id"] == str(pollster.id)
        assert row["pollster_identifier"] == ""
        assert row["pollster_name"] == ""
        assert row["party_name"] == "Labour"

    def test_not_found_returns_404(self, app: Flask, db: Database) -> None:
        response = app.test_client().get("/polls/99999/csv")

        assert response.status_code == 404
        assert response.get_data(as_text=True) == "Poll not found"
