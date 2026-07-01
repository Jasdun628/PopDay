#!/usr/bin/env python3
"""Backfill EDGAR acceptance timestamps for existing PopDay detections."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from popday.config import load_config
from popday.db import Database
from popday.edgar_fetch import EdgarClient
from popday.filing_parser import parse_sec_filing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch SEC filing headers and store missing acceptance datetimes."
    )
    parser.add_argument("--db-path", help="SQLite database path. Defaults to PopDay config.")
    parser.add_argument("--limit", type=int, help="Maximum rows to backfill.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report timestamps without writing to the database.",
    )
    parser.add_argument(
        "--all-detections",
        action="store_true",
        help="Backfill dismissed detections too. Default is qualifying alert candidates only.",
    )
    args = parser.parse_args()

    config = load_config()
    db = Database(args.db_path or config.db_path)
    client = EdgarClient(config.sec_user_agent, delay_seconds=config.request_delay_seconds)

    updated = 0
    missing = 0
    try:
        rows = db.detections_missing_acceptance_datetime(
            limit=args.limit,
            qualifying_only=not args.all_detections,
        )
        for row in rows:
            raw = client.get_text(str(row["filing_url"]))
            parsed = parse_sec_filing(raw)
            acceptance_datetime = parsed.acceptance_datetime
            if not acceptance_datetime:
                missing += 1
                print(f"missing acceptance datetime: detection {row['id']} {row['accession_number']}")
                continue
            print(
                f"{'would update' if args.dry_run else 'updated'} detection "
                f"{row['id']} {row['accession_number']}: {acceptance_datetime}"
            )
            if not args.dry_run:
                db.set_detection_acceptance_datetime(int(row["id"]), acceptance_datetime)
                db.sync_processed_acceptance_datetime(
                    str(row["accession_number"]),
                    acceptance_datetime,
                )
            updated += 1
    finally:
        db.close()

    print(f"Acceptance datetime backfill complete: {updated} updated, {missing} missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
