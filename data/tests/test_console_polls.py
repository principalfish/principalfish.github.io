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
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from flask import Flask

from db import Database
from model_support.uncertainty import UncertaintyConfigError
from models import PollBase

from tests.console_fixtures import app, flashes as _flashes
from tests.uk_fixtures import WestminsterWorld, add_poll_with_rows


@pytest.fixture(autouse=True)
def _use_temp_db(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every test's ``get_db`` to the shared temp-DB fixture."""
    monkeypatch.setattr("console.blueprints.polls.get_db", lambda: db)


@pytest.fixture(autouse=True)
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the uncertainty settings at a temp file (absent: the defaults).

    The committed ``uncertainty_config.json`` holds calibrated values that may
    change; the expected margins here are computed from the defaults.
    """
    path = tmp_path / "uncertainty_config.json"
    monkeypatch.setattr("model_support.uncertainty.UNCERTAINTY_CONFIG_PATH", path)
    return path


def _table_headers(body: str) -> list[str]:
    """Return the ``<th>`` texts inside the page's single ``<thead>`` block."""
    match = re.search(r"<thead>(.*?)</thead>", body, re.DOTALL)
    assert match is not None, "no <thead> in body"
    headers = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", match.group(1))
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
            "n",
            "n source",
            "Weighted n",
            "n_eff",
            "MoE at 30%",
            "Actions",
        ]
        rows = _table_rows(body)
        assert len(rows) == 1
        [row] = rows
        # n_eff 777 x 0.80 / 1.5 = 414.4; MoE at 30%:
        # 1.645 x sqrt(0.3 x 0.7 / 414.4) = 3.7 points.
        assert [html.unescape(cell).strip() for cell in row[:11]] == [
            f'<a href="/polls/{poll.id}">#{poll.id}</a>',
            "Named Pollster Ltd (named_pollster)",
            "2026-02-01 to 2026-02-06",
            "777",
            "-",
            "1",
            "777",
            "sample_size",
            "-",
            "414",
            "±3.7",
        ]
        assert f'action="/polls/{poll.id}/delete"' in row[11]


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
        # The margins beside the shares are covered by TestPollDetailError.
        rows = [
            [re.sub(r" <small>.*?</small>", "", cell) for cell in row]
            for row in _table_rows(body)
        ]
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


# ── Respondent n ──────────────────────────────────────────────────────────────


def _add_base(
    db: Database,
    poll_id: int,
    unweighted_n: int | None,
    *,
    region_id: int | None = None,
    weighted_n: int | None = None,
    kind: str = "decided",
    split_from_macro: bool = False,
) -> None:
    with db.session() as session:
        session.add(
            PollBase(
                poll_id=poll_id,
                region_id=region_id,
                unweighted_n=unweighted_n,
                weighted_n=weighted_n,
                kind=kind,
                split_from_macro=split_from_macro,
            )
        )


def _tfoot_rows(body: str) -> dict[str, list[str]]:
    """Return the matrix footer rows as ``{label: [cell, ...]}``."""
    match = re.search(r"<tfoot>(.*?)</tfoot>", body, re.DOTALL)
    assert match is not None, "no <tfoot> in body"
    rows: dict[str, list[str]] = {}
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.DOTALL):
        label = re.search(r'<th scope="row">(.*?)</th>', row_html, re.DOTALL)
        assert label is not None
        cells = re.findall(r"<td>(.*?)</td>", row_html, re.DOTALL)
        rows[html.unescape(label.group(1).strip())] = [
            html.unescape(cell.strip()) for cell in cells
        ]
    return rows


