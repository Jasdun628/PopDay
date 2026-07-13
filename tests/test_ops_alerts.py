"""Regression tests for the July 2026 ops-alert-email feature.

PopDay's scan-health signals (scan_runs, coverage canary, the homepage
banner) were all display-only: nothing emailed Jason if a scan failed or
the scheduler died. These tests pin the fix's state machine so a real
outage cannot go silent again, while a flapping retry cannot spam.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

from popday.db import Database


def _db():
    return Database(tempfile.mkstemp(suffix=".sqlite3")[1])


def test_new_failure_fires_once():
    db = _db()
    v1 = db.check_and_update_ops_alert("scan_failure", is_broken=True, detail="403")
    assert v1 == "new_failure"
    v2 = db.check_and_update_ops_alert("scan_failure", is_broken=True, detail="403 again")
    assert v2 == "none"  # immediate retry must not spam
    db.close()


def test_stale_reminder_after_cooldown():
    db = _db()
    db.check_and_update_ops_alert("scan_failure", is_broken=True, detail="403")
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat(timespec="seconds")
    db.conn.execute(
        "UPDATE ops_alerts SET last_sent_utc = ? WHERE alert_key = 'scan_failure'", (old,)
    )
    db.conn.commit()
    verdict = db.check_and_update_ops_alert("scan_failure", is_broken=True, detail="still 403")
    assert verdict == "still_failing"
    db.close()


def test_recovery_fires_once_then_quiet():
    db = _db()
    db.check_and_update_ops_alert("scan_failure", is_broken=True, detail="403")
    v1 = db.check_and_update_ops_alert("scan_failure", is_broken=False, detail="")
    assert v1 == "recovered"
    v2 = db.check_and_update_ops_alert("scan_failure", is_broken=False, detail="")
    assert v2 == "none"
    db.close()


def test_healthy_to_healthy_never_emails():
    db = _db()
    verdict = db.check_and_update_ops_alert("scan_failure", is_broken=False, detail="")
    assert verdict == "none"
    db.close()


def test_alert_keys_are_independent():
    # scan_failure and heartbeat are separate keys and must not interfere.
    db = _db()
    v1 = db.check_and_update_ops_alert("scan_failure", is_broken=True, detail="x")
    v2 = db.check_and_update_ops_alert("heartbeat", is_broken=True, detail="y")
    assert v1 == "new_failure" and v2 == "new_failure"
    db.close()
