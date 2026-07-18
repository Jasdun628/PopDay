#!/usr/bin/env python3
"""Backfill EDGAR's self-reported website field for existing PopDay companies.

Fills the `companies` table (popday/db.py) for every distinct CIK already in
the database, so the curated-then-EDGAR resolution order (flask_app.py's
`_company_website()`) has data to fall back on for companies nobody has
hand-curated a link for. Resumable by design: a company that already has a
`companies` row (captured here or by the scan-time hook in cli.py) is
skipped on a re-run, so an interrupted backfill picks up where it left off
without re-hitting data.sec.gov for companies already checked. Pass
--recheck to force a fresh fetch anyway.

Run with --dry-run first (Step 2a: reports EDGAR field coverage, writes
nothing) before running for real (Step 2b) against the live database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from popday.company_websites import fetch_edgar_website, normalize_cik
from popday.config import load_config
from popday.db import Database
from popday.edgar_fetch import EdgarBlockedError, EdgarClient, EdgarUnavailableError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", help="SQLite database path. Defaults to PopDay config.")
    parser.add_argument(
        "--market", default="US", help="Market to backfill (default: US - EDGAR-sourced companies only)."
    )
    parser.add_argument("--limit", type=int, help="Maximum companies to process (debug/testing).")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.12,
        help="Delay between data.sec.gov requests (default 0.12s - EDGAR etiquette caps at <=10 req/s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report websites without writing to the database.",
    )
    parser.add_argument(
        "--recheck",
        action="store_true",
        help="Re-fetch companies that already have a companies row (default: skip them).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config()
    db = Database(args.db_path or config.db_path)
    client = EdgarClient(config.sec_user_agent, delay_seconds=args.delay_seconds)

    checked = 0
    found = 0
    failed = 0
    skipped_existing = 0
    stored: list[tuple[str, str, str]] = []  # (company_name, cik, website)

    try:
        companies = db.distinct_detection_companies(market=args.market)
        if args.limit:
            companies = companies[: args.limit]
        print(f"Companies in scope: {len(companies)} (market={args.market}).")

        for row in companies:
            cik = normalize_cik(row["cik"])
            company_name = str(row["company_name"])
            if not cik:
                continue
            if not args.recheck and db.has_company_website_row(cik):
                skipped_existing += 1
                continue

            checked += 1
            try:
                website = fetch_edgar_website(client, cik)
            except (EdgarBlockedError, EdgarUnavailableError) as exc:
                failed += 1
                print(f"FAILED     {company_name} ({cik}): {exc}")
                continue

            if website:
                found += 1
                stored.append((company_name, cik, website))
                print(f"{'would store' if args.dry_run else 'stored':<11}{company_name} ({cik}): {website}")
            else:
                print(f"{'—':<11}{company_name} ({cik}): no usable website on EDGAR")

            if not args.dry_run:
                db.upsert_company_website(
                    cik=cik, company_name=company_name, edgar_website=website, market=args.market
                )
    finally:
        db.close()

    print()
    print(
        f"EDGAR website backfill {'DRY-RUN ' if args.dry_run else ''}complete: "
        f"{checked} checked, {found} with a usable website "
        f"({found}/{checked} coverage)" + (f", {failed} failed" if failed else "")
        + (f", {skipped_existing} already captured (skipped)" if skipped_existing else "") + "."
    )
    if args.dry_run and stored:
        print(f"\n{len(stored)} company -> URL pairs that would be written:")
        for company_name, cik, website in stored:
            print(f"  {company_name} ({cik}): {website}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
