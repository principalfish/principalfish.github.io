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
    },
    {
        # The current Senate composition is a snapshot, not an election — its result file is
        # generated directly by convert_senate_current.py (prebuilt), so the export only
        # registers the manifest entry. Shares the 50-state Senate geometry (map 23).
        "id": "current-senate",
        "name": "Current Senate",
        "type": ElectionType.us_senate.value,
        "mapId": 23,
        "parliament": "us_senate",
        "resultFile": "senate-current.json",
        "prebuilt": True,
        "multiMember": True,
        "insertBeforeId": "2024-us-senate",
        "noComparison": True,
    },
]


SUPPLEMENTAL_LEGACY_ELECTION_NAMES = {
    str(entry["name"]).strip().lower()
    for entry in SUPPLEMENTAL_LEGACY_ELECTIONS
}


def reposition_supplemental_entries(
    manifest_entries: list[dict[str, Any]],
    parliaments: set[str] | None = None,
) -> None:
    """Re-apply each supplemental's ``insertBeforeId`` / ``insertAfterId`` position in-place.

    ``reorder_manifest_entries`` sorts entries by the previous manifest's order, which can
    override the position ``apply_supplemental_legacy_elections`` gave a supplemental (e.g.
    a newly-promoted "Current Senate" that should lead its parliament). Running this after
    the reorder restores the configured position. Idempotent.

    Args:
        manifest_entries: Manifest election list to reorder in-place.
    """
    by_id = {entry.get("id"): entry for entry in manifest_entries}
    for supplemental in SUPPLEMENTAL_LEGACY_ELECTIONS:
        if parliaments is not None and supplemental.get("parliament", "westminster") not in parliaments:
            continue
        before_id = supplemental.get("insertBeforeId")
        after_id = supplemental.get("insertAfterId")
        entry = by_id.get(supplemental["id"])
        if entry is None or (before_id is None and after_id is None):
            continue
        manifest_entries.remove(entry)
        if before_id is not None:
            index = next((i for i, e in enumerate(manifest_entries) if e.get("id") == before_id), len(manifest_entries))
        else:
            index = next((i + 1 for i, e in enumerate(manifest_entries) if e.get("id") == after_id), len(manifest_entries))
        manifest_entries.insert(index, entry)


def apply_supplemental_legacy_elections(
    manifest_entries: list[dict[str, Any]],
    map_files_by_id: dict[str, str],
    data_files_by_election_id: dict[str, str],
    results_dir: Path,
    legacy_files_dir: Path,
    dry_run: bool,
    manifest_parties: list[dict[str, Any]] | None = None,
    manifest_regions_by_map_id: dict[str, list[dict[str, Any]]] | None = None,
    parliaments: set[str] | None = None,
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
        if parliaments is not None and supplemental.get("parliament", "westminster") not in parliaments:
            continue
        election_id = supplemental["id"]
        map_id = int(supplemental["mapId"])
        result_filename = supplemental["resultFile"]
        result_path = results_dir / result_filename

        map_relpath = map_files_by_id.get(str(map_id), f"maps/{map_filename_for_map_id(map_id)}")
        map_files_by_id[str(map_id)] = map_relpath
        data_files_by_election_id[election_id] = f"results/{result_filename}"

        # Prebuilt entries (e.g. the Current Senate snapshot) have their result file generated
        # by a separate converter; the export only registers the manifest entry.
        if supplemental.get("prebuilt"):
            if not result_path.exists():
                print(f"WARNING: prebuilt result file missing: {result_path} — run its converter first")
            elif dry_run:
                print(f"Would register prebuilt supplemental: {result_path}")
        elif (source_path := legacy_files_dir / supplemental["sourceFile"]) and not source_path.exists():
            raise FileNotFoundError(f"Supplemental legacy results file not found: {source_path}")
        elif dry_run:
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
        # A multi-member chamber (each map seat returns more than one member): drives the
        # front-end to tally members for the majority subtitle and to hide the vote columns.
        if supplemental.get("multiMember"):
            supplemental_entry["multiMember"] = True

        existing_index = next((idx for idx, entry in enumerate(manifest_entries) if entry.get("id") == election_id), None)
        if existing_index is not None:
            manifest_entries[existing_index] = supplemental_entry
            continue

        insert_before_id = supplemental.get("insertBeforeId")
        if insert_before_id is not None:
            insert_index = next(
                (idx for idx, entry in enumerate(manifest_entries) if entry.get("id") == insert_before_id),
                len(manifest_entries),
            )
        else:
            insert_after_id = supplemental.get("insertAfterId")
            insert_index = next(
                (idx + 1 for idx, entry in enumerate(manifest_entries) if entry.get("id") == insert_after_id),
                len(manifest_entries),
            )
        manifest_entries.insert(insert_index, supplemental_entry)
