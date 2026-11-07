"""Tests for the NYT results CLI and its diff report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.us_results.fetch as fetch_module
from db import Database
from scripts.us_results import cli
from scripts.us_results.cli import SeatResult, diff_against_existing, main
from scripts.us_results.sources import source_for

FIXTURES = Path(__file__).parent / "fixtures" / "nyt"


def _seed_us_parties(db: Database) -> None:
    for name in ("Democratic", "Republican", "Libertarian", "US Green", "Independent", "Others"):
        db.add_party(name)


@pytest.fixture()
def nyt(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Serve fixture files in place of NYT; returns the URL -> body table."""
    bodies = {
        str(source.url): (FIXTURES / fixture).read_bytes()
        for source, fixture in (
            (source_for(2026, "house"), "schema-a.json"),
            (source_for(2026, "senate"), "schema-a.json"),
            (source_for(2020, "senate"), "schema-b.json"),
            (source_for(2020, "senate", "runoff"), "schema-b-runoff.json"),
        )
    }

    def fake_download(url: str) -> bytes:
        return bodies[url]

    monkeypatch.setattr(fetch_module, "_download", fake_download)
    return bodies


def _files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.is_file())


def _args(command: str, year: int, chamber: str, tmp_path: Path, *extra: str) -> list[str]:
    return [
        command,
        "--year", str(year),
        "--chamber", chamber,
        "--cache-dir", str(tmp_path / "cache"),
        "--out-dir", str(tmp_path / "out"),
        *extra,
    ]  # fmt: skip


def _db_counts(db: Database) -> tuple[int, int]:
    elections = [
        election
        for map_id in (21, 23, 24, 25)
        for election in db.get_elections_for_map(map_id)
    ]
    votes = sum(len(db.get_votes_for_election(election.id)) for election in elections)
    return len(elections), votes


# --- commands ------------------------------------------------------------------


