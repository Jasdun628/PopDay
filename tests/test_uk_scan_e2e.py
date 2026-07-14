"""End-to-end UK scan dry runs with monkeypatched fetches (no network).

Exercises the full run_scan --source investegate pipeline: index fetch,
headline matching, detail fetch, detection, canary control on quiet days,
and the scan_runs dead-man's-switch rows - against fixture/synthetic HTML.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from popday import cli
from popday.db import Database
from popday.sources.investegate import InvestegateClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INDEX_HTML = (FIXTURES / "investegate_index_2026-07-14.html").read_text(encoding="utf-8")
CANARY_HTML = (FIXTURES / "investegate_canary_glencore_9184165.html").read_text(encoding="utf-8")

# A minimal one-row index page in Investegate's real markup shape, headlining
# a Capital Markets Day so the scan takes the match->detail->detect path.
CMD_INDEX_HTML = """
<table class="table-investegate">
  <tbody>
    <tr>
      <td>21 Oct 2025 09:00 AM</td>
      <td><div class="text-center"><a class="regulatory source-RNS" href="https://www.investegate.co.uk/source/RNS">RNS</a></div></td>
      <td><div><a href="https://www.investegate.co.uk/company/GLEN">Glencore (GLEN)</a></div></td>
      <td><a class="announcement-link" href="https://www.investegate.co.uk/announcement/rns/glencore--glen/notice-of-capital-markets-day/9184165">Notice of Capital Markets Day</a></td>
    </tr>
  </tbody>
</table>
"""


class UkScanEndToEndTests(unittest.TestCase):
    def setUp(self):
        # Other test modules export POPDAY_DB_PATH / POPDAY_ADMIN_PASSWORD for
        # the flask test client, and env vars outrank --config in load_config -
        # scrub them so this test's temp config actually wins.
        self._saved_env = {
            key: os.environ.pop(key)
            for key in ("POPDAY_DB_PATH", "POPDAY_CONFIG_JSON")
            if key in os.environ
        }
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        fd, self.config_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as handle:
            json.dump(
                {"db_path": self.db_path, "sec_user_agent": "PopDay/0.1 test@example.com"},
                handle,
            )

    def tearDown(self):
        os.unlink(self.db_path)
        os.unlink(self.config_path)
        os.environ.update(self._saved_env)

    def _run(self, fake_get_html) -> int:
        with mock.patch.object(InvestegateClient, "get_html", fake_get_html), \
             mock.patch.object(InvestegateClient, "_check_robots", lambda self: None), \
             mock.patch.object(InvestegateClient, "__init__", lambda self, ua, delay=2.0, **kw: (
                 setattr(self, "user_agent", ua),
                 setattr(self, "delay_seconds", 0.0),
                 setattr(self, "_last_request", 0.0),
                 setattr(self, "stats", mock.Mock()),
             )[0] or None):
            return cli.main(
                [
                    "--source", "investegate",
                    "--date", "2025-10-21",
                    "--dry-run",
                    "--config", self.config_path,
                ]
            )

    def test_quiet_day_verified_by_canary(self):
        def fake_get_html(self, url):
            if "today-announcements" in url:
                return INDEX_HTML  # real fixture: no CMD headlines that day
            return CANARY_HTML  # the canary probe

        exit_code = self._run(fake_get_html)
        self.assertEqual(exit_code, 0)
        db = Database(self.db_path)
        run = db.conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
        db.close()
        self.assertEqual(run["source"], "investegate")
        self.assertEqual(run["status"], "dry-run")
        self.assertEqual(run["filings_seen"], 0)
        self.assertEqual(run["discovery_control"], "ok")

    def test_match_day_detects_without_writing(self):
        def fake_get_html(self, url):
            if "today-announcements" in url:
                return CMD_INDEX_HTML
            return CANARY_HTML  # detail page fetch for the matched row

        exit_code = self._run(fake_get_html)
        self.assertEqual(exit_code, 0)
        db = Database(self.db_path)
        run = db.conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
        detections = db.conn.execute("SELECT count(*) FROM detections").fetchone()[0]
        processed = db.conn.execute("SELECT count(*) FROM processed_filings").fetchone()[0]
        db.close()
        self.assertEqual(run["status"], "dry-run")
        self.assertEqual(run["filings_seen"], 1)
        self.assertEqual(run["filings_parsed"], 1)
        self.assertEqual(detections, 0, "dry-run must not write detections")
        self.assertEqual(processed, 0, "dry-run must not mark processed")

    def test_broken_canary_fails_the_run(self):
        def fake_get_html(self, url):
            if "today-announcements" in url:
                return INDEX_HTML  # zero matches -> canary probe
            return "<html><body>totally different site now</body></html>"

        exit_code = self._run(fake_get_html)
        self.assertEqual(exit_code, 1)
        db = Database(self.db_path)
        run = db.conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
        db.close()
        self.assertEqual(run["status"], "failed")
        self.assertIn("instrument broken", str(run["error"]))


if __name__ == "__main__":
    unittest.main()
