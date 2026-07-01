#!/usr/bin/env python3
"""Refresh cached Price Reaction rows for qualifying PopDay announcements."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from popday.config import load_config
from popday.db import Database
from popday.stock_reaction import refresh_price_reactions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config()
    db_path = str(args.db_path or config.db_path)
    db = Database(db_path)
    try:
        rows = refresh_price_reactions(db, user_agent=config.sec_user_agent)
    finally:
        db.close()

    print(f"Price Reaction cache refreshed: {len(rows)} announcement(s)")
    for row in rows:
        ticker = row.get("ticker") or "no ticker"
        status = row.get("status")
        reaction_date = row.get("reaction_date") or "no reaction date"
        move = row.get("announcement_move_pct")
        move_text = f"{move:.2f}%" if isinstance(move, (float, int)) else "unknown"
        print(f"- {row.get('company_name')} | {ticker} | {reaction_date} | {move_text} | {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
