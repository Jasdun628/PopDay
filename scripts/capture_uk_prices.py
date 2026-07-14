#!/usr/bin/env python3
"""Daily UK price capture - runs as a PythonAnywhere scheduled task.

Suggested schedule: 20:30 UTC daily (LSE closes 16:30 London; the margin
comfortably covers Yahoo's end-of-day data lag year-round, DST included).

Capture only - no signals, no reaction math (see popday/prices.py). Fetch
failures are printed and retried on the next run; the exit code is 0 unless
the run as a whole could not start, so a single bad ticker never turns the
task red.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from popday.config import load_config
from popday.db import Database
from popday.prices import capture_uk_prices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Path to config.json.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch and report, write nothing."
    )
    args = parser.parse_args()

    config = load_config(args.config)
    db = Database(config.db_path)
    try:
        results = capture_uk_prices(config, db, dry_run=args.dry_run)
    finally:
        db.close()

    if not results:
        print("No UK events in an active capture window today; nothing to fetch.")
        return 0

    failures = 0
    for row in results:
        if row.get("error"):
            failures += 1
            print(
                f"- {row['company_name']} ({row['epic']} -> {row['yahoo_symbol'] or '?'}): "
                f"ERROR {row['error']}"
            )
        else:
            print(
                f"- {row['company_name']} ({row['epic']} -> {row['yahoo_symbol']}): "
                f"{row['stored']} close(s) {'would be ' if args.dry_run else ''}stored "
                f"({row.get('currency', '?')})"
            )
    print(
        f"UK price capture finished: {len(results)} ticker-window(s), "
        f"{failures} error(s){' [dry-run]' if args.dry_run else ''}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
