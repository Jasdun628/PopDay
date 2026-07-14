"""Per-source coverage canary and health-verdict tests (UK extension).

A UK block must never be masked by a healthy US run, and vice versa - these
pin the per-source plumbing in scripts/generate_status_json.py.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "generate_status_json",
    Path(__file__).resolve().parent.parent / "scripts" / "generate_status_json.py",
)
gsj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gsj)

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _mem_db_with_runs(rows) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT, started_utc TEXT, finished_utc TEXT, status TEXT,
            discovery_source TEXT, filings_seen INTEGER DEFAULT 0,
            filings_parsed INTEGER DEFAULT 0, alerts_sent INTEGER DEFAULT 0,
            error TEXT, efts_total_hits INTEGER DEFAULT 0,
            discovery_control TEXT DEFAULT '', run_kind TEXT DEFAULT 'scheduled',
            source TEXT DEFAULT 'edgar'
        )
        """
    )
    con.execute(
        "CREATE TABLE detections (id INTEGER PRIMARY KEY, status TEXT, "
        "created_timestamp TEXT, market TEXT DEFAULT 'US')"
    )
    for row in rows:
        con.execute(
            "INSERT INTO scan_runs (run_date, started_utc, finished_utc, status, "
            "discovery_source, filings_seen, discovery_control, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    con.commit()
    return con


class AssessCoverageSourceTests(unittest.TestCase):
    def test_all_zero_investegate_with_control_ok_is_verified_quiet(self):
        verdict = gsj._assess_coverage(
            recent_filings_seen=[0, 0, 0, 0, 0, 0],
            days_since_candidate=2,
            discovery_control="ok",
            discovery_source="investegate-index",
        )
        self.assertEqual(verdict["level"], "ok")

    def test_all_zero_daily_index_is_still_broken(self):
        verdict = gsj._assess_coverage(
            recent_filings_seen=[0, 0, 0, 0, 0, 0],
            days_since_candidate=2,
            discovery_control="",
            discovery_source="daily-index",
        )
        self.assertEqual(verdict["level"], "broken")


class PerSourceScanHealthTests(unittest.TestCase):
    def test_failed_uk_run_not_masked_by_healthy_us_run(self):
        con = _mem_db_with_runs(
            [
                ("2026-07-13", "2026-07-14T04:00:00+00:00", "2026-07-14T04:01:00+00:00",
                 "failed", "investegate-index", 0, "", "investegate"),
                ("2026-07-13", "2026-07-14T07:00:00+00:00", "2026-07-14T07:01:00+00:00",
                 "ok", "efts", 3, "ok", "edgar"),
            ]
        )
        us = gsj._scan_health(con, source="edgar")
        uk = gsj._scan_health(con, source="investegate")
        self.assertEqual(us["last_run_status"], "ok")
        self.assertEqual(uk["last_run_status"], "failed")

        us_verdict = gsj._source_health_verdict(us, {"level": "ok"}, None, NOW)
        uk_verdict = gsj._source_health_verdict(
            uk, {"level": "ok"}, None, NOW, label="UK (Investegate) "
        )
        self.assertEqual(us_verdict["level"], "LIVE")
        self.assertEqual(uk_verdict["level"], "BROKEN")
        self.assertIn("UK (Investegate)", uk_verdict["summary"])

    def test_pre_source_column_database_falls_back_gracefully(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE scan_runs (id INTEGER PRIMARY KEY, run_date TEXT, "
            "started_utc TEXT, finished_utc TEXT, status TEXT, discovery_source TEXT, "
            "error TEXT, run_kind TEXT DEFAULT 'scheduled')"
        )
        con.execute(
            "INSERT INTO scan_runs (run_date, started_utc, finished_utc, status, "
            "discovery_source, error) VALUES ('2026-07-13', '2026-07-14T04:00:00+00:00', "
            "'2026-07-14T04:01:00+00:00', 'ok', 'efts', '')"
        )
        con.commit()
        health = gsj._scan_health(con, source="investegate")
        self.assertTrue(health["table_present"])
        self.assertEqual(health["last_run_status"], "ok")

    def test_worst_verdict_wins_in_health(self):
        db_status = {
            "exists": True,
            "scan_health": {},
            "coverage_health": {},
            "sources": {
                "edgar": {
                    "scan_health": {
                        "table_present": True,
                        "last_run_status": "ok",
                        "last_success_utc": "2026-07-14T07:00:00+00:00",
                        "last_run_source": "efts",
                        "last_run_error": "",
                    },
                    "coverage_health": {"level": "ok"},
                },
                "investegate": {
                    "scan_health": {
                        "table_present": True,
                        "last_run_status": "failed",
                        "last_success_utc": "2026-07-13T04:00:00+00:00",
                        "last_run_source": "investegate-index",
                        "last_run_error": "blocked",
                    },
                    "coverage_health": {"level": "ok"},
                },
            },
        }
        verdict = gsj._health(
            latest_run=None, db_status=db_status, err_log=Path("/nonexistent"), now=NOW
        )
        self.assertEqual(verdict["level"], "BROKEN")
        self.assertIn("UK (Investegate)", verdict["summary"])


class BuildStatusTopLevelOutputTests(unittest.TestCase):
    """Regression test: build_status()'s _database_status() computes a
    per-source block, but it must actually be copied into the top-level dict
    that gets written to status.json - an internal-only computation is
    invisible to flask_app.py's per-source banner (caught live 2026-07-14:
    the first deploy silently wrote 'sources': {} to the real status file)."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        con = sqlite3.connect(self.db_path)
        con.executescript(
            """
            CREATE TABLE scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_date TEXT, started_utc TEXT,
                finished_utc TEXT, status TEXT, discovery_source TEXT,
                filings_seen INTEGER DEFAULT 0, filings_parsed INTEGER DEFAULT 0,
                alerts_sent INTEGER DEFAULT 0, error TEXT,
                efts_total_hits INTEGER DEFAULT 0, discovery_control TEXT DEFAULT '',
                run_kind TEXT DEFAULT 'scheduled', source TEXT DEFAULT 'edgar'
            );
            CREATE TABLE processed_filings (accession_number TEXT PRIMARY KEY);
            CREATE TABLE detections (id INTEGER PRIMARY KEY, status TEXT,
                created_timestamp TEXT, market TEXT DEFAULT 'US');
            CREATE TABLE known_announcements (id INTEGER PRIMARY KEY);
            CREATE TABLE alert_recipients (email TEXT PRIMARY KEY, active INTEGER);
            """
        )
        con.execute(
            "INSERT INTO scan_runs (run_date, started_utc, finished_utc, status, "
            "discovery_source, source) VALUES ('2026-07-14', '2026-07-14T04:00:00+00:00', "
            "'2026-07-14T04:01:00+00:00', 'ok', 'efts', 'edgar')"
        )
        con.execute(
            "INSERT INTO scan_runs (run_date, started_utc, finished_utc, status, "
            "discovery_source, source) VALUES ('2026-07-14', '2026-07-14T05:30:00+00:00', "
            "'2026-07-14T05:31:00+00:00', 'ok', 'investegate-index', 'investegate')"
        )
        con.commit()
        con.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_sources_block_present_in_final_output(self):
        args = gsj.build_parser().parse_args(
            ["--db-path", self.db_path, "--runtime-dir", tempfile.gettempdir()]
        )
        status = gsj.build_status(args)
        self.assertIn("sources", status)
        self.assertEqual(set(status["sources"].keys()), {"edgar", "investegate"})
        self.assertEqual(
            status["sources"]["edgar"]["scan_health"]["last_run_status"], "ok"
        )
        self.assertEqual(
            status["sources"]["investegate"]["scan_health"]["last_run_status"], "ok"
        )


if __name__ == "__main__":
    unittest.main()
