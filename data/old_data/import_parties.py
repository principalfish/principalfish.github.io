"""
Import party rows into the parties table.

Usage:
    python old_data/import_parties.py
    python old_data/import_parties.py --dry-run
    python old_data/import_parties.py --skip-existing
    python old_data/import_parties.py --skip-existing --dry-run

Behavior:
    - Ensures a baseline set of parties exist.
    - Generates short_name using rules:
        - spaces/punctuation removed
        - acronyms where commonly used (e.g. DUP, SDLP, SNP, UUP)
    - Applies party colours for known UK parties.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from models import Party


PARTY_DEFINITIONS = (
    {"name": "Alliance", "colour": "#F6CB2F"},
    {"name": "Conservative", "colour": "#0087DC"},
    {"name": "Democratic Unionist Party", "colour": "#D46A4C"},
    {"name": "Green", "colour": "#6AB023"},
    {"name": "Labour", "colour": "#E4003B"},
    {"name": "Liberal Democrats", "colour": "#FAA61A"},
    {"name": "Other", "colour": "#808080"},
    {"name": "Others", "colour": "#808080"},
    {"name": "Plaid Cymru", "colour": "#005B54"},
    {"name": "Reform UK", "colour": "#12B6CF"},
    {"name": "SDLP", "colour": "#2AA82C"},
    {"name": "Scottish National Party", "colour": "#FFF95D"},
    {"name": "Sinn Féin", "colour": "#326760"},
    {"name": "Ulster Unionist Party", "colour": "#48A5EE"},
)

ACRONYM_PARTIES = {
    "Democratic Unionist Party",
    "Social Democratic and Labour Party",
    "Scottish National Party",
    "Ulster Unionist Party",
    "SDLP",
}


def generate_short_name(name: str) -> str:
    if name.isupper() and len(name) <= 6:
        return name

    if name in ACRONYM_PARTIES:
        words = [part for part in name.split() if part.lower() not in {"and", "of", "the"}]
        return "".join(word[0] for word in words if word).upper()

    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(char for char in normalized if not unicodedata.combining(char))
    compact = "".join(char for char in ascii_name if char.isalnum())
    return compact.lower()


def upsert_party(
    db: Database,
    name: str,
    short_name: str,
    colour: str | None,
    dry_run: bool,
    skip_existing: bool,
) -> str:
    existing = db.get_party_by_name(name)

    if existing is None:
        if dry_run:
            return (
                f"[dry-run] would create: {name} "
                f"(short_name={short_name!r}, colour={colour!r})"
            )
        db.add_party(name=name, short_name=short_name, colour=colour)
        return f"created: {name} (short_name={short_name!r}, colour={colour!r})"

    if skip_existing:
        return f"skipped existing: {name}"

    needs_short_name_update = existing.short_name != short_name
    needs_colour_update = existing.colour != colour
    if needs_short_name_update or needs_colour_update:
        if dry_run:
            changes: list[str] = []
            if needs_short_name_update:
                changes.append(f"short_name {existing.short_name!r} -> {short_name!r}")
            if needs_colour_update:
                changes.append(f"colour {existing.colour!r} -> {colour!r}")
            return f"[dry-run] would update: {name} ({', '.join(changes)})"

        with db.session() as session:
            db_party = session.get(Party, existing.id)
            if db_party is not None:
                db_party.short_name = short_name
                db_party.colour = colour

        changes = []
        if needs_short_name_update:
            changes.append(f"short_name {existing.short_name!r} -> {short_name!r}")
        if needs_colour_update:
            changes.append(f"colour {existing.colour!r} -> {colour!r}")
        return f"updated: {name} ({', '.join(changes)})"

    return f"unchanged: {name}"


def drop_long_name_column(db: Database, dry_run: bool) -> None:
    statement = "ALTER TABLE IF EXISTS parties DROP COLUMN IF EXISTS long_name"
    if dry_run:
        print(f"- [dry-run] would run: {statement}")
        return

    with db.engine.begin() as connection:
        connection.execute(text(statement))
    print("- removed obsolete column: parties.long_name (if it existed)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview inserts/updates without writing",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip existing parties instead of updating them",
    )
    args = parser.parse_args()

    db = Database()
    db.create_tables()

    print("Applying schema cleanup...")
    drop_long_name_column(db, args.dry_run)

    created = 0
    updated = 0
    unchanged = 0

    print("Importing parties...")
    for party in PARTY_DEFINITIONS:
        name = party["name"]
        colour = party.get("colour")
        short_name = generate_short_name(name)
        result = upsert_party(db, name, short_name, colour, args.dry_run, args.skip_existing)
        print(f"- {result}")

        if "created:" in result or "would create:" in result:
            created += 1
        elif "updated:" in result or "would update:" in result:
            updated += 1
        else:
            unchanged += 1

    print("\n--- Party Import Summary ---")
    print(f"Created: {created}")
    print(f"Updated: {updated}")
    print(f"Unchanged: {unchanged}")
    if args.dry_run:
        print("Dry-run mode: no database writes")


if __name__ == "__main__":
    main()
