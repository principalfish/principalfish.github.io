"""Supplemental legacy-election handling for the election export."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from models import Election, ElectionType, Party, Region
from scripts.export.naming import map_filename_for_map_id, normalize_region_name
from scripts.export.payload import convert_legacy_seatinfo_to_v4
from scripts.export.serialize import write_json


SUPPLEMENTAL_LEGACY_ELECTIONS: list[dict[str, Any]] = [
    {
        "id": "2019-general-changed-boundaries",
        "name": "2019 Election (changed boundaries)",
        "type": ElectionType.uk_general.value,
        "mapId": 2,
        "sourceFile": "2019election_new.json",
        "resultFile": "uk-general-2019-changed-boundaries.json",
        "insertAfterId": "2024-general",
        "noComparison": True,
    }
]


SUPPLEMENTAL_LEGACY_ELECTION_NAMES = {
    str(entry["name"]).strip().lower()
    for entry in SUPPLEMENTAL_LEGACY_ELECTIONS
}


def apply_supplemental_legacy_elections(
    manifest_entries: list[dict[str, Any]],
    map_files_by_id: dict[str, str],
    data_files_by_election_id: dict[str, str],
    results_dir: Path,
    legacy_files_dir: Path,
    dry_run: bool,
    manifest_parties: list[dict[str, Any]] | None = None,
    manifest_regions_by_map_id: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    """Write result files and inject manifest entries for supplemental legacy elections.

    Iterates over ``SUPPLEMENTAL_LEGACY_ELECTIONS``, reads the source JSON
    from ``legacy_files_dir``, optionally converts legacy ``seatInfo``
    payloads to ``pf-results-v4``, writes the result file, and inserts or
    updates the corresponding entry in ``manifest_entries``.

    Args:
        manifest_entries: Manifest election list to update in-place.
        map_files_by_id: ``{str(map_id): relpath}`` dict updated in-place
            with any new map references.
        data_files_by_election_id: ``{election_id: relpath}`` dict updated
            in-place with the supplemental result paths.
        results_dir: Absolute path to the ``results/`` output directory.
        legacy_files_dir: Directory containing legacy source JSON files.
        dry_run: When ``True``, print planned writes instead of executing
            them.
        manifest_parties: Party settings list (used for legacy conversion).
            Pass ``None`` to skip conversion and write the raw payload.
        manifest_regions_by_map_id: Region settings dict (used for legacy
            conversion).  Pass ``None`` to skip conversion.

    Raises:
        FileNotFoundError: If a supplemental source file does not exist
            under ``legacy_files_dir``.
    """
    for supplemental in SUPPLEMENTAL_LEGACY_ELECTIONS:
        election_id = supplemental["id"]
        map_id = int(supplemental["mapId"])
        source_path = legacy_files_dir / supplemental["sourceFile"]
        result_filename = supplemental["resultFile"]
        result_path = results_dir / result_filename

        if not source_path.exists():
            raise FileNotFoundError(f"Supplemental legacy results file not found: {source_path}")

        map_relpath = map_files_by_id.get(str(map_id), f"maps/{map_filename_for_map_id(map_id)}")
        map_files_by_id[str(map_id)] = map_relpath
        data_files_by_election_id[election_id] = f"results/{result_filename}"

        if dry_run:
            print(f"Would write supplemental results: {result_path} (from {source_path.name})")
        else:
            raw_payload = json.loads(source_path.read_text(encoding="utf-8"))
            # Convert legacy seatInfo/partyInfo format to pf-results-v4 if needed
            if manifest_parties and manifest_regions_by_map_id and raw_payload.get("schema") is None:
                party_key_to_id = {p["key"]: p["id"] for p in manifest_parties}
                region_rows = manifest_regions_by_map_id.get(str(map_id)) or []
                region_key_to_id = {
                    normalize_region_name(r["name"]): r["id"]
                    for r in region_rows
                }
                payload = convert_legacy_seatinfo_to_v4(raw_payload, party_key_to_id, region_key_to_id)
            else:
                payload = raw_payload
            write_json(result_path, payload)

        supplemental_entry = {
            "id": election_id,
            "name": supplemental["name"],
            "type": supplemental["type"],
            "mapId": map_id,
            "parliament": supplemental.get("parliament", "westminster"),
        }

        existing_index = next((idx for idx, entry in enumerate(manifest_entries) if entry.get("id") == election_id), None)
        if existing_index is not None:
            manifest_entries[existing_index] = supplemental_entry
            continue

        insert_after_id = supplemental.get("insertAfterId")
        insert_index = next(
            (idx + 1 for idx, entry in enumerate(manifest_entries) if entry.get("id") == insert_after_id),
            len(manifest_entries),
        )
        manifest_entries.insert(insert_index, supplemental_entry)
