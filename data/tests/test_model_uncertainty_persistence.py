"""Storing model runs' Monte Carlo summaries beside their outputs.

``replace_output`` writes a run's summary in the run's own transaction and,
on every save while the table exists, keeps the region and seat blocks on the
scope's latest-dated run only. Runs without a summary store nothing, and on a
database without the table they do not touch it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest

from console.services.model_outputs import (
    delete_model_output,
    delete_selected_model_outputs,
)
from db import Database
from model_support.monte_carlo import (
    NationalPartyOutcome,
    RegionPartyOutcome,
    SeatHistogram,
    SeatPartyOutcome,
    SimulationSummary,
)
from model_support.persistence import (
    UNCERTAINTY_SCHEMA,
    OutputScope,
    OutputVote,
    StoredUncertainty,
    UncertaintyPayload,
    UncertaintyRun,
    UncertaintyStatus,
    UncertaintyStoreError,
    delete_outputs,
    iter_national_blocks,
    latest_run,
    latest_saved_run,
    load_uncertainty,
    replace_output,
    uncertainty_status,
    westminster_uns_scope,
)
from models import ElectionType

Row = tuple[str, str, str | None, str | None]


@pytest.fixture()
def scopes(db: Database) -> dict[str, OutputScope]:
    """The scope under test plus bystanders: another model type, another map."""
    main_map = db.add_map("Main map").id
    other_map = db.add_map("Other map").id
    return {
        "main": OutputScope("model_uns", main_map, "UNS"),
        "other_type": OutputScope("model_run", main_map, "UNS"),
        "other_map": OutputScope("model_uns", other_map, "UNS"),
    }


@pytest.fixture()
def path(only_the_test_database: Path) -> Path:
    return only_the_test_database


@pytest.fixture()
def statements(path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every SQL statement run through ``sqlite3.connect`` connections."""
    log: list[str] = []
    guarded_connect = sqlite3.connect

    def tracing(
        database: object, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        conn: sqlite3.Connection = guarded_connect(  # type: ignore[call-overload]
            database, *args, **kwargs
        )
        conn.set_trace_callback(log.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracing)
    return log


def payload(tag: int, *, detail: bool = True) -> UncertaintyPayload:
    """A summary whose numbers identify it by ``tag``."""
    return UncertaintyPayload(
        national={"hung": tag / 100, "parties": {"3": {"v": [20.5, 27.25 + tag]}}},
        regions=(
            {"13": {"parties": {"3": {"v": [18.0, 30.0 + tag], "s": [1, 4, 9]}}}}
            if detail
            else None
        ),
        seats={"101": {"3": {"v": [30.0, 45.5], "w": tag / 1000}}} if detail else None,
    )


def save(
    path: Path,
    scope: OutputScope,
    day: int,
    uncertainty: UncertaintyPayload | None = None,
) -> int:
    """Store a one-vote run for 2026-10-``day``; return its election id."""
    as_of = date(2026, 10, day)
    _, election_id = replace_output(
        path,
        scope,
        as_of,
        f"{scope.name_prefix} {as_of}",
        [OutputVote(1, 3, "", 42.0, True)],
        uncertainty=uncertainty,
    )
    return election_id


def row(path: Path, election_id: int) -> Row | None:
    with closing(sqlite3.connect(path)) as conn:
        found: Row | None = conn.execute(
            "SELECT schema, national_json, regions_json, seats_json "
            "FROM model_uncertainty WHERE election_id = ?",
            (election_id,),
        ).fetchone()
        return found


def all_rows(path: Path) -> dict[int, tuple[str, bool, bool]]:
    """Every stored row: election id -> (national text, has regions, has seats)."""
    with closing(sqlite3.connect(path)) as conn:
        return {
            election_id: (national, regions is not None, seats is not None)
            for election_id, national, regions, seats in conn.execute(
                "SELECT election_id, national_json, regions_json, seats_json "
                "FROM model_uncertainty"
            )
        }


def compact(block: object) -> str:
    return json.dumps(block, sort_keys=True, separators=(",", ":"))


def drop_table(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DROP TABLE model_uncertainty")
        conn.commit()


def has_table(path: Path) -> bool:
    with closing(sqlite3.connect(path)) as conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'model_uncertainty'"
        ).fetchone()
        return found is not None


def uses_of_table(statements: list[str]) -> list[str]:
    """Statements reading or writing the table (the catalog probe excluded)."""
    return [
        sql
        for sql in statements
        if "model_uncertainty" in sql
        and not sql.startswith("SELECT 1 FROM sqlite_master")
    ]


def engine_summary() -> SimulationSummary:
    """A small summary in the engine's own types, with distinctive values."""
    return SimulationSummary(
        simulations=10_000,
        interval=0.9,
        seed=20261014,
        config_hash="abc123",
        national={
            3: NationalPartyOutcome(
                vote=(22.81, 29.64),
                seats=(222, 299, 372),
                histogram=SeatHistogram(start=220, counts=(1, 0, 3)),
                p_majority=0.2834,
                p_largest=0.9731,
            ),
            12: NationalPartyOutcome(
                vote=(19.6, 26.5),
                seats=(47, 114, 203),
                histogram=SeatHistogram(start=45, counts=(2,)),
                p_majority=0.0,
                p_largest=0.0269,
            ),
        },
        p_hung=0.7166,
        regions={13: {3: RegionPartyOutcome(vote=(31.2, 40.07), seats=(40, 51, 59))}},
        seats={101: {3: SeatPartyOutcome(vote=(38.5, 52.25), p_win=0.9912)}},
    )


class TestWrite:
    """A payload is stored with its run, as compact, sorted JSON."""

    def test_engine_summary_round_trips(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        summary = engine_summary()
        election_id = save(
            path,
            scopes["main"],
            1,
            UncertaintyPayload(
                national=summary.national_json(),
                regions=summary.regions_json(),
                seats=summary.seats_json(),
            ),
        )

        stored = row(path, election_id)
        assert stored is not None
        assert stored[0] == UNCERTAINTY_SCHEMA == "pf-model-uncertainty-v1"
        assert load_uncertainty(path, election_id) == StoredUncertainty(
            election_id=election_id,
            national=summary.national_json(),
            regions=summary.regions_json(),
            seats=summary.seats_json(),
        )

    def test_text_is_compact_with_sorted_keys(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        national = {"parties": {"3": {"v": [1.5, 2.5]}}, "hung": 0.5}
        election_id = save(path, scopes["main"], 1, UncertaintyPayload(national))

        assert row(path, election_id) == (
            UNCERTAINTY_SCHEMA,
            '{"hung":0.5,"parties":{"3":{"v":[1.5,2.5]}}}',
            None,
            None,
        )

    def test_key_order_does_not_change_the_text(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        first = save(path, scopes["main"], 1, UncertaintyPayload({"b": 1, "a": 2}))
        second = save(path, scopes["main"], 2, UncertaintyPayload({"a": 2, "b": 1}))

        first_row, second_row = row(path, first), row(path, second)
        assert first_row is not None and second_row is not None
        assert first_row[1] == second_row[1] == '{"a":2,"b":1}'

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), {1, 2}])
    def test_non_json_value_is_refused_before_writing(
        self, path: Path, scopes: dict[str, OutputScope], bad: object
    ) -> None:
        kept = save(path, scopes["main"], 1)

        with pytest.raises(ValueError):
            save(path, scopes["main"], 1, UncertaintyPayload({"hung": 0.1}, {"x": bad}))

        with closing(sqlite3.connect(path)) as conn:
            ids = conn.execute("SELECT id FROM elections").fetchall()
        assert ids == [(kept,)]
        assert all_rows(path) == {}


class TestPruneByRunDate:
    """Only the latest-dated run of a scope keeps region and seat blocks."""

    def test_a_later_run_prunes_the_earlier_detail_but_keeps_national(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        earlier = save(path, scopes["main"], 1, payload(1))
        later = save(path, scopes["main"], 2, payload(2))

        assert all_rows(path) == {
            earlier: (compact(payload(1).national), False, False),
            later: (compact(payload(2).national), True, True),
        }

    def test_resaving_an_older_date_keeps_the_latest_runs_detail(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        latest = save(path, scopes["main"], 5, payload(5))
        save(path, scopes["main"], 3, payload(3))

        # The older date is written last and has the higher id, so only the
        # date can keep the latest run's detail.
        older = save(path, scopes["main"], 3, payload(33))

        assert older > latest
        assert all_rows(path) == {
            latest: (compact(payload(5).national), True, True),
            older: (compact(payload(33).national), False, False),
        }

    def test_three_runs_out_of_order_leave_detail_on_the_latest(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        middle = save(path, scopes["main"], 2, payload(2))
        latest = save(path, scopes["main"], 9, payload(9))
        first = save(path, scopes["main"], 1, payload(1))

        rows = all_rows(path)
        assert set(rows) == {first, middle, latest}
        assert {eid: detail for eid, (_, *detail) in rows.items()} == {
            first: [False, False],
            middle: [False, False],
            latest: [True, True],
        }

    def test_latest_run_without_a_summary_still_takes_precedence(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        save(path, scopes["main"], 9)

        older = save(path, scopes["main"], 4, payload(4))

        assert all_rows(path) == {older: (compact(payload(4).national), False, False)}

    def test_same_date_replaces_the_row(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        save(path, scopes["main"], 1, payload(1))

        replaced = save(path, scopes["main"], 1, payload(11))

        assert all_rows(path) == {replaced: (compact(payload(11).national), True, True)}

    @pytest.mark.parametrize("bystander", ["other_type", "other_map"])
    def test_other_scopes_are_untouched(
        self, path: Path, scopes: dict[str, OutputScope], bystander: str
    ) -> None:
        other_early = save(path, scopes[bystander], 1, payload(71))
        save(path, scopes[bystander], 2, payload(72))
        other_late = save(path, scopes[bystander], 30, payload(73))
        mine_early = save(path, scopes["main"], 3, payload(3))
        mine_late = save(path, scopes["main"], 4, payload(4))
        before = all_rows(path)[other_late]

        save(path, scopes["main"], 5, payload(5))

        rows = all_rows(path)
        assert rows[other_late] == before == (compact(payload(73).national), True, True)
        assert rows[other_early][1:] == (False, False)
        assert rows[mine_early][1:] == rows[mine_late][1:] == (False, False)


class TestWithoutPayload:
    """A run with no summary leaves the table as it is, or absent."""

    def test_a_later_run_prunes_older_detail_and_stores_nothing(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        earlier = save(path, scopes["main"], 1, payload(1))

        later = save(path, scopes["main"], 2)

        assert row(path, later) is None
        assert all_rows(path) == {earlier: (compact(payload(1).national), False, False)}

    def test_an_older_run_leaves_the_latest_detail(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        latest = save(path, scopes["main"], 5, payload(5))

        save(path, scopes["main"], 2)

        assert all_rows(path) == {latest: (compact(payload(5).national), True, True)}

    @pytest.mark.parametrize("payload_first", [True, False])
    def test_write_order_does_not_change_the_stored_state(
        self, path: Path, scopes: dict[str, OutputScope], payload_first: bool
    ) -> None:
        if payload_first:
            older = save(path, scopes["main"], 1, payload(1))
            newer = save(path, scopes["main"], 2)
        else:
            newer = save(path, scopes["main"], 2)
            older = save(path, scopes["main"], 1, payload(1))

        assert row(path, newer) is None
        assert all_rows(path) == {older: (compact(payload(1).national), False, False)}

    def test_same_date_deletes_the_replaced_runs_row(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        bystander = save(path, scopes["main"], 1, payload(1))
        replaced = save(path, scopes["main"], 2, payload(2))

        replacement = save(path, scopes["main"], 2)

        # SQLite hands the deleted top id to the next insert, so a row left
        # behind would describe the replacement run.
        assert replacement == replaced
        assert load_uncertainty(path, replacement) is None
        assert set(all_rows(path)) == {bystander}

    def test_works_on_a_database_without_the_table(
        self, path: Path, scopes: dict[str, OutputScope], statements: list[str]
    ) -> None:
        drop_table(path)
        statements.clear()

        save(path, scopes["main"], 1)
        save(path, scopes["main"], 1)
        save(path, scopes["main"], 2)
        day = date(2026, 10, 1)
        deleted = delete_outputs(path, scopes["main"], day, day)

        assert deleted == (1, 1)
        assert not has_table(path)
        assert uses_of_table(statements) == []

    def test_a_payload_without_the_table_writes_nothing(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        kept = save(path, scopes["main"], 1)
        drop_table(path)

        with pytest.raises(UncertaintyStoreError, match="migrate_add_model_unc"):
            save(path, scopes["main"], 1, payload(1))

        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("SELECT id FROM elections").fetchall() == [(kept,)]
            assert conn.execute("SELECT COUNT(*) FROM votes").fetchone() == (1,)
        assert not has_table(path)


def snapshot(path: Path) -> dict[str, list[tuple[object, ...]]]:
    """Every row of the tables a run write touches, by value."""
    with closing(sqlite3.connect(path)) as conn:
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in ("elections", "votes", "model_uncertainty")
        }


def add_failing_trigger(path: Path, event: str) -> None:
    """Make the next *event* (``INSERT`` / ``UPDATE``) on the table abort."""
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            f"CREATE TRIGGER fail_uncertainty BEFORE {event} ON model_uncertainty "
            "BEGIN SELECT RAISE(ABORT, 'injected uncertainty failure'); END"
        )
        conn.commit()


class TestLateFailureRollsBack:
    """A failure after the election and votes are written undoes the whole run."""

    def test_failed_summary_insert_restores_the_replaced_run(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        # A detailed earlier run, the run being replaced (same date), and a
        # bystander in another scope: all must survive untouched.
        save(path, scopes["main"], 1, payload(1))
        save(path, scopes["main"], 2, payload(2))
        save(path, scopes["other_map"], 9, payload(7))  # names are unique
        before = snapshot(path)
        assert len(before["model_uncertainty"]) == 3
        add_failing_trigger(path, "INSERT")

        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            save(path, scopes["main"], 2, payload(22))

        assert snapshot(path) == before

    def test_failed_prune_update_rolls_back_the_new_run(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        earlier = save(path, scopes["main"], 1, payload(1))
        save(path, scopes["other_map"], 9, payload(7))  # names are unique
        before = snapshot(path)
        assert all_rows(path)[earlier] == (compact(payload(1).national), True, True)
        add_failing_trigger(path, "UPDATE")

        # The newer run's INSERTs succeed; pruning the earlier run's detail
        # is the statement that fails.
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            save(path, scopes["main"], 2, payload(2))

        assert snapshot(path) == before

    def test_failed_prune_without_a_payload_rolls_back_too(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        save(path, scopes["main"], 1, payload(1))
        before = snapshot(path)
        add_failing_trigger(path, "UPDATE")

        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            save(path, scopes["main"], 2)

        assert snapshot(path) == before


class TestDelete:
    """Deleting runs deletes their rows, and only theirs."""

    def test_delete_outputs_removes_matching_rows(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        first = save(path, scopes["main"], 1, payload(1))
        save(path, scopes["main"], 2, payload(2))
        save(path, scopes["main"], 3, payload(3))
        last = save(path, scopes["main"], 4, payload(4))
        # Names are unique across scopes, so bystanders take their own dates.
        other = save(path, scopes["other_type"], 12, payload(72))

        deleted = delete_outputs(
            path, scopes["main"], date(2026, 10, 2), date(2026, 10, 3)
        )

        assert deleted == (2, 2)
        assert set(all_rows(path)) == {first, last, other}
        assert all_rows(path)[other] == (compact(payload(72).national), True, True)

    def test_console_delete_cascades_to_the_row(
        self, db: Database, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        target = save(path, scopes["main"], 1, payload(1))
        bystander = save(path, scopes["main"], 2, payload(2))

        assert delete_model_output(
            db, election_id=target, election_type=ElectionType.model_uns
        ) == 1

        assert set(all_rows(path)) == {bystander}

    def test_console_bulk_delete_cascades_to_the_rows(
        self, db: Database, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        targets = [save(path, scopes["main"], day, payload(day)) for day in (1, 2)]
        bystander = save(path, scopes["main"], 3, payload(3))

        assert delete_selected_model_outputs(
            db, election_ids=targets, election_type=ElectionType.model_uns
        ) == (2, 2)

        assert set(all_rows(path)) == {bystander}

    def test_console_delete_works_without_the_table(
        self, db: Database, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        target = save(path, scopes["main"], 1)
        drop_table(path)
        # Pooled connections may hold the dropped table's schema.
        db.engine.dispose()

        assert delete_model_output(
            db, election_id=target, election_type=ElectionType.model_uns
        ) == 1


class TestRead:
    """load_uncertainty and uncertainty_status."""

    def test_load_returns_none_without_row_table_or_file(
        self, path: Path, scopes: dict[str, OutputScope], tmp_path: Path
    ) -> None:
        election_id = save(path, scopes["main"], 1)

        assert load_uncertainty(path, election_id) is None
        drop_table(path)
        assert load_uncertainty(path, election_id) is None
        assert load_uncertainty(tmp_path / "absent.db", election_id) is None

    def test_load_pruned_run_has_national_only(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        earlier = save(path, scopes["main"], 1, payload(1))
        save(path, scopes["main"], 2, payload(2))

        assert load_uncertainty(path, earlier) == StoredUncertainty(
            election_id=earlier,
            national=dict(payload(1).national),
            regions=None,
            seats=None,
        )

    @pytest.mark.parametrize(
        ("column", "value", "message"),
        [
            ("schema", "pf-model-uncertainty-v0", "stored format"),
            ("national_json", "{not json", "national_json is not valid JSON"),
            ("regions_json", "[1, 2]", "regions_json is not a JSON object"),
            ("seats_json", '"text"', "seats_json is not a JSON object"),
        ],
    )
    def test_load_refuses_unreadable_rows(
        self,
        path: Path,
        scopes: dict[str, OutputScope],
        column: str,
        value: str,
        message: str,
    ) -> None:
        election_id = save(path, scopes["main"], 1, payload(1))
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                f"UPDATE model_uncertainty SET {column} = ? WHERE election_id = ?",
                (value, election_id),
            )
            conn.commit()

        with pytest.raises(UncertaintyStoreError, match=message):
            load_uncertainty(path, election_id)

    def test_latest_run_is_by_date_then_id(self) -> None:
        # The highest id is the earliest date: date wins.
        assert latest_run([(9, date(2026, 10, 1)), (3, date(2026, 10, 5))]) == (
            3, date(2026, 10, 5),
        )
        # Equal dates: the higher id wins, whatever the order given.
        assert latest_run([(7, date(2026, 10, 5)), (4, date(2026, 10, 5))]) == (
            7, date(2026, 10, 5),
        )
        assert latest_run([]) is None

    def test_latest_saved_run_skips_undated_and_other_scopes(
        self, path: Path, scopes: dict[str, OutputScope], tmp_path: Path
    ) -> None:
        latest = save(path, scopes["main"], 5)
        save(path, scopes["main"], 2)  # saved later: higher id, earlier date
        save(path, scopes["other_map"], 9)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "INSERT INTO elections (map_id, year, name, type) VALUES (?,?,?,?)",
                (scopes["main"].map_id, 2026, "UNS latest", "model_uns"),
            )
            conn.commit()
        assert latest_saved_run(path, scopes["main"]) == (latest, date(2026, 10, 5))
        missing = tmp_path / "missing.db"
        assert latest_saved_run(missing, scopes["main"]) is None
        assert not missing.exists()

    def test_national_blocks_cover_the_scope_only_including_pruned_runs(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        earlier = save(path, scopes["main"], 1, payload(1))
        later = save(path, scopes["main"], 2, payload(2))
        save(path, scopes["main"], 3)
        save(path, scopes["other_type"], 4, payload(4))
        save(path, scopes["other_map"], 5, payload(5))

        with closing(sqlite3.connect(path)) as conn:
            blocks = dict(iter_national_blocks(conn, scopes["main"]))

        assert blocks == {
            earlier: dict(payload(1).national),
            later: dict(payload(2).national),
        }

    def test_national_blocks_without_the_table(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        save(path, scopes["main"], 1)
        drop_table(path)
        with closing(sqlite3.connect(path)) as conn:
            assert list(iter_national_blocks(conn, scopes["main"])) == []

    @pytest.mark.parametrize(
        ("column", "value", "message"),
        [
            ("schema", "pf-model-uncertainty-v0", "stored format"),
            ("national_json", "[1]", "national_json is not a JSON object"),
        ],
    )
    def test_national_blocks_refuse_unreadable_rows(
        self,
        path: Path,
        scopes: dict[str, OutputScope],
        column: str,
        value: str,
        message: str,
    ) -> None:
        election_id = save(path, scopes["main"], 1, payload(1))
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                f"UPDATE model_uncertainty SET {column} = ? WHERE election_id = ?",
                (value, election_id),
            )
            conn.commit()
            with pytest.raises(UncertaintyStoreError, match=message):
                list(iter_national_blocks(conn, scopes["main"]))

    def test_status_counts_the_scopes_runs_and_names_the_detailed_one(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        save(path, scopes["main"], 1, payload(1))
        latest = save(path, scopes["main"], 7, payload(7))
        save(path, scopes["main"], 3, payload(3))
        save(path, scopes["main"], 2)  # no summary: not counted
        save(path, scopes["other_type"], 19, payload(79))
        save(path, scopes["other_map"], 29, payload(89))

        assert uncertainty_status(path, scopes["main"]) == UncertaintyStatus(
            stored_runs=3,
            detail_run=UncertaintyRun(latest, "UNS 2026-10-07", date(2026, 10, 7)),
        )

    def test_status_names_the_latest_of_several_detailed_runs(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        # replace_output never leaves two, so make them by hand, plus a row on
        # a scope election whose name carries no date (not counted). The
        # expected run has the later date but the lower id.
        later = save(path, scopes["main"], 6, payload(6))
        earlier = save(path, scopes["main"], 1, payload(1))
        assert earlier > later
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "UPDATE model_uncertainty SET regions_json = '{}' "
                "WHERE election_id = ?",
                (earlier,),
            )
            undated = conn.execute(
                "INSERT INTO elections (map_id, year, name, type) "
                "VALUES (?, 2026, 'UNS draft', 'model_uns')",
                (scopes["main"].map_id,),
            ).lastrowid
            conn.execute(
                "INSERT INTO model_uncertainty "
                "(election_id, schema, national_json, seats_json) "
                "VALUES (?, ?, '{}', '{}')",
                (undated, UNCERTAINTY_SCHEMA),
            )
            conn.commit()

        assert uncertainty_status(path, scopes["main"]) == UncertaintyStatus(
            stored_runs=2,
            detail_run=UncertaintyRun(later, "UNS 2026-10-06", date(2026, 10, 6)),
        )

    def test_equal_dates_keep_detail_on_the_highest_id(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        # A second run on the same date can only come from outside
        # replace_output (which deletes every run of its date), e.g. a
        # suffixed name; the next save's prune must still pick one.
        first = save(path, scopes["main"], 5, payload(5))
        with closing(sqlite3.connect(path)) as conn:
            rerun = conn.execute(
                "INSERT INTO elections (map_id, year, name, type) "
                "VALUES (?, 2026, 'UNS 2026-10-05 rerun', 'model_uns')",
                (scopes["main"].map_id,),
            ).lastrowid
            conn.execute(
                "INSERT INTO model_uncertainty "
                "(election_id, schema, national_json, regions_json, seats_json) "
                "VALUES (?, ?, '{}', '{}', '{}')",
                (rerun, UNCERTAINTY_SCHEMA),
            )
            conn.commit()
        assert rerun is not None and rerun > first

        save(path, scopes["main"], 1)

        assert all_rows(path) == {
            first: (compact(payload(5).national), False, False),
            rerun: ("{}", True, True),
        }
        # The status breaks the same tie the same way.
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "UPDATE model_uncertainty SET seats_json = '{}' WHERE election_id = ?",
                (first,),
            )
            conn.commit()
        assert uncertainty_status(path, scopes["main"]) == UncertaintyStatus(
            stored_runs=2,
            detail_run=UncertaintyRun(
                rerun, "UNS 2026-10-05 rerun", date(2026, 10, 5)
            ),
        )

    def test_status_after_deleting_the_detailed_run(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        save(path, scopes["main"], 1, payload(1))
        save(path, scopes["main"], 2, payload(2))

        delete_outputs(path, scopes["main"], date(2026, 10, 2), date(2026, 10, 2))

        assert uncertainty_status(path, scopes["main"]) == UncertaintyStatus(1, None)

    def test_status_with_no_detail_left(
        self, path: Path, scopes: dict[str, OutputScope]
    ) -> None:
        save(path, scopes["main"], 9)
        save(path, scopes["main"], 4, payload(4))

        assert uncertainty_status(path, scopes["main"]) == UncertaintyStatus(1, None)

    def test_status_is_none_without_the_table(
        self, path: Path, scopes: dict[str, OutputScope], tmp_path: Path
    ) -> None:
        assert uncertainty_status(path, scopes["main"]) == UncertaintyStatus(0, None)
        drop_table(path)
        assert uncertainty_status(path, scopes["main"]) is None
        assert uncertainty_status(tmp_path / "absent.db", scopes["main"]) is None



def test_westminster_uns_scope() -> None:
    assert westminster_uns_scope(7) == OutputScope("model_uns", 7, "UNS")