class TestPollListN:
    """GET /polls shows each poll's resolved national n and its source."""

    def test_captured_base_beats_sample_size_per_poll(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        based = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="more_in_common",
            fieldwork_end=date(2026, 9, 21), national={labour: 26.0}, sample_size=2113,
        )
        plain = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="find_out_now",
            fieldwork_end=date(2026, 9, 20), national={labour: 27.0}, sample_size=2607,
        )
        unsized = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="techne",
            fieldwork_end=date(2026, 9, 19), national={labour: 25.0}, sample_size=None,
        )
        _add_base(db, based.id, 1491, weighted_n=1402)
        # A regional base must not stand in for the national n.
        _add_base(db, plain.id, 239, region_id=world.region_ids["London"])
        weighted_only = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="find_out_now",
            fieldwork_end=date(2026, 9, 30), national={labour: 27.0}, sample_size=2607,
        )
        _add_base(db, weighted_only.id, None, weighted_n=1452)

        body = app.test_client().get("/polls").get_data(as_text=True)

        rows = {
            re.sub(r"<[^>]+>", "", row[0]).strip(): [
                html.unescape(c).strip() for c in (row[3], *row[6:11])
            ]
            for row in _table_rows(body)
        }
        # n_eff = n x adjustment / 1.5; MoE = 1.645 x sqrt(0.21 / n_eff).
        # Decided 1491 -> 994 -> 2.4; sample size 2607 x 0.80 -> 1390.4 -> 2.0;
        # weighted-only decided 1452 -> 968 -> 2.4.
        assert rows[f"#{based.id}"] == [
            "2113",
            "1491",
            "base:decided",
            "1402",
            "994",
            "±2.4",
        ]
        assert rows[f"#{plain.id}"] == [
            "2607",
            "2607",
            "sample_size",
            "-",
            "1390",
            "±2.0",
        ]
        assert rows[f"#{unsized.id}"] == ["None", "-", "-", "-", "-", "-"]
        assert rows[f"#{weighted_only.id}"] == [
            "2607",
            "1452",
            "base:decided(weighted)",
            "1452",
            "968",
            "±2.4",
        ]
        assert 'href="/polls/coverage"' in body

    def test_error_columns_only_for_westminster_polls(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        holyrood_map = db.add_map("Holyrood Witness Map", parliament="holyrood")
        labour = westminster_world.party_ids["Labour"]
        poll = add_poll_with_rows(
            db,
            map_id=holyrood_map.id,
            pollster_identifier="survation",
            fieldwork_end=date(2026, 9, 21),
            national={labour: 20.0},
            sample_size=1020,
        )

        body = app.test_client().get("/polls").get_data(as_text=True)

        [row] = _table_rows(body)
        assert re.sub(r"<[^>]+>", "", row[0]).strip() == f"#{poll.id}"
        # n still resolves; n_eff and the margin are Westminster's settings.
        assert [html.unescape(c).strip() for c in row[6:11]] == [
            "1020",
            "sample_size",
            "-",
            "-",
            "-",
        ]

    def test_capped_n_eff_is_marked(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="yougov",
            fieldwork_end=date(2026, 9, 21),
            national={world.party_ids["Labour"]: 20.0},
            sample_size=17_230,
        )

        body = app.test_client().get("/polls").get_data(as_text=True)

        [row] = _table_rows(body)
        # 17,230 x 0.75 / 1.5 = 8615, capped at 3000; 1.645 x sqrt(0.21/3000).
        assert [html.unescape(c).strip() for c in row[9:11]] == [
            "3000 (capped)",
            "±1.4",
        ]


class TestPollDetailN:
    """GET /polls/<id> footer rows give each column's n, source and weighted n."""

    def test_footer_resolves_each_column(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        london, wales, scotland, ni = (
            world.region_ids[name]
            for name in ("London", "Wales", "Scotland", "Northern Ireland")
        )
        poll = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_end=date(2026, 9, 28), national={labour: 23.0},
            regional={
                london: {labour: 32.0},
                scotland: {labour: 17.0},
                wales: {labour: 18.0},
                ni: {labour: 0.0},
            },
            sample_size=2310,
        )
        _add_base(db, poll.id, 2286, weighted_n=2286, kind="all_adults")
        _add_base(db, poll.id, 241, region_id=london, weighted_n=280, kind="all_adults")
        _add_base(
            db,
            poll.id,
            187,
            region_id=scotland,
            kind="all_adults",
            split_from_macro=True,
        )

        body = app.test_client().get(f"/polls/{poll.id}").get_data(as_text=True)

        assert _table_headers(body) == [
            "Party", "London", "National", "Northern Ireland", "Scotland", "Wales",
        ]
        # Wales: 2286 x 3.1m / (9.1m + 5.5m + 3.1m) = 400.4; Northern Ireland
        # has only a zero placeholder row, so it is neither estimated nor in
        # the denominator.
        assert _tfoot_rows(body) == {
            "n": ["241", "2286", "-", "187", "400"],
            "n source": [
                "base:all_adults", "base:all_adults", "-", "base:all_adults (split)",
                "estimate:all_adults",
            ],
            "Weighted n": ["280", "2286", "-", "-", "-"],
            # All-adult bases and the estimate scaled from one: x0.80, then
            # / 1.5.
            "Adjustment": ["×0.8", "×0.8", "-", "×0.8", "×0.8"],
            "n_eff": ["129", "1219", "-", "100", "213"],
        }


class TestPollCoverage:
    """GET /polls/coverage summarises captured bases per pollster."""

    def test_counts_per_pollster(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        london, wales = world.region_ids["London"], world.region_ids["Wales"]
        based = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            pollster_name="YouGov",
            fieldwork_end=date(2026, 9, 28), national={labour: 23.0},
            regional={london: {labour: 32.0}, wales: {labour: 18.0}},
        )
        add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_end=date(2026, 10, 5), national={labour: 22.0},
            regional={london: {labour: 31.0}, wales: {labour: 19.0}},
        )
        _add_base(db, based.id, 2310)
        _add_base(db, based.id, 241, region_id=london)

        body = app.test_client().get("/polls/coverage").get_data(as_text=True)

        assert _table_headers(body) == [
            "Pollster", "Polls", "With national base", "National %", "Crossbreaks",
            "With base", "Crossbreak %",
        ]
        cells = [[html.unescape(c).strip() for c in row] for row in _table_rows(body)]
        assert cells == [["YouGov (yougov)", "2", "1", "50%", "4", "1", "25%"]]

    def test_no_westminster_map_shows_empty_message(
        self, app: Flask, db: Database
    ) -> None:
        body = app.test_client().get("/polls/coverage").get_data(as_text=True)

        assert "No polls on this map." in body


