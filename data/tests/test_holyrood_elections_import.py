"""Holyrood election imports against synthetic JSON and temporary SQLite data."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Protocol, cast

import pytest

from db import Database
from models import ElectionType, Map, Party, Seat


class Spec(Protocol):
    year: int
    name: str
    constituency_file: str
    list_file: str
    election_date: str
    map_name: str


class Stats(Protocol):
    seats_seen: int
    seats_matched: int
    seats_unmatched: int
    electorates_updated: int
    votes_inserted: int
    list_regions_seen: int
    list_seats_inserted: int


class Importer(Protocol):
    FILES_DIR: Path
    ELECTION_SPECS: tuple[Spec, ...]
    REMAPPED_ELECTION_SPECS: tuple[Spec, ...]
    MAP_NAME_CANDIDATES: tuple[str, ...]

    def normalize_name(self, value: str) -> str: ...
    def normalize_party_key(self, value: str) -> str: ...
    def humanize_party_name(self, value: str) -> str: ...
    def ensure_party(
        self, db: Database, cache: dict[str, Party], key: str,
    ) -> Party: ...
    def pick_winner_key(self, current: str, parties: dict[str, object]) -> str: ...
    def dhondt_allocate(
        self, votes: dict[str, int], constituency: dict[str, int], seats: int,
    ) -> dict[str, int]: ...
    def import_constituency_results(
        self, db: Database, spec: Spec, map_row: Map, cache: dict[str, Party],
        dry_run: bool, skip_existing: bool, refresh: bool = False,
    ) -> Stats: ...
    def import_list_results(
        self, db: Database, spec: Spec, map_row: Map, parent: int | None,
        cache: dict[str, Party], dry_run: bool, skip_existing: bool,
        refresh: bool = False,
    ) -> Stats: ...
    def main(self) -> None: ...


def _load() -> Importer:
    path = Path(__file__).resolve().parents[1] / (
        "old_data/scripts/holyrood/import_holyrood_elections.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_holyrood_elections_module", path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast(Importer, module)


importer = _load()
SPEC = importer.ELECTION_SPECS[2]


@pytest.fixture()
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(importer, "FILES_DIR", tmp_path)
    return tmp_path


def _write(files: Path, spec: Spec = SPEC, *, labour: int = 40) -> None:
    constituency: dict[str, object] = {
        "Alpha": {
            "seatInfo": {"current": "S.N.P.", "electorate": "1200"},
            "partyInfo": {
                "snp": {"total": 30, "name": "Alice"},
                "labour": {"total": labour, "name": "Bob"},
                "invalid": "not a candidate",
            },
        },
        "beta and gamma": {
            "partyInfo": {"labour": {"name": "Carol"}},
        },
        "Orkney": {"partyInfo": {}},
        **{f"Missing {i}": {} for i in range(11)},
    }
    (files / spec.constituency_file).write_text(json.dumps(constituency))
    (files / spec.list_file).write_text(json.dumps({
        "central-scotland": {
            "regionVotes": {"S.N.P.": 90, "Labour": labour},
            "seats": {"S.N.P.": 1, "Labour": 2},
        },
        "Lothian": {
            "regionVotes": {"snp": 90, "labour": 60},
            "constituencySeatsWon": {"S.N.P.": 2},
        },
        "Empty": {},
        "Missing region": {"regionVotes": {"labour": 50}},
    }))


def _world(db: Database, name: str = SPEC.map_name) -> tuple[Map, list[Seat]]:
    decoy = db.add_map(f"Unrelated map for {name}", parliament="westminster")
    decoy_region = db.add_region(decoy.id, "Central Scotland")
    db.add_seat(decoy.id, "Alpha", region_id=decoy_region.id, electorate=999)
    map_row = db.add_map(name, parliament="holyrood")
    region = db.add_region(map_row.id, "Central Scotland")
    db.add_region(map_row.id, "Lothian")
    db.add_region(map_row.id, "Empty")
    return map_row, [
        db.add_seat(map_row.id, name, region_id=region.id, electorate=700)
        for name in ("Alpha", "Beta & Gamma", "Orkney Islands")
    ]


@pytest.mark.parametrize("value, expected", [
    (" King's & Queen's! 42 ", "kingsandqueens42"), ("S.N.P.", "snp"),
    ("", ""),
])
def test_normalization(value: str, expected: str) -> None:
    assert importer.normalize_name(value) == expected
    assert importer.normalize_party_key(value) == expected


@pytest.mark.parametrize("key, expected", [
    ("Scottish-Greens", "Scottish Greens"),
    ("__new-local  party__", "New Local Party"), ("", ""),
])
def test_humanize(key: str, expected: str) -> None:
    assert importer.humanize_party_name(key) == expected


def test_party_creation_existing_and_normalized_cache(db: Database) -> None:
    existing = db.add_party("Scottish National Party")
    cache: dict[str, Party] = {}
    assert importer.ensure_party(db, cache, "S.N.P.").id == existing.id
    assert importer.ensure_party(db, cache, "snp").id == existing.id
    created = importer.ensure_party(db, cache, "new-local_party")
    assert created.name == "New Local Party"
    assert {key: party.id for key, party in cache.items()} == {
        "snp": existing.id, "newlocalparty": created.id,
    }
    assert len(db.get_all_parties()) == 2


@pytest.mark.parametrize("current, parties, winner", [
    ("S.N.P.", {"snp": {"total": 1}, "labour": {"total": 99}}, "snp"),
    ("unknown", {"bad": "invalid", "missing": {}, "labour": {"total": 2}}, "labour"),
    ("", {"second": {"total": 5}, "first": {"total": 5}}, "second"),
])
def test_winner(current: str, parties: dict[str, object], winner: str) -> None:
    assert importer.pick_winner_key(current, parties) == winner


@pytest.mark.parametrize("votes, constituency, seats, expected", [
    ({"a": 100, "b": 60, "c": 20}, {}, 5, {"a": 3, "b": 2}),
    ({"a": 100, "b": 60}, {"a": 3}, 3, {"a": 1, "b": 2}),
    ({"z": 10, "a": 10}, {}, 1, {"z": 1}),
    ({"a": 10, "z": 10}, {}, 1, {"a": 1}),
    ({"a": 10}, {}, 0, {}), ({}, {}, 0, {}),
])
def test_dhondt(
    votes: dict[str, int], constituency: dict[str, int], seats: int,
    expected: dict[str, int],
) -> None:
    assert importer.dhondt_allocate(votes, constituency, seats) == expected


def test_constituencies_persist_matching_votes_and_electorates(
    db: Database, files: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    map_row, seats = _world(db)
    _write(files)
    stats = importer.import_constituency_results(db, SPEC, map_row, {}, False, False)
    assert (stats.seats_seen, stats.seats_matched, stats.seats_unmatched) == (14, 3, 11)
    assert (stats.electorates_updated, stats.votes_inserted) == (1, 3)
    election = db.get_election_by_name(SPEC.name)
    assert election is not None
    assert (election.map_id, election.type, election.election_date) == (
        map_row.id, ElectionType.holyrood_general, date(2021, 5, 6),
    )
    parties = {party.id: party.name for party in db.get_all_parties()}
    votes = db.get_votes_for_election(election.id)
    assert {(v.seat_id, parties[v.party_id], v.vote_total, v.candidate_name, v.elected)
            for v in votes if v.party_id is not None} == {
        (seats[0].id, "Scottish National Party", 30.0, "Alice", True),
        (seats[0].id, "Labour", 40.0, "Bob", False),
        (seats[1].id, "Labour", None, "Carol", True),
    }
    for seat, expected in zip(seats, [1200, 700, 700], strict=True):
        current = db.get_seat(seat.id)
        assert current is not None and current.electorate == expected
    decoy = db.get_seats_for_map(map_row.id - 1)
    assert decoy[0].electorate == 999
    output = capsys.readouterr().out
    assert "11 unmatched seats" in output and "... and 1 more" in output


@pytest.mark.parametrize("kind", ["constituency", "list"])
def test_existing_skip_refresh_and_empty_reuse(
    db: Database, files: Path, kind: str,
) -> None:
    map_row, seats = _world(db)
    _write(files)
    parent = db.add_election(map_row.id, 2020, "Parent", ElectionType.holyrood_general)
    name = SPEC.name if kind == "constituency" else f"{SPEC.name} (List)"
    election = db.add_election(
        map_row.id, SPEC.year, name,
        (ElectionType.holyrood_general
         if kind == "constituency" else ElectionType.holyrood_list),
        parent_election_id=parent.id,
    )
    unrelated = db.add_election(
        map_row.id, 2016, "Unrelated", ElectionType.holyrood_general,
    )
    unrelated_vote = db.add_vote(
        unrelated.id, seats[0].id, vote_total=123, elected=True,
    )

    def run(*, skip: bool = False, refresh: bool = False) -> Stats:
        if kind == "constituency":
            return importer.import_constituency_results(
                db, SPEC, map_row, {}, False, skip, refresh,
            )
        return importer.import_list_results(
            db, SPEC, map_row, parent.id, {}, False, skip, refresh,
        )

    run()
    before = [(v.id, v.vote_total) for v in db.get_votes_for_election(election.id)]
    seat_ids = {seat.seat_name: seat.id for seat in db.get_seats_for_map(map_row.id)}
    with pytest.raises(RuntimeError, match="already has"):
        run()
    skipped = run(skip=True)
    assert skipped.votes_inserted == skipped.list_seats_inserted == 0
    assert [
        (v.id, v.vote_total) for v in db.get_votes_for_election(election.id)
    ] == before
    _write(files, labour=75)
    run(skip=True, refresh=True)
    refreshed = db.get_election_by_name(name)
    assert refreshed is not None and refreshed.id == election.id
    assert refreshed.parent_election_id == parent.id
    assert {
        seat.seat_name: seat.id for seat in db.get_seats_for_map(map_row.id)
    } == seat_ids
    assert any(v.vote_total == 75 for v in db.get_votes_for_election(election.id))
    assert len(db.get_votes_for_election(election.id)) == len(before)
    assert [
        (v.id, v.vote_total, v.elected)
        for v in db.get_votes_for_election(unrelated.id)
    ] == [
        (unrelated_vote.id, 123.0, True),
    ]


def test_lists_persist_allocations_all_party_totals_and_parent(
    db: Database, files: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    map_row, _ = _world(db)
    _write(files)
    parent = db.add_election(map_row.id, 2021, "Parent", ElectionType.holyrood_general)
    stats = importer.import_list_results(db, SPEC, map_row, parent.id, {}, False, False)
    assert (stats.list_regions_seen, stats.list_seats_inserted) == (4, 10)
    election = db.get_election_by_name(f"{SPEC.name} (List)")
    assert election is not None
    assert (
        election.map_id, election.parent_election_id,
        election.type, election.election_date,
    ) == (
        map_row.id, parent.id, ElectionType.holyrood_list, date(2021, 5, 6),
    )
    snp = db.get_party_by_name("Scottish National Party")
    labour = db.get_party_by_name("Labour")
    assert snp is not None and labour is not None
    regions = {r.id: r.name for r in db.get_regions_for_map(map_row.id)}
    list_seats = [
        s for s in db.get_seats_for_map(map_row.id) if " List " in s.seat_name
    ]
    assert len(db.get_votes_for_election(election.id)) == 20
    for seat in list_seats:
        votes = db.get_votes_for_seat_election(election.id, seat.id)
        central = seat.seat_name.startswith("central-scotland")
        assert seat.map_id == map_row.id
        assert seat.region_id is not None
        assert regions[seat.region_id] == ("Central Scotland" if central else "Lothian")
        assert {(v.party_id, v.vote_total, v.candidate_name) for v in votes} == {
            (snp.id, 90.0, None), (labour.id, 40.0 if central else 60.0, None),
        }
        elected = [v.party_id for v in votes if v.elected]
        slot = int(seat.seat_name.rsplit(" ", 1)[1])
        # Lothian round winners are Labour, SNP, Labour, SNP, Labour, SNP, SNP.
        # The final 15/15 quotient tie goes to SNP, the first source key.
        snp_wins = slot == 1 if central else slot <= 4
        assert elected == [snp.id if snp_wins else labour.id]
    assert "Missing region' not found" in capsys.readouterr().out


def test_constituency_dry_run_currently_creates_only_missing_parties(
    db: Database, files: Path,
) -> None:
    map_row, seats = _world(db)
    _write(files)
    existing = db.add_party("Labour")
    stats = importer.import_constituency_results(db, SPEC, map_row, {}, True, False)
    assert (
        stats.seats_matched, stats.votes_inserted, stats.electorates_updated,
    ) == (3, 0, 0)
    assert db.get_elections_for_map(map_row.id) == []
    assert {p.name for p in db.get_all_parties()} == {
        "Labour", "Scottish National Party",
    }
    labour = db.get_party_by_name("Labour")
    assert labour is not None and labour.id == existing.id
    for seat in seats:
        current = db.get_seat(seat.id)
        assert current is not None and current.electorate == 700


def test_list_dry_run_writes_no_rows(db: Database, files: Path) -> None:
    map_row, seats = _world(db)
    _write(files)
    stats = importer.import_list_results(db, SPEC, map_row, None, {}, True, False)
    assert (stats.list_regions_seen, stats.list_seats_inserted) == (4, 0)
    assert db.get_all_parties() == []
    assert db.get_elections_for_map(map_row.id) == []
    assert [s.id for s in db.get_seats_for_map(map_row.id)] == [s.id for s in seats]


@pytest.mark.parametrize(
    "spec", [importer.ELECTION_SPECS[0], importer.ELECTION_SPECS[3]],
)
def test_missing_constituency_file_hints(db: Database, files: Path, spec: Spec) -> None:
    map_row, _ = _world(db)
    hint = "scrape_holyrood_2026" if spec.year == 2026 else "check it out"
    with pytest.raises(FileNotFoundError, match=hint):
        importer.import_constituency_results(db, spec, map_row, {}, False, False)


def test_missing_list_file_returns_empty_stats(
    db: Database, files: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    map_row, _ = _world(db)
    stats = importer.import_list_results(db, SPEC, map_row, None, {}, False, False)
    assert stats.list_regions_seen == stats.list_seats_inserted == 0
    assert db.get_elections_for_map(map_row.id) == []
    assert "skipping list seat import" in capsys.readouterr().out


@pytest.mark.parametrize("include_remapped, dry_run, legacy", [
    (False, False, False), (True, False, False), (True, True, False),
    (False, False, True),
])
def test_main_filters_year_links_lists_and_resolves_legacy_map(
    db: Database, files: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], include_remapped: bool, dry_run: bool,
    legacy: bool,
) -> None:
    name = "Legacy Holyrood" if legacy else SPEC.map_name
    map_row, _ = _world(db, name)
    if legacy:
        monkeypatch.setattr(importer, "MAP_NAME_CANDIDATES", ("Absent candidate", name))
    remapped = importer.REMAPPED_ELECTION_SPECS[0]
    second, _ = _world(db, remapped.map_name)
    _write(files)
    _write(files, remapped)
    monkeypatch.setenv("DATABASE_PATH", str(db.engine.url.database))
    args = ["holyrood", "--only-year", "2021"]
    if include_remapped:
        args.append("--include-remapped")
    if dry_run:
        args.append("--dry-run")
    monkeypatch.setattr(sys, "argv", args)
    importer.main()
    expected_specs = [SPEC, remapped] if include_remapped else [SPEC]
    expected_maps = [map_row, second] if include_remapped else [map_row]
    if dry_run:
        assert db.get_elections_for_map(map_row.id) == []
        assert db.get_elections_for_map(second.id) == []
        assert "Dry-run mode" in capsys.readouterr().out
    else:
        for spec, target in zip(expected_specs, expected_maps, strict=True):
            constituency = db.get_election_by_name(spec.name)
            listed = db.get_election_by_name(f"{spec.name} (List)")
            assert constituency is not None and listed is not None
            assert constituency.map_id == listed.map_id == target.id
            assert listed.parent_election_id == constituency.id
            assert len(db.get_votes_for_election(constituency.id)) == 3
            assert len(db.get_votes_for_election(listed.id)) == 20
        for spec in importer.ELECTION_SPECS:
            if spec.year != 2021:
                assert db.get_election_by_name(spec.name) is None
        if not include_remapped:
            assert db.get_election_by_name(remapped.name) is None


@pytest.mark.parametrize("missing_map", [False, True])
def test_main_rejects_empty_selection_or_missing_map(
    db: Database, files: Path, monkeypatch: pytest.MonkeyPatch, missing_map: bool,
) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(db.engine.url.database))
    monkeypatch.setattr(sys, "argv", ["holyrood"])
    if not missing_map:
        monkeypatch.setattr(importer, "ELECTION_SPECS", ())
    message = "not found" if missing_map else "No elections selected"
    with pytest.raises(ValueError, match=message):
        importer.main()


@pytest.mark.parametrize("repeat_flag", ["--skip-existing", "--refresh"])
def test_main_shared_map_multiple_years_and_repeat_flags(
    db: Database, files: Path, monkeypatch: pytest.MonkeyPatch, repeat_flag: str,
) -> None:
    map_row, _ = _world(db)
    previous = importer.ELECTION_SPECS[1]
    monkeypatch.setattr(importer, "ELECTION_SPECS", (previous, SPEC))
    for spec in (previous, SPEC):
        _write(files, spec)
    monkeypatch.setenv("DATABASE_PATH", str(db.engine.url.database))
    monkeypatch.setattr(sys, "argv", ["holyrood"])
    importer.main()
    elections = {e.name: e.id for e in db.get_elections_for_map(map_row.id)}
    assert len(elections) == 4
    monkeypatch.setattr(sys, "argv", [
        "holyrood", "--only-year", "2016", "--only-year", "2021", repeat_flag,
    ])
    importer.main()
    assert {e.name: e.id for e in db.get_elections_for_map(map_row.id)} == elections
    for name, election_id in elections.items():
        assert len(db.get_votes_for_election(election_id)) == (
            20 if name.endswith("(List)") else 3
        )


@pytest.mark.parametrize("unknown_count", [0, 1])
def test_short_or_absent_unmatched_seat_warning(
    db: Database, files: Path, capsys: pytest.CaptureFixture[str], unknown_count: int,
) -> None:
    map_row, _ = _world(db)
    source: dict[str, object] = {
        "Alpha": {}, **{f"Unknown {i}": {} for i in range(unknown_count)},
    }
    (files / SPEC.constituency_file).write_text(json.dumps(source))
    stats = importer.import_constituency_results(db, SPEC, map_row, {}, False, False)
    assert stats.seats_unmatched == unknown_count
    output = capsys.readouterr().out
    assert ("unmatched seats" in output) == bool(unknown_count)
    assert "... and" not in output


def test_main_missing_new_boundary_map_has_no_legacy_fallback(
    db: Database, files: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _world(db)
    monkeypatch.setenv("DATABASE_PATH", str(db.engine.url.database))
    monkeypatch.setattr(sys, "argv", ["holyrood", "--only-year", "2026"])
    with pytest.raises(
        ValueError, match="Scottish Parliament Constituencies 2026.*not found",
    ):
        importer.main()
