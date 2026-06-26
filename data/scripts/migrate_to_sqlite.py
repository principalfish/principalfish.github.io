"""One-time migration: Supabase (Postgres) + model_uns.db → single SQLite elections.db.

Run this ONCE to move off Supabase. It produces a self-contained SQLite file
holding every table, with seat geometry stored as plain WKB blobs and the
model-run archive (model_uns.db) merged into the unified elections/votes tables.

This script is intentionally self-contained: it builds its own Postgres engine
from the ``SUPABASE_*`` environment variables (loaded from the repo-root .env)
and reads geometry via ``ST_AsBinary`` — so it does NOT depend on the app's
``config.py`` / ``models.py`` (which by now target SQLite and no longer carry the
geoalchemy2 Postgres geometry type).

Run from the data root:
  ./election_data/bin/python scripts/migrate_to_sqlite.py
  ./election_data/bin/python scripts/migrate_to_sqlite.py --dry-run
  ./election_data/bin/python scripts/migrate_to_sqlite.py --force
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from shapely import wkb as shapely_wkb
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

ROOT_DIR = Path(__file__).resolve().parent.parent      # .../data
REPO_ROOT = ROOT_DIR.parent
load_dotenv(REPO_ROOT / ".env", override=True)

DEFAULT_DEST = Path("/home/philiph/dbs/elections.db")
DEFAULT_MODEL_UNS = Path("/home/philiph/dbs/model_uns.db")

# DDL mirroring the SQLAlchemy schema in models.py. SQLite is dynamically typed,
# so the declared types only need to be compatible with how the ORM reads them:
#   - geometry: BLOB (plain WKB bytes; the app's GeometryWKB decorator loads it)
#   - elections.type / dates: TEXT (Enum stored by value; dates ISO strings)
#   - votes.elected: INTEGER (SQLAlchemy Boolean ↔ 0/1)
SCHEMA_DDL = """
CREATE TABLE parties (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    short_name TEXT,
    colour TEXT
);
CREATE TABLE maps (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    parliament TEXT NOT NULL DEFAULT 'westminster'
);
CREATE TABLE regions (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES regions(id),
    population INTEGER
);
CREATE TABLE seats (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    seat_name TEXT NOT NULL,
    region_id INTEGER REFERENCES regions(id),
    electorate INTEGER,
    geometry BLOB
);
CREATE TABLE elections (
    id INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    year INTEGER NOT NULL,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    parent_election_id INTEGER REFERENCES elections(id),
    election_date TEXT
);
CREATE TABLE votes (
    id INTEGER PRIMARY KEY,
    election_id INTEGER NOT NULL REFERENCES elections(id),
    seat_id INTEGER NOT NULL REFERENCES seats(id),
    party_id INTEGER REFERENCES parties(id),
    candidate_name TEXT,
    vote_total REAL,
    elected INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE pollsters (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    identifier TEXT NOT NULL UNIQUE,
    weight REAL,
    regions_mapping TEXT
);
CREATE TABLE polls (
    id INTEGER PRIMARY KEY,
    pollster_id INTEGER NOT NULL REFERENCES pollsters(id),
    map_id INTEGER NOT NULL REFERENCES maps(id),
    fieldwork_start TEXT NOT NULL,
    fieldwork_end TEXT NOT NULL,
    sample_size INTEGER,
    source_url TEXT
);
CREATE TABLE poll_rows (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    region_id INTEGER REFERENCES regions(id),
    party_id INTEGER NOT NULL REFERENCES parties(id),
    percentage REAL NOT NULL
);
CREATE INDEX idx_votes_election_id ON votes(election_id);
CREATE INDEX idx_votes_seat_id ON votes(seat_id);
CREATE INDEX idx_elections_name ON elections(name);
CREATE INDEX idx_poll_rows_poll_id ON poll_rows(poll_id);
"""

# Remap of Supabase election ids that collide with the model_uns.db archive id
# space. Populated in main() before any copying. The model archive has the
# larger, actively-growing id sequence, so the small set of colliding Supabase
# elections (Holyrood) is moved out of the way instead — preserving the archive's
# native ids and the "latest model run by id" semantics that the app relies on.
ELECTION_REMAP: dict[int, int] = {}
_REMAP_BASE = 100000


def _rid(election_id: int | None) -> int | None:
    """Translate an election id through ELECTION_REMAP (identity if absent)."""
    if election_id is None:
        return None
    return ELECTION_REMAP.get(election_id, election_id)


def _iso(value: Any) -> str | None:
    """Render a date as an ISO string for SQLite, or pass through None/str."""
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _wkb(value: Any) -> bytes | None:
    """Normalise an ST_AsBinary result (bytes/memoryview) to plain bytes or None."""
    if value is None:
        return None
    return bytes(value)


# Per-table spec: (table, source SELECT body, dest column list, row transformer).
# The SELECT returns columns in the same order as the dest column list. The
# transformer applies id remapping, date→ISO, bool→int, and geometry→bytes.
_TABLE_SPECS: list[tuple[str, str, list[str], Callable[[Any], tuple]]] = [
    ("parties", "id, name, short_name, colour",
     ["id", "name", "short_name", "colour"],
     lambda r: (r[0], r[1], r[2], r[3])),
    ("maps", "id, name, parliament",
     ["id", "name", "parliament"],
     lambda r: (r[0], r[1], r[2])),
    ("regions", "id, map_id, name, parent_id, population",
     ["id", "map_id", "name", "parent_id", "population"],
     lambda r: (r[0], r[1], r[2], r[3], r[4])),
    ("seats", "id, map_id, seat_name, region_id, electorate, ST_AsBinary(geometry)",
     ["id", "map_id", "seat_name", "region_id", "electorate", "geometry"],
     lambda r: (r[0], r[1], r[2], r[3], r[4], _wkb(r[5]))),
    ("elections", "id, map_id, year, name, type, parent_election_id, election_date",
     ["id", "map_id", "year", "name", "type", "parent_election_id", "election_date"],
     lambda r: (_rid(r[0]), r[1], r[2], r[3], r[4], _rid(r[5]), _iso(r[6]))),
    ("votes", "id, election_id, seat_id, party_id, candidate_name, vote_total, elected",
     ["id", "election_id", "seat_id", "party_id", "candidate_name", "vote_total", "elected"],
     lambda r: (r[0], _rid(r[1]), r[2], r[3], r[4], r[5], int(bool(r[6])))),
    ("pollsters", "id, name, identifier, weight, regions_mapping",
     ["id", "name", "identifier", "weight", "regions_mapping"],
     lambda r: (r[0], r[1], r[2], r[3], r[4])),
    ("polls", "id, pollster_id, map_id, fieldwork_start, fieldwork_end, sample_size, source_url",
     ["id", "pollster_id", "map_id", "fieldwork_start", "fieldwork_end", "sample_size", "source_url"],
     lambda r: (r[0], r[1], r[2], _iso(r[3]), _iso(r[4]), r[5], r[6])),
    ("poll_rows", "id, poll_id, region_id, party_id, percentage",
     ["id", "poll_id", "region_id", "party_id", "percentage"],
     lambda r: (r[0], r[1], r[2], r[3], r[4])),
]

_BATCH = 2000


def _source_engine() -> Engine:
    """Build a Postgres engine for the Supabase session pooler from env vars."""
    region = os.environ.get("SUPABASE_REGION")
    user = os.environ.get("SUPABASE_DB_USERNAME")
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if not (region and user and password):
        raise SystemExit("SUPABASE_REGION / SUPABASE_DB_USERNAME / SUPABASE_DB_PASSWORD must be set.")
    host = f"{region}.pooler.supabase.com"
    url = f"postgresql://{user}:{password}@{host}:5432/postgres"
    return create_engine(url, hide_parameters=True)


def _insert_sql(table: str, columns: list[str]) -> str:
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"


def _copy_table(
    engine: Engine,
    dest: sqlite3.Connection,
    table: str,
    select_body: str,
    columns: list[str],
    transform: Callable[[Any], tuple],
    *,
    dry_run: bool,
) -> int:
    """Stream all rows of one Supabase table into the SQLite destination."""
    sql = _insert_sql(table, columns)
    count = 0
    batch: list[tuple] = []
    with engine.connect().execution_options(stream_results=True, yield_per=_BATCH) as conn:
        result = conn.execute(text(f"SELECT {select_body} FROM {table}"))
        for row in result:
            count += 1
            if dry_run:
                continue
            batch.append(transform(row))
            if len(batch) >= _BATCH:
                dest.executemany(sql, batch)
                batch.clear()
        if batch and not dry_run:
            dest.executemany(sql, batch)
    if not dry_run:
        dest.commit()
    print(f"  {'[dry-run] would copy' if dry_run else 'copied'} {count:>8} {table}")
    return count


def _archive_election_ids(model_uns_path: Path) -> set[int]:
    """Return the set of election ids present in the model_uns archive."""
    if not model_uns_path.exists():
        return set()
    src = sqlite3.connect(f"file:{model_uns_path}?mode=ro", uri=True)
    try:
        return {r[0] for r in src.execute("SELECT id FROM elections").fetchall()}
    finally:
        src.close()


def _merge_model_uns(dest: sqlite3.Connection, model_uns_path: Path, *, dry_run: bool) -> tuple[int, int]:
    """Merge the model_uns.db archive into the unified elections/votes tables.

    The archive keeps its native ids — the colliding Supabase elections were
    already remapped out of the way (see ELECTION_REMAP), so a plain INSERT is
    safe. The archive's ``election_type`` column maps to the unified ``type``
    column. ``seat_id``/``party_id`` already reference the shared id space.
    """
    if not model_uns_path.exists():
        print(f"  model_uns archive not found ({model_uns_path}) — skipping merge")
        return (0, 0)

    src = sqlite3.connect(f"file:{model_uns_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        elections = src.execute(
            "SELECT id, map_id, year, name, election_type, election_date FROM elections"
        ).fetchall()

        if not dry_run:
            existing = {r[0] for r in dest.execute("SELECT id FROM elections").fetchall()}
            clash = [e["id"] for e in elections if e["id"] in existing]
            if clash:
                raise SystemExit(f"Archive id collision not resolved: {clash[:10]}")
            dest.executemany(
                "INSERT INTO elections (id, map_id, year, name, type, election_date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(e["id"], e["map_id"], e["year"], e["name"], e["election_type"], e["election_date"])
                 for e in elections],
            )

        n_votes = 0
        insert_sql = (
            "INSERT INTO votes (id, election_id, seat_id, party_id, candidate_name, vote_total, elected) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        batch: list[tuple] = []
        for v in src.execute(
            "SELECT id, election_id, seat_id, party_id, candidate_name, vote_total, elected FROM votes"
        ):
            n_votes += 1
            if dry_run:
                continue
            batch.append((v["id"], v["election_id"], v["seat_id"], v["party_id"],
                          v["candidate_name"], v["vote_total"], int(bool(v["elected"]))))
            if len(batch) >= _BATCH:
                dest.executemany(insert_sql, batch)
                batch.clear()
        if batch and not dry_run:
            dest.executemany(insert_sql, batch)
        if not dry_run:
            dest.commit()
    finally:
        src.close()

    print(f"  {'[dry-run] would merge' if dry_run else 'merged'} {len(elections)} model_uns "
          f"elections, {n_votes} votes")
    return (len(elections), n_votes)


def _validate_geometry(engine: Engine, dest: sqlite3.Connection) -> None:
    """Round-trip one seat geometry and compare its area against PostGIS ST_Area."""
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, ST_Area(geometry) FROM seats WHERE geometry IS NOT NULL LIMIT 1"
        )).first()
    if row is None:
        print("  no seat geometry present to validate")
        return
    seat_id, src_area = row[0], float(row[1])
    blob = dest.execute("SELECT geometry FROM seats WHERE id = ?", (seat_id,)).fetchone()[0]
    dest_area = shapely_wkb.loads(bytes(blob)).area
    ok = abs(src_area - dest_area) < 1e-6
    print(f"  geometry round-trip seat {seat_id}: src_area={src_area:.6f} "
          f"dest_area={dest_area:.6f} {'OK' if ok else 'MISMATCH'}")
    if not ok:
        raise SystemExit("Geometry round-trip mismatch — aborting")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Supabase + model_uns.db into one SQLite file.")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="Destination SQLite path")
    parser.add_argument("--model-uns", type=Path, default=DEFAULT_MODEL_UNS, help="model_uns.db archive to merge")
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing destination file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = _source_engine()

    if args.dest.exists() and not args.dry_run and not args.force:
        print(f"Destination {args.dest} already exists — use --force to overwrite.", file=sys.stderr)
        return 1

    print(f"Migrating Supabase → {args.dest}{' (dry-run)' if args.dry_run else ''}")

    # Resolve id collisions: any Supabase election whose id falls inside the
    # archive's id space is remapped to a fresh id (the archive keeps its own).
    archive_ids = _archive_election_ids(args.model_uns)
    with engine.connect() as conn:
        supabase_ids = {r[0] for r in conn.execute(text("SELECT id FROM elections"))}
    collisions = sorted(archive_ids & supabase_ids)
    ELECTION_REMAP.clear()
    ELECTION_REMAP.update({old: _REMAP_BASE + i for i, old in enumerate(collisions, start=1)})
    if collisions:
        print(f"  remapping {len(collisions)} colliding Supabase election ids → "
              f"{_REMAP_BASE + 1}..{_REMAP_BASE + len(collisions)}: {collisions}")

    if not args.dry_run:
        args.dest.parent.mkdir(parents=True, exist_ok=True)
        if args.dest.exists():
            args.dest.unlink()
        dest_conn = sqlite3.connect(args.dest)
    else:
        dest_conn = sqlite3.connect(":memory:")
    dest_conn.executescript("PRAGMA foreign_keys=OFF;")
    dest_conn.executescript(SCHEMA_DDL)

    try:
        print("Copying Supabase tables:")
        for table, select_body, columns, transform in _TABLE_SPECS:
            _copy_table(engine, dest_conn, table, select_body, columns, transform, dry_run=args.dry_run)

        print("Merging model_uns archive:")
        _merge_model_uns(dest_conn, args.model_uns, dry_run=args.dry_run)

        if not args.dry_run:
            print("Validating:")
            _validate_geometry(engine, dest_conn)
            print("Final row counts:")
            for table, _, _, _ in _TABLE_SPECS:
                n = dest_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table:>12}: {n}")
            integrity = dest_conn.execute("PRAGMA integrity_check;").fetchone()[0]
            print(f"  integrity_check: {integrity}")
            fk_violations = dest_conn.execute("PRAGMA foreign_key_check;").fetchall()
            print(f"  foreign_key_check: {'ok' if not fk_violations else fk_violations[:10]}")
            if fk_violations:
                raise SystemExit("Foreign key violations after migration — aborting")
    finally:
        dest_conn.close()

    print("Done." if not args.dry_run else "Dry-run complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
