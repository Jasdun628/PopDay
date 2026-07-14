#!/usr/bin/env python3
"""UK market extension - explicit, backup-first migration runner.

The schema changes themselves live in popday/db.py (SCHEMA for fresh
installs, Database._migrate() for existing ones - both idempotent, both
guarded by column-existence checks). Simply opening the database with the
new code applies them. This script exists so the ONE irreversible step of
the UK rollout - touching the live PythonAnywhere database - happens as a
deliberate, checkpointed act with a timestamped file backup taken first and
a printed before/after plan, rather than as a side effect of the first page
view after a deploy.

Adds (all additive, no data rewritten):
- market TEXT NOT NULL DEFAULT 'US' on detections, processed_filings,
  known_announcements, hype_tracking, price_reactions
- source TEXT NOT NULL DEFAULT 'edgar' on scan_runs
- new tables: prices, ticker_mappings

Run twice safely: the second run reports "nothing to do".
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXPECTED = {
    "detections": "market",
    "processed_filings": "market",
    "known_announcements": "market",
    "hype_tracking": "market",
    "price_reactions": "market",
    "scan_runs": "source",
}
NEW_TABLES = ("prices", "ticker_mappings")


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(con: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _plan(db_path: Path) -> tuple[list[str], dict[str, int]]:
    con = sqlite3.connect(db_path)
    try:
        pending: list[str] = []
        for table, column in EXPECTED.items():
            existing = _columns(con, table)
            if existing and column not in existing:
                pending.append(f"ALTER TABLE {table} ADD COLUMN {column} (with default)")
        for table in NEW_TABLES:
            if table not in _tables(con):
                pending.append(f"CREATE TABLE {table}")
        counts = {}
        for table in ("detections", "processed_filings", "scan_runs", "hype_tracking"):
            try:
                counts[table] = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                counts[table] = -1
        return pending, counts
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, help="Path to the SQLite database.")
    parser.add_argument(
        "--backup-dir",
        help="Directory for the pre-migration file backup (default: <db dir>/backups).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the plan and exit without changing anything."
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    pending, before_counts = _plan(db_path)
    print(f"Database: {db_path}")
    print(f"Row counts before: {before_counts}")
    if not pending:
        print("Migration plan: nothing to do (already migrated).")
        return 0
    print("Migration plan:")
    for step in pending:
        print(f"  - {step}")
    if args.dry_run:
        print("[dry-run] No changes made.")
        return 0

    backup_dir = Path(args.backup_dir) if args.backup_dir else db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.name}.{stamp}.pre-uk-migration.bak"
    shutil.copy2(db_path, backup_path)
    print(f"Backup written: {backup_path}")

    from popday.db import Database

    db = Database(str(db_path))  # opening applies SCHEMA + _migrate()
    db.close()

    still_pending, after_counts = _plan(db_path)
    print(f"Row counts after:  {after_counts}")
    if still_pending:
        print(f"ERROR - steps still pending after migration: {still_pending}")
        return 1
    if before_counts != after_counts:
        print("ERROR - row counts changed during an additive migration; investigate before trusting this.")
        return 1
    print("Migration complete and verified (all columns/tables present, row counts unchanged).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
