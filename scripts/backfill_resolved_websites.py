#!/usr/bin/env python3
"""Backfill the Wikidata-resolved website for existing PopDay companies
whose link is still missing after curation and EDGAR.

Targets the exact same universe scripts/generate_status_json.py's
_missing_company_websites() surfaces on System Health - every company behind
a live alert_candidate/alert_candidate_tbd detection or a known_announcement
- so a "before" and "after" run of this script directly explains any change
in that count. Resumable by design: a company that already has a
resolved_company_websites row (attempted here or by the scan-time hook in
cli.py) is skipped on a re-run. Pass --recheck to force a fresh attempt.

Run with --dry-run first to see what it would resolve without writing
anything.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from popday.company_websites import company_key, normalize_cik, resolve_company_website
from popday.config import load_config
from popday.db import Database
from popday.stock_reaction import fetch_cik_ticker_map
from popday.wikidata_resolver import resolve_website_wikidata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", help="SQLite database path. Defaults to PopDay config.")
    parser.add_argument("--limit", type=int, help="Maximum companies to process (debug/testing).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Attempt resolution and report without writing to the database.",
    )
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Re-attempt companies that already have a resolved_company_websites row (default: skip them).",
    )
    return parser


def _missing_universe(con: sqlite3.Connection, curated_websites: dict, edgar_websites: dict) -> list[tuple[str, str]]:
    """Every distinct (company_name, cik) still missing a link after curated
    + EDGAR - same query shape as generate_status_json.py's
    _missing_company_websites(), kept in sync deliberately (a change to one
    should prompt a look at the other)."""
    pairs: dict[str, tuple[str, str]] = {}
    for name, cik in con.execute(
        """
        SELECT company_name, cik FROM detections
        WHERE event_type IS NOT NULL
          AND ((status = 'alert_candidate' AND event_date IS NOT NULL)
               OR status = 'alert_candidate_tbd')
        """
    ).fetchall():
        pairs.setdefault(company_key(name), (str(name), str(cik) if cik is not None else ""))
    for name, cik in con.execute(
        "SELECT company_name, cik_override FROM known_announcements"
    ).fetchall():
        pairs.setdefault(company_key(name), (str(name), str(cik) if cik is not None else ""))

    return sorted(
        (name, cik)
        for name, cik in pairs.values()
        if not resolve_company_website(name, cik, curated_websites, edgar_websites)
    )


def main() -> int:
    args = build_parser().parse_args()
    config = load_config()
    db_path = args.db_path or config.db_path
    db = Database(db_path)

    checked = 0
    found = 0
    skipped_existing = 0
    stored: list[tuple[str, str, str]] = []  # (company_name, cik, website)

    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            curated_websites = config.company_websites
            edgar_websites = db.company_websites_by_cik()
            missing = _missing_universe(con, curated_websites, edgar_websites)
        finally:
            con.close()

        if args.limit:
            missing = missing[: args.limit]
        print(f"Companies missing a link (curated + EDGAR): {len(missing)}.")

        try:
            cik_tickers = fetch_cik_ticker_map(user_agent=config.sec_user_agent)
        except Exception as exc:  # noqa: BLE001 - ticker is a disambiguation nicety, not required
            print(f"WARNING - could not fetch CIK-ticker map: {exc}")
            cik_tickers = {}

        for company_name, cik in missing:
            key = company_key(company_name)
            if not args.recheck and db.has_resolved_website_row(key):
                skipped_existing += 1
                continue

            checked += 1
            ticker = cik_tickers.get(normalize_cik(cik), "") if cik else ""
            website = resolve_website_wikidata(company_name, config.sec_user_agent, ticker=ticker)
            if website:
                found += 1
                stored.append((company_name, cik, website))
                print(f"{'would store' if args.dry_run else 'stored':<11}{company_name}: {website}")
            else:
                print(f"{'—':<11}{company_name}: no confident Wikidata match")

            if not args.dry_run:
                db.upsert_resolved_website(
                    company_key=key,
                    company_name=company_name,
                    cik=normalize_cik(cik),
                    resolved_website=website,
                    resolution_method="wikidata",
                )
    finally:
        db.close()

    print()
    print(
        f"Auto-resolve backfill {'DRY-RUN ' if args.dry_run else ''}complete: "
        f"{checked} attempted, {found} resolved ({found}/{checked} yield)"
        + (f", {skipped_existing} already attempted (skipped)" if skipped_existing else "") + "."
    )
    if stored:
        print(f"\n{len(stored)} company -> URL pairs {'that would be ' if args.dry_run else ''}written:")
        for company_name, cik, website in stored:
            print(f"  {company_name}: {website}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
