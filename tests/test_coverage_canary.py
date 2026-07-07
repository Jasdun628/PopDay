"""Tests for the output-based coverage canary (generate_status_json._assess_coverage).

Watches PopDay's output (discovery volume, candidate freshness) so a scan that
runs green but silently finds nothing is caught — the June 2026 outage shape.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "generate_status_json",
    Path(__file__).resolve().parent.parent / "scripts" / "generate_status_json.py",
)
gsj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gsj)


class CoverageCanaryTests(unittest.TestCase):
    def test_healthy_when_discovering_and_fresh(self):
        v = gsj._assess_coverage(recent_filings_seen=[2, 0, 3, 1, 0, 4], days_since_candidate=1)
        self.assertEqual(v["level"], "ok")

    def test_broken_when_discovery_dry_across_window(self):
        v = gsj._assess_coverage(recent_filings_seen=[0, 0, 0, 0, 0, 0], days_since_candidate=12)
        self.assertEqual(v["level"], "broken")
        self.assertIn("no filings", v["message"])

    def test_no_false_alarm_with_too_few_runs(self):
        # A couple of quiet days must not trip the discovery canary.
        v = gsj._assess_coverage(recent_filings_seen=[0, 0], days_since_candidate=2)
        self.assertEqual(v["level"], "ok")

    def test_stale_when_candidates_dry_but_discovery_alive(self):
        v = gsj._assess_coverage(recent_filings_seen=[3, 1, 2, 0, 1, 2], days_since_candidate=9)
        self.assertEqual(v["level"], "stale")

    def test_fresh_candidate_is_ok(self):
        v = gsj._assess_coverage(recent_filings_seen=[1, 0, 0, 0, 0, 1], days_since_candidate=3)
        self.assertEqual(v["level"], "ok")

    def test_none_candidate_age_does_not_crash(self):
        v = gsj._assess_coverage(recent_filings_seen=[1, 2, 1, 1, 1, 1], days_since_candidate=None)
        self.assertEqual(v["level"], "ok")


if __name__ == "__main__":
    unittest.main()
