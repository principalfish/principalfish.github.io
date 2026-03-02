#!/usr/bin/env python3
"""Export parties/regions metadata into election-maps manifest settings.

Updates `election-maps/data/elections.json` in place, setting:
- settings.parties
- settings.partiesByKey
- settings.regionsByMapId
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

from db import Database
from models import Party, Region
from sqlalchemy import select

from export_non_simulation_elections import (
    OUTPUT_ROOT_DEFAULT,
    build_manifest_party_settings,
    build_manifest_regions_by_map_id,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export parties/regions metadata into elections manifest")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=OUTPUT_ROOT_DEFAULT,
        help="Output directory containing elections.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_root = args.output_root.resolve()
    manifest_path = output_root / "elections.json"

    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    db = Database()
    with db.session() as session:
        parties = session.execute(select(Party)).scalars().all()
        regions = session.execute(select(Region)).scalars().all()

    manifest_parties, manifest_parties_by_key = build_manifest_party_settings(parties)
    manifest_regions_by_map_id = build_manifest_regions_by_map_id(regions)

    settings = manifest.get("settings") or {}
    settings["parties"] = manifest_parties
    settings["partiesByKey"] = manifest_parties_by_key
    settings["regionsByMapId"] = manifest_regions_by_map_id
    manifest["settings"] = settings

    if args.dry_run:
        print(f"Would write manifest metadata: {manifest_path}")
        print(f"parties={len(manifest_parties)} keys={len(manifest_parties_by_key)} maps={len(manifest_regions_by_map_id)}")
        return

    write_json(manifest_path, manifest)
    print(f"Wrote manifest metadata: {manifest_path}")
    print(f"parties={len(manifest_parties)} keys={len(manifest_parties_by_key)} maps={len(manifest_regions_by_map_id)}")


if __name__ == "__main__":
    main()
