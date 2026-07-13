"""The Tue-Sat schedule means Sat morning's scan is legitimately the newest
until Tue. Pin the weekend allowance so the front door stops going red every
Monday on a healthy system (first noticed 13 Jul 2026)."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from flask_app import _no_scan_day_allowance_hours as flask_allowance

_spec = importlib.util.spec_from_file_location(
    "generate_status_json",
    Path(__file__).resolve().parent.parent / "scripts" / "generate_status_json.py",
)
gsj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gsj)


def _dt(day, hour):
    return datetime(2026, 7, day, hour, 0, tzinfo=timezone.utc)


def test_saturday_to_monday_gap_gets_48h_allowance():
    # Sat 11 Jul 07:00 scan; Mon 13 Jul 21:00 now: Sun + Mon = 48h extra.
    for fn in (flask_allowance, gsj._no_scan_day_allowance_hours):
        assert fn(_dt(11, 7), _dt(13, 21)) == 48


def test_midweek_gap_gets_no_allowance():
    # Tue 07:00 -> Weds 21:00 contains no Sunday/Monday.
    for fn in (flask_allowance, gsj._no_scan_day_allowance_hours):
        assert fn(_dt(7, 7), _dt(8, 21)) == 0


def test_monday_evening_is_not_broken():
    # 62h since Sat's scan, 48h allowance: under both the 26+48 stale and
    # 50+48 broken thresholds, so a healthy weekend stays green.
    age_h = (_dt(13, 21) - _dt(11, 7)).total_seconds() / 3600
    allowance = gsj._no_scan_day_allowance_hours(_dt(11, 7), _dt(13, 21))
    assert age_h < 26 + allowance


def test_missed_tuesday_scan_surfaces_same_day():
    # If Tue's scan never runs, staleness must trip during Tuesday itself.
    now = _dt(14, 10)  # Tue 10:00, scan due 07:00
    age_h = (now - _dt(11, 7)).total_seconds() / 3600
    allowance = gsj._no_scan_day_allowance_hours(_dt(11, 7), now)
    assert age_h > 26 + allowance