class TestDeletePollWithBases:
    def test_deleting_a_poll_deletes_only_its_bases(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        doomed = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_end=date(2026, 9, 28), national={labour: 23.0},
        )
        kept = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_end=date(2026, 10, 5), national={labour: 22.0},
        )
        _add_base(db, doomed.id, 2310)
        _add_base(db, kept.id, 2312)

        response = app.test_client().post(f"/polls/{doomed.id}/delete")

        assert response.status_code == 302
        with sqlite3.connect(db.config.database_path) as conn:
            rows = conn.execute(
                "SELECT poll_id, unweighted_n FROM poll_bases"
            ).fetchall()
        assert rows == [(kept.id, 2312)]


class TestPollDetailError:
    """GET /polls/<id> shows each column's adjustment, n_eff and share margins."""

    def test_each_kind_gets_its_adjustment_and_margin(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        reform = world.party_ids["Reform UK"]
        london, scotland, wales, north_east, ni = (
            world.region_ids[name]
            for name in (
                "London",
                "Scotland",
                "Wales",
                "North East England",
                "Northern Ireland",
            )
        )
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="mixed_bases",
            fieldwork_end=date(2026, 9, 28),
            national={reform: 27.0},
            regional={
                london: {reform: 11.0},
                north_east: {reform: 30.0},
                ni: {reform: 0.0},
                scotland: {reform: 18.0},
                wales: {reform: 30.0},
            },
            sample_size=7000,
        )
        _add_base(db, poll.id, 6000)
        _add_base(db, poll.id, 218, region_id=london, kind="all_adults")
        _add_base(db, poll.id, 204, region_id=scotland)
        _add_base(db, poll.id, 300, region_id=wales, kind="likely_voters")

        body = app.test_client().get(f"/polls/{poll.id}").get_data(as_text=True)

        assert _table_headers(body) == [
            "Party",
            "London",
            "National",
            "North East England",
            "Northern Ireland",
            "Scotland",
            "Wales",
        ]
        footer = _tfoot_rows(body)
        # North East: estimate 6000 x 2.7m / 20.4m = 794 from the decided
        # national base; Northern Ireland has only a zero placeholder row.
        assert footer["n"] == ["218", "6000", "794", "-", "204", "300"]
        assert footer["n source"] == [
            "base:all_adults",
            "base:decided",
            "estimate:decided",
            "-",
            "base:decided",
            "base:likely_voters",
        ]
        assert footer["Adjustment"] == ["×0.8", "×1", "×1", "-", "×1", "×0.9"]
        # 218 x 0.80 / 1.5 = 116.3; 6000 / 1.5 = 4000 -> capped; 794 / 1.5;
        # 204 / 1.5 = 136; 300 x 0.9 / 1.5 = 180.
        assert footer["n_eff"] == [
            "116",
            "3000 (capped)",
            "529",
            "-",
            "136",
            "180",
        ]
        [reform_row] = _table_rows(body)
        # 1.645 x sqrt(p(1-p) / n_eff): London 11% at 116.3 -> 4.8; national
        # 27% at 3000 -> 1.3; North East 30% at 529.3 -> 3.3; Scotland 18% at
        # 136 -> 5.4; Wales 30% at 180 -> 5.6. Northern Ireland: no n, no
        # margin.
        assert [html.unescape(cell) for cell in reform_row] == [
            "Reform UK",
            "11.0 <small>±4.8</small>",
            "27.0 <small>±1.3</small>",
            "30.0 <small>±3.3</small>",
            "0.0",
            "18.0 <small>±5.4</small>",
            "30.0 <small>±5.6</small>",
        ]

    def test_shares_without_a_margin(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        snp = world.party_ids["Scottish National Party"]
        green = world.party_ids["Green"]
        london = world.region_ids["London"]
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="decided_pollster",
            fieldwork_end=date(2026, 9, 28),
            # Green has no London row (a blank cell); the SNP is on 0 in
            # London; a corrupt national share above 100 must not crash.
            national={labour: 100.5, snp: 3.0, green: 100.0},
            regional={london: {labour: 40.0, snp: 0.0}},
        )
        _add_base(db, poll.id, 1500)
        _add_base(db, poll.id, 300, region_id=london)

        response = app.test_client().get(f"/polls/{poll.id}")

        assert response.status_code == 200
        rows = [
            [html.unescape(cell) for cell in row]
            for row in _table_rows(response.get_data(as_text=True))
        ]
        # 1.645 x sqrt(0.4 x 0.6 / 200) = 5.7; sqrt(0.03 x 0.97 / 1000) -> 0.9.
        assert rows == [
            ["Green", "", "100.0"],
            ["Labour", "40.0 <small>±5.7</small>", "100.5"],
            ["Scottish National Party", "0.0", "3.0 <small>±0.9</small>"],
        ]

    def test_sample_size_fallback_gets_the_decided_fraction(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="techne",
            fieldwork_end=date(2026, 9, 28),
            national={labour: 20.0},
            regional={world.region_ids["Wales"]: {labour: 25.0}},
            sample_size=1640,
        )

        body = app.test_client().get(f"/polls/{poll.id}").get_data(as_text=True)

        footer = _tfoot_rows(body)
        # National 1640 x 0.80 / 1.5 = 874.7; Wales is the only reported
        # region, so its estimate is the whole sample, adjusted the same way.
        assert footer["n source"] == ["sample_size", "estimate:sample_size"]
        assert footer["Adjustment"] == ["×0.8", "×0.8"]
        assert footer["n_eff"] == ["875", "875"]
        [row] = _table_rows(body)
        # 1.645 x sqrt(0.2 x 0.8 / 874.7) = 2.2; sqrt(0.25 x 0.75 / 874.7) -> 2.4.
        assert [html.unescape(cell) for cell in row] == [
            "Labour",
            "20.0 <small>±2.2</small>",
            "25.0 <small>±2.4</small>",
        ]

    def test_unresolved_n_shows_dashes(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="techne",
            fieldwork_end=date(2026, 9, 28),
            national={world.party_ids["Labour"]: 20.0},
            sample_size=None,
        )

        body = app.test_client().get(f"/polls/{poll.id}").get_data(as_text=True)

        footer = _tfoot_rows(body)
        assert footer["n"] == ["-"]
        assert footer["Adjustment"] == ["-"]
        assert footer["n_eff"] == ["-"]
        assert [html.unescape(c) for c in _table_rows(body)[0]] == ["Labour", "20.0"]

    def test_other_maps_show_no_error_rows_and_skip_the_settings(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        config_path: Path,
    ) -> None:
        # A malformed settings file must not break other parliaments' pages.
        config_path.write_text("{not json", encoding="utf-8")
        holyrood_map = db.add_map("Holyrood Witness Map", parliament="holyrood")
        poll = add_poll_with_rows(
            db,
            map_id=holyrood_map.id,
            pollster_identifier="survation",
            fieldwork_end=date(2026, 9, 21),
            national={westminster_world.party_ids["Labour"]: 20.0},
            sample_size=1020,
        )

        response = app.test_client().get(f"/polls/{poll.id}")

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert list(_tfoot_rows(body)) == ["n", "n source", "Weighted n"]
        assert [html.unescape(c) for c in _table_rows(body)[0]] == ["Labour", "20.0"]
        assert "<small>" not in body

    def test_westminster_page_reports_a_malformed_settings_file(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        config_path: Path,
    ) -> None:
        config_path.write_text('{"design_effect": 0}', encoding="utf-8")
        world = westminster_world
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="yougov",
            fieldwork_end=date(2026, 9, 28),
            national={world.party_ids["Labour"]: 20.0},
        )

        with pytest.raises(UncertaintyConfigError, match="design_effect"):
            app.test_client().get(f"/polls/{poll.id}")

    def test_reads_the_settings_file(
        self,
        app: Flask,
        db: Database,
        westminster_world: WestminsterWorld,
        config_path: Path,
    ) -> None:
        config_path.write_text(
            json.dumps(
                {
                    "design_effect": 2.0,
                    "decided_fraction": 0.6,
                    "interval": 0.95,
                    "p_ref": 0.4,
                }
            ),
            encoding="utf-8",
        )
        world = westminster_world
        poll = add_poll_with_rows(
            db,
            map_id=world.map_id,
            pollster_identifier="yougov",
            fieldwork_end=date(2026, 9, 28),
            national={world.party_ids["Labour"]: 20.0},
            sample_size=2000,
        )

        detail = app.test_client().get(f"/polls/{poll.id}").get_data(as_text=True)
        listing = app.test_client().get("/polls").get_data(as_text=True)

        footer = _tfoot_rows(detail)
        # 2000 x 0.6 / 2.0 = 600; 95%: 1.960 x sqrt(0.2 x 0.8 / 600) = 3.2.
        assert footer["Adjustment"] == ["×0.6"]
        assert footer["n_eff"] == ["600"]
        assert [html.unescape(c) for c in _table_rows(detail)[0]] == [
            "Labour",
            "20.0 <small>±3.2</small>",
        ]
        # The list's margin at p_ref 40%: 1.960 x sqrt(0.24 / 600) = 3.9
        # (3.7 at 30%).
        assert "MoE at 40%" in _table_headers(listing)
        [row] = _table_rows(listing)
        assert [html.unescape(c).strip() for c in row[9:11]] == ["600", "±3.9"]
        assert "95% margin of error" in listing
