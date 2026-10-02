"""Tests for the Westminster general-election importer.

The fixtures use synthetic election JSON and the temporary database supplied by
``conftest.py``. No election files, network resources, or live database are used.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

from db import Database
from models import ElectionType, Map, Party


class GeneralElectionImporter(Protocol):
    __file__: str
    PARTY_KEY_TO_NAME: dict[str, str]
    FILES_DIR: Path
    ELECTION_SPECS: tuple[object, ...]

    def normalize_name(self, value: str) -> str: ...

    def normalize_party_key(self, party_key: str) -> str: ...

    def humanize_party_name(self, party_key: str) -> str: ...

    def choose_map(
        self,
        db: Database,
        map_name: str | None,
        map_candidates: tuple[str, ...],
        label: str,
    ) -> Map: ...

    def ensure_party(
        self, db: Database, cache: dict[str, Party], party_key: str
    ) -> Party: ...

    def pick_winner_key(
        self, current_key: str, party_info: dict[str, object]
    ) -> str: ...

    def main(self) -> None: ...


def _load() -> GeneralElectionImporter:
    path = (
        Path(__file__).resolve().parents[1]
        / "old_data/scripts/westminster/import_general_elections.py"
    )
    name = "test_westminster_general_elections_module"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(GeneralElectionImporter, module)


importer = _load()

_ELECTION_DATA: dict[str, object] = {
    "Alpha": {
        "seatInfo": {"current": "labour", "electorate": 1000},
        "partyInfo": {
            "labour": {"total": 600, "name": "Alice Candidate"},
            "conservative": {"total": 400, "name": "Bob Candidate"},
            "independent": "not a candidate record",
        },
    },
    "Beta & Gamma": {
        "seatInfo": {"current": "conservative", "electorate": 900},
        "partyInfo": {"conservative": {"total": 500}},
    },
    "Unknown Seat": {
        "seatInfo": {},
        "partyInfo": {},
    },
}


def _write_election(path: Path) -> None:
    path.write_text(json.dumps(_ELECTION_DATA), encoding="utf-8")


def _setup_main(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, int, int]:
    files_dir = tmp_path / "westminster"
    files_dir.mkdir()
    _write_election(files_dir / "2024election.json")
    _write_election(files_dir / "2019election.json")
    _write_election(files_dir / "2010election.json")
    monkeypatch.setattr(importer, "FILES_DIR", files_dir)
    monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

    pre_map = db.add_map("UK Constituencies pre 2019")
    post_map = db.add_map("UK 2024 Constituencies")
    unrelated_map = db.add_map("Unrelated Holyrood map", parliament="holyrood")
    unrelated_region = db.add_region(unrelated_map.id, "Unrelated region")
    db.add_seat(unrelated_map.id, "Unrelated seat", region_id=unrelated_region.id)
    pre_seat = db.add_seat(pre_map.id, "Alpha")
    exact_seat = db.add_seat(post_map.id, "Alpha")
    normalized_seat = db.add_seat(post_map.id, "Beta and Gamma")
    return pre_seat.id, exact_seat.id, normalized_seat.id


def _run_main(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> None:
    monkeypatch.setattr(sys, "argv", ["import_general_elections.py", *arguments])
    importer.main()


class TestPartyKeyToName:
    """The party-key map keeps Reform UK and UKIP as distinct parties."""

    def test_reform_maps_to_reform_uk(self) -> None:
        assert importer.PARTY_KEY_TO_NAME["reform"] == "Reform UK"
        assert importer.humanize_party_name("reform") == "Reform UK"

    def test_ukip_stays_ukip(self) -> None:
        assert importer.PARTY_KEY_TO_NAME["ukip"] == "UK Independence Party"
        assert importer.humanize_party_name("ukip") == "UK Independence Party"

    def test_reform_and_ukip_are_distinct(self) -> None:
        assert importer.PARTY_KEY_TO_NAME["reform"] != importer.PARTY_KEY_TO_NAME["ukip"]


def test_normalize_names_and_party_keys() -> None:
    assert importer.normalize_name("St. John's & North") == "stjohnsandnorth"
    assert importer.normalize_party_key("Lib-Dems") == "libdems"


def test_humanize_party_name_fallback() -> None:
    assert importer.humanize_party_name("new_party-name") == "New Party Name"


def test_choose_map_prefers_explicit_name_and_uses_candidates(db: Database) -> None:
    first = db.add_map("First candidate")
    second = db.add_map("Explicit map")

    selected_explicit = importer.choose_map(
        db, "Explicit map", ("First candidate",), "test"
    )
    selected_candidate = importer.choose_map(
        db, None, ("Missing", "First candidate"), "test"
    )
    assert selected_explicit.id == second.id
    assert selected_candidate.id == first.id


def test_choose_map_missing_raises(db: Database) -> None:
    with pytest.raises(ValueError, match="No suitable test map found"):
        importer.choose_map(db, None, ("Missing",), "test")
    with pytest.raises(ValueError, match="Map 'Missing' not found"):
        importer.choose_map(db, "Missing", (), "test")


def test_ensure_party_caches_and_creates(db: Database) -> None:
    cache: dict[str, Party] = {}

    labour = importer.ensure_party(db, cache, "Labour")
    assert labour.name == "Labour"
    assert importer.ensure_party(db, cache, "LAB-OUR") is labour
    assert len(db.get_all_parties()) == 1


def test_pick_winner_key_uses_declared_key_then_vote_fallback() -> None:
    parties: dict[str, object] = {
        "lab-our": {"total": 100},
        "conservative": {"total": 1000},
    }
    assert importer.pick_winner_key("Labour", parties) == "lab-our"
    assert importer.pick_winner_key("unknown", parties) == "conservative"


def test_main_only_year_matches_exact_and_normalized_seats(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seat_ids = _setup_main(db, tmp_path, monkeypatch)

    _run_main(monkeypatch, "--only-year", "2024")

    output = capsys.readouterr().out
    assert "Seats in JSON: 3" in output
    assert "Matched seats: 2" in output
    assert "Unmatched seats: 1" in output
    election = db.get_election_by_name("2024 General Election")
    assert election is not None
    assert election.year == 2024
    post_map = db.get_map_by_name("UK 2024 Constituencies")
    assert post_map is not None
    assert election.map_id == post_map.id
    assert election.type == ElectionType.uk_general
    votes = db.get_votes_for_election(election.id)
    assert len(votes) == 3
    assert {vote.seat_id for vote in votes} == {seat_ids[1], seat_ids[2]}
    assert sum(vote.elected for vote in votes) == 2
    winners = {vote.seat_id: vote for vote in votes if vote.elected}
    labour = db.get_party_by_name("Labour")
    assert labour is not None
    assert labour.id == winners[seat_ids[1]].party_id
    assert winners[seat_ids[1]].candidate_name == "Alice Candidate"
    exact_seat = db.get_seat(seat_ids[1])
    normalized_seat = db.get_seat(seat_ids[2])
    assert exact_seat is not None and exact_seat.electorate == 1000
    assert normalized_seat is not None and normalized_seat.electorate == 900
    unrelated = db.get_map_by_name("Unrelated Holyrood map")
    assert unrelated is not None and unrelated.parliament == "holyrood"
    assert [seat.seat_name for seat in db.get_seats_for_map(unrelated.id)] == [
        "Unrelated seat"
    ]
    assert db.get_election_by_name("2019 General Election") is None


def test_main_routes_pre_2024_election_to_pre_2019_map(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_seat_id, _, _ = _setup_main(db, tmp_path, monkeypatch)
    matching_data = json.dumps({"Alpha": _ELECTION_DATA["Alpha"]})
    for year in (2010, 2019):
        (tmp_path / "westminster" / f"{year}election.json").write_text(
            matching_data,
            encoding="utf-8",
        )

    _run_main(monkeypatch, "--only-year", "2010", "--only-year", "2019")

    pre_map = db.get_map_by_name("UK Constituencies pre 2019")
    assert pre_map is not None
    for year in (2010, 2019):
        election = db.get_election_by_name(f"{year} General Election")
        assert election is not None and election.map_id == pre_map.id
        votes = db.get_votes_for_election(election.id)
        assert len(votes) == 2
        assert {vote.seat_id for vote in votes} == {pre_seat_id}


def test_main_reuses_existing_empty_election(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, seat_id, _ = _setup_main(db, tmp_path, monkeypatch)
    post_map = db.get_map_by_name("UK 2024 Constituencies")
    assert post_map is not None
    election = db.add_election(
        post_map.id,
        2024,
        "2024 General Election",
        ElectionType.uk_general,
    )

    _run_main(monkeypatch, "--only-year", "2024")

    reused = db.get_election_by_name("2024 General Election")
    assert reused is not None and reused.id == election.id
    assert len(db.get_votes_for_election(election.id)) == 3
    seat_ids = {seat.id for seat in db.get_seats_for_map(election.map_id)}
    assert {vote.seat_id for vote in db.get_votes_for_election(election.id)} == seat_ids
    assert seat_id in seat_ids


def test_main_no_selected_specs_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "ELECTION_SPECS", ())

    with pytest.raises(ValueError, match="No elections selected"):
        _run_main(monkeypatch, "--only-year", "2024")


def test_main_reports_unmatched_seats_beyond_first_twenty(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_main(db, tmp_path, monkeypatch)
    data_file = tmp_path / "westminster" / "2024election.json"
    data_file.write_text(
        json.dumps({f"Unknown {index}": {} for index in range(21)}),
        encoding="utf-8",
    )

    _run_main(monkeypatch, "--only-year", "2024", "--dry-run")

    assert "... and 1 more" in capsys.readouterr().out


def test_main_existing_election_raises_then_skip_existing(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, seat_id, _ = _setup_main(db, tmp_path, monkeypatch)
    post_map = db.get_map_by_name("UK 2024 Constituencies")
    assert post_map is not None
    election = db.add_election(
        post_map.id, 2024,
        "2024 General Election", ElectionType.uk_general,
    )
    party = db.add_party("Labour")
    db.add_vote(election.id, seat_id, party_id=party.id, vote_total=1, elected=True)

    with pytest.raises(RuntimeError, match="already has data"):
        _run_main(monkeypatch, "--only-year", "2024")

    _run_main(monkeypatch, "--only-year", "2024", "--skip-existing")
    assert "Skipping '2024 General Election'" in capsys.readouterr().out
    assert len(db.get_votes_for_election(election.id)) == 1


def test_main_refresh_keeps_election_id_and_replaces_votes(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, seat_id, _ = _setup_main(db, tmp_path, monkeypatch)
    post_map = db.get_map_by_name("UK 2024 Constituencies")
    assert post_map is not None
    election = db.add_election(
        post_map.id, 2024,
        "2024 General Election", ElectionType.uk_general,
    )
    party = db.add_party("Old party")
    db.add_vote(election.id, seat_id, party_id=party.id, vote_total=1, elected=True)

    _run_main(monkeypatch, "--only-year", "2024", "--refresh")

    refreshed = db.get_election_by_name("2024 General Election")
    assert refreshed is not None and refreshed.id == election.id
    assert refreshed.map_id == election.map_id
    assert refreshed.year == election.year
    assert refreshed.type == election.type
    votes = db.get_votes_for_election(election.id)
    assert len(votes) == 3
    assert all(vote.party_id != party.id for vote in votes)


def test_main_dry_run_with_seeded_parties_preserves_existing_data(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, exact_seat_id, normalized_seat_id = _setup_main(db, tmp_path, monkeypatch)
    db.add_party("Labour")
    db.add_party("Conservative")
    existing_parties = {party.id for party in db.get_all_parties()}

    _run_main(monkeypatch, "--only-year", "2024", "--dry-run")

    output = capsys.readouterr().out
    assert "Matched seats: 2" in output
    assert "Dry-run mode: no database writes" in output
    assert db.get_election_by_name("2024 General Election") is None
    assert {party.id for party in db.get_all_parties()} == existing_parties
    exact_seat = db.get_seat(exact_seat_id)
    normalized_seat = db.get_seat(normalized_seat_id)
    assert exact_seat is not None and exact_seat.electorate is None
    assert normalized_seat is not None and normalized_seat.electorate is None


def test_main_dry_run_creates_missing_parties(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, exact_seat_id, _ = _setup_main(db, tmp_path, monkeypatch)

    _run_main(monkeypatch, "--only-year", "2024", "--dry-run")

    assert db.get_election_by_name("2024 General Election") is None
    assert {party.name for party in db.get_all_parties()} == {"Labour", "Conservative"}
    exact_seat = db.get_seat(exact_seat_id)
    assert exact_seat is not None and exact_seat.electorate is None


def test_main_missing_selected_file_raises_before_database_access(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files_dir = tmp_path / "empty"
    files_dir.mkdir()
    monkeypatch.setattr(importer, "FILES_DIR", files_dir)
    monkeypatch.setenv("DATABASE_PATH", db.config.database_path)

    with pytest.raises(FileNotFoundError, match="2024election.json"):
        _run_main(monkeypatch, "--only-year", "2024")
