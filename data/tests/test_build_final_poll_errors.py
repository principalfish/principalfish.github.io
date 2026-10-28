"""Tests for ``scripts/build_final_poll_errors.py`` on a small synthetic workbook.

The workbook copies PollBase's layout: a header naming ``Fieldwork`` (day
text, with the start date in the unlabelled column after it), ``Published``,
``Polling``, ``Publisher`` and the party columns, a leader-ratings block that
reuses party names, and a ``Result`` row after the exit polls.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import pytest

from model_support.calibration import POLLBASE_URL, parse_final_poll_table
from scripts import build_final_poll_errors as builder
from scripts.build_final_poll_errors import (
    PollBaseError,
    build_election,
    build_elections,
    fieldwork_end,
    published_date,
    read_workbook,
    table_json,
)
from tests.uk_fixtures import build_workbook

POLLING_DAY = date(2019, 12, 12)


@pytest.fixture(autouse=True)
def _no_skip_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real skip list names PollBase rows the synthetic workbook lacks."""
    monkeypatch.setattr(builder, "SKIPPED_ROWS", {})

HEADER: tuple[object, ...] = (
    "Year", "Month", "Fieldwork", None, "Published", "Polling", "Publisher",
    "Con", None, "Lab", None, "LD", None, "Green", None, "UKIP", None,
    "Con lead", "Georgraphy", "Leader:", "Con", "Lab",
)  # fmt: skip


def _row(
    fieldwork: object,
    start: date | None,
    published: object,
    pollster: str,
    con: float | None,
    lab: float | None,
    ld: float | None,
    green: float | None = None,
    ukip: float | None = None,
    geography: str | None = None,
) -> tuple[object, ...]:
    start_cell = None if start is None else datetime(start.year, start.month, start.day)
    return (
        None, "Dec", fieldwork, start_cell, published, pollster, "Paper",
        con, None, lab, None, ld, None, green, None, ukip, None,
        None, geography, None, 99, 99,
    )  # fmt: skip


def _december(day: int) -> date:
    return date(2019, 12, day)


def _rows() -> list[tuple[object, ...]]:
    d = _december
    return [
        # Outside the 3-day window (ends 8 Dec).
        _row("6-8", d(6), 9, "Far", 40, 30, 10),
        # Alpha: two polls ending 11 Dec. The later-published one comes first
        # in the sheet, so only the publication tie-break picks it.
        _row("10-11", d(10), 12, "Alpha", 44, 33, 12, 3),
        _row("9-11", d(9), 11, "Alpha", 41, 36, 12, 3),
        # Theta: its later-ending poll comes first and was published first,
        # so only the fieldwork end picks it.
        _row("10-11", d(10), 11, "Theta", 44, 33, 12),
        _row("9-10", d(9), 12, "Theta", 40, 37, 13),
        # Beta: ends on polling day, started before it, published that day.
        _row("11-12", d(11), 12, "Beta", 43, 34, 11, 2, geography="UK"),
        # Gamma: an on-the-day poll (started on polling day).
        _row("12", d(12), 12, "Gamma", 50, 20, 10),
        # Delta: last poll never published; its earlier window poll counts.
        _row("9-10", d(9), 10, "Delta", 42, 35, 13, 4),
        _row("11", d(11), None, "Delta", 30, 30, 30),
        # Epsilon: released after the election.
        _row("10-11", d(10), datetime(2020, 4, 1), "Epsilon", 20, 50, 20),
        # Zeta: no LD share, so not a voting-intention row.
        _row("10-11", d(10), 11, "Zeta", 45, 30, None),
        # Eta: fieldwork across the month end, from November.
        _row("30-11", date(2019, 11, 30), 12, "Eta", 45, 32, 13, None, 5),
        (None, None, "Exit polls", None, None, "Ipsos", "BBC", 46, None, 32),
        ("", None, None, None, 12, "Result", None, 44.7, None, 32.9, None,
         11.8, None, 2.8),
    ]  # fmt: skip


def _workbook(tmp_path: Path, rows: Sequence[Sequence[object]] | None = None) -> Path:
    path = tmp_path / "PollBase-test.xlsx"
    book = build_workbook(
        {
            "Introduction": [("Welcome to PollBase",)],
            "17-19": [HEADER, *(_rows() if rows is None else rows)],
        }
    )
    book.save(path)
    return path


