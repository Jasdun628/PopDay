"""Tests for generate_status_json._missing_company_websites.

Computes the set of companies on the public Investor Days tab whose
resolved link (curated + EDGAR) is still empty - informational only, feeds
the System Health tab's "Missing website links" line.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "generate_status_json",
    Path(__file__).resolve().parent.parent / "scripts" / "generate_status_json.py",
)
gsj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gsj)

from popday.db import Database


def _insert_alert_candidate(db: Database, *, company_name: str, cik: str, accession: str) -> None:
    db.conn.execute(
        """
        INSERT INTO detections
        (accession_number, company_name, cik, form_type, filing_date, filing_url,
         event_type, event_date, matched_phrase, matched_location, snippet, status,
         dismissal_reason, created_timestamp, market)
        VALUES (?, ?, ?, '8-K', '2026-07-01', 'https://www.sec.gov/x.txt',
                'Investor Day', '2026-09-15', 'investor day', 'press_release', 'snippet',
                'alert_candidate', NULL, '2026-07-01T00:00:00+00:00', 'US')
        """,
        (accession, company_name, cik),
    )
    db.conn.commit()


class MissingCompanyWebsitesTests(unittest.TestCase):
    def _empty_config(self):
        return mock.Mock(company_websites={})

    def test_curated_company_is_not_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            _insert_alert_candidate(
                db, company_name="Curated Co", cik="0000000001", accession="acc-1"
            )
            db.close()
            con = sqlite3.connect(str(Path(tmpdir) / "popday.sqlite3"))
            con.row_factory = sqlite3.Row
            with mock.patch.object(
                gsj, "load_config", return_value=mock.Mock(company_websites={"Curated Co": "https://x.com/"})
            ):
                result = gsj._missing_company_websites(con)
            con.close()
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["companies"], [])

    def test_edgar_covered_company_is_not_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            _insert_alert_candidate(
                db, company_name="Edgar Co", cik="0000000002", accession="acc-2"
            )
            db.upsert_company_website(
                cik="0000000002", company_name="Edgar Co", edgar_website="https://edgar.example.com/"
            )
            db.close()
            con = sqlite3.connect(str(Path(tmpdir) / "popday.sqlite3"))
            con.row_factory = sqlite3.Row
            with mock.patch.object(gsj, "load_config", return_value=self._empty_config()):
                result = gsj._missing_company_websites(con)
            con.close()
        self.assertEqual(result["count"], 0)

    def test_uncovered_company_is_reported_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            _insert_alert_candidate(
                db, company_name="Uncovered Co", cik="0000000003", accession="acc-3"
            )
            db.close()
            con = sqlite3.connect(str(Path(tmpdir) / "popday.sqlite3"))
            con.row_factory = sqlite3.Row
            with mock.patch.object(gsj, "load_config", return_value=self._empty_config()):
                result = gsj._missing_company_websites(con)
            con.close()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["companies"], ["Uncovered Co"])

    def test_known_announcement_without_curated_link_is_reported_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            db.add_known_announcement(
                company_name="Known Co",
                event_type="Investor Day",
                event_date="2026-09-15",
                announcement_date="2026-07-01",
                source_url="https://example.com/press-release",
                source_label="Business Wire release",
                source_type="press_release",
            )
            db.close()
            con = sqlite3.connect(str(Path(tmpdir) / "popday.sqlite3"))
            con.row_factory = sqlite3.Row
            with mock.patch.object(gsj, "load_config", return_value=self._empty_config()):
                result = gsj._missing_company_websites(con)
            con.close()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["companies"], ["Known Co"])

    def test_dismissed_detection_is_not_counted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            db.conn.execute(
                """
                INSERT INTO detections
                (accession_number, company_name, cik, form_type, filing_date, filing_url,
                 event_type, event_date, matched_phrase, matched_location, snippet, status,
                 dismissal_reason, created_timestamp, market)
                VALUES ('acc-4', 'Dismissed Co', '0000000004', '8-K', '2026-07-01',
                        'https://www.sec.gov/x.txt', 'Investor Day', '2026-09-15',
                        'investor day', 'press_release', 'snippet', 'dismissed',
                        'not_qualifying', '2026-07-01T00:00:00+00:00', 'US')
                """
            )
            db.conn.commit()
            db.close()
            con = sqlite3.connect(str(Path(tmpdir) / "popday.sqlite3"))
            con.row_factory = sqlite3.Row
            with mock.patch.object(gsj, "load_config", return_value=self._empty_config()):
                result = gsj._missing_company_websites(con)
            con.close()
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
