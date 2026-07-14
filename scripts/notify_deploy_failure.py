#!/usr/bin/env python3
"""Reuses the existing ops-alert state machine (same one scan failures use)
under a separate 'deploy_failure' key, so automated deploys get the same
cooldown/recovery behaviour already proven for scan alerts: one email when
it breaks, a reminder if it stays broken, one all-clear when it's fixed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from popday.config import load_config
from popday.db import Database
from popday.emailer import send_ops_alert_email


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to config.json.")
    parser.add_argument("--db-path", help="Override the database path.")
    parser.add_argument("--broken", action="store_true", help="Report failure.")
    parser.add_argument("--recovered", action="store_true", help="Report recovery.")
    parser.add_argument("--detail", default="", help="Failure detail text.")
    args = parser.parse_args()

    config = load_config(args.config)
    db = Database(args.db_path or config.db_path)

    is_broken = args.broken and not args.recovered
    verdict = db.check_and_update_ops_alert(
        "deploy_failure", is_broken=is_broken, detail=args.detail
    )

    if verdict == "new_failure":
        send_ops_alert_email(
            config, "PopDay ALARM - automated deploy failed", args.detail
        )
        print(f"Deploy-failure alarm sent (new): {args.detail}")
    elif verdict == "still_failing":
        send_ops_alert_email(
            config,
            "PopDay ALARM - deploy still failing (reminder)",
            args.detail + "\n\nThis is a repeat reminder; still not fixed.",
        )
        print(f"Deploy-failure alarm sent (reminder): {args.detail}")
    elif verdict == "recovered":
        send_ops_alert_email(
            config,
            "PopDay - automated deploy recovered",
            "The next scheduled auto-deploy completed successfully. "
            "The earlier deploy-failure alarm is cleared.",
        )
        print("Deploy recovery email sent.")
    else:
        print("No ops-alert email needed for this deploy outcome.")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
