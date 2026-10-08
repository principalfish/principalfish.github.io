"""Characterise full-export ordering, comparison phases and default selection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload

import export_elections
from db import Database
from export_elections import _export_page
from models import Election, ElectionType, Map
from scripts.export.legacy import SUPPLEMENTAL_LEGACY_ELECTIONS
from scripts.export.ordering import (
    finalize_manifest_order,
    float_model_entries_first,
    reorder_manifest_entries,
    reposition_supplemental_entries,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _prepare_page(tmp_path: Path, map_ids: list[int]) -> Path:
    output_root = tmp_path / "page" / "data"
    for map_id in map_ids:
        _write_json(output_root / "maps" / f"map-{map_id}.topo.json", {})
    for filename in ("650map.json", "650map_new.json", "2019election_new.json"):
        _write_json(tmp_path / filename, {})
    return output_root


def _export(
    db: Database,
    tmp_path: Path,
    output_root: Path,
    election_ids: list[int],
    parliaments: set[str],
    *,
    dry_run: bool = False,
    single_election_mode: bool = False,
    output_file: Path | None = None,
) -> None:
    args = argparse.Namespace(
        dry_run=dry_run,
        output_file=output_file,
        legacy_files_dir=tmp_path,
    )
    # Inject the fixture session: never let .env choose the export's database.
    with db.session() as session:
        elections_by_id = {
            election.id: election
            for election in session.scalars(
                select(Election).options(joinedload(Election.map)),
            )
        }
        _export_page(
            session=session,
            elections=[elections_by_id[election_id] for election_id in election_ids],
            output_root=output_root,
            parliaments=parliaments,
            args=args,
            manifest_parties=[],
            manifest_regions_by_map_id={},
            has_electorate=True,
            has_electoral_votes=True,
            single_election_mode=single_election_mode,
        )


def _assert_manifest(
    output_root: Path,
    expected_ids: list[str],
    expected_comparisons: dict[str, str | None],
    expected_default: str,
) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(
        (output_root / "map-modes.json").read_text(encoding="utf-8"),
    )
    entries = manifest["elections"]
    ids = [entry["id"] for entry in entries]
    assert ids == expected_ids
    assert len(ids) == len(set(ids))
    assert {
        entry["id"]: entry.get("comparisonElectionId") for entry in entries
    } == expected_comparisons
    assert manifest["defaultElection"] == expected_default
    assert set(manifest["files"]["elections"]["electionsById"]) == set(ids)
    refs = manifest["files"]["elections"]
    assert all((output_root / path).is_file() for path in refs["mapsById"].values())
    assert all(
        (output_root / path).is_file() for path in refs["electionsById"].values()
    )
    return manifest


@pytest.fixture
def uk_page(db: Database, tmp_path: Path) -> tuple[Path, list[int]]:
    old_westminster = db.add_map("UK Constituencies old")
    westminster = db.add_map("UK Constituencies post 2022")
    old_holyrood = db.add_map("Holyrood 2021", parliament="holyrood")
    holyrood = db.add_map("Holyrood 2026", parliament="holyrood")
    # The real supplemental descriptor targets map 2.
    assert westminster.id == 2
    seat = db.add_seat(westminster.id, "Test constituency")
    party = db.add_party("Labour", colour="#ff0000")
    general = db.add_election(
        westminster.id, 2024, "2024 General Election", ElectionType.uk_general,
    )
    by_election = db.add_election(
        westminster.id, 2025, "Test by-election", ElectionType.by_election,
        parent_election_id=general.id,
    )
    db.add_vote(general.id, seat.id, party_id=party.id, vote_total=100)
    db.add_vote(by_election.id, seat.id, party_id=party.id, vote_total=120)
    prediction = db.add_election(
        westminster.id, 2029, "UK forecast", ElectionType.model_uns,
    )
    latest_holyrood = db.add_election(
        holyrood.id, 2026, "2026 Scottish Parliament Election",
        ElectionType.holyrood_general,
    )
    prior_holyrood = db.add_election(
        old_holyrood.id, 2021, "2021 Scottish Parliament Election",
        ElectionType.holyrood_general,
    )
    remapped_holyrood = db.add_election(
        holyrood.id, 2021, "2021 Scottish Parliament Election (2026 Boundaries)",
        ElectionType.holyrood_general,
    )
    prior_general = db.add_election(
        old_westminster.id, 2019, "2019 General Election", ElectionType.uk_general,
    )
    oldest_general = db.add_election(
        old_westminster.id, 2017, "2017 General Election", ElectionType.uk_general,
    )
    output_root = _prepare_page(
        tmp_path,
        [old_westminster.id, westminster.id, old_holyrood.id, holyrood.id],
    )
    return output_root, [
        prediction.id,
        latest_holyrood.id,
        by_election.id,
        general.id,
        prior_holyrood.id,
        remapped_holyrood.id,
        prior_general.id,
        oldest_general.id,
    ]


@pytest.mark.parametrize("configured_default", [None, "missing", "2019-general"])
def test_uk_curated_order_restoration_and_comparison_phases(
    db: Database,
    tmp_path: Path,
    uk_page: tuple[Path, list[int]],
    configured_default: str | None,
) -> None:
    output_root, election_ids = uk_page
    baseline_file = "results/holyrood-general-2021-changed-boundaries.json"
    prediction_file = "results/holyrood-forecast.json"
    _write_json(output_root / prediction_file, {"schema": "pf-results-v4", "seats": []})
    prior_entries: list[dict[str, Any]] = [
        {"id": "2019-general-changed-boundaries"},
        {"id": "2019-general"},
        {"id": "2021-holyrood"},
        {"id": "current-parliament"},
        {"id": "2024-general"},
        {"id": "2026-holyrood"},
        {"id": "current-prediction"},
        {
            "id": "2021-holyrood-2026", "type": "holyrood_general",
            "parliament": "holyrood", "mapId": 4,
        },
        {
            "id": "current-holyrood-prediction", "type": "holyrood_uns",
            "parliament": "holyrood", "mapId": 4, "model": True,
            "comparisonElectionId": "2026-holyrood",
        },
        {
            "id": "missing-prediction", "type": "holyrood_uns",
            "parliament": "holyrood", "mapId": 4, "model": True,
        },
        {"id": "removed-election", "type": "uk_general"},
    ]
    _write_json(output_root / "map-modes.json", {
        "elections": prior_entries,
        "files": {"elections": {"electionsById": {
            "2021-holyrood-2026": baseline_file,
            "current-holyrood-prediction": prediction_file,
            "missing-prediction": "results/does-not-exist.json",
        }}},
    })
    shell: dict[str, Any] = {"misc": {"orderingFixture": True}}
    if configured_default is not None:
        shell["defaultElection"] = configured_default
    _write_json(output_root / "map-modes-shell.json", shell)

    expected_ids = [
        "current-prediction", "2019-general", "2017-general",
        "current-holyrood-prediction", "2021-holyrood", "current-parliament",
        "2024-general", "2019-general-changed-boundaries", "2026-holyrood",
        "2021-holyrood-2026",
    ]
    expected_comparisons = {
        "current-prediction": "2024-general",
        "2019-general": "2017-general",
        "2017-general": None,
        "current-holyrood-prediction": "2026-holyrood",
        "2021-holyrood": None,
        "current-parliament": "2024-general",
        "2024-general": "2019-general-changed-boundaries",
        "2019-general-changed-boundaries": None,
        "2026-holyrood": "2021-holyrood-2026",
        "2021-holyrood-2026": None,
    }
    expected_default = (
        "2019-general" if configured_default == "2019-general"
        else "current-holyrood-prediction"
    )
    # Repeat with the generated manifest as input; these representative inputs
    # must retain their order, links and default on the next ordinary export.
    for _ in range(2):
        _export(db, tmp_path, output_root, election_ids, {"westminster", "holyrood"})
        manifest = _assert_manifest(
            output_root, expected_ids, expected_comparisons, expected_default,
        )
        refs = manifest["files"]["elections"]["electionsById"]
        assert refs["2021-holyrood-2026"] == baseline_file
        assert refs["current-holyrood-prediction"] == prediction_file
        assert (output_root / baseline_file).is_file()
        current = next(
            entry for entry in manifest["elections"]
            if entry["id"] == "current-parliament"
        )
        assert current["byElectionSeats"] == ["Test constituency"]


@pytest.mark.parametrize("with_prediction", [False, True])
@pytest.mark.parametrize("configured_default", [None, "missing"])
def test_new_uk_manifest_current_parliament_placement_and_default(
    db: Database,
    tmp_path: Path,
    uk_page: tuple[Path, list[int]],
    with_prediction: bool,
    configured_default: str | None,
) -> None:
    output_root, election_ids = uk_page
    if not with_prediction:
        election_ids = election_ids[1:]
    if configured_default is not None:
        _write_json(output_root / "map-modes-shell.json", {
            "defaultElection": configured_default,
        })
    _export(db, tmp_path, output_root, election_ids, {"westminster", "holyrood"})
    expected_ids = (["current-prediction"] if with_prediction else []) + [
        "current-parliament", "2026-holyrood", "2024-general",
        "2019-general-changed-boundaries", "2021-holyrood", "2019-general",
        "2017-general",
    ]
    expected_comparisons = {
        "current-parliament": "2024-general",
        "2026-holyrood": None,
        "2024-general": "2019-general-changed-boundaries",
        "2019-general-changed-boundaries": None,
        "2021-holyrood": None,
        "2019-general": "2017-general",
        "2017-general": None,
    }
    if with_prediction:
        expected_comparisons["current-prediction"] = "2024-general"
    _assert_manifest(
        output_root, expected_ids, expected_comparisons, "current-parliament",
    )


@pytest.fixture
def us_page(db: Database, tmp_path: Path) -> tuple[Path, list[int]]:
    # Match the shipped map IDs, including the supplemental Senate descriptor.
    with db.session() as session:
        session.add_all([
            Map(id=21, name="US House", parliament="us_house"),
            Map(id=22, name="US President", parliament="us_presidential"),
            Map(id=23, name="US Senate", parliament="us_senate"),
        ])
    specs = [
        (21, 2026, "House forecast", ElectionType.us_house_model),
        (23, 2026, "Senate forecast", ElectionType.us_senate_model),
        (22, 2028, "President forecast", ElectionType.us_presidential_model),
        (21, 2024, "2024 US House", ElectionType.us_house),
        (22, 2024, "2024 US President", ElectionType.us_presidential),
        (23, 2024, "2024 US Senate", ElectionType.us_senate),
        (21, 2022, "2022 US House", ElectionType.us_house),
        (23, 2020, "2020 US Senate", ElectionType.us_senate),
    ]
    election_ids = [
        db.add_election(map_id, year, name, election_type).id
        for map_id, year, name, election_type in specs
    ]
    output_root = _prepare_page(tmp_path, [21, 22, 23])
    _write_json(output_root / "results" / "senate-current.json", {
        "schema": "pf-results-v4", "seats": [],
    })
    return output_root, election_ids


@pytest.mark.parametrize("configured_default", [None, "missing", "current-senate"])
@pytest.mark.parametrize("remembered_order", [False, True])
def test_us_combined_order_supplemental_forecasts_and_default(
    db: Database,
    tmp_path: Path,
    us_page: tuple[Path, list[int]],
    configured_default: str | None,
    remembered_order: bool,
) -> None:
    output_root, election_ids = us_page
    prior: dict[str, Any] = {"elections": [
        {"id": election_id} for election_id in [
            "2024-us-president", "2024-us-house", "2020-us-senate",
            "2022-us-house", "2024-us-senate", "current-senate",
            "current-us-house", "current-us-senate", "current-us-president",
            "removed-election",
        ]
    ]}
    if not remembered_order:
        prior["elections"] = []
    if configured_default is not None:
        prior["defaultElection"] = configured_default
    _write_json(output_root / "map-modes.json", prior)
    expected_ids = [
        "current-us-president", "2024-us-president", "current-us-house",
        "2024-us-house", "current-us-senate", "2020-us-senate", "2022-us-house",
        "current-senate", "2024-us-senate",
    ]
    if not remembered_order:
        expected_ids = [
            "current-us-house", "current-us-senate", "current-us-president",
            "2024-us-house", "2024-us-president", "current-senate",
            "2024-us-senate", "2022-us-house", "2020-us-senate",
        ]
    expected_comparisons = {
        "current-us-president": "2024-us-president",
        "2024-us-president": None,
        "current-us-house": "2024-us-house",
        "2024-us-house": "2022-us-house",
        "current-us-senate": "2020-us-senate",
        "2020-us-senate": None,
        "2022-us-house": None,
        "current-senate": None,
        "2024-us-senate": None,
    }
    expected_default = (
        "current-senate" if configured_default == "current-senate"
        else "current-us-house"
    )
    for _ in range(2):
        _export(
            db, tmp_path, output_root, election_ids,
            {"us_house", "us_presidential", "us_senate"},
        )
        manifest = _assert_manifest(
            output_root, expected_ids, expected_comparisons, expected_default,
        )
        assert manifest["files"]["elections"]["electionsById"]["current-senate"] == (
            "results/senate-current.json"
        )


@pytest.mark.parametrize("mode", ["single-election", "output-file"])
def test_partial_export_bypasses_manifest_ordering_and_supplementals(
    db: Database,
    tmp_path: Path,
    uk_page: tuple[Path, list[int]],
    mode: str,
) -> None:
    output_root, election_ids = uk_page
    manifest_path = output_root / "map-modes.json"
    _write_json(manifest_path, {"elections": [], "defaultElection": "sentinel"})
    original = manifest_path.read_bytes()
    # A full export would fail looking for this source. Partial exports must
    # return before supplemental registration or manifest assembly.
    (tmp_path / "2019election_new.json").unlink()
    result_path = (
        tmp_path / "single-result.json" if mode == "output-file"
        else output_root / "results" / "uk-general-2024.json"
    )
    _export(
        db, tmp_path, output_root, [election_ids[3]], {"westminster"},
        single_election_mode=mode == "single-election",
        output_file=result_path if mode == "output-file" else None,
    )
    assert result_path.is_file()
    assert manifest_path.read_bytes() == original
    supplemental = output_root / "results" / "uk-general-2019-changed-boundaries.json"
    assert not supplemental.exists()


def test_full_dry_run_preserves_output_files(
    db: Database,
    tmp_path: Path,
    uk_page: tuple[Path, list[int]],
) -> None:
    output_root, election_ids = uk_page
    _write_json(output_root / "maps" / "stale.topo.json", {"stale": True})
    _write_json(output_root / "map-modes.json", {"elections": []})
    before = {
        path.relative_to(output_root): path.read_bytes()
        for path in output_root.rglob("*") if path.is_file()
    }
    _export(
        db, tmp_path, output_root, election_ids, {"westminster", "holyrood"},
        dry_run=True,
    )
    after = {
        path.relative_to(output_root): path.read_bytes()
        for path in output_root.rglob("*") if path.is_file()
    }
    assert after == before


def test_metadata_only_preserves_election_order_comparisons_and_default(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = {name: tmp_path / name / "data" for name in ("uk", "us")}
    prior = {
        "defaultElection": "historical",
        "elections": [
            {"id": "historical", "comparisonElectionId": "baseline"},
            {"id": "forecast", "model": True},
            {"id": "baseline"},
        ],
    }
    for root in roots.values():
        _write_json(root / "map-modes.json", prior)
    monkeypatch.setattr(export_elections, "Database", lambda: db)
    monkeypatch.setattr(export_elections, "PAGE_OUTPUT_ROOTS", roots)
    monkeypatch.setattr(export_elections, "parse_args", lambda: argparse.Namespace(
        election_name=None, current_simulation=False, metadata_only=True, dry_run=False,
    ))
    export_elections.main()
    for root in roots.values():
        manifest = json.loads((root / "map-modes.json").read_text(encoding="utf-8"))
        assert manifest["elections"] == prior["elections"]
        assert manifest["defaultElection"] == prior["defaultElection"]


def test_reorder_anchor_precedence_first_comparer_and_stable_ties() -> None:
    entries = [
        {"id": "first-comparer", "comparisonElectionId": "new-baseline"},
        {"id": "later-comparer", "comparisonElectionId": "new-baseline"},
        {"id": "new-before-a", "comparisonElectionId": "old"},
        {"id": "new-before-b", "comparisonElectionId": "old"},
        {"id": "new-baseline", "comparisonElectionId": "old"},
        {"id": "old"},
        {"id": "unanchored-a"},
        {"id": "unanchored-b"},
    ]
    reordered = reorder_manifest_entries(
        entries, ["later-comparer", "removed", "old", "first-comparer"],
    )
    assert [entry["id"] for entry in reordered] == [
        "later-comparer", "new-before-a", "new-before-b", "old",
        "first-comparer", "new-baseline", "unanchored-a", "unanchored-b",
    ]
    assert {id(entry) for entry in reordered} == {id(entry) for entry in entries}


def test_multiple_forecasts_keep_relative_order_with_interleaved_parliaments() -> None:
    entries: list[dict[str, Any]] = [
        {"id": "house-old", "parliament": "us_house"},
        {"id": "senate-old", "parliament": "us_senate"},
        {"id": "house-model-a", "parliament": "us_house", "model": True},
        {"id": "president-old", "parliament": "us_presidential", "model": False},
        {"id": "house-older", "parliament": "us_house"},
        {"id": "senate-model", "parliament": "us_senate", "model": True},
        {"id": "house-model-b", "parliament": "us_house", "model": "yes"},
        {"id": "senate-older", "parliament": "us_senate"},
    ]
    original_objects = {id(entry) for entry in entries}
    float_model_entries_first(entries)
    assert [entry["id"] for entry in entries] == [
        "house-model-a", "house-model-b", "house-old", "senate-model",
        "senate-old", "president-old", "house-older", "senate-older",
    ]
    assert {id(entry) for entry in entries} == original_objects


def test_supplemental_missing_anchor_appends_without_regrouping() -> None:
    entries = [
        {"id": "current-senate", "parliament": "us_senate"},
        {"id": "2024-us-house", "parliament": "us_house"},
        {"id": "2020-us-senate", "parliament": "us_senate"},
    ]
    reposition_supplemental_entries(
        entries,
        SUPPLEMENTAL_LEGACY_ELECTIONS,
        parliaments={"us_senate"},
    )
    assert [entry["id"] for entry in entries] == [
        "2024-us-house", "2020-us-senate", "current-senate",
    ]


def test_finalize_uses_supplied_descriptors_without_mutating_input_list() -> None:
    entries = [
        {"id": "other-snapshot", "parliament": "westminster"},
        {"id": "custom-snapshot", "parliament": "us_senate"},
        {"id": "senate-anchor", "parliament": "us_senate"},
    ]
    supplemental_entries = [
        {
            "id": "custom-snapshot",
            "parliament": "us_senate",
            "insertBeforeId": "senate-anchor",
        },
        {"id": "other-snapshot", "insertBeforeId": "senate-anchor"},
    ]

    ordered = finalize_manifest_order(
        entries,
        ("senate-anchor", "other-snapshot", "custom-snapshot"),
        supplemental_entries,
        parliaments={"us_senate"},
    )

    assert [entry["id"] for entry in ordered] == [
        "custom-snapshot", "senate-anchor", "other-snapshot",
    ]
    assert [entry["id"] for entry in entries] == [
        "other-snapshot", "custom-snapshot", "senate-anchor",
    ]
    assert ordered is not entries
    assert ordered[0] is entries[1]
    assert ordered[1] is entries[2]
    assert ordered[2] is entries[0]
