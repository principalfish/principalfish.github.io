"""Synthetic TopoJSON decoding and Westminster map import integration tests."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest
from sqlalchemy.exc import IntegrityError

from db import Database
from models import ElectionType


class TopojsonImporter(Protocol):
    __file__: str

    def decode_topojson(
        self, topo: dict[str, object], object_name: str
    ) -> list[dict[str, object]]: ...

    def import_file(
        self,
        db: Database,
        filepath: str,
        map_name: str,
        skip_existing: bool,
        refresh: bool = False,
    ) -> None: ...

    def main(self) -> None: ...


def _load() -> TopojsonImporter:
    path = (
        Path(__file__).resolve().parents[1]
        / "old_data/scripts/westminster/import_topojson.py"
    )
    name = "test_westminster_import_topojson_module"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(TopojsonImporter, module)


importer = _load()

_MAP_NAME = "Synthetic Westminster map"
_ARCS: list[list[list[float]]] = [
    [[0, 0], [2, 0], [0, 2]],
    [[0, 0], [0, 2], [2, 0]],
]
_TRANSFORMED_RING = [[10, -5], [14, -5], [14, 1], [10, 1], [10, -5]]
_REVERSED_RING = [[10, -5], [10, 1], [14, 1], [14, -5], [10, -5]]


def _polygon(name: str, region: str, *, reverse: bool = False) -> dict[str, object]:
    return {
        "type": "Polygon",
        "properties": {"name": name, "region": region},
        "arcs": [[1, -1]] if reverse else [[0, -2]],
    }


def _topology(
    geometries: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "type": "Topology",
        "transform": {"scale": [2, 3], "translate": [10, -5]},
        "arcs": copy.deepcopy(_ARCS),
        "objects": {
            "map": {
                "type": "GeometryCollection",
                "geometries": geometries
                if geometries is not None
                else [
                    _polygon("Alpha", "london"),
                    _polygon("Beta", "wales", reverse=True),
                ],
            }
        },
    }


def _write(path: Path, topology: dict[str, object]) -> Path:
    path.write_text(json.dumps(topology), encoding="utf-8")
    return path


def _seed_unrelated(db: Database) -> tuple[int, int, int]:
    unrelated = db.add_map("Unrelated map", parliament="holyrood")
    region = db.add_region(unrelated.id, "London", population=1234)
    seat = db.add_seat(unrelated.id, "Alpha", region_id=region.id, electorate=4321)
    return unrelated.id, region.id, seat.id


def _assert_unrelated(db: Database, ids: tuple[int, int, int]) -> None:
    map_id, region_id, seat_id = ids
    map_row = db.get_map(map_id)
    region = db.get_region(region_id)
    seat = db.get_seat(seat_id)
    assert map_row is not None and map_row.parliament == "holyrood"
    assert region is not None and region.population == 1234
    assert region.map_id == map_id
    assert seat is not None and seat.electorate == 4321
    assert seat.region_id == region_id and seat.map_id == map_id
    assert len(db.get_regions_for_map(map_id)) == 1
    assert len(db.get_seats_for_map(map_id)) == 1


def _assert_map(
    db: Database,
    name: str,
    expected_assignments: dict[str, str],
) -> tuple[int, dict[str, int], dict[str, int]]:
    map_row = db.get_map_by_name(name)
    assert map_row is not None
    assert map_row.parliament == "westminster"
    regions = {region.name: region.id for region in db.get_regions_for_map(map_row.id)}
    seats = {seat.seat_name: seat for seat in db.get_seats_for_map(map_row.id)}
    assert set(seats) == set(expected_assignments)
    assert set(regions) == set(expected_assignments.values())
    for seat_name, region_name in expected_assignments.items():
        seat = seats[seat_name]
        assert seat.map_id == map_row.id
        assert seat.region_id == regions[region_name]
    return map_row.id, regions, {name: seat.id for name, seat in seats.items()}


class TestDecodeTopojson:
    def test_delta_transform_arc_reversal_and_endpoint_stitching(self) -> None:
        topology = _topology()
        original = copy.deepcopy(topology)

        features = importer.decode_topojson(topology, "map")

        assert features == [
            {
                "properties": {"name": "Alpha", "region": "london"},
                "geometry": {"type": "Polygon", "coordinates": [_TRANSFORMED_RING]},
            },
            {
                "properties": {"name": "Beta", "region": "wales"},
                "geometry": {"type": "Polygon", "coordinates": [_REVERSED_RING]},
            },
        ]
        assert topology == original

    @pytest.mark.parametrize("empty_transform", [False, True])
    def test_without_transform_coordinates_are_absolute(
        self, empty_transform: bool
    ) -> None:
        topology = _topology([_polygon("Alpha", "london")])
        topology["arcs"] = [
            [[5, 7], [9, 7], [9, 11]],
            [[5, 7], [5, 11], [9, 11]],
        ]
        if empty_transform:
            topology["transform"] = {}
        else:
            del topology["transform"]

        features = importer.decode_topojson(topology, "map")

        assert features == [
            {
                "properties": {"name": "Alpha", "region": "london"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[5, 7], [9, 7], [9, 11], [5, 11], [5, 7]]],
                },
            }
        ]

    def test_polygon_holes_and_multipolygon_keep_coordinate_nesting(self) -> None:
        topology = _topology(
            [
                {"type": "Polygon", "arcs": [[0, -2], [1, -1]]},
                {"type": "MultiPolygon", "arcs": [[[0, -2]], [[1, -1]]]},
            ]
        )

        features = importer.decode_topojson(topology, "map")

        assert features == [
            {
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [_TRANSFORMED_RING, _REVERSED_RING],
                },
            },
            {
                "properties": {},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[_TRANSFORMED_RING], [_REVERSED_RING]],
                },
            },
        ]

    def test_uses_requested_object(self) -> None:
        topology = _topology()
        topology["objects"] = {
            "unrelated": {"geometries": [{"type": "Point"}]},
            "requested": {"geometries": [_polygon("Chosen", "london")]},
        }

        features = importer.decode_topojson(topology, "requested")

        assert len(features) == 1
        assert features[0]["properties"] == {"name": "Chosen", "region": "london"}

    def test_missing_object_raises(self) -> None:
        with pytest.raises(KeyError, match="missing"):
            importer.decode_topojson(_topology(), "missing")

    @pytest.mark.parametrize("geometry_type", ["Point", "LineString"])
    def test_unsupported_geometry_raises(self, geometry_type: str) -> None:
        topology = _topology([{"type": geometry_type}])

        with pytest.raises(
            ValueError, match=f"Unsupported geometry type: {geometry_type}"
        ):
            importer.decode_topojson(topology, "map")


class TestImportFile:
    @pytest.mark.parametrize("refresh", [False, True])
    def test_creates_map_with_deduplicated_display_regions_and_seats(
        self, db: Database, tmp_path: Path, refresh: bool
    ) -> None:
        unrelated = _seed_unrelated(db)
        path = _write(
            tmp_path / "map.json",
            _topology(
                [
                    _polygon("Alpha", "london"),
                    _polygon("Beta", "unmapped-region"),
                    _polygon("Gamma", "london"),
                ]
            ),
        )

        importer.import_file(db, str(path), _MAP_NAME, False, refresh=refresh)

        map_id, regions, seats = _assert_map(
            db,
            _MAP_NAME,
            {"Alpha": "London", "Beta": "unmapped-region", "Gamma": "London"},
        )
        assert map_id != unrelated[0]
        assert unrelated[1] not in regions.values()
        assert unrelated[2] not in seats.values()
        assert len(db.get_all_maps()) == 2
        _assert_unrelated(db, unrelated)

    @pytest.mark.parametrize("refresh", [False, True])
    def test_skip_existing_precedes_reading_file_and_refresh(
        self,
        db: Database,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        refresh: bool,
    ) -> None:
        path = _write(tmp_path / "map.json", _topology())
        importer.import_file(db, str(path), _MAP_NAME, False)
        before = _assert_map(db, _MAP_NAME, {"Alpha": "London", "Beta": "Wales"})
        capsys.readouterr()

        importer.import_file(
            db, str(tmp_path / "does-not-exist.json"), _MAP_NAME, True, refresh
        )

        assert "Skipping existing map" in capsys.readouterr().out
        after = _assert_map(db, _MAP_NAME, {"Alpha": "London", "Beta": "Wales"})
        assert after == before
        assert len(db.get_all_maps()) == 1

    def test_existing_map_without_flags_raises_unique_constraint_error(
        self, db: Database, tmp_path: Path
    ) -> None:
        path = _write(tmp_path / "map.json", _topology())
        importer.import_file(db, str(path), _MAP_NAME, False)
        before = _assert_map(db, _MAP_NAME, {"Alpha": "London", "Beta": "Wales"})

        with pytest.raises(IntegrityError, match="UNIQUE constraint failed: maps.name"):
            importer.import_file(db, str(path), _MAP_NAME, False)

        after = _assert_map(db, _MAP_NAME, {"Alpha": "London", "Beta": "Wales"})
        assert after == before
        assert len(db.get_all_maps()) == 1

    def test_refresh_preserves_ids_votes_and_existing_region_assignment(
        self, db: Database, tmp_path: Path
    ) -> None:
        unrelated = _seed_unrelated(db)
        path = _write(tmp_path / "map.json", _topology())
        importer.import_file(db, str(path), _MAP_NAME, False)
        map_id, original_regions, original_seats = _assert_map(
            db, _MAP_NAME, {"Alpha": "London", "Beta": "Wales"}
        )
        election = db.add_election(
            map_id, 2024, "Linked election", ElectionType.uk_general
        )
        vote = db.add_vote(
            election.id,
            original_seats["Alpha"],
            candidate_name="Existing candidate",
            vote_total=42,
            elected=True,
        )
        _write(
            path,
            _topology(
                [
                    _polygon("Alpha", "wales"),
                    _polygon("Beta", "london"),
                    _polygon("Gamma", "new-region"),
                    _polygon("Delta", "new-region"),
                ]
            ),
        )

        importer.import_file(db, str(path), _MAP_NAME, False, refresh=True)

        # Existing seats are returned unchanged by get_or_create_seat; only
        # newly created seats receive the region from the refreshed source.
        refreshed_id, regions, seats = _assert_map(
            db,
            _MAP_NAME,
            {
                "Alpha": "London",
                "Beta": "Wales",
                "Gamma": "new-region",
                "Delta": "new-region",
            },
        )
        assert refreshed_id == map_id
        assert {name: regions[name] for name in original_regions} == original_regions
        assert {name: seats[name] for name in original_seats} == original_seats
        persisted_vote = db.get_vote(vote.id)
        assert persisted_vote is not None
        assert persisted_vote.seat_id == original_seats["Alpha"]
        assert persisted_vote.election_id == election.id
        assert persisted_vote.vote_total == 42 and persisted_vote.elected
        assert len(db.get_votes_for_election(election.id)) == 1
        assert len(db.get_all_maps()) == 2
        _assert_unrelated(db, unrelated)

    def test_decode_failure_does_not_create_map(
        self, db: Database, tmp_path: Path
    ) -> None:
        path = _write(tmp_path / "invalid.json", _topology([{"type": "Point"}]))

        with pytest.raises(ValueError, match="Unsupported geometry type: Point"):
            importer.import_file(db, str(path), _MAP_NAME, False)

        assert db.get_all_maps() == []


class TestMain:
    @pytest.mark.parametrize("repeat_flag", [None, "--skip-existing", "--refresh"])
    def test_cli_imports_both_temp_inputs_into_env_database(
        self,
        db: Database,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        repeat_flag: str | None,
    ) -> None:
        unrelated = _seed_unrelated(db)
        root = tmp_path / "old_data"
        inputs = root / "files/westminster"
        inputs.mkdir(parents=True)
        _write(inputs / "650map.json", _topology())
        _write(inputs / "650map_new.json", _topology())
        monkeypatch.setattr(
            importer, "__file__", str(root / "scripts/westminster/import_topojson.py")
        )
        # config is already imported; this wins over its initial .env load.
        monkeypatch.setenv("DATABASE_PATH", db.config.database_path)
        monkeypatch.setattr(sys, "argv", ["import_topojson.py"])

        importer.main()

        names = ["UK Constituencies pre 2019", "UK Constituencies post 2022"]
        before = {
            name: _assert_map(db, name, {"Alpha": "London", "Beta": "Wales"})
            for name in names
        }
        assert len({unrelated[0], *(state[0] for state in before.values())}) == 3
        assert len(db.get_all_maps()) == 3
        output = capsys.readouterr().out
        for name in names:
            assert f"{name}: 2 regions, 2 seats" in output
        assert "Done!" in output

        if repeat_flag is not None:
            monkeypatch.setattr(sys, "argv", ["import_topojson.py", repeat_flag])

            importer.main()

            after = {
                name: _assert_map(db, name, {"Alpha": "London", "Beta": "Wales"})
                for name in names
            }
            assert after == before
            assert len(db.get_all_maps()) == 3
            output = capsys.readouterr().out
            expected = (
                "Skipping existing map"
                if repeat_flag == "--skip-existing"
                else "Reusing existing map"
            )
            assert output.count(expected) == 2
        _assert_unrelated(db, unrelated)

    def test_dry_run_is_rejected_before_database_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def unexpected_database() -> Database:
            raise AssertionError("Argument rejection must happen before DB creation")

        monkeypatch.setattr(importer, "Database", unexpected_database)
        monkeypatch.setattr(sys, "argv", ["import_topojson.py", "--dry-run"])

        with pytest.raises(SystemExit) as exc:
            importer.main()

        assert exc.value.code == 2
        assert "unrecognized arguments: --dry-run" in capsys.readouterr().err
