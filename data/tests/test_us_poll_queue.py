"""Tests for the US poll review queue service (``console.services.us_poll_queue``).

Rows are built directly as ``UsPollRow``s (and once through ``rows_for_page``
on fixture HTML), polls are seeded through the real commit path, and the model
runner is a recorder — nothing touches the network, the live database or a
subprocess.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from console.paths import EXPORT_ELECTION_SCRIPT
from console.services import us_poll_queue
from console.services.us_models import UsModelRun
from console.services.us_poll_queue import (
    AUTO_TRACKING_KEY,
    MODEL_ERROR_KEY,
    MODEL_RUN_KEY,
    PER_RACE_CUTOFF_NOTE,
    STATE_KEY,
    BulkOutcome,
    approve_group,
    build_us_queue,
    confirm_us_item,
    finish_us_queue,
    group_key,
    prepare_us_item,
)
from console.services.wikipedia_queue import (
    NO_CUTOFF,
    QueueItem,
    QueueState,
    advance,
    current_item,
)
from db import Database
from polls.importers.us.us_polls_common import CandidateReading, pollster_identifier
from polls.importers.us.us_wikipedia_polls import (
    SENATE_RACES,
    US_CONTESTS_BY_SLUG,
    UsPollIndex,
    UsPollRow,
    apply_auto_tracked_matchups,
    build_us_import_plan,
    commit_us_import_plan,
    rows_for_page,
)

HOUSE_MAP = "US House Districts 2024"
SENATE_MAP = "US Senate 2024"
PRESIDENT_MAP = "US Presidential 2024"

MICHIGAN_MATCHUP = "Rogers (R) vs El-Sayed (D)"
TEXAS_MATCHUP = "Paxton (R) vs Talarico (D)"
NEWSOM_MATCHUP = "Vance (R) vs Newsom (D)"
SHAPIRO_MATCHUP = "Vance (R) vs Shapiro (D)"

NATIONWIDE_PATH = "Opinion polling › General election › Nationwide"

ALASKA_URL = (
    "https://en.wikipedia.org/wiki/2026_United_States_Senate_election_in_Alaska"
)

# Alaska's top-four table (copied from the live shape): two Republicans share a
# surname, a fourth candidate carries the unknown "(WCP)" suffix, and a rowspan
# pair lists the same poll twice (the second is dropped as a variant).
ALASKA_PAGE = """
<html><body><div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="General_election">General election</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Polling">Polling</h3></div>
<table class="wikitable sortable" id="alaska">
<tbody><tr>
<th>Poll source</th><th>Date(s)<br />administered</th><th>Sample<br />size</th>
<th>Dan S. Sullivan<br /><small>(R)</small></th><th>Mary Peltola<br /><small>(D)</small></th>
<th>Dan J. Sullivan<br /><small>(R)</small></th><th>Gerald Heikes<br /><small>(WCP)</small></th>
<th>Undecided</th></tr>
<tr>
<td rowspan="2">Alaska Survey Research</td><td rowspan="2">July 7–9, 2026</td>
<td rowspan="2">1,203 (LV)</td>
<td>40%</td><td><b>44%</b></td><td>3%</td><td>2%</td><td>11%</td></tr>
<tr><td>43%</td><td><b>47%</b></td><td>4%</td><td>2%</td><td>4%</td></tr>
</tbody></table>
</div></body></html>
"""


# ── Fixtures and builders ─────────────────────────────────────────────────────


@pytest.fixture()
def us_db(db: Database) -> Database:
    """Seed the three US maps, the seats these tests use and the parties."""
    house = db.add_map(HOUSE_MAP)
    for seat_name in ("CA-40", "TX-02"):
        db.add_seat(house.id, seat_name)
    senate = db.add_map(SENATE_MAP)
    for seat_name in ("Alaska", "Michigan", "Texas"):
        db.add_seat(senate.id, seat_name)
    president = db.add_map(PRESIDENT_MAP)
    db.add_seat(president.id, "Nevada")
    for party in ("Democratic", "Republican", "Independent"):
        db.add_party(party)
    return db


def _map_id(db: Database, map_name: str) -> int:
    poll_map = db.get_map_by_name(map_name)
    assert poll_map is not None
    return poll_map.id


def _seat_id(db: Database, map_name: str, seat_name: str) -> int:
    for seat in db.get_seats_for_map(_map_id(db, map_name)):
        if seat.seat_name == seat_name:
            return seat.id
    raise AssertionError(f"no seat {seat_name!r} on {map_name!r}")


def _readings(
    *candidates: tuple[str | None, str, float],
) -> tuple[CandidateReading, ...]:
    return tuple(
        CandidateReading(party_name=party, candidate_name=name, percentage=pct)
        for party, name, pct in candidates
    )


MICHIGAN_READINGS = _readings(
    ("Republican", "Mike Rogers", 45.0),
    ("Democratic", "Abdul El-Sayed", 44.0),
)
TEXAS_READINGS = _readings(
    ("Republican", "Ken Paxton", 47.0),
    ("Democratic", "James Talarico", 45.0),
)
NEWSOM_READINGS = _readings(
    ("Republican", "JD Vance", 45.0),
    ("Democratic", "Gavin Newsom", 44.0),
)
SHAPIRO_READINGS = _readings(
    ("Republican", "JD Vance", 46.0),
    ("Democratic", "Josh Shapiro", 43.0),
)


def _row(
    db: Database,
    *,
    contest: str = "senate_races",
    seat: str | None = "Michigan",
    pollster: str = "Glengariff Group",
    start: date = date(2026, 6, 1),
    end: date = date(2026, 6, 4),
    matchup: str | None = MICHIGAN_MATCHUP,
    readings: tuple[CandidateReading, ...] = MICHIGAN_READINGS,
    heading_path: str = "General election › Polling",
    **overrides: Any,
) -> UsPollRow:
    """Build a scraped row the way the contest layer would."""
    spec = US_CONTESTS_BY_SLUG[contest]
    fields: dict[str, Any] = {
        "fieldwork_start": start,
        "fieldwork_end": end,
        "date_label": f"{start} – {end}",
        "pollster_label": pollster,
        "pollster_identifier": pollster_identifier(pollster, spec.pollster_suffix),
        "sample_size_label": "600 (LV)",
        "source_url": "https://example.invalid/poll",
        "matchup": matchup,
        "contest": contest,
        "page_url": "https://en.wikipedia.org/wiki/Example",
        "heading_path": heading_path,
        "seat_name": seat,
        "seat_id": None if seat is None else _seat_id(db, spec.map_name, seat),
        "map_name": spec.map_name,
        "readings": readings,
        "sample_size": 600,
        "population": "LV",
        "is_lead": True,
    }
    fields.update(overrides)
    return UsPollRow(**fields)


def _president_row(db: Database, **overrides: Any) -> UsPollRow:
    """A nationwide Vance vs Newsom row, filed under its matchup heading."""
    fields: dict[str, Any] = {
        "contest": "president",
        "seat": None,
        "pollster": "Emerson College",
        "matchup": NEWSOM_MATCHUP,
        "readings": NEWSOM_READINGS,
        "heading_path": f"{NATIONWIDE_PATH} › JD Vance vs. Gavin Newsom",
    }
    fields.update(overrides)
    return _row(db, **fields)


def _index(*rows: UsPollRow) -> UsPollIndex:
    return UsPollIndex(
        rows=tuple(rows),
        page_failures={},
        notes=(),
        collapsed_only_races=(),
        unknown_suffixes={},
        variants_dropped=0,
        unmatched_seats=(),
        empty_tables=(),
        pages_fetched=0,
    )


def _store(db: Database, row: UsPollRow) -> int:
    """Commit a row through the real import path, returning its poll id."""
    return commit_us_import_plan(db, row, build_us_import_plan(db, row)).poll_id


def _queue(
    db: Database,
    *rows: UsPollRow,
    cutoff: date | None = None,
    run_model_at_end: bool = True,
) -> QueueState:
    return build_us_queue(
        db, _index(*rows), cutoff=cutoff, run_model_at_end=run_model_at_end
    )


def _queued(state: QueueState) -> list[UsPollRow]:
    rows = [item.row for item in state.items]
    assert all(isinstance(row, UsPollRow) for row in rows)
    return [row for row in rows if isinstance(row, UsPollRow)]


def _prepared(db: Database, row: UsPollRow, *others: UsPollRow) -> QueueItem:
    """Queue a row (with any companions) and prepare it."""
    state = _queue(db, row, *others)
    item = next(item for item in state.items if item.row == row)
    prepare_us_item(db, item, state)
    return item


class _RecordingRunner:
    """Stand-in for ``run_python_script`` that records calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(
        self, script: Path, *args: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(script.name)
        return subprocess.CompletedProcess(
            args=[str(script), *args], returncode=0, stdout="ok", stderr=""
        )


# ── Building the queue ────────────────────────────────────────────────────────


class TestCutoffs:
    def test_each_race_is_windowed_on_its_own_latest_poll(
        self, us_db: Database
    ) -> None:
        _store(us_db, _row(us_db, pollster="Stored Poll", end=date(2026, 6, 10)))
        busy_old = _row(us_db, pollster="Old Poll", end=date(2026, 6, 1))
        busy_same_day = _row(us_db, pollster="Same Day", end=date(2026, 6, 10))
        busy_new = _row(us_db, pollster="New Poll", end=date(2026, 6, 20))
        quiet_old = _row(
            us_db,
            seat="Texas",
            pollster="Quiet Poll",
            end=date(2026, 1, 5),
            matchup=TEXAS_MATCHUP,
            readings=TEXAS_READINGS,
        )
        # Same race, but a matchup with nothing stored: a scope of its own.
        other_matchup = _row(
            us_db,
            pollster="Hypothetical",
            end=date(2026, 5, 1),
            matchup="Rogers (R) vs Stevens (D)",
        )

        state = _queue(
            us_db, busy_old, busy_same_day, busy_new, quiet_old, other_matchup
        )

        assert _queued(state) == [other_matchup, busy_same_day, busy_new, quiet_old]
        assert state.cutoff == NO_CUTOFF
        assert state.cutoff_note == PER_RACE_CUTOFF_NOTE

    def test_scopes_do_not_leak_across_maps(self, us_db: Database) -> None:
        # A legacy national, matchup-less poll on the Senate map shares the
        # (seat None, matchup None) scope with the House generic ballot.
        senate_national = _row(
            us_db, seat=None, pollster="Legacy", end=date(2026, 9, 1), matchup=None
        )
        _store(us_db, senate_national)
        generic_ballot = _row(
            us_db,
            contest="house_national",
            seat=None,
            pollster="RealClearPolitics",
            end=date(2026, 8, 1),
            matchup=None,
            readings=_readings(("Republican", "", 44.0), ("Democratic", "", 47.0)),
        )

        state = _queue(us_db, generic_ballot)

        assert _queued(state) == [generic_ballot]

    def test_an_explicit_cutoff_overrides_every_race(self, us_db: Database) -> None:
        _store(us_db, _row(us_db, pollster="Stored Poll", end=date(2026, 6, 10)))
        before = _row(us_db, pollster="Before", end=date(2026, 6, 14))
        on_cutoff = _row(us_db, pollster="On Cutoff", end=date(2026, 6, 15))
        quiet_old = _row(
            us_db,
            seat="Texas",
            pollster="Quiet Poll",
            end=date(2026, 1, 5),
            matchup=TEXAS_MATCHUP,
            readings=TEXAS_READINGS,
        )

        state = _queue(us_db, before, on_cutoff, quiet_old, cutoff=date(2026, 6, 15))

        assert _queued(state) == [on_cutoff]
        assert state.cutoff == date(2026, 6, 15)
        assert state.cutoff_note == ""

    def test_the_reported_cutoff_is_the_earliest_race_cutoff(
        self, us_db: Database
    ) -> None:
        _store(us_db, _row(us_db, pollster="Stored", end=date(2026, 6, 10)))
        _store(
            us_db,
            _row(
                us_db,
                seat="Texas",
                pollster="Stored",
                end=date(2026, 3, 1),
                matchup=TEXAS_MATCHUP,
                readings=TEXAS_READINGS,
            ),
        )
        michigan = _row(us_db, pollster="New", end=date(2026, 6, 20))
        texas = _row(
            us_db,
            seat="Texas",
            pollster="New",
            end=date(2026, 3, 5),
            matchup=TEXAS_MATCHUP,
            readings=TEXAS_READINGS,
        )

        state = _queue(us_db, michigan, texas)

        assert state.cutoff == date(2026, 3, 1)


class TestPresence:
    def test_a_stored_poll_is_dropped_but_other_matchups_and_seats_are_kept(
        self, us_db: Database
    ) -> None:
        stored = _row(us_db)
        _store(us_db, stored)
        other_matchup = _row(us_db, matchup="Rogers (R) vs Stevens (D)")
        other_seat = _row(us_db, seat="Texas")

        state = _queue(us_db, stored, other_matchup, other_seat)

        assert _queued(state) == [other_matchup, other_seat]
        assert state.skipped_present == 1

    def test_presence_is_checked_on_the_row_s_own_map(self, us_db: Database) -> None:
        stored = _president_row(us_db)
        _store(us_db, stored)
        # The same identity, but scraped on another map, is another poll.
        elsewhere = stored.model_copy(
            update={"map_name": SENATE_MAP, "contest": "senate_races"}
        )

        state = _queue(us_db, stored, elsewhere)

        assert _queued(state) == [elsewhere]
        assert state.skipped_present == 1


class TestOrderAndGroups:
    def test_rows_are_reviewed_race_by_race(self, us_db: Database) -> None:
        texas = _row(
            us_db,
            seat="Texas",
            pollster="A",
            end=date(2026, 5, 1),
            matchup=TEXAS_MATCHUP,
        )
        michigan_late = _row(us_db, pollster="B", end=date(2026, 7, 1))
        michigan_early_z = _row(us_db, pollster="Z", end=date(2026, 6, 1))
        michigan_early_a = _row(us_db, pollster="A", end=date(2026, 6, 1))
        house = _row(
            us_db,
            contest="house_districts",
            seat="CA-40",
            pollster="C",
            end=date(2026, 9, 1),
            matchup="Calvert (R) vs Kim (R)",
        )
        president_state = _president_row(us_db, seat="Nevada", end=date(2026, 1, 1))
        president_shapiro = _president_row(
            us_db, matchup=SHAPIRO_MATCHUP, readings=SHAPIRO_READINGS
        )
        president_newsom = _president_row(us_db)

        state = _queue(
            us_db,
            president_state,
            texas,
            president_shapiro,
            michigan_late,
            house,
            michigan_early_z,
            president_newsom,
            michigan_early_a,
        )

        assert _queued(state) == [
            house,
            michigan_early_a,
            michigan_early_z,
            michigan_late,
            texas,
            president_newsom,
            president_shapiro,
            president_state,
        ]
        assert state.index == 0

    def test_group_key_is_the_contest_and_the_seat(self, us_db: Database) -> None:
        assert group_key(_row(us_db)) == (
            "senate_races",
            _seat_id(us_db, SENATE_MAP, "Michigan"),
        )
        assert group_key(_president_row(us_db)) == ("president", None)


# ── Preparing an item: plan and warnings ──────────────────────────────────────


def _has(item: QueueItem, fragment: str) -> bool:
    return any(fragment in warning for warning in item.warnings)


class TestPrepare:
    def test_builds_and_stashes_the_plan(self, us_db: Database) -> None:
        item = _prepared(us_db, _row(us_db))
        assert item.status == "pending"
        assert item.plan is not None
        assert item.plan.seat_id == _seat_id(us_db, SENATE_MAP, "Michigan")

    def test_a_planning_failure_marks_the_item_failed(self, us_db: Database) -> None:
        item = _prepared(us_db, _row(us_db).model_copy(update={"seat_name": "Narnia"}))
        assert item.status == "failed"
        assert "no seat named 'Narnia'" in item.detail
        assert item.plan is None

    def test_a_prepared_item_is_not_rebuilt(self, us_db: Database) -> None:
        state = _queue(us_db, _row(us_db))
        item = state.items[0]
        prepare_us_item(us_db, item, state)
        plan = item.plan
        prepare_us_item(us_db, item, state)
        assert item.plan is plan

    def test_a_clean_lead_row_has_no_warnings_once_its_pollster_exists(
        self, us_db: Database
    ) -> None:
        _store(us_db, _row(us_db, end=date(2026, 5, 1)))
        item = _prepared(us_db, _row(us_db))
        assert item.warnings == []


class TestWarnings:
    def test_new_pollster(self, us_db: Database) -> None:
        item = _prepared(us_db, _row(us_db))
        assert _has(item, "new pollster: Glengariff Group (US Senate)")

    def test_partisan_sponsor_tag(self, us_db: Database) -> None:
        item = _prepared(us_db, _row(us_db, pollster_tags=("R",)))
        assert _has(item, "Partisan poll: the pollster is tagged (R)")

    def test_page_shaped_row_reports_suffix_summing_and_variants(
        self, us_db: Database
    ) -> None:
        seat_ids = {
            seat.seat_name: seat.id
            for seat in us_db.get_seats_for_map(_map_id(us_db, SENATE_MAP))
        }
        (row,) = rows_for_page(
            SENATE_RACES, ALASKA_URL, ALASKA_PAGE, seat_ids=seat_ids
        ).rows

        item = _prepared(us_db, row)

        assert _has(
            item, "Not imported: no party for Gerald Heikes — unrecognised suffix"
        )
        assert _has(
            item,
            "2 Republican candidates (Dan S. Sullivan, Dan J. Sullivan) are summed",
        )
        assert _has(item, "1 repeat row(s) dropped")
        # The table note about the suffix is not repeated alongside the
        # per-candidate warning.
        assert not _has(item, "unrecognised party suffix")

    def test_heading_that_does_not_name_a_candidate(self, us_db: Database) -> None:
        misfiled = _president_row(
            us_db, matchup=SHAPIRO_MATCHUP, readings=SHAPIRO_READINGS
        )
        item = _prepared(us_db, misfiled)
        assert _has(item, "does not name Shapiro")

    def test_heading_that_names_both_candidates(self, us_db: Database) -> None:
        item = _prepared(us_db, _president_row(us_db))
        assert not _has(item, "does not name")

    def test_other_matchups_from_the_same_poll(self, us_db: Database) -> None:
        newsom = _president_row(us_db)
        shapiro = _president_row(
            us_db,
            matchup=SHAPIRO_MATCHUP,
            readings=SHAPIRO_READINGS,
            heading_path=f"{NATIONWIDE_PATH} › JD Vance vs. Josh Shapiro",
        )
        # Same pollster and matchup, another seat: a different poll.
        nevada = _president_row(us_db, seat="Nevada", matchup="Vance (R) vs Harris (D)")

        item = _prepared(us_db, newsom, shapiro, nevada)

        assert _has(
            item,
            "1 other matchup(s) from this poll are also in the queue: "
            f"{SHAPIRO_MATCHUP}",
        )

    def test_not_the_lead_table(self, us_db: Database) -> None:
        item = _prepared(us_db, _row(us_db, is_lead=False))
        assert _has(item, "Not the race's lead table")

    def test_collapsed_hypothetical(self, us_db: Database) -> None:
        row = _row(
            us_db,
            is_lead=False,
            collapsed=True,
            notes=("collapsed hypothetical table",),
        )
        item = _prepared(us_db, row)
        assert _has(item, "collapsed hypothetical table, imported because")
        assert not _has(item, "Not the race's lead table")
        assert "collapsed hypothetical table" not in item.warnings

    def test_president_without_a_national_matchup(self, us_db: Database) -> None:
        item = _prepared(us_db, _president_row(us_db))
        assert _has(item, "No national presidential matchup is set")

    def test_president_paused_national_matchup_counts_as_unset(
        self, us_db: Database
    ) -> None:
        us_db.set_tracked_matchup(
            _map_id(us_db, PRESIDENT_MAP), None, None, source="manual"
        )
        item = _prepared(us_db, _president_row(us_db))
        assert _has(item, "No national presidential matchup is set")

    def test_president_row_that_is_not_the_tracked_matchup(
        self, us_db: Database
    ) -> None:
        us_db.set_tracked_matchup(
            _map_id(us_db, PRESIDENT_MAP), None, SHAPIRO_MATCHUP, source="manual"
        )
        item = _prepared(us_db, _president_row(us_db))
        assert _has(item, f"Not the tracked presidential matchup ({SHAPIRO_MATCHUP})")

    def test_president_row_that_is_the_tracked_matchup(self, us_db: Database) -> None:
        us_db.set_tracked_matchup(
            _map_id(us_db, PRESIDENT_MAP), None, NEWSOM_MATCHUP, source="manual"
        )
        item = _prepared(us_db, _president_row(us_db))
        assert not _has(item, "presidential matchup")

    def test_manual_override_that_differs(self, us_db: Database) -> None:
        us_db.set_tracked_matchup(
            _map_id(us_db, SENATE_MAP),
            _seat_id(us_db, SENATE_MAP, "Michigan"),
            "Rogers (R) vs Stevens (D)",
            source="manual",
        )
        item = _prepared(us_db, _row(us_db))
        assert _has(item, "manually set to follow Rogers (R) vs Stevens (D)")

    def test_manual_override_ignoring_the_race(self, us_db: Database) -> None:
        us_db.set_tracked_matchup(
            _map_id(us_db, SENATE_MAP),
            _seat_id(us_db, SENATE_MAP, "Michigan"),
            None,
            source="manual",
        )
        item = _prepared(us_db, _row(us_db))
        assert _has(item, "manually set to be ignored")

    def test_manual_override_that_matches_and_automatic_rows_are_quiet(
        self, us_db: Database
    ) -> None:
        senate = _map_id(us_db, SENATE_MAP)
        us_db.set_tracked_matchup(
            senate,
            _seat_id(us_db, SENATE_MAP, "Michigan"),
            MICHIGAN_MATCHUP,
            source="manual",
        )
        us_db.set_tracked_matchup(
            senate,
            _seat_id(us_db, SENATE_MAP, "Texas"),
            "Cornyn (R) vs Allred (D)",
            source="auto",
        )
        michigan = _prepared(us_db, _row(us_db))
        texas = _prepared(us_db, _row(us_db, seat="Texas", matchup=TEXAS_MATCHUP))
        assert not _has(michigan, "manually set")
        assert not _has(texas, "manually set")


# ── Confirming one item ───────────────────────────────────────────────────────


class TestConfirm:
    def test_commits_the_poll_and_the_cursor_can_advance(self, us_db: Database) -> None:
        row = _row(us_db)
        state = _queue(us_db, row)
        item = state.items[0]
        prepare_us_item(us_db, item, state)

        confirm_us_item(us_db, item)
        advance(state)

        assert item.status == "imported"
        assert item.poll_id is not None
        assert item.detail == f"Poll #{item.poll_id}, 2 rows inserted"
        assert item.plan is None
        poll = us_db.get_poll(item.poll_id)
        assert poll is not None
        assert poll.matchup == MICHIGAN_MATCHUP
        assert poll.seat_id == _seat_id(us_db, SENATE_MAP, "Michigan")
        assert current_item(state) is None

    def test_a_poll_imported_in_the_meantime_is_skipped(self, us_db: Database) -> None:
        row = _row(us_db)
        state = _queue(us_db, row)
        item = state.items[0]
        prepare_us_item(us_db, item, state)
        stored_id = _store(us_db, row)

        confirm_us_item(us_db, item)

        assert item.status == "skipped"
        assert item.detail == f"Already in the database (poll #{stored_id})"
        assert len(us_db.get_polls_for_map(_map_id(us_db, SENATE_MAP))) == 1

    def test_an_unprepared_item_is_left_alone(self, us_db: Database) -> None:
        state = _queue(us_db, _row(us_db))
        item = state.items[0]

        confirm_us_item(us_db, item)

        assert item.status == "pending"
        assert us_db.get_polls_for_map(_map_id(us_db, SENATE_MAP)) == []

    def test_a_commit_failure_marks_the_item_failed(
        self, us_db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*args: object) -> None:
            raise ValueError("seat 7 is not on map 2")

        state = _queue(us_db, _row(us_db))
        item = state.items[0]
        prepare_us_item(us_db, item, state)
        monkeypatch.setattr(us_poll_queue, "commit_us_import_plan", broken)

        confirm_us_item(us_db, item)

        assert item.status == "failed"
        assert item.detail == "seat 7 is not on map 2"
        assert item.plan is not None  # kept, so a retry starts from a clean slate


# ── Approving a whole race ────────────────────────────────────────────────────


class TestApproveGroup:
    def test_one_failure_does_not_stop_the_batch(self, us_db: Database) -> None:
        first = _row(us_db, pollster="A", end=date(2026, 6, 1))
        unplannable = _row(
            us_db,
            pollster="B",
            end=date(2026, 6, 2),
            readings=_readings(("Whig", "Henry Clay", 50.0)),
        )
        last = _row(us_db, pollster="C", end=date(2026, 6, 3))
        texas = _row(
            us_db, seat="Texas", matchup=TEXAS_MATCHUP, readings=TEXAS_READINGS
        )
        state = _queue(us_db, texas, last, unplannable, first)

        outcome = approve_group(us_db, state, group_key(first))

        assert outcome == BulkOutcome(imported=2, skipped=0, failed=1)
        assert outcome.total == 3
        statuses = {item.row.pollster_label: item for item in state.items}
        assert statuses["A"].status == "imported"
        assert statuses["C"].status == "imported"
        assert statuses["B"].status == "failed"
        assert "resolved to a party" in statuses["B"].detail
        assert current_item(state) is statuses["Glengariff Group"]
        assert statuses["Glengariff Group"].status == "pending"
        assert len(us_db.get_polls_for_map(_map_id(us_db, SENATE_MAP))) == 2

    def test_presence_is_rechecked_within_the_batch(self, us_db: Database) -> None:
        row = _row(us_db)
        # Wikipedia listing the same poll twice in one race.
        state = _queue(us_db, row, row.model_copy())

        outcome = approve_group(us_db, state, group_key(row))

        assert outcome == BulkOutcome(imported=1, skipped=1, failed=0)
        assert [item.status for item in state.items] == ["imported", "skipped"]
        assert len(us_db.get_polls_for_map(_map_id(us_db, SENATE_MAP))) == 1

    def test_already_prepared_items_keep_their_plan(self, us_db: Database) -> None:
        state = _queue(us_db, _row(us_db, pollster="A"), _row(us_db, pollster="B"))
        prepare_us_item(us_db, state.items[0], state)

        outcome = approve_group(us_db, state, group_key(_row(us_db)))

        assert outcome.imported == 2
        assert current_item(state) is None

    def test_other_races_are_untouched(self, us_db: Database) -> None:
        michigan = _row(us_db)
        texas = _row(
            us_db, seat="Texas", matchup=TEXAS_MATCHUP, readings=TEXAS_READINGS
        )
        state = _queue(us_db, michigan, texas)

        approve_group(us_db, state, group_key(texas))

        assert [item.status for item in state.items] == ["pending", "imported"]
        assert state.index == 0


# ── Finishing ─────────────────────────────────────────────────────────────────


def _imported_payload(
    db: Database, *rows: UsPollRow, run_model_at_end: bool = True
) -> dict[str, Any]:
    """Queue rows, approve them race by race, and wrap the state in a payload."""
    state = _queue(db, *rows, run_model_at_end=run_model_at_end)
    for key in dict.fromkeys(group_key(item.row) for item in state.items):
        approve_group(db, state, key)
    return {"type": "us_wikipedia_queue", STATE_KEY: state}


class _TrackingSpy:
    """Counts calls to ``apply_auto_tracked_matchups`` and passes them through."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, db: Database, rows: list[UsPollRow]) -> dict[str, int]:
        self.calls += 1
        return apply_auto_tracked_matchups(db, rows)


class TestFinish:
    def test_tracks_the_imported_races_and_keeps_a_manual_row(
        self, us_db: Database
    ) -> None:
        senate = _map_id(us_db, SENATE_MAP)
        texas_id = _seat_id(us_db, SENATE_MAP, "Texas")
        us_db.set_tracked_matchup(
            senate, texas_id, "Cornyn (R) vs Allred (D)", source="manual"
        )
        payload = _imported_payload(
            us_db,
            _row(us_db),
            _row(us_db, seat="Texas", matchup=TEXAS_MATCHUP, readings=TEXAS_READINGS),
        )

        finish_us_queue(us_db, payload, runner=_RecordingRunner())

        assert payload[AUTO_TRACKING_KEY]["created"] == 1
        assert payload[AUTO_TRACKING_KEY]["kept_manual"] == 1
        michigan = us_db.get_tracked_matchup(
            senate, _seat_id(us_db, SENATE_MAP, "Michigan")
        )
        assert michigan is not None
        assert (michigan.matchup, michigan.source) == (MICHIGAN_MATCHUP, "auto")
        texas = us_db.get_tracked_matchup(senate, texas_id)
        assert texas is not None
        assert (texas.matchup, texas.source) == ("Cornyn (R) vs Allred (D)", "manual")
        assert texas.auto_matchup == TEXAS_MATCHUP

    def test_runs_the_models_once_when_something_was_imported(
        self, us_db: Database
    ) -> None:
        runner = _RecordingRunner()
        payload = _imported_payload(us_db, _row(us_db))

        finish_us_queue(us_db, payload, runner=runner)

        run = payload[MODEL_RUN_KEY]
        assert isinstance(run, UsModelRun)
        assert run.return_code == 0
        # No presidential matchup is set, so that chamber is skipped.
        assert run.skipped == frozenset({"president"})
        assert len(runner.calls) == 3
        assert runner.calls[-1] == EXPORT_ELECTION_SCRIPT.name

    def test_a_refresh_repeats_nothing(
        self, us_db: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = _TrackingSpy()
        monkeypatch.setattr(us_poll_queue, "apply_auto_tracked_matchups", spy)
        runner = _RecordingRunner()
        payload = _imported_payload(us_db, _row(us_db))

        finish_us_queue(us_db, payload, runner=runner)
        first_tracking = payload[AUTO_TRACKING_KEY]
        first_run = payload[MODEL_RUN_KEY]
        finish_us_queue(us_db, payload, runner=runner)

        assert spy.calls == 1
        assert len(runner.calls) == 3
        assert payload[AUTO_TRACKING_KEY] is first_tracking
        assert payload[MODEL_RUN_KEY] is first_run

    def test_nothing_imported_runs_no_models(self, us_db: Database) -> None:
        row = _row(us_db)
        state = _queue(us_db, row)
        prepare_us_item(us_db, state.items[0], state)
        _store(us_db, row)
        confirm_us_item(us_db, state.items[0])
        payload: dict[str, Any] = {STATE_KEY: state}
        runner = _RecordingRunner()

        finish_us_queue(us_db, payload, runner=runner)

        assert state.items[0].status == "skipped"
        assert runner.calls == []
        assert MODEL_RUN_KEY not in payload
        assert set(payload[AUTO_TRACKING_KEY].values()) == {0}

    def test_the_model_option_off_runs_no_models(self, us_db: Database) -> None:
        runner = _RecordingRunner()
        payload = _imported_payload(us_db, _row(us_db), run_model_at_end=False)

        finish_us_queue(us_db, payload, runner=runner)

        assert runner.calls == []
        assert MODEL_RUN_KEY not in payload

    def test_a_model_run_that_raises_is_recorded(self, us_db: Database) -> None:
        def timed_out(
            script: Path, *args: str, timeout: int
        ) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=str(script), timeout=timeout)

        payload = _imported_payload(us_db, _row(us_db))

        finish_us_queue(us_db, payload, runner=timed_out)

        assert payload[MODEL_RUN_KEY] is None
        assert payload[MODEL_ERROR_KEY].startswith("US model run failed:")

    def test_abandon_runs_no_models_but_still_tracks(self, us_db: Database) -> None:
        runner = _RecordingRunner()
        payload = _imported_payload(us_db, _row(us_db))

        finish_us_queue(us_db, payload, runner=runner, abandon=True)
        finish_us_queue(us_db, payload, runner=runner)

        assert runner.calls == []
        assert MODEL_RUN_KEY not in payload
        assert payload[STATE_KEY].run_model_at_end is False
        assert payload[AUTO_TRACKING_KEY]["created"] == 1

    def test_a_payload_without_a_queue_is_rejected(self, us_db: Database) -> None:
        with pytest.raises(TypeError, match="no queue state"):
            finish_us_queue(us_db, {}, runner=_RecordingRunner())
