#!/usr/bin/env python3
"""Migrate existing election results and elections.json to the v4 payload format.

Changes applied:
- results files (pf-results-v3 → pf-results-v4):
    - r: string region key → integer region ID (from elections.json regionsByMapId)
    - Remove e (electorate) and t (turnout) fields
    - p tuples: remove 3rd element (candidate/party name), skip zero-value entries
    - Round float vote values to 2 decimal places
- elections.json settings:
    - Remove partiesByKey
    - Remove byElectionFilesByElectionId
- elections entries:
    - Remove mapFile
    - Remove dataFile

Usage:
    python data/scripts/migrate_results_to_v4.py
    python data/scripts/migrate_results_to_v4.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_ROOT = REPO_ROOT / "electionmaps" / "data"
ELECTIONS_JSON = OUTPUT_ROOT / "elections.json"


def normalize_region_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def normalize_vote_total(value: float) -> int | float:
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def build_region_key_to_id_by_map(manifest: dict) -> dict[int, dict[str, int]]:
    """Build per-mapId mapping from normalized region key to region ID."""
    by_map: dict[int, dict[str, int]] = {}
    for map_id_str, region_rows in (manifest.get("settings") or {}).get("regionsByMapId", {}).items():
        map_id = int(map_id_str)
        mapping: dict[str, int] = {}
        for region in region_rows:
            key = normalize_region_key(region.get("name", ""))
            if key and region.get("id"):
                mapping[key] = region["id"]
        by_map[map_id] = mapping
    return by_map


def build_party_key_to_id(manifest: dict) -> dict[str, int]:
    """Build a mapping from canonical party key to party ID from elections.json parties array."""
    return {
        p["key"]: p["id"]
        for p in (manifest.get("settings") or {}).get("parties") or []
        if p.get("key") and p.get("id")
    }


def build_results_file_to_map_id(manifest: dict) -> dict[str, int]:
    """Build a mapping from results filename to mapId."""
    settings = manifest.get("settings") or {}
    data_files = settings.get("dataFilesByElectionId") or {}

    # election id → mapId from elections array
    election_to_map: dict[str, int] = {}
    for election in manifest.get("elections") or []:
        map_id = election.get("mapId")
        if map_id:
            election_to_map[election["id"]] = map_id

    # results filename → mapId
    file_to_map: dict[str, int] = {}
    for election_id, relpath in data_files.items():
        map_id = election_to_map.get(election_id)
        if map_id:
            filename = Path(relpath).name
            file_to_map[filename] = map_id

    return file_to_map


def convert_legacy_to_v4_seats(
    data: dict,
    region_key_to_id: dict[str, int],
    party_key_to_id: dict[str, int],
) -> list[dict]:
    """Convert legacy seatInfo/partyInfo keyed-by-name format to v4 seats list."""
    new_seats = []
    for seat_name, value in data.items():
        if not isinstance(value, dict) or "seatInfo" not in value:
            continue
        seat_info = value["seatInfo"]
        party_info = value.get("partyInfo") or {}

        region_key = normalize_region_key(seat_info.get("region") or "")
        region_id = region_key_to_id.get(region_key, 0)

        winner_raw = seat_info.get("current") or ""
        winner_key_norm = re.sub(r"[^a-z0-9]", "", winner_raw.lower())
        winner_id = party_key_to_id.get(winner_key_norm, 0)

        compact = []
        for pkey, pdata in party_info.items():
            total = normalize_vote_total(float((pdata.get("total") or 0)))
            if total <= 0:
                continue
            norm_key = re.sub(r"[^a-z0-9]", "", pkey.lower())
            pid = party_key_to_id.get(norm_key, 0)
            compact.append([pid, total])

        compact.sort(key=lambda row: float(row[1]), reverse=True)

        new_seats.append({
            "n": seat_name,
            "r": region_id,
            "w": winner_id,
            "p": compact,
        })

    return sorted(new_seats, key=lambda s: s["n"])


def migrate_results_file(
    path: Path,
    region_key_to_id_by_map: dict[int, dict[str, int]],
    file_to_map_id: dict[str, int],
    party_key_to_id: dict[str, int],
    dry_run: bool,
) -> bool:
    """Migrate a single results file to v4. Returns True if changed."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    schema = data.get("schema")
    is_legacy = schema is None and isinstance(data, dict) and not data.get("seats")

    if schema not in ("pf-results-v3", None) or (schema is None and not is_legacy):
        print(f"  Skipping {path.name}: schema={schema!r}")
        return False

    map_id = file_to_map_id.get(path.name)
    if map_id is None:
        print(f"  Skipping {path.name}: no mapId found in elections.json")
        return False

    region_key_to_id = region_key_to_id_by_map.get(map_id, {})
    if not region_key_to_id:
        print(f"  Skipping {path.name}: no region mapping for mapId={map_id}")
        return False

    if is_legacy:
        new_seats = convert_legacy_to_v4_seats(data, region_key_to_id, party_key_to_id)
    else:
        seats = data.get("seats") or []
        new_seats = []
        for seat in seats:
            # Resolve region key → region ID
            raw_region = seat.get("r") or seat.get("region") or ""
            if isinstance(raw_region, int):
                region_id = raw_region
            else:
                region_key = normalize_region_key(str(raw_region))
                region_id = region_key_to_id.get(region_key, 0)

            # Compact party rows: remove name, skip zeros, round floats
            compact = []
            for entry in seat.get("p") or []:
                if not isinstance(entry, list) or len(entry) < 2:
                    continue
                pid = entry[0]
                total = normalize_vote_total(float(entry[1] or 0))
                if float(total) <= 0:
                    continue
                compact.append([pid, total])

            new_seat: dict = {
                "n": seat.get("n") or seat.get("seat") or "",
                "r": region_id,
                "w": seat.get("w") if seat.get("w") is not None else seat.get("winner"),
                "p": compact,
            }
            new_seats.append(new_seat)

    new_data = {"schema": "pf-results-v4", "seats": new_seats}
    new_raw = json.dumps(new_data, ensure_ascii=False, separators=(",", ":"))

    if dry_run:
        old_size = len(raw.encode())
        new_size = len(new_raw.encode())
        print(f"  {path.name}: {old_size//1024}KB → {new_size//1024}KB (saved {(old_size-new_size)//1024}KB)")
        return True

    path.write_text(new_raw, encoding="utf-8")
    old_size = len(raw.encode())
    new_size = len(new_raw.encode())
    print(f"  {path.name}: {old_size//1024}KB → {new_size//1024}KB (saved {(old_size-new_size)//1024}KB)")
    return True