class TestFieldworkEnd:
    @pytest.mark.parametrize(
        ("text", "start", "expected"),
        [
            ("24-25", date(1992, 3, 24), date(1992, 3, 25)),
            ("31-1", date(2001, 5, 31), date(2001, 6, 1)),
            ("29-2", date(2005, 4, 29), date(2005, 5, 2)),
            ("c.6-9", date(1992, 4, 6), date(1992, 4, 9)),
            ("25-28?", date(1997, 4, 25), date(1997, 4, 28)),
            ("29-1 ?", date(2010, 4, 29), date(2010, 5, 1)),
            ("?-9", date(2001, 5, 7), date(2001, 5, 9)),
            ("10/12", date(1992, 3, 10), date(1992, 3, 12)),
            ("30/5-21/6", date(2024, 5, 30), date(2024, 6, 21)),
            ("10-1/7", date(2024, 6, 10), date(2024, 7, 1)),
            ("27-10", date(2019, 11, 27), date(2019, 12, 10)),
            (29, date(1997, 4, 29), date(1997, 4, 29)),
            (None, date(2001, 6, 4), date(2001, 6, 4)),
            ("  ", date(2001, 6, 4), date(2001, 6, 4)),
        ],
    )
    def test_reads_the_last_day(
        self, text: object, start: date, expected: date
    ) -> None:
        assert fieldwork_end(text, start) == expected

    @pytest.mark.parametrize("text", ["-28", "May", "1-2-3", 29.5, "0", "32", True])
    def test_rejects_unreadable_text(self, text: object) -> None:
        with pytest.raises(PollBaseError):
            fieldwork_end(text, date(2019, 12, 1))

    def test_rejects_a_dated_end_before_the_start(self) -> None:
        with pytest.raises(PollBaseError):
            fieldwork_end("1-1/5", date(2024, 6, 10))


class TestPublishedDate:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (9, date(1992, 4, 9)),
            (1, date(1992, 5, 1)),
            ("26/27", date(1992, 4, 26)),
            ("2-3", date(1992, 5, 2)),
            (datetime(2025, 4, 14), date(2025, 4, 14)),
            (None, None),
            (" ", None),
        ],
    )
    def test_reads_forward_from_the_start(
        self, value: object, expected: date | None
    ) -> None:
        assert published_date(value, date(1992, 4, 8)) == expected

    @pytest.mark.parametrize("value", ["soon", 0, 7.5, False])
    def test_rejects_unreadable_values(self, value: object) -> None:
        with pytest.raises(PollBaseError):
            published_date(value, date(1992, 4, 8))


class TestSelection:
    def _election(self, tmp_path: Path) -> builder.ElectionPolls:
        path = _workbook(tmp_path)
        return build_election(read_workbook(path, (POLLING_DAY,)), POLLING_DAY)

    def test_final_poll_per_pollster(self, tmp_path: Path) -> None:
        election = self._election(tmp_path)
        finals = {(p.pollster, p.fieldwork) for p in election.final_polls}
        assert finals == {
            ("Alpha", "10-11"),
            ("Beta", "11-12"),
            ("Delta", "9-10"),
            ("Eta", "30-11"),
            ("Theta", "10-11"),
        }

    def test_exclusion_reasons(self, tmp_path: Path) -> None:
        election = self._election(tmp_path)
        reasons = {
            (item.poll.pollster, item.poll.fieldwork): item.reason
            for item in election.excluded
        }
        assert reasons == {
            ("Alpha", "9-11"): "superseded by a later poll from the same pollster",
            ("Theta", "9-10"): "superseded by a later poll from the same pollster",
            ("Delta", "11"): "no publication date",
            ("Epsilon", "10-11"): "published after polling day (2020-04-01)",
        }

    def test_averages_over_reporting_pollsters(self, tmp_path: Path) -> None:
        election = self._election(tmp_path)
        assert election.parties == ("con", "lab", "ld", "green")
        assert election.averages["con"] == pytest.approx((44 + 43 + 42 + 45 + 44) / 5)
        assert election.averages["green"] == pytest.approx((3 + 2 + 4) / 3)
        assert election.reporting == {"con": 5, "lab": 5, "ld": 5, "green": 3}

    def test_a_party_without_a_result_is_left_out(self, tmp_path: Path) -> None:
        # Eta reports UKIP but the Result row has none.
        election = self._election(tmp_path)
        assert "ukip" not in election.parties
        assert election.result.shares == {
            "con": 44.7,
            "lab": 32.9,
            "ld": 11.8,
            "green": 2.8,
        }

    def test_geography_is_recorded(self, tmp_path: Path) -> None:
        election = self._election(tmp_path)
        beta = next(p for p in election.final_polls if p.pollster == "Beta")
        assert beta.geography == "UK"

    def test_ratings_block_columns_are_ignored(self, tmp_path: Path) -> None:
        # The ratings block's "Con"/"Lab" hold 99 in every row.
        election = self._election(tmp_path)
        assert all(p.shares["con"] != 99 for p in election.final_polls)

    def test_missing_result_is_an_error(self, tmp_path: Path) -> None:
        path = _workbook(tmp_path, _rows()[:-1])
        with pytest.raises(PollBaseError, match="Result row"):
            build_election(read_workbook(path, (POLLING_DAY,)), POLLING_DAY)

    def test_unreadable_cell_names_its_row(self, tmp_path: Path) -> None:
        rows = _rows()
        rows.insert(1, _row("soon", date(2019, 12, 10), 11, "Bad", 40, 30, 10))
        path = _workbook(tmp_path, rows)
        with pytest.raises(PollBaseError, match="row 3"):
            read_workbook(path, (POLLING_DAY,))

    def test_publication_before_the_fieldwork_end_is_an_error(
        self, tmp_path: Path
    ) -> None:
        rows = _rows()
        rows.insert(0, _row("8-11", date(2019, 12, 8), 10, "Iota", 40, 30, 10))
        path = _workbook(tmp_path, rows)
        with pytest.raises(PollBaseError, match="before the fieldwork end"):
            read_workbook(path, (POLLING_DAY,))

    def test_listed_rows_are_skipped_and_recorded(self, tmp_path: Path) -> None:
        rows = _rows()
        rows.insert(0, _row("8-11", date(2019, 12, 8), 10, "Iota", 40, 30, 10))
        path = _workbook(tmp_path, rows)
        skips = {("17-19", 2, "Iota", "8-11"): "dates disagree"}
        workbook = read_workbook(path, (POLLING_DAY,), skips)
        assert workbook.skipped == skips
        assert "Iota" not in {poll.pollster for poll in workbook.polls}
        table = table_json([], path.name, "abc", workbook.skipped)
        assert table["skipped_rows"] == [
            {
                "sheet": "17-19",
                "row": 2,
                "pollster": "Iota",
                "fieldwork": "8-11",
                "reason": "dates disagree",
            }
        ]

    def test_a_listed_row_that_is_missing_is_an_error(self, tmp_path: Path) -> None:
        path = _workbook(tmp_path)
        with pytest.raises(PollBaseError, match="not found"):
            read_workbook(path, (POLLING_DAY,), {("17-19", 99, "Gone", "1"): "x"})

    def test_rows_far_from_any_election_are_not_parsed(self, tmp_path: Path) -> None:
        rows = _rows()
        rows.insert(0, _row("soon", date(2019, 6, 1), 2, "Old", 40, 30, 10))
        path = _workbook(tmp_path, rows)
        assert build_election(read_workbook(path, (POLLING_DAY,)), POLLING_DAY)


