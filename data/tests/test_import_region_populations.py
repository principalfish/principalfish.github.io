"""Population seed readers and CLI writes against temporary inputs and maps."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from db import Database

OLD_SCRIPTS = Path(__file__).resolve().parents[1] / "old_data" / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, OLD_SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


populations = _load("import_region_populations")


@pytest.mark.parametrize(
    ("name", "expected"),
    [("  North   EAST\t\n", "north east"), ("Wales", "wales"), (" \t", "")],
)
def test_normalize_name(name: str, expected: str) -> None:
    assert populations.normalize_name(name) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5430000, 5430000),
        (0, 0),
        ("5,430,000", 5430000),
        (" 5_430_000 ", 5430000),
        ("123", 123),
    ],
)
def test_parse_population(value: str | int, expected: int) -> None:
    assert populations.parse_population(value) == expected


@pytest.mark.parametrize("value", ["", "unknown", "1.5"])
def test_parse_population_rejects_invalid_numbers(value: str) -> None:
    with pytest.raises(ValueError):
        populations.parse_population(value)


def test_read_csv_trims_values_and_skips_empty_regions(tmp_path: Path) -> None:
    path = tmp_path / "population.csv"
    path.write_text(
        'region,population,extra\n Scotland ,"5,430,000",ignore\n'
        ',123,ignore\n   ,,ignore\nWales,3100000,ignore\n',
        encoding="utf-8",
    )
    assert populations.read_csv(path) == [
        {"region": "Scotland", "population": "5,430,000"},
        {"region": "Wales", "population": "3100000"},
    ]


@pytest.mark.parametrize(
    "content", ["", "region\nScotland\n", "area,population\nS,1\n"]
)
def test_read_csv_requires_headers(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="CSV must include headers"):
        populations.read_csv(path)


@pytest.mark.parametrize("row", ["Scotland,\n", "Scotland\n"])
def test_read_csv_requires_population_for_named_region(
    tmp_path: Path, row: str
) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("region,population\n" + row, encoding="utf-8")
    with pytest.raises(ValueError, match="Missing population for region 'Scotland'"):
        populations.read_csv(path)


def test_read_json_preserves_population_types_and_skips_blank_regions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "population.json"
    path.write_text(
        json.dumps([
            {"region": " Scotland ", "population": 5430000},
            {"region": "Wales", "population": "3,100,000", "extra": "ignored"},
            {"region": " "},
            {},
        ]),
        encoding="utf-8",
    )
    assert populations.read_json(path) == [
        {"region": "Scotland", "population": 5430000},
        {"region": "Wales", "population": "3,100,000"},
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{}", "JSON input must be a list"),
        ("[42]", "Each JSON item must be an object"),
        ('[{"region": "Scotland"}]', "Missing population for region 'Scotland'"),
    ],
)
def test_read_json_rejects_invalid_structure(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        populations.read_json(path)


@pytest.mark.parametrize(
    ("suffix", "content", "expected_population"),
    [
        (".CSV", "region,population\nWales,123\n", "123"),
        (".JSON", '[{"region": "Wales", "population": 123}]', 123),
    ],
)
def test_load_records_dispatches_case_insensitive_suffix(
    tmp_path: Path, suffix: str, content: str, expected_population: str | int
) -> None:
    path = tmp_path / f"input{suffix}"
    path.write_text(content, encoding="utf-8")
    assert populations.load_records(path) == [
        {"region": "Wales", "population": expected_population}
    ]


def test_load_records_rejects_unsupported_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"Input file must be \.csv or \.json"):
        populations.load_records(tmp_path / "input.txt")


def _set_cli(
    monkeypatch: pytest.MonkeyPatch, db: Database, path: Path, *, dry_run: bool = False
) -> None:
    monkeypatch.setenv("DATABASE_PATH", db.config.database_path)
    argv = [
        "import_region_populations.py", "--map-name", "Target", "--input", str(path)
    ]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)


@pytest.mark.parametrize("dry_run", [False, True])
def test_main_matches_names_and_preserves_other_maps_and_unlisted_regions(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
) -> None:
    target = db.add_map("Target")
    changed = db.add_region(target.id, "North   East", population=100)
    unchanged = db.add_region(target.id, "Scotland", population=200)
    unlisted = db.add_region(target.id, "Wales", population=300)
    other_map = db.add_map("Other")
    other = db.add_region(other_map.id, "North East", population=400)
    assert len({changed.id, unchanged.id, unlisted.id, other.id}) == 4
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps([
            {"region": " NORTH east ", "population": "1,234"},
            {"region": "Scotland", "population": 200},
            {"region": "Missing", "population": 500},
        ]),
        encoding="utf-8",
    )
    _set_cli(monkeypatch, db, path, dry_run=dry_run)

    populations.main()

    stored = {
        region.id: (region.name, region.population)
        for region in db.get_regions_for_map(target.id)
    }
    assert stored == {
        changed.id: ("North   East", 100 if dry_run else 1234),
        unchanged.id: ("Scotland", 200),
        unlisted.id: ("Wales", 300),
    }
    other_regions = db.get_regions_for_map(other_map.id)
    other_rows = [(region.id, region.name, region.population) for region in other_regions]
    assert other_rows == [(other.id, "North East", 400)]
    output = capsys.readouterr().out
    assert "Updated: 1" in output
    assert "Unchanged: 1" in output
    assert "Missing region matches: 1" in output
    assert "missing region: Missing" in output
    assert ("Dry-run mode: no database writes" in output) is dry_run


def test_main_missing_input_raises_before_database_access(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cli(monkeypatch, db, tmp_path / "missing.csv")
    with pytest.raises(FileNotFoundError, match="Input file not found"):
        populations.main()


def test_main_empty_input_returns_without_requiring_map(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "empty.json"
    path.write_text("[]", encoding="utf-8")
    _set_cli(monkeypatch, db, path)
    populations.main()
    assert capsys.readouterr().out.strip() == "No records found in input file"


def test_main_missing_map_raises_without_changing_existing_regions(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_map = db.add_map("Other")
    region = db.add_region(other_map.id, "Scotland", population=100)
    path = tmp_path / "input.csv"
    path.write_text("region,population\nScotland,200\n", encoding="utf-8")
    _set_cli(monkeypatch, db, path)
    with pytest.raises(ValueError, match="Map not found: 'Target'"):
        populations.main()
    stored = db.get_region(region.id)
    assert stored is not None
    assert stored.population == 100


def test_main_rejects_duplicate_normalized_regions_before_writing(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = db.add_map("Target")
    first = db.add_region(target.id, "North East", population=100)
    second = db.add_region(target.id, " NORTH  EAST ", population=200)
    path = tmp_path / "input.json"
    path.write_text('[{"region": "North East", "population": 300}]', encoding="utf-8")
    _set_cli(monkeypatch, db, path)
    with pytest.raises(ValueError, match="Duplicate normalized region name"):
        populations.main()
    stored = {
        region.id: region.population for region in db.get_regions_for_map(target.id)
    }
    assert stored == {first.id: 100, second.id: 200}


def test_main_invalid_population_rolls_back_prior_updates(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = db.add_map("Target")
    first = db.add_region(target.id, "Scotland", population=100)
    second = db.add_region(target.id, "Wales", population=200)
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps([
            {"region": "Scotland", "population": 300},
            {"region": "Wales", "population": "invalid"},
        ]),
        encoding="utf-8",
    )
    _set_cli(monkeypatch, db, path)
    with pytest.raises(ValueError):
        populations.main()
    stored = {
        region.id: region.population for region in db.get_regions_for_map(target.id)
    }
    assert stored == {first.id: 100, second.id: 200}