def migrate_elections_json(path: Path, dry_run: bool) -> None:
    """Migrate elections.json: remove partiesByKey, byElectionFilesByElectionId, mapFile, dataFile."""
    data = json.loads(path.read_text(encoding="utf-8"))

    settings = data.get("settings") or {}
    settings.pop("partiesByKey", None)
    settings.pop("byElectionFilesByElectionId", None)
    data["settings"] = settings

    for entry in data.get("elections") or []:
        entry.pop("mapFile", None)
        entry.pop("dataFile", None)

    new_raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if dry_run:
        print(f"  elections.json: would remove partiesByKey, byElectionFilesByElectionId, mapFile/dataFile from entries")
        return
    path.write_text(new_raw, encoding="utf-8")
    print(f"  elections.json: removed partiesByKey, byElectionFilesByElectionId, mapFile/dataFile from entries")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate election results files to v4 format")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    manifest = json.loads(ELECTIONS_JSON.read_text(encoding="utf-8"))
    region_key_to_id_by_map = build_region_key_to_id_by_map(manifest)
    file_to_map_id = build_results_file_to_map_id(manifest)
    party_key_to_id = build_party_key_to_id(manifest)

    results_dir = OUTPUT_ROOT / "results"
    print("\nMigrating results files:")
    for results_file in sorted(results_dir.glob("*.json")):
        if results_file.name == "model_output_trends_meta.json":
            continue
        migrate_results_file(results_file, region_key_to_id_by_map, file_to_map_id, party_key_to_id, dry_run=args.dry_run)

    print("\nMigrating elections.json:")
    migrate_elections_json(ELECTIONS_JSON, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