def test_fetch_caches_general_and_skips_pending_runoff(
    nyt: dict[str, bytes],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(_args("fetch", 2026, "house", tmp_path)) == 0

    names = [path.name for path in _files(tmp_path / "cache")]
    assert "house-general-latest.json" in names
    assert len(names) == 2
    assert "skipping pending runoff 2026/house" in capsys.readouterr().out


def test_fetch_gets_runoffs_too(nyt: dict[str, bytes], tmp_path: Path) -> None:
    assert main(_args("fetch", 2020, "senate", tmp_path)) == 0

    names = {path.name for path in _files(tmp_path / "cache")}
    assert {"senate-general-latest.json", "senate-runoff-latest.json"} <= names


def test_fetch_dry_run_downloads_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def no_download(url: str) -> bytes:
        raise AssertionError(f"downloaded {url}")

    monkeypatch.setattr(fetch_module, "_download", no_download)

    assert main(_args("fetch", 2020, "senate", tmp_path, "--dry-run")) == 0

    assert not (tmp_path / "cache").exists()
    out = capsys.readouterr().out
    assert f"would fetch general 2020/senate: {source_for(2020, 'senate').url}" in out
    assert "would fetch runoff 2020/senate" in out


def test_all_converts_and_imports(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)

    assert main(_args("all", 2020, "senate", tmp_path), db=db) == 0

    out_dir = tmp_path / "out"
    assert sorted(path.name for path in out_dir.iterdir()) == [
        "senate-2020-special.json",
        "senate-2020.json",
    ]
    regular = db.get_election_by_name("2020 US Senate Election")
    special = db.get_election_by_name("2020 US Senate Special Election")
    assert regular is not None and special is not None
    assert special.parent_election_id == regular.id
    out = capsys.readouterr().out
    assert "senate-2020.json: 3 seats, 3 called" in out
    assert "imported 2020 US Senate Special Election" in out


def test_convert_then_import_then_refresh_keeps_ids(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    assert main(_args("fetch", 2026, "house", tmp_path)) == 0
    assert main(_args("convert", 2026, "house", tmp_path), db=db) == 0
    assert (tmp_path / "out" / "house-2026.json").exists()
    assert main(_args("import", 2026, "house", tmp_path), db=db) == 0
    first = db.get_election_by_name("2026 US House Election")
    assert first is not None

    # A second import needs --refresh.
    assert main(_args("import", 2026, "house", tmp_path), db=db) == 1
    assert "pass --refresh" in capsys.readouterr().err

    assert main(_args("import", 2026, "house", tmp_path, "--refresh"), db=db) == 0
    again = db.get_election_by_name("2026 US House Election")
    assert again is not None and again.id == first.id
    assert len(db.get_seat_statuses(first.id)) == 6


def test_all_without_refresh_refuses_before_writing(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
) -> None:
    _seed_us_parties(db)
    assert main(_args("all", 2026, "house", tmp_path), db=db) == 0
    out_file = tmp_path / "out" / "house-2026.json"
    written = out_file.read_text(encoding="utf-8")
    out_file.write_text("{}", encoding="utf-8")

    assert main(_args("all", 2026, "house", tmp_path), db=db) == 1

    assert out_file.read_text(encoding="utf-8") == "{}"
    assert written != "{}"


def test_dry_runs_write_nothing(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)

    # all --dry-run downloads into a throwaway folder, not the cache.
    assert main(_args("all", 2026, "house", tmp_path, "--dry-run"), db=db) == 0
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "out").exists()
    assert _db_counts(db) == (0, 0)
    assert "dry run: nothing written" in capsys.readouterr().out

    assert main(_args("fetch", 2026, "house", tmp_path)) == 0
    cached = _files(tmp_path / "cache")
    assert main(_args("convert", 2026, "house", tmp_path, "--dry-run"), db=db) == 0
    assert main(_args("all", 2026, "house", tmp_path, "--dry-run", "--offline"), db=db) == 0
    assert not (tmp_path / "out").exists()
    assert _files(tmp_path / "cache") == cached

    assert main(_args("convert", 2026, "house", tmp_path), db=db) == 0
    out_files = {path: path.read_bytes() for path in _files(tmp_path / "out")}
    assert main(_args("import", 2026, "house", tmp_path, "--dry-run"), db=db) == 0
    assert _db_counts(db) == (0, 0)
    assert db.get_map(24) is None
    assert {path: path.read_bytes() for path in _files(tmp_path / "out")} == out_files
    assert "dry run: nothing imported" in capsys.readouterr().out


def test_import_dry_run_reports_changes_against_the_db(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    assert main(_args("all", 2026, "house", tmp_path), db=db) == 0
    path = tmp_path / "out" / "house-2026.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["AZ-06"]["seatInfo"].update({"current": "democrat", "called": True, "pct": 99.0})
    data["AZ-06"]["partyInfo"]["democrat"]["total"] += 5000
    path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()

    assert main(_args("import", 2026, "house", tmp_path, "--dry-run"), db=db) == 0

    out = capsys.readouterr().out
    assert "already exists: a real run needs --refresh" in out
    assert "vs database: 6 -> 6 seats" in out
    assert "newly called: 1\n      AZ-06" in out
    assert "% counted changes: 1" in out
    assert "AZ-06: 80.5% -> 99%" in out
    assert "AZ-06: 181,000 -> 186,000 (largest: democrat +5,000)" in out


def test_convert_compares_with_the_committed_file(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "senate-2020.json").write_text(json.dumps({
        "Alabama": {"seatInfo": {"current": "democrat"},
                    "partyInfo": {"democrat": {"total": 1, "name": "D"}}},
        "Maine": {"seatInfo": {"current": "republican"},
                  "partyInfo": {"republican": {"total": 1, "name": "R"}}},
    }), encoding="utf-8")

    assert main(_args("all", 2020, "senate", tmp_path, "--dry-run"), db=db) == 0

    out = capsys.readouterr().out
    assert "vs committed senate-2020.json: 2 -> 3 seats (+2 added, -1 removed)" in out
    assert "removed: Maine" in out
    assert "Alabama: democrat -> republican" in out


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"[]", "error: "),
        (json.dumps({"races": "nope"}).encode(), "error: "),
    ],
)
def test_bad_files_exit_non_zero(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    body: bytes,
    message: str,
) -> None:
    monkeypatch.setattr(fetch_module, "_download", lambda url: body)

    assert main(_args("all", 2026, "house", tmp_path, "--dry-run"), db=db) == 1

    assert capsys.readouterr().err.startswith(message)


