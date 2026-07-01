#!/usr/bin/env python3
"""Stable operator entrypoint for routine PopDay maintenance commands."""

from __future__ import annotations

import argparse
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DB = Path("/Users/jasondunne/PopDayRuntime/popday.sqlite3")
PYTHONANYWHERE_APP_DIR = "/home/Jasdun/popday"
PYTHONANYWHERE_SSH_TARGET = "Jasdun@ssh.pythonanywhere.com"


def run(args: list[str]) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=REPO_ROOT, check=True)


def verify_live(_: argparse.Namespace) -> int:
    run([sys.executable, "scripts/verify_live_popday.py"])
    run([sys.executable, "scripts/verify_live_popday_buttons.py"])
    return 0


def deploy(_: argparse.Namespace) -> int:
    run(["bash", "scripts/deploy_dev_to_pythonanywhere.sh"])
    return 0


def backfill_acceptance(args: argparse.Namespace) -> int:
    if not args.dry_run and not args.skip_backup:
        run([sys.executable, "scripts/backup_popday_runtime.py", "--reason", "pre acceptance datetime backfill"])

    command = [
        sys.executable,
        "scripts/backfill_acceptance_datetimes.py",
        "--db-path",
        str(args.db_path),
    ]
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    if args.dry_run:
        command.append("--dry-run")
    if args.all_detections:
        command.append("--all-detections")
    run(command)
    return 0


def refresh_price_reaction(args: argparse.Namespace) -> int:
    if not args.skip_backup:
        run([sys.executable, "scripts/backup_popday_runtime.py", "--reason", "pre price reaction refresh"])
    run([sys.executable, "scripts/refresh_price_reaction.py", "--db-path", str(args.db_path)])
    return 0


def _runtime_con(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def runtime_summary(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    con = _runtime_con(db_path)
    try:
        qualifying = con.execute(
            "SELECT COUNT(*) AS count FROM detections WHERE status = 'alert_candidate'"
        ).fetchone()["count"]
        acceptance = con.execute(
            """
            SELECT COUNT(*) AS count
            FROM detections
            WHERE status = 'alert_candidate'
              AND acceptance_datetime IS NOT NULL
              AND acceptance_datetime != ''
            """
        ).fetchone()["count"]
        print(f"Mac Mini runtime DB: {db_path}")
        print(f"Qualifying EDGAR announcements: {qualifying}")
        print(f"With EDGAR acceptance time: {acceptance}")
        rows = con.execute(
            """
            SELECT company_name, filing_date, acceptance_datetime
            FROM detections
            WHERE status = 'alert_candidate'
            ORDER BY filing_date DESC, company_name
            """
        ).fetchall()
        for row in rows:
            print(
                f"- {row['company_name']} | filed {row['filing_date']} | "
                f"accepted {row['acceptance_datetime'] or 'missing'}"
            )
    finally:
        con.close()
    return 0


def check_pythonanywhere_db(_: argparse.Namespace) -> int:
    remote_code = (
        "import sqlite3; "
        "con=sqlite3.connect('popday.sqlite3'); "
        "con.row_factory=sqlite3.Row; "
        "rows=con.execute(\"SELECT company_name, filing_date, acceptance_datetime "
        "FROM detections WHERE status='alert_candidate' ORDER BY filing_date DESC, company_name\").fetchall(); "
        "print('PythonAnywhere DB: /home/Jasdun/popday/popday.sqlite3'); "
        "[print('- ' + r['company_name'] + ' | filed ' + str(r['filing_date']) + "
        "' | accepted ' + str(r['acceptance_datetime'] or 'missing')) for r in rows]"
    )
    run(
        [
            "ssh",
            PYTHONANYWHERE_SSH_TARGET,
            f"cd {shlex.quote(PYTHONANYWHERE_APP_DIR)} && python3 -c {shlex.quote(remote_code)}",
        ]
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stable PopDay operator command for routine live/runtime work."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser("verify-live", help="Run live public verifiers.")
    verify_parser.set_defaults(func=verify_live)

    deploy_parser = subparsers.add_parser("deploy", help="Deploy Mac Mini repo/runtime to PythonAnywhere.")
    deploy_parser.set_defaults(func=deploy)

    backfill_parser = subparsers.add_parser(
        "backfill-acceptance",
        help="Backfill missing EDGAR acceptance timestamps for qualifying announcements.",
    )
    backfill_parser.add_argument("--db-path", type=Path, default=RUNTIME_DB)
    backfill_parser.add_argument("--limit", type=int)
    backfill_parser.add_argument("--dry-run", action="store_true")
    backfill_parser.add_argument("--all-detections", action="store_true")
    backfill_parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Do not create the normal runtime backup before writing.",
    )
    backfill_parser.set_defaults(func=backfill_acceptance)

    price_parser = subparsers.add_parser(
        "refresh-price-reaction",
        help="Refresh cached daily Price Reaction rows for qualifying announcements.",
    )
    price_parser.add_argument("--db-path", type=Path, default=RUNTIME_DB)
    price_parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Do not create the normal runtime backup before writing.",
    )
    price_parser.set_defaults(func=refresh_price_reaction)

    runtime_parser = subparsers.add_parser("runtime-summary", help="Summarize Mac Mini runtime DB.")
    runtime_parser.add_argument("--db-path", type=Path, default=RUNTIME_DB)
    runtime_parser.set_defaults(func=runtime_summary)

    pa_parser = subparsers.add_parser(
        "check-pythonanywhere-db",
        help="Check qualifying announcement timestamps on PythonAnywhere.",
    )
    pa_parser.set_defaults(func=check_pythonanywhere_db)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