class TestOutput:
    def test_table_passes_the_calibration_loader(self, tmp_path: Path) -> None:
        path = _workbook(tmp_path)
        elections = build_elections(read_workbook(path, (POLLING_DAY,)), (POLLING_DAY,))
        table = table_json(elections, path.name, "abc")
        parsed = parse_final_poll_table(json.loads(json.dumps(table)))
        assert parsed.credit["url"] == POLLBASE_URL
        assert "Mark Pack" in parsed.credit["text"]
        (election,) = parsed.elections
        assert election.source_url == POLLBASE_URL
        assert election.final_polls == 5

    def test_dry_run_writes_nothing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(builder, "POLLING_DAYS", (POLLING_DAY,))
        output = tmp_path / "out.json"
        workbook = str(_workbook(tmp_path))
        code = builder.main([workbook, "--output", str(output), "--dry-run"])
        assert code == 0
        assert not output.exists()
        lines = capsys.readouterr().out.splitlines()
        assert "  con     A  43.60  R  44.70  (5 pollsters)" in lines

    def test_writes_the_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(builder, "POLLING_DAYS", (POLLING_DAY,))
        output = tmp_path / "out.json"
        assert builder.main([str(_workbook(tmp_path)), "--output", str(output)]) == 0
        table = json.loads(output.read_text(encoding="utf-8"))
        assert [e["polling_day"] for e in table["elections"]] == ["2019-12-12"]
        assert table["credit"]["edition"] == "PollBase-test.xlsx"

    @pytest.mark.parametrize("damage", ["not_a_zip", "truncated", "no_workbook"])
    def test_damaged_workbook_is_a_clean_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        damage: str,
    ) -> None:
        monkeypatch.setattr(builder, "POLLING_DAYS", (POLLING_DAY,))
        bad = tmp_path / "bad.xlsx"
        good = _workbook(tmp_path).read_bytes()
        if damage == "not_a_zip":
            bad.write_bytes(b"not a workbook")
        elif damage == "truncated":
            bad.write_bytes(good[: len(good) // 2])
        else:
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("readme.txt", "no workbook here")
        output = tmp_path / "out.json"
        assert builder.main([str(bad), "--output", str(output)]) == 1
        assert not output.exists()
        assert capsys.readouterr().out.startswith("error: ")

    def test_corrupt_sheet_data_is_a_clean_error(self, tmp_path: Path) -> None:
        # A sheet member whose compressed bytes are damaged: openpyxl's
        # read-only mode only decompresses it while iterating the rows.
        path = _workbook(tmp_path)
        damaged = tmp_path / "damaged.xlsx"
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(damaged, "w") as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename.endswith("sheet2.xml"):
                    data = data[: len(data) // 2]
                target.writestr(item, data)
        with pytest.raises(PollBaseError, match="not a readable workbook"):
            read_workbook(damaged, (POLLING_DAY,))

    def test_write_is_atomic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(builder, "POLLING_DAYS", (POLLING_DAY,))
        output = tmp_path / "out" / "table.json"
        output.parent.mkdir()
        output.write_text("previous\n", encoding="utf-8")

        def fail(outputs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(builder, "write_text_files", fail)
        assert builder.main([str(_workbook(tmp_path)), "--output", str(output)]) == 1
        assert output.read_text(encoding="utf-8") == "previous\n"
