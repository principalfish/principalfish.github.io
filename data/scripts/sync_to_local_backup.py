"""Full resync tool: copy all data from Supabase to local Postgres.

Day-to-day mirroring is handled automatically by ``Database.session()`` in
``db.py`` — every successful primary commit is replicated to local Postgres
without any manual intervention.

Use this script for full recovery when the local backup has fallen behind or
when IDs have diverged (e.g. after recreating the local Docker volume, or to
bootstrap a fresh local DB).

By default this script does a safe upsert (merge by PK) — existing local rows
with the same ID are updated, new rows are inserted. Use ``--truncate`` to
wipe all local data first and do a clean re-import (required when local IDs
differ from Supabase IDs, e.g. after running import_all.sh locally).

Only meaningful when Supabase environment variables are configured. When local
Postgres is already the primary (no SUPABASE_* vars set), this script exits
immediately with a no-op message.

Syncs in foreign-key dependency order:
  parties → maps → regions → seats → elections → votes
  → pollsters → polls → poll_rows

Run from data root:
  ./election_data/bin/python scripts/sync_to_local_backup.py
  ./election_data/bin/python scripts/sync_to_local_backup.py --truncate
  ./election_data/bin/python scripts/sync_to_local_backup.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config import DatabaseConfig
from db import Database
from models import Election, Map, Party, Poll, PollRow, Pollster, Region, Seat, Vote

# All managed tables in FK-dependency order (parents before children).
_SYNC_TABLES: list[tuple[type[Any], str]] = [
    (Party, "parties"),
    (Map, "maps"),
    (Region, "regions"),
    (Seat, "seats"),
    (Election, "elections"),
    (Vote, "votes"),
    (Pollster, "pollsters"),
    (Poll, "polls"),
    (PollRow, "poll_rows"),
]

# Top-level tables with no FK parents — truncating these with CASCADE
# removes all dependent rows in a single statement.
_TRUNCATE_ROOTS = ("parties", "maps", "pollsters")


def _col_names(model_class: type[Any]) -> list[str]:
    """Return column attribute names for a SQLAlchemy declarative model.

    Args:
        model_class: A SQLAlchemy ORM model class with a ``__table__`` attribute.

    Returns:
        List of column key names in table definition order.
    """
    return [col.key for col in model_class.__table__.columns]


def _truncate_all(backup_db: Database) -> None:
    """Truncate all managed tables in the backup DB via CASCADE.

    Args:
        backup_db: Destination database (local Postgres).
    """
    roots = ", ".join(_TRUNCATE_ROOTS)
    with backup_db.engine.connect() as conn:
        conn.execute(text(f"TRUNCATE {roots} CASCADE"))
        conn.commit()
    print(f"  Truncated: {roots} (CASCADE)")


def _sync_table(
    primary_db: Database,
    backup_db: Database,
    model_class: type[Any],
    label: str,
    *,
    dry_run: bool = False,
) -> int:
    """Sync all rows of a single table from primary to backup via merge (upsert by PK).

    Reads every row from the primary database, then calls ``session.merge()``
    on the backup session for each row. This inserts rows that don't exist in
    the backup and updates rows that do, keyed on the primary key (``id``).

    Call tables in FK-dependency order so parent rows exist before children.

    Args:
        primary_db: Source database (Supabase).
        backup_db: Destination database (local Postgres).
        model_class: SQLAlchemy declarative model class to sync.
        label: Human-readable table name used in log output.
        dry_run: If True, log what would be synced without writing anything.

    Returns:
        Number of rows that were (or would be) synced.
    """
    cols = _col_names(model_class)

    with primary_db.session() as ps:
        rows = ps.execute(select(model_class)).scalars().all()
        row_dicts: list[dict[str, Any]] = [
            {col: getattr(row, col) for col in cols}
            for row in rows
        ]

    if dry_run:
        print(f"  [dry-run] Would sync {len(row_dicts)} {label} rows")
        return len(row_dicts)

    with backup_db.session() as bs:
        for d in row_dicts:
            bs.merge(model_class(**d))

    print(f"  Synced {len(row_dicts)} {label} rows")
    return len(row_dicts)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for this script.

    Supported flags:

    - ``--truncate``: Wipe all local data before syncing (clean re-import).
    - ``--dry-run``: Log what would be synced without writing to the backup.

    Returns:
        An ``argparse.Namespace`` with attributes ``truncate`` and ``dry_run``.
    """
    parser = argparse.ArgumentParser(
        description="Sync all data from primary (Supabase) to local Postgres backup."
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help=(
            "Truncate all local tables before syncing. "
            "Required when local IDs differ from Supabase IDs."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be synced without writing to the backup database",
    )
    return parser.parse_args()


def main() -> int:
    """Sync all tables from Supabase to local Postgres backup.

    When Supabase is not configured (no SUPABASE_* env vars), this exits
    immediately as a no-op — local Postgres is already the primary and
    there is nothing to back up.

    Returns:
        0 on success or when Supabase is not configured (no-op), 1 on error.
    """
    args = parse_args()

    primary_config = DatabaseConfig.from_env()
    if not (
        primary_config.supabase_region
        and primary_config.supabase_db_username
        and primary_config.supabase_db_password
    ):
        print("Supabase not configured — local Postgres is the primary; nothing to back up.")
        return 0

    backup_config = DatabaseConfig.local()
    primary_db = Database(primary_config)
    backup_db = Database(backup_config)

    suffix = " (dry-run)" if args.dry_run else ""
    print(f"Syncing all data: Supabase → local Postgres{suffix}...")

    try:
        if args.truncate and not args.dry_run:
            print("Truncating local tables...")
            _truncate_all(backup_db)

        total = sum(
            _sync_table(primary_db, backup_db, model_class, label, dry_run=args.dry_run)
            for model_class, label in _SYNC_TABLES
        )
    except Exception as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1

    print(f"Sync complete{suffix}: {total} rows total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