def test_offline_without_cache_exits_non_zero(
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(_args("convert", 2026, "house", tmp_path), db=db) == 1
    assert "error:" in capsys.readouterr().err


def test_import_without_converted_file_exits_non_zero(
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(_args("import", 2026, "house", tmp_path), db=db) == 1
    assert "run convert first" in capsys.readouterr().err


# --- diff ----------------------------------------------------------------------


def _seat(current: str | None, totals: dict[str, int], **status: Any) -> dict[str, Any]:
    return {
        "seatInfo": {"current": current, **status},
        "partyInfo": {party: {"total": total, "name": party} for party, total in totals.items()},
    }


def test_diff_against_file_and_db() -> None:
    new = {
        "meta": {"year": 2026},
        "A-01": _seat("democrat", {"democrat": 60, "republican": 40}, called=True, pct=100.0),
        "A-02": _seat("republican", {"democrat": 40, "republican": 60}, called=True, pct=95.0),
        "A-03": _seat(None, {"democrat": 50, "republican": 49}, called=False, pct=70.0),
        "A-04": _seat("democrat", {"democrat": 10}, called=True, pct=None),
    }
    old_file = {
        "A-01": _seat("republican", {"democrat": 40, "republican": 60}),
        "A-02": _seat("republican", {"democrat": 40, "republican": 60}),
        "A-03": _seat("democrat", {"democrat": 50, "republican": 49}),
        "A-05": _seat("democrat", {"democrat": 1}),
    }
    db_snapshot = {
        "A-01": SeatResult(winner=None, called=False, pct=80.0, totals={"democrat": 50.0, "republican": 40.0}),
        "A-02": SeatResult(winner="republican", called=True, pct=95.0, totals={"democrat": 40.0, "republican": 60.0}),
        "A-03": SeatResult(winner=None, called=False, pct=60.0, totals={"democrat": 45.0, "republican": 49.0}),
        "A-04": SeatResult(winner="democrat", called=True, pct=None, totals={"democrat": 10.0}),
    }

    report = diff_against_existing(new, old_file, db_snapshot)

    vs_file, vs_db = report.comparisons
    assert vs_file.label == "committed file"
    assert vs_file.added == ("A-04",)
    assert vs_file.removed == ("A-05",)
    assert [(c.seat, c.old, c.new) for c in vs_file.winner_changes] == [("A-01", "republican", "democrat")]
    assert vs_file.newly_uncalled == ("A-03",)
    assert vs_file.newly_called == ()
    # The 538-style file has no % counted, so nothing to compare.
    assert vs_file.pct_changes == ()
    assert [d.seat for d in vs_file.vote_deltas] == ["A-01"]
    assert vs_file.vote_deltas[0].magnitude == 40

    assert vs_db.label == "database"
    assert (vs_db.added, vs_db.removed) == ((), ())
    assert vs_db.winner_changes == ()
    assert vs_db.newly_called == ("A-01",)
    assert [(c.seat, c.old, c.new) for c in vs_db.pct_changes] == [("A-01", 80.0, 100.0), ("A-03", 60.0, 70.0)]
    assert [(d.seat, d.party_deltas) for d in vs_db.vote_deltas] == [
        ("A-01", (("democrat", 10.0),)),
        ("A-03", (("democrat", 5.0),)),
    ]

    summary = report.summary(top_n=1)
    assert "vs committed file: 4 -> 4 seats (+1 added, -1 removed)" in summary
    assert "A-01: republican -> democrat" in summary
    assert "A-01: 90 -> 100 (largest: democrat +10)" in summary
    # top_n caps the listed rows, not the counts.
    assert "A-03: 94 -> 99" not in summary
    assert "% counted changes: 2" in summary


def test_diff_lists_backfilled_and_nominal_seats() -> None:
    new = {
        "A-01": _seat("democrat", {"democrat": 900}, called=True, votesSource="538"),
        "A-02": _seat("democrat", {"democrat": 100}, called=True, votesSource="nominal"),
        "A-03": _seat("democrat", {"democrat": 5}, called=True, votesSource="nyt"),
    }

    report = diff_against_existing(new, None, None)

    assert (report.backfilled, report.nominal) == (("A-01",), ("A-02",))
    summary = report.summary()
    assert "unopposed seats with 538 totals: 1 (A-01)" in summary
    assert "unopposed seats with nominal totals: 1 (A-02)" in summary


def test_convert_backfills_from_the_file_in_out_dir(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "house-2026.json").write_text(json.dumps({
        "FL-10": {"seatInfo": {"current": "democrat"},
                  "partyInfo": {"democrat": {"total": 180000, "name": "Maxwell Frost"}}},
    }), encoding="utf-8")
    assert main(_args("fetch", 2026, "house", tmp_path)) == 0

    assert main(_args("convert", 2026, "house", tmp_path), db=db) == 0
    assert "unopposed seats with 538 totals: 1 (FL-10)" in capsys.readouterr().out
    # Converting again over the file just written keeps the totals.
    assert main(_args("convert", 2026, "house", tmp_path), db=db) == 0

    data = json.loads((out_dir / "house-2026.json").read_text(encoding="utf-8"))
    assert data["FL-10"]["seatInfo"]["votesSource"] == "538"
    assert data["FL-10"]["partyInfo"]["democrat"]["total"] == 180000


def test_diff_with_nothing_to_compare() -> None:
    report = diff_against_existing({"A-01": _seat("democrat", {"democrat": 1})}, None, None)
    assert report.comparisons == ()
    assert report.summary().startswith("no earlier file")


def test_snapshot_reads_db_rows(nyt: dict[str, bytes], db: Database, tmp_path: Path) -> None:
    _seed_us_parties(db)
    assert main(_args("all", 2026, "house", tmp_path), db=db) == 0

    snapshot = cli.snapshot_election(db, "2026 US House Election")

    assert snapshot is not None
    assert snapshot["AZ-06"] == SeatResult(
        winner=None, called=False, pct=80.5, totals={"democrat": 91000.0, "republican": 90000.0},
    )
    assert snapshot["TX-28"].winner == "democrat"
    assert cli.snapshot_election(db, "1999 US House Election") is None
    data = json.loads((tmp_path / "out" / "house-2026.json").read_text(encoding="utf-8"))
    no_change = diff_against_existing(data, None, snapshot).comparisons[0]
    assert (no_change.winner_changes, no_change.newly_called, no_change.vote_deltas) == ((), (), ())


def test_importer_errors_exit_non_zero(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The seed importers' SystemExit becomes an error line, not an escaping exception."""
    assert main(_args("fetch", 2026, "house", tmp_path)) == 0
    assert main(_args("convert", 2026, "house", tmp_path), db=db) == 0
    capsys.readouterr()

    # No parties seeded, so the importer refuses.
    assert main(_args("import", 2026, "house", tmp_path), db=db) == 1

    assert capsys.readouterr().err.startswith(
        "error: house-2026.json: Party 'Democratic' not found",
    )


def _stale_special(tmp_path: Path) -> Path:
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    stale = out_dir / "senate-2026-special.json"
    stale.write_text(json.dumps({"Texas": _seat("democrat", {"democrat": 1})}), encoding="utf-8")
    return stale


def test_stale_special_file_is_refused_by_import(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    assert main(_args("fetch", 2026, "senate", tmp_path)) == 0
    assert main(_args("convert", 2026, "senate", tmp_path), db=db) == 0
    # Drop the specials from the conversion, as if NYT had none this time.
    main_path = tmp_path / "out" / "senate-2026.json"
    data = json.loads(main_path.read_text(encoding="utf-8"))
    data["meta"]["special_file"] = None
    main_path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()

    assert main(_args("import", 2026, "senate", tmp_path), db=db) == 1

    err = capsys.readouterr().err
    assert "senate-2026-special.json is left over" in err
    assert "remove it" in err
    assert (tmp_path / "out" / "senate-2026-special.json").exists()
    assert db.get_election_by_name("2026 US Senate Election") is None


def test_missing_named_special_file_is_refused(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    assert main(_args("fetch", 2026, "senate", tmp_path)) == 0
    assert main(_args("convert", 2026, "senate", tmp_path), db=db) == 0
    (tmp_path / "out" / "senate-2026-special.json").unlink()
    capsys.readouterr()

    assert main(_args("import", 2026, "senate", tmp_path), db=db) == 1
    assert "which is missing" in capsys.readouterr().err


def test_stale_special_file_is_refused_by_all_and_convert(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    # A Senate specials file never matters to a House run.
    stale = _stale_special(tmp_path)
    assert main(_args("all", 2026, "house", tmp_path), db=db) == 0

    # Without Minnesota's same-day special, 2026 Senate has no specials file.
    fixture = json.loads((FIXTURES / "schema-a.json").read_text(encoding="utf-8"))
    fixture["races"] = [race for race in fixture["races"] if race["nyt_id"] != "MN-S_W-S-2026-11-03"]
    body = json.dumps(fixture).encode()
    nyt[str(source_for(2026, "senate").url)] = body
    capsys.readouterr()

    assert main(_args("all", 2026, "senate", tmp_path), db=db) == 1
    assert "left over" in capsys.readouterr().err
    assert not (tmp_path / "out" / "senate-2026.json").exists()
    assert db.get_election_by_name("2026 US Senate Election") is None

    # convert refuses too; a dry run only warns.
    assert main(_args("convert", 2026, "senate", tmp_path), db=db) == 1
    assert "left over" in capsys.readouterr().err
    assert not (tmp_path / "out" / "senate-2026.json").exists()
    assert main(_args("convert", 2026, "senate", tmp_path, "--dry-run"), db=db) == 0
    warning = "warning: senate-2026-special.json exists but this conversion has no specials"
    assert warning in capsys.readouterr().out
    assert stale.exists()


def test_house_all_ignores_a_senate_special(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
) -> None:
    _seed_us_parties(db)
    senate_map = db.add_map("US Senate 2024", parliament="us_senate")
    from models import ElectionType

    db.add_election(
        senate_map.id, 2026, "2026 US Senate Special Election", ElectionType.us_senate,
    )

    assert main(_args("all", 2026, "house", tmp_path), db=db) == 0
    assert db.get_election_by_name("2026 US House Election") is not None


def test_read_json_refuses_a_non_object(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(cli.CliError, match="does not hold a JSON object"):
        cli._read_json(path)


def _converted_house(nyt: dict[str, bytes], db: Database, tmp_path: Path) -> Path:
    assert main(_args("fetch", 2026, "house", tmp_path)) == 0
    assert main(_args("convert", 2026, "house", tmp_path), db=db) == 0
    return tmp_path / "out" / "house-2026.json"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["TX-28"]["seatInfo"].update(pct="99"), "pct must be a number 0-100 or null, got '99'"),
        (lambda d: d["TX-28"]["seatInfo"].update(pct=101), "got 101"),
        (lambda d: d["TX-28"]["partyInfo"]["democrat"].update(total="x"), "democrat: total must be a number >= 0"),
        (lambda d: d["TX-28"]["partyInfo"]["democrat"].pop("name"), "democrat: name must be a string"),
        (lambda d: d.update({"ZZ-01": d.pop("TX-28")}), "'ZZ-01': not a known state or district key"),
        (lambda d: d.update({"Texas": d.pop("TX-28")}), "'Texas': not a known state or district key"),
        (lambda d: d["TX-28"]["seatInfo"].update(called="yes"), "called/sameParty must be true or false"),
        (lambda d: d["TX-28"]["seatInfo"].update(round="third"), "unknown round 'third'"),
        (lambda d: d["TX-28"]["seatInfo"].update(current="whig"), "current 'whig' must be a party key"),
        (lambda d: d["TX-28"].update(seatInfo=[]), "needs seatInfo and partyInfo"),
        (lambda d: d.update(meta=[]), "'meta' must be an object"),
        (lambda d: d["meta"].update(special_file=3), "special_file must be"),
    ],
)
def test_malformed_files_are_refused_with_an_error_line(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutate: Any,
    message: str,
) -> None:
    _seed_us_parties(db)
    path = _converted_house(nyt, db, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()

    # import refuses it, and so does convert (it reads the file it would replace).
    assert main(_args("import", 2026, "house", tmp_path), db=db) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: house-2026.json: ")
    assert message in err
    assert main(_args("convert", 2026, "house", tmp_path, "--dry-run"), db=db) == 1
    assert message in capsys.readouterr().err
    assert db.get_map(24) is None


def test_senate_file_with_a_bad_state_is_refused(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    assert main(_args("fetch", 2026, "senate", tmp_path)) == 0
    assert main(_args("convert", 2026, "senate", tmp_path), db=db) == 0
    path = tmp_path / "out" / "senate-2026.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["Atlantis"] = data.pop("Ohio")
    path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()

    assert main(_args("import", 2026, "senate", tmp_path), db=db) == 1
    assert "'Atlantis': not a known state or district key" in capsys.readouterr().err


def test_invalid_json_is_refused(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _converted_house(nyt, db, tmp_path)
    path.write_text("{not json", encoding="utf-8")
    capsys.readouterr()

    assert main(_args("import", 2026, "house", tmp_path), db=db) == 1
    assert "is not valid JSON" in capsys.readouterr().err
    with pytest.raises(cli.CliError, match="is not valid JSON"):
        cli._read_json(path)


def test_unexpected_data_errors_become_error_lines(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    _converted_house(nyt, db, tmp_path)
    capsys.readouterr()

    def broken_import(*args: object, **kwargs: object) -> int:
        raise KeyError("name")

    monkeypatch.setattr(cli, "import_house", broken_import)
    assert main(_args("import", 2026, "house", tmp_path), db=db) == 1
    assert capsys.readouterr().err.startswith("error: house-2026.json is malformed: KeyError")

    def broken_convert(*args: object, **kwargs: object) -> object:
        raise ValueError("bad")

    monkeypatch.setattr(cli, "convert_cycle", broken_convert)
    assert main(_args("convert", 2026, "house", tmp_path), db=db) == 1
    assert capsys.readouterr().err.startswith("error: could not convert 2026 house")


def test_special_file_named_differently_is_refused(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    assert main(_args("fetch", 2026, "senate", tmp_path)) == 0
    assert main(_args("convert", 2026, "senate", tmp_path), db=db) == 0
    path = tmp_path / "out" / "senate-2026.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["meta"]["special_file"] = "senate-2024-special.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()

    assert main(_args("import", 2026, "senate", tmp_path), db=db) == 1
    assert "'senate-2024-special.json' is not 'senate-2026-special.json'" in (
        capsys.readouterr().err
    )


def _senate_2026_converted(nyt: dict[str, bytes], db: Database, tmp_path: Path) -> Path:
    assert main(_args("fetch", 2026, "senate", tmp_path)) == 0
    assert main(_args("convert", 2026, "senate", tmp_path), db=db) == 0
    return tmp_path / "out"


@pytest.mark.parametrize(
    "breakage",
    [
        {"election_date": "3 Nov 2026"},
        {"source_note": 5},
        {"map_id": "23"},
    ],
)
def test_bad_specials_file_stops_the_whole_import(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    breakage: dict[str, object],
) -> None:
    _seed_us_parties(db)
    out_dir = _senate_2026_converted(nyt, db, tmp_path)
    assert main(_args("import", 2026, "senate", tmp_path), db=db) == 0
    regular = db.get_election_by_name("2026 US Senate Election")
    assert regular is not None
    votes_before = [(v.seat_id, v.vote_total) for v in db.get_votes_for_election(regular.id)]

    main_path = out_dir / "senate-2026.json"
    main_data = json.loads(main_path.read_text(encoding="utf-8"))
    main_data["Ohio"]["partyInfo"]["democrat"]["total"] += 1000
    main_path.write_text(json.dumps(main_data), encoding="utf-8")
    special_path = out_dir / "senate-2026-special.json"
    special = json.loads(special_path.read_text(encoding="utf-8"))
    special["meta"].update(breakage)
    special_path.write_text(json.dumps(special), encoding="utf-8")
    capsys.readouterr()

    assert main(_args("import", 2026, "senate", tmp_path, "--refresh"), db=db) == 1

    assert capsys.readouterr().err.startswith("error: senate-2026-special.json: ")
    after = [(v.seat_id, v.vote_total) for v in db.get_votes_for_election(regular.id)]
    assert after == votes_before


def test_bad_specials_file_on_a_first_import_imports_nothing(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
) -> None:
    _seed_us_parties(db)
    out_dir = _senate_2026_converted(nyt, db, tmp_path)
    special_path = out_dir / "senate-2026-special.json"
    special = json.loads(special_path.read_text(encoding="utf-8"))
    special["meta"]["election_date"] = "soon"
    special_path.write_text(json.dumps(special), encoding="utf-8")

    assert main(_args("import", 2026, "senate", tmp_path), db=db) == 1

    assert _db_counts(db) == (0, 0)
    assert db.get_map(23) is None


def test_stale_special_refusal_mentions_a_lingering_db_special(
    nyt: dict[str, bytes],
    db: Database,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_us_parties(db)
    out_dir = _senate_2026_converted(nyt, db, tmp_path)
    assert main(_args("import", 2026, "senate", tmp_path), db=db) == 0
    main_path = out_dir / "senate-2026.json"
    data = json.loads(main_path.read_text(encoding="utf-8"))
    data["meta"]["special_file"] = None
    main_path.write_text(json.dumps(data), encoding="utf-8")
    capsys.readouterr()

    assert main(_args("import", 2026, "senate", tmp_path, "--refresh"), db=db) == 1
    err = capsys.readouterr().err
    assert "left over" in err
    assert "the database still holds '2026 US Senate Special Election'" in err

    # With the stale file removed, the import goes ahead but still warns.
    (out_dir / "senate-2026-special.json").unlink()
    assert main(_args("import", 2026, "senate", tmp_path, "--refresh"), db=db) == 0
    out = capsys.readouterr().out
    assert "warning: the database still holds '2026 US Senate Special Election'" in out
