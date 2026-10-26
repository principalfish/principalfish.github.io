"""Candidate anchors pin evidence bytes without providing an activation mechanism."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import cast

import openpyxl
import pytest

import model_support.us_house_anchors as anchors
from model_support.senate_cycles import resolve_senate_cycle
from scripts.audit_us_house_anchors import audit_workbook

JsonObject = dict[str, object]


def _object(value: object) -> JsonObject:
    return cast(JsonObject, value)


def _array(value: object) -> list[object]:
    return cast(list[object], value)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _document() -> JsonObject:
    return _object(json.loads(anchors.CATALOGUE_PATH.read_text()))


def _write_candidate(
    document: JsonObject, path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Path:
    # Structural tests model a developer pinning a new candidate revision.
    document["payload_sha256"] = _digest(document["payload"])
    raw = json.dumps(document).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(anchors, "_CATALOGUE_SHA256", hashlib.sha256(raw).hexdigest())
    return path


def _year(document: JsonObject, index: int) -> JsonObject:
    return _object(_array(_object(document["payload"])["years"])[index])


def test_candidates_have_two_explicit_unaccepted_count_views() -> None:
    catalogue = anchors.inspect_house_anchor_catalogue()
    assert [item.year for item in catalogue.years] == [2016, 2018, 2020, 2022, 2024]
    operational = catalogue.for_baselines(["2020-us-senate", "2022-us-senate"])
    assert [item.measured_units for item in operational] == [434, 433]
    assert operational[0].unmeasured_units == ("FL-25",)
    assert operational[1].unmeasured_units == ("FL-05", "LA-04")
    assert operational[0].view("published-general").counts == anchors.AnchorCounts(
        77548029, 72835828, 2260206, 152644063,
    )
    assert operational[1].view("published-general").counts == anchors.AnchorCounts(
        51530747, 54440810, 1743481, 107715038,
    )
    assert operational[1].view("final-rcv").counts == anchors.AnchorCounts(
        51551519, 54428784, 1715755, 107696058,
    )
    assert all(view.status == "pending" for item in operational for view in item.views)
    historical = catalogue.for_baselines(["2018-us-senate"])[0]
    assert historical.uncertified_units == ("NC-09",)
    assert all(view.status == "blocked" for view in historical.views)
    audit_only = catalogue.for_baselines(["2024-us-senate"])[0]
    assert audit_only.view("published-general").counts is None
    assert all(view.status == "unavailable" for view in audit_only.views)
    with pytest.raises(dataclasses.FrozenInstanceError):
        catalogue.years = ()  # type: ignore[misc]


def test_required_anchor_years_follow_actual_race_baseline_ids() -> None:
    catalogue = anchors.inspect_house_anchor_catalogue()
    cycle = resolve_senate_cycle(date(2026, 6, 1))
    assert {race.baseline_id for race in cycle.races if race.special} == {
        "2022-us-senate",
    }
    assert [item.year for item in catalogue.for_baselines(
        race.baseline_id for race in cycle.races
    )] == [2020, 2022]
    future = resolve_senate_cycle(date(2027, 1, 1))
    assert [item.year for item in catalogue.for_baselines(
        race.baseline_id for race in future.races
    )] == [2022]
    assert future.roster is None
    assert [item.year for item in catalogue.for_baselines([
        "2016-us-senate", "2020-us-senate", "2018-us-senate",
    ])] == [2016, 2018, 2020]
    for invalid in (
        [], ["2020-us-house"], ["2020-us-senate-extra"], ["2019-us-senate"],
    ):
        with pytest.raises(anchors.HouseAnchorError):
            catalogue.for_baselines(invalid)


def test_recomputed_payload_hash_cannot_repin_changed_counts(tmp_path: Path) -> None:
    document = _document()
    counts = _object(_object(_array(_year(document, 2)["views"])[0])["counts"])
    counts["D"] = 77548030
    counts["denominator"] = 152644064
    document["payload_sha256"] = _digest(document["payload"])
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(document))
    with pytest.raises(anchors.HouseAnchorError, match="bytes differ"):
        anchors.inspect_house_anchor_catalogue(path)


@pytest.mark.parametrize("change", [
    "schema", "accepted", "activation", "payload-hash", "duplicate-year",
    "duplicate-view", "invalid-year", "count-sum", "boolean-count", "zero-count",
    "source-hash", "source-url", "extraction-hash", "duplicate-source-row",
    "unit-count", "duplicate-unit", "missing-denominator-policy",
    "missing-qualification", "promote-2018", "promote-2024", "null-ready-count",
    "correction-source", "correction-limit",
])
def test_structure_is_validated_beyond_the_outer_pin(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    year = _year(document, 2)
    view = _object(_array(year["views"])[0])
    counts = _object(view["counts"])
    if change == "schema":
        document["schema"] = "v-next"
    elif change == "accepted":
        document["accepted"] = True
    elif change == "activation":
        document["activation_policy"] = "published-general"
    elif change == "duplicate-year":
        _array(_object(document["payload"])["years"])[2] = _year(document, 1)
    elif change == "duplicate-view":
        _array(year["views"])[1] = view
    elif change == "invalid-year":
        year["year"] = 2019
    elif change == "count-sum":
        counts["other"] = 0
    elif change == "boolean-count":
        counts["other"] = True
    elif change == "zero-count":
        counts["D"] = 0
    elif change == "source-hash":
        _object(year["source"])["sha256"] = "not-a-hash"
    elif change == "source-url":
        _object(year["source"])["url"] = "file:///tmp/source.xlsx"
    elif change == "extraction-hash":
        _object(year["extraction"])["selected_records_sha256"] = None
    elif change == "duplicate-source-row":
        _object(year["extraction"])["record_numbers"] = [3, 3]
    elif change == "unit-count":
        year["measured_units"] = 435
    elif change == "duplicate-unit":
        year["unmeasured_units"] = ["FL-25", "FL-25"]
    elif change == "missing-denominator-policy":
        year["denominator_policy"] = ""
    elif change == "missing-qualification":
        view["qualification"] = None
    elif change == "promote-2018":
        _object(_array(_year(document, 1)["views"])[0])["status"] = "pending"
    elif change == "promote-2024":
        _object(_array(_year(document, 4)["views"])[1])["status"] = "pending"
    elif change == "null-ready-count":
        view["counts"] = None
    elif change == "correction-source":
        _object(_array(year["corrections"])[0])["source_identity"] = ""
    elif change == "correction-limit":
        _object(_array(_year(document, 4)["corrections"])[0])["verification_limit"] = ""
    path = _write_candidate(document, tmp_path / "invalid.json", monkeypatch)
    if change == "payload-hash":
        document["payload_sha256"] = "0" * 64
        raw = json.dumps(document).encode()
        path.write_bytes(raw)
        monkeypatch.setattr(
            anchors, "_CATALOGUE_SHA256", hashlib.sha256(raw).hexdigest(),
        )
    with pytest.raises(anchors.HouseAnchorError):
        anchors.inspect_house_anchor_catalogue(path)


def test_workbook_extraction_groups_candidate_fusion_and_ignores_id_collisions(
    tmp_path: Path,
) -> None:
    # This is fabricated evidence for the extractor, not an election candidate.
    path = tmp_path / "synthetic.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Synthetic regular House"
    records: list[list[object]] = []
    for row_number, (candidate, party, votes) in enumerate([
        ("Candidate D", "D", 50), ("Candidate R", "R", 40),
        ("Candidate D", "WF", 10), ("Standalone minor", "CRV", 5),
    ], 1):
        row: list[object] = [None] * 16
        row[1], row[3], row[4] = "NY", "01", "same-broken-FEC-ID"
        row[8], row[10], row[15] = candidate, party, votes
        sheet.append(row)
        records.append([row_number, "NY-01", candidate.casefold(), party, votes])
    # A subtotal and a separate unexpired-term candidate must not be counted.
    subtotal: list[object] = [None] * 16
    subtotal[1], subtotal[3], subtotal[9], subtotal[15] = "NY", "01", "Total", 105
    sheet.append(subtotal)
    special: list[object] = [None] * 16
    special[1], special[3], special[8] = "NY", "01-Unexpired Term", "Candidate D"
    special[10], special[15] = "D", 10000
    sheet.append(special)
    workbook.save(path)
    workbook.close()
    real = anchors.inspect_house_anchor_catalogue().years[0]
    extraction = _object(json.loads(real.extraction_json))
    extraction.update({
        "sheet": sheet.title, "record_numbers": [1, 2, 3, 4],
        "selected_records_sha256": _digest(records),
    })
    views = tuple(dataclasses.replace(
        view, counts=anchors.AnchorCounts(60, 40, 5, 105),
    ) for view in real.views)
    synthetic = dataclasses.replace(
        real, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        measured_units=1, unmeasured_units=(),
        extraction_json=json.dumps(extraction), views=views,
    )
    report = audit_workbook(synthetic, path)
    assert report["published-general"] == {
        "D": 60, "R": 40, "other": 5, "denominator": 105,
    }
    assert report["accepted"] is False
    assert report["activation_policy"] is None
    with pytest.raises(anchors.HouseAnchorError, match="bytes differ"):
        audit_workbook(real, path)


def test_standalone_cli_bootstraps_without_pythonpath(tmp_path: Path) -> None:
    script = anchors.CATALOGUE_PATH.parents[3] / "scripts/audit_us_house_anchors.py"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(script), "--help"], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--correction-pdf" in result.stdout
