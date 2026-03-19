#!/usr/bin/env python3
"""Export parties/regions metadata into electionmaps manifest settings.

Updates `electionmaps/data/elections.json` in place, setting:
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
    """Parse command-line arguments for the metadata export script.

    Returns:
        Parsed argument namespace with the following attributes:
            output_root (Path): Directory containing ``elections.json``.
                Defaults to ``OUTPUT_ROOT_DEFAULT`` from
                ``export_non_simulation_elections``.
            dry_run (bool): When True, preview actions without writing any files.
    """
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
    """Load parties and regions from the database and update the elections manifest.

    Reads ``elections.json`` from the resolved output root, queries all
    ``Party`` and ``Region`` rows, rebuilds ``settings.parties`` and
    ``settings.regionsByMapId`` using helpers from
    ``export_non_simulation_elections``, removes the legacy
    ``settings.partiesByKey`` key, and writes the manifest back in place.

    In dry-run mode the file is not written; a summary is printed instead.

    Raises:
        FileNotFoundError: If ``elections.json`` does not exist at the
            resolved output path.
    """
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

    manifest_parties = build_manifest_party_settings(parties)
    manifest_regions_by_map_id = build_manifest_regions_by_map_id(regions)

    settings = manifest.get("settings") or {}
    settings["parties"] = manifest_parties
    settings.pop("partiesByKey", None)
    settings["regionsByMapId"] = manifest_regions_by_map_id
    manifest["settings"] = settings

    if args.dry_run:
        print(f"Would write manifest metadata: {manifest_path}")
        print(f"parties={len(manifest_parties)} maps={len(manifest_regions_by_map_id)}")
        return

    write_json(manifest_path, manifest)
    print(f"Wrote manifest metadata: {manifest_path}")
    print(f"parties={len(manifest_parties)} maps={len(manifest_regions_by_map_id)}")


if __name__ == "__main__":
    main()
