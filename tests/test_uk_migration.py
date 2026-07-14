"""Migration idempotency and per-source scan_runs tests (UK extension)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import date

from popday.db import Database
from popday.edgar_fetch import Filing

# A pre-UK-extension schema fragment: the tables the migration must upgrade,
# WITHOUT market/source columns, as they existed on the live DB before today.
OLD_SCHEMA = """
CREATE TABLE processed_filings (
    accession_number TEXT PRIMARY KEY,
    cik TEXT NOT NULL,
    company_name TEXT NOT NULL,
    form_type TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    acceptance_datetime TEXT,
    filing_url TEXT NOT NULL,
    processed_timestamp TEXT NOT NULL
);
CREATE TABLE scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    started_utc TEXT NOT NULL,
    finished_utc TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    discovery_source TEXT,
    filings_seen INTEGER NOT NULL DEFAULT 0,
    filings_parsed INTEGER NOT NULL DEFAULT 0,
    alerts_sent INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    efts_total_hits INTEGER NOT NULL DEFAULT 0,
    discovery_control TEXT NOT NULL DEFAULT '',
    run_kind TEXT NOT NULL DEFAULT 'scheduled'
);
"""


class MigrationTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_old_schema_gains_market_and_source_columns_with_us_defaults(self):
        con = sqlite3.connect(self.db_path)
        con.executescript(OLD_SCHEMA)
        con.execute(
            "INSERT INTO processed_filings VALUES ('0001-26-1', '1', 'Old Co', '8-K', "
            "'2026-01-02', NULL, 'https://sec.test/f', '2026-01-02T10:00:00+00:00')"
        )
        con.execute(
            "INSERT INTO scan_runs (run_date, started_utc, status) "
            "VALUES ('2026-01-02', '2026-01-02T09:00:00+00:00', 'ok')"
        )
        con.commit()
        con.close()

        db = Database(self.db_path)  # opening migrates
        row = db.conn.execute("SELECT market FROM processed_filings").fetchone()
        self.assertEqual(row["market"], "US")
        run = db.conn.execute("SELECT source FROM scan_runs").fetchone()
        self.assertEqual(run["source"], "edgar")
        db.close()

    def test_migration_is_idempotent(self):
        db = Database(self.db_path)
        db.close()
        db = Database(self.db_path)  # second open: re-runs SCHEMA + _migrate
        tables = {
            r["name"]
            for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertIn("prices", tables)
        self.assertIn("ticker_mappings", tables)
        db.close()

    def test_fresh_schema_matches_migrated_schema(self):
        db = Database(self.db_path)
        detection_cols = {
            r["name"] for r in db.conn.execute("PRAGMA table_info(detections)")
        }
        self.assertIn("market", detection_cols)
        scan_cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(scan_runs)")}
        self.assertIn("source", scan_cols)
        db.close()


class PerSourceScanRunTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def _finish(self, run_id: int, status: str = "ok", source: str = "efts"):
        self.db.finish_scan_run(
            run_id,
            status=status,
            filings_seen=0,
            filings_parsed=0,
            alerts_sent=0,
            source=source,
            error="",
        )

    def test_coverage_is_per_source(self):
        us_run = self.db.start_scan_run(date(2026, 7, 10), source="edgar")
        self._finish(us_run)
        uk_run = self.db.start_scan_run(date(2026, 7, 13), source="investegate")
        self._finish(uk_run, source="investegate-index")

        self.assertEqual(
            self.db.covered_ok_dates(date(2026, 7, 1), source="edgar"),
            {date(2026, 7, 10)},
        )
        self.assertEqual(
            self.db.covered_ok_dates(date(2026, 7, 1), source="investegate"),
            {date(2026, 7, 13)},
        )
        self.assertEqual(self.db.earliest_ok_run_date(source="edgar"), date(2026, 7, 10))
        self.assertEqual(
            self.db.earliest_ok_run_date(source="investegate"), date(2026, 7, 13)
        )
        self.assertEqual(
            self.db.scan_sources_with_ok_runs(), ["edgar", "investegate"]
        )

    def test_latest_scan_health_per_source(self):
        us_run = self.db.start_scan_run(date(2026, 7, 10), source="edgar")
        self._finish(us_run, status="failed")
        uk_run = self.db.start_scan_run(date(2026, 7, 10), source="investegate")
        self._finish(uk_run, source="investegate-index")

        self.assertEqual(
            self.db.latest_scan_health(source="edgar")["last_run_status"], "failed"
        )
        self.assertEqual(
            self.db.latest_scan_health(source="investegate")["last_run_status"], "ok"
        )

    def test_mark_processed_stores_market(self):
        filing = Filing(
            accession_number="9184165",
            cik="",
            company_name="Glencore",
            form_type="RNS",
            filing_date="2025-10-21",
            filing_url="https://investegate.test/x",
            primary_document="",
        )
        self.db.mark_processed(filing, market="UK")
        row = self.db.conn.execute(
            "SELECT market FROM processed_filings WHERE accession_number = '9184165'"
        ).fetchone()
        self.assertEqual(row["market"], "UK")


if __name__ == "__main__":
    unittest.main()
