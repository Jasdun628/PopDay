#!/usr/bin/env python3
"""Independent PopDay heartbeat check - run as its OWN PythonAnywhere
scheduled task, separate from the Mac Mini scanner.

Why this has to live independently of the scanner: if the Mac Mini's launchd
job stops firing at all (asleep, crashed, unplugged), the scanner process
never runs and can never email anyone that it's dead - it doesn't exist to
send the email. This script reads the synced database copy on PythonAnywhere
(scripts/sync_status_to_pythonanywhere.sh already copies popday.sqlite3 there)
and fires an ops alert purely from elapsed wall-clock time, independent of
whether the Mac Mini is even switched on.

Suggested PA scheduled task: run this hourly.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from popday.config import load_config
from popday.coverage_gap import no_scan_day_allowance_hours
from popday.db import Database
from popday.emailer import send_ops_alert_email

STALE_HOURS = 26.0  # matches the homepage banner's own overdue threshold


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to config.json.")
    parser.add_argument(
        "--db-path", help="Override the database path (defaults to config's db_path)."
    )
    args = parser.parse_args()

    config = load_config(args.config)
    db_path = args.db_path or config.db_path
    db = Database(db_path)

    last_ok = db.conn.execute(
        "SELECT finished_utc FROM scan_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    now = datetime.now(timezone.utc)
    if last_ok and last_ok["finished_utc"]:
        try:
            last_dt = datetime.fromisoformat(last_ok["finished_utc"])
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            last_dt = None
    else:
        last_dt = None

    age_hours = (now - last_dt).total_seconds() / 3600 if last_dt else None
    # The scanner runs Tue-Sat; Sunday and Monday stretch the threshold so a
    # healthy weekend never alarms, while a missed Tuesday run still does.
    threshold = STALE_HOURS + (
        no_scan_day_allowance_hours(last_dt, now) if last_dt else 0
    )
    is_broken = age_hours is None or age_hours > threshold

    if is_broken:
        detail = (
            f"No successful PopDay scan recorded in the last {threshold:.0f} hours "
            f"(last success: {last_ok['finished_utc'] if last_ok else 'never'}). "
            "This heartbeat check runs independently on PythonAnywhere and does not "
            "depend on the Mac Mini being awake - if the scheduler itself has died, "
            "this is the only thing that will tell you."
        )
    else:
        detail = ""

    verdict = db.check_and_update_ops_alert("heartbeat", is_broken=is_broken, detail=detail)

    if verdict == "new_failure":
        send_ops_alert_email(
            config,
            "PopDay ALARM - scan heartbeat missing",
            detail + "\n\nCheck the Mac Mini: is it awake, and is launchd running the job?",
        )
        print(f"Heartbeat alarm sent (new): {detail}")
    elif verdict == "still_failing":
        send_ops_alert_email(
            config,
            "PopDay ALARM - still down (reminder)",
            detail + "\n\nThis is a repeat reminder; the outage has not been fixed yet.",
        )
        print(f"Heartbeat alarm sent (reminder): {detail}")
    elif verdict == "recovered":
        send_ops_alert_email(
            config,
            "PopDay - scan heartbeat recovered",
            "PopDay has completed a successful scan again. The earlier alarm is cleared.",
        )
        print("Heartbeat recovered; all-clear email sent.")
    else:
        if is_broken:
            print(f"Still down but within the resend cooldown (age_hours={age_hours}); no email sent.")
        else:
            print(f"Heartbeat OK (age_hours={age_hours}).")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
