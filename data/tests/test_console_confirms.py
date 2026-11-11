"""Confirm prompts on the console's destructive forms, and the copy around them.

Each prompt is an inline ``onsubmit`` handler written with ``tojson``, so the
tests read the handler back off the rendered page: the message must name the item
the form acts on and say it cannot be undone from the console. The prompts that
already existed (Rebuild Database, Restore, the US model rebuild, the export
write) are pinned word for word so a later edit cannot change them unnoticed.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from flask import Flask

from console.us.matchups import race_map_name
from console.us.model_form import rebuild_confirm_message
from console.us.models import US_CHAMBERS_BY_SLUG
from core.db import Database
from core.schema import ElectionType
from tests.console_fixtures import app, confirm_text, onsubmit_handlers  # noqa: F401
from tests.test_console_us import (
    _add_matchup_polls,
    _seed_president_matchups,
    _seed_senate_races,
    _seed_us_house,
)
from tests.uk_fixtures import WestminsterWorld, add_poll_with_rows

_UNDONE = "can't be undone from the console"


def _form_handler(body: str, action: str) -> str:
    """The ``onsubmit`` handler of the one ``<form>`` posting to ``action``."""
    match = re.search(rf'<form [^>]*action="{re.escape(action)}"[^>]*>', body)
    assert match is not None, f"no form posting to {action}"
    handlers = onsubmit_handlers(match.group(0))
    assert len(handlers) == 1, f"{action} has no confirm"
    return handlers[0]


class TestPollDelete:
    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.polls.views.get_db", lambda: db)

    def test_the_confirm_names_the_pollster_and_the_fieldwork_dates(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        # An earlier poll keeps the target's id away from 1 and from the row count.
        add_poll_with_rows(
            db,
            map_id=westminster_world.map_id,
            pollster_identifier="earlier_pollster",
            fieldwork_end=date(2026, 1, 1),
            national={westminster_world.party_ids["Labour"]: 30.0},
        )
        poll = add_poll_with_rows(
            db,
            map_id=westminster_world.map_id,
            pollster_identifier="named_pollster",
            pollster_name='Named "Pollster" Ltd',
            fieldwork_start=date(2026, 2, 1),
            fieldwork_end=date(2026, 2, 6),
            national={
                westminster_world.party_ids["Labour"]: 30.0,
                westminster_world.party_ids["Conservative"]: 25.0,
            },
        )
        assert poll.id > 1

        for path in ("/polls", "/westminster/polls"):
            body = app.test_client().get(path).get_data(as_text=True)
            message = confirm_text(_form_handler(body, f"/polls/{poll.id}/delete"))

            assert f"Delete poll #{poll.id} by " in message
            assert 'Named "Pollster" Ltd' in message
            assert "(fieldwork 2026-02-01 to 2026-02-06)" in message
            assert " and its 2 poll rows?" in message
            assert _UNDONE in message


class TestModelOutputDeletes:
    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        for module in (
            "console.westminster.outputs",
            "console.us.outputs",
            "console.us.views",
        ):
            monkeypatch.setattr(f"{module}.get_db", lambda: db)

    def test_the_single_delete_names_the_election_and_year(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        election = db.add_election(
            westminster_world.map_id,
            2026,
            "UNS <june> run",
            ElectionType.model_uns,
            election_date=date(2026, 6, 1),
        )

        body = app.test_client().get("/westminster/outputs").get_data(as_text=True)
        message = confirm_text(
            _form_handler(body, f"/westminster/outputs/{election.id}/delete")
        )

        assert "UNS <june> run" in message
        assert "(2026)" in message
        assert "vote rows" in message
        assert _UNDONE in message

    def test_the_single_delete_confirm_is_on_a_us_page_too(
        self, app: Flask, db: Database
    ) -> None:
        seeded = _seed_us_house(db)

        body = app.test_client().get("/house/outputs").get_data(as_text=True)
        message = confirm_text(
            _form_handler(body, f"/house/outputs/{seeded['model_id']}/delete")
        )

        assert "US House UNS 2026-06-01" in message
        assert _UNDONE in message

    def test_the_bulk_delete_counts_the_ticked_boxes(
        self, app: Flask, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        db.add_election(
            westminster_world.map_id,
            2026,
            "UNS run",
            ElectionType.model_uns,
            election_date=date(2026, 6, 1),
        )

        body = app.test_client().get("/westminster/outputs").get_data(as_text=True)
        handler = _form_handler(body, "/westminster/outputs/delete-selected")

        # The count is taken from the page's own checkboxes at submit time.
        assert ".model-select:checked').length" in handler
        message = confirm_text(handler)
        assert message.startswith("Delete ")
        assert "selected model output(s) and their vote rows" in message
        assert _UNDONE in message
        assert 'class="model-select"' in body

    def test_a_declined_bulk_confirm_leaves_the_form_alone(self) -> None:
        script = (
            Path(__file__).parents[1] / "console" / "static" / "model_outputs.js"
        ).read_text()

        submit = script.split('bulkDeleteForm.addEventListener("submit"')[1]
        assert submit.index("event.defaultPrevented") < submit.index("hidden")


class TestMatchupRebuildConfirms:
    @pytest.fixture(autouse=True)
    def _use_temp_db(self, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("console.us.views.get_db", lambda: db)

    @staticmethod
    def _assert_rebuild_prompt(handler: str, label: str) -> None:
        # Only prompts when the box is ticked, and reads the box by name so the
        # form's several buttons cannot shadow it.
        assert handler.startswith("return !this.rebuild_history.checked || confirm(")
        message = confirm_text(handler)
        assert f"Rebuild {label} history?" in message
        assert f"re-runs the {label} model over its whole trend history" in message
        assert "then the export" in message
        assert "takes a long time" in message
        assert _UNDONE in message

    def test_the_president_form_names_the_chamber(
        self, app: Flask, db: Database
    ) -> None:
        _seed_president_matchups(db)

        body = app.test_client().get("/president/matchup").get_data(as_text=True)

        self._assert_rebuild_prompt(
            _form_handler(body, "/president/matchup"), "US President"
        )

    def test_every_senate_race_form_names_the_chamber(
        self, app: Flask, db: Database
    ) -> None:
        seeded = _seed_senate_races(db)

        body = app.test_client().get("/senate/matchups").get_data(as_text=True)

        for seat in ("texas", "georgia"):
            action = f"/senate/matchups/{seeded['map_id']}/{seeded[seat]}"
            self._assert_rebuild_prompt(_form_handler(body, action), "US Senate")

    def test_a_house_race_form_names_the_chamber(
        self, app: Flask, db: Database
    ) -> None:
        name = race_map_name("house")
        assert name is not None
        house = db.add_map(name)
        seat = db.add_seat(house.id, "TX-02")
        _add_matchup_polls(
            db, house.id, "Hunt (R) vs Doe (D)", [date(2026, 9, 1)], seat_id=seat.id
        )

        body = app.test_client().get("/house/matchups").get_data(as_text=True)

        self._assert_rebuild_prompt(
            _form_handler(body, f"/house/matchups/{house.id}/{seat.id}"), "US House"
        )


class TestExistingConfirmsAreUnchanged:
    def test_the_admin_confirms(self, app: Flask) -> None:
        body = app.test_client().get("/admin").get_data(as_text=True)

        assert onsubmit_handlers(body) == [
            "return confirm('Rebuild the DATABASE from source files? This re-imports "
            "parties, maps, geometry and every historical election result, and "
            "downloads live boundary/by-election data from the internet — it can "
            "take many minutes (up to an hour). Your polls and model runs are "
            "preserved.');",
            "return confirm('Restore the database from the newest archive? This "
            "overwrites the current local database (a .prerestore copy is kept).');",
        ]

    def test_the_us_model_rebuild_confirm(
        self, app: Flask, db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("console.us.views.get_db", lambda: db)

        body = app.test_client().get("/senate/model").get_data(as_text=True)

        [handler] = onsubmit_handlers(body)
        assert handler.startswith("return !this.rebuild_history.checked || confirm(")
        message = confirm_text(handler)
        assert message == (
            "Rebuild US Senate history? The Senate model recomputes its whole trend "
            "history, then the export runs if Export when done is ticked. It runs "
            "in the background and can take hours; its progress shows on the run "
            "page."
        )
        assert message == rebuild_confirm_message(US_CHAMBERS_BY_SLUG["senate"])

    def test_the_export_write_confirm(self, app: Flask) -> None:
        body = app.test_client().get("/export").get_data(as_text=True)

        assert onsubmit_handlers(body) == [
            "return this.dry_run.value !== 'false' || confirm("
            '"Write the export into the repository for: " + '
            "Array.from(this.querySelectorAll('input[name=sections]:checked'), "
            "function (box) { return box.dataset.label; }).join(', ') + '?');"
        ]


class TestStaleCopy:
    @pytest.mark.parametrize(
        "path", ["/westminster/polls/import", "/senate/polls/import"]
    )
    def test_the_import_pages_say_when_a_reload_discards_a_queue(
        self, app: Flask, path: str
    ) -> None:
        body = app.test_client().get(path).get_data(as_text=True)
        text = " ".join(re.sub(r"<[^>]+>", " ", body).split())

        assert "CONSOLE_DEBUG=1" in text
        assert "automatic reload" not in text.lower()
        assert "restarting the server" in text

    def test_the_admin_page_says_export_for_publishing_site_data(
        self, app: Flask
    ) -> None:
        body = " ".join(app.test_client().get("/admin").get_data(as_text=True).split())

        assert "then export the site data." in body
        assert "rebuild the site" not in body.lower()
