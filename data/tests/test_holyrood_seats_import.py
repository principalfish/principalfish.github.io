"""Tests for the Holyrood constituency-seat importer using temporary data."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

from db import Database
from models import Map


class HolyroodSeatsImporter(Protocol):
    __file__: str
    SOURCE_FILE: Path

    def ensure_map(self, db: Database, map_id: int, name: str) -> Map: ...

    def import_map_seats(
        self, db: Database, map_id: int, name: str, seats: dict[str, str]
    ) -> int: ...

    def main(self) -> None: ...


def _load() -> HolyroodSeatsImporter:
    path = (
        Path(__file__).resolve().parents[1]
        / "old_data/scripts/holyrood/import_holyrood_seats.py"
    )
    name = "test_holyrood_seats_import_module"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(HolyroodSeatsImporter, module)


importer = _load()


def _assert_map_structure(
    db: Database,
    map_id: int,
    map_name: str,
    expected_seats: dict[str, str],
) -> tuple[dict[str, int], dict[str, int]]:
    map_row = db.get_map(map_id)
    assert map_row is not None
    assert map_row.name == map_name
    assert map_row.parliament == "holyrood"

    regions = {region.name: region for region in db.get_regions_for_map(map_id)}
    seats = {seat.seat_name: seat for seat in db.get_seats_for_map(map_id)}
    assert set(seats) == set(expected_seats)
    assert set(regions) == set(expected_seats.values())
    for seat_name, region_name in expected_seats.items():
        seat = seats[seat_name]
        assert seat.map_id == map_id
        assert seat.region_id == regions[region_name].id
    return (
        {name: region.id for name, region in regions.items()},
        {name: seat.id for name, seat in seats.items()},
    )


def test_ensure_map_creates_fixed_id_holyrood_map(db: Database) -> None:
    created = importer.ensure_map(db, 11, "Synthetic Holyrood map")

    assert created.id == 11
    assert created.name == "Synthetic Holyrood map"
    assert created.parliament == "holyrood"
    assert [map_row.id for map_row in db.get_all_maps()] == [created.id]


def test_ensure_map_reuses_existing_row_without_renaming(db: Database) -> None:
    existing = db.add_map("Existing map", parliament="westminster")

    reused = importer.ensure_map(db, existing.id, "Replacement name")

    assert reused.id == existing.id
    assert reused.name == "Existing map"
    assert reused.parliament == "westminster"
    assert len(db.get_all_maps()) == 1


def test_import_map_seats_creates_shared_regions_and_seats(db: Database) -> None:
    expected = {
        "Edinburgh Central": "Lothian",
        "Edinburgh Northern": "Lothian",
        "Glasgow Central": "Glasgow",
    }

    ensured = importer.import_map_seats(
        db, 11, "Synthetic Holyrood map", expected
    )

    assert ensured == 3
    regions, seats = _assert_map_structure(
        db, 11, "Synthetic Holyrood map", expected
    )
    assert len(regions) == 2
    assert len(seats) == 3


def test_import_map_seats_reuses_existing_rows_and_adds_missing_rows(
    db: Database,
) -> None:
    map_row = db.add_map("Existing Holyrood map", parliament="holyrood")
    old_region = db.add_region(map_row.id, "Original region")
    existing_seat = db.add_seat(
        map_row.id,
        "Existing seat",
        region_id=old_region.id,
        electorate=12345,
    )
    unrelated = db.add_map("Unrelated map", parliament="westminster")
    unrelated_region = db.add_region(unrelated.id, "Unrelated region")
    unrelated_seat = db.add_seat(
        unrelated.id,
        "Existing seat",
        region_id=unrelated_region.id,
        electorate=6789,
    )

    expected = {
        "Existing seat": "New source region",
        "New seat": "New source region",
    }
    assert importer.import_map_seats(
        db, map_row.id, "Ignored replacement name", expected
    ) == 2
    first_new_region = db.get_regions_for_map(map_row.id)
    new_region = next(
        region for region in first_new_region if region.name == "New source region"
    )
    current_seat = db.get_seat(existing_seat.id)
    assert current_seat is not None
    assert current_seat.region_id == old_region.id
    assert current_seat.electorate == 12345
    preserved_unrelated_seat = db.get_seat(unrelated_seat.id)
    assert preserved_unrelated_seat is not None
    assert preserved_unrelated_seat.region_id == unrelated_region.id

    first_ids = (
        {region.name: region.id for region in first_new_region},
        {seat.seat_name: seat.id for seat in db.get_seats_for_map(map_row.id)},
    )
    assert importer.import_map_seats(
        db, map_row.id, "Ignored replacement name", expected
    ) == 2
    second_ids = (
        {region.name: region.id for region in db.get_regions_for_map(map_row.id)},
        {seat.seat_name: seat.id for seat in db.get_seats_for_map(map_row.id)},
    )
    assert second_ids == first_ids
    assert db.get_region(new_region.id) is not None
    preserved_map = db.get_map(map_row.id)
    assert preserved_map is not None
    assert preserved_map.name == "Existing Holyrood map"
    assert len(db.get_regions_for_map(unrelated.id)) == 1
    assert len(db.get_seats_for_map(unrelated.id)) == 1


@pytest.mark.parametrize("refresh", [False, True])
def test_main_imports_synthetic_maps_and_refresh_is_id_preserving(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    refresh: bool,
) -> None:
    unrelated_map = db.add_map("Unrelated map", parliament="westminster")
    unrelated_region = db.add_region(unrelated_map.id, "Unrelated region")
    unrelated_seat = db.add_seat(
        unrelated_map.id,
        "Unrelated seat",
        region_id=unrelated_region.id,
        electorate=9876,
    )
    source_file = tmp_path / "constituency_regions.json"
    source = {
        "11": {
            "name": "Synthetic Holyrood 2021",
            "seats": {
                "Edinburgh Central": "Lothian",
                "Glasgow Central": "Glasgow",
            },
        },
        "12": {
            "name": "Synthetic Holyrood 2016",
            "seats": {
                "Edinburgh Central": "Lothian",
                "Dundee City East": "Tayside",
            },
        },
    }
    source_file.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(importer, "SOURCE_FILE", source_file)
    monkeypatch.setenv("DATABASE_PATH", db.config.database_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["import_holyrood_seats.py", "--refresh"]
        if refresh
        else ["import_holyrood_seats.py"],
    )

    importer.main()

    output = capsys.readouterr().out
    assert "map 11 (Synthetic Holyrood 2021): 2 constituency seats ensured" in output
    assert "map 12 (Synthetic Holyrood 2016): 2 constituency seats ensured" in output
    map_11 = _assert_map_structure(
        db,
        11,
        "Synthetic Holyrood 2021",
        {"Edinburgh Central": "Lothian", "Glasgow Central": "Glasgow"},
    )
    map_12 = _assert_map_structure(
        db,
        12,
        "Synthetic Holyrood 2016",
        {"Edinburgh Central": "Lothian", "Dundee City East": "Tayside"},
    )
    assert map_11[0]["Lothian"] != map_12[0]["Lothian"]
    assert map_11[1]["Edinburgh Central"] != map_12[1]["Edinburgh Central"]
    preserved_map = db.get_map(unrelated_map.id)
    preserved_region = db.get_region(unrelated_region.id)
    preserved_seat = db.get_seat(unrelated_seat.id)
    assert preserved_map is not None and preserved_map.parliament == "westminster"
    assert preserved_region is not None and preserved_region.map_id == unrelated_map.id
    assert preserved_seat is not None
    assert preserved_seat.region_id == unrelated_region.id
    assert preserved_seat.electorate == 9876
