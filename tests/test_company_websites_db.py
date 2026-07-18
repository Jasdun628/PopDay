"""Tests for the companies table (popday/db.py) backing EDGAR website capture."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from popday.db import Database


class CompanyWebsiteDbTests(unittest.TestCase):
    def _db(self, tmpdir: str) -> Database:
        return Database(str(Path(tmpdir) / "popday.sqlite3"))

    def test_no_row_until_captured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                self.assertFalse(db.has_company_website_row("0001005516"))
                self.assertEqual(db.company_websites_by_cik(), {})
            finally:
                db.close()

    def test_upsert_then_lookup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.upsert_company_website(
                    cik="0001005516",
                    company_name="BOS Better Online Solutions Ltd",
                    edgar_website="https://www.boscom.com",
                )
                self.assertTrue(db.has_company_website_row("0001005516"))
                self.assertEqual(
                    db.company_websites_by_cik(),
                    {"0001005516": "https://www.boscom.com"},
                )
            finally:
                db.close()

    def test_empty_website_still_records_row_but_no_lookup_entry(self):
        """A checked-but-empty result must gate re-fetching (has_company_website_row
        True) without ever appearing as a resolvable link."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.upsert_company_website(
                    cik="0000000001", company_name="No Website Co", edgar_website=""
                )
                self.assertTrue(db.has_company_website_row("0000000001"))
                self.assertEqual(db.company_websites_by_cik(), {})
            finally:
                db.close()

    def test_upsert_overwrites_previous_value_for_same_cik(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.upsert_company_website(
                    cik="0000000001", company_name="Old Name Co", edgar_website="https://old.example.com"
                )
                db.upsert_company_website(
                    cik="0000000001", company_name="New Name Co", edgar_website="https://new.example.com"
                )
                self.assertEqual(
                    db.company_websites_by_cik(), {"0000000001": "https://new.example.com"}
                )
            finally:
                db.close()

    def test_distinct_detection_companies_dedupes_by_cik_most_recent_name_wins(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.conn.executemany(
                    """
                    INSERT INTO detections
                    (accession_number, company_name, cik, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, created_timestamp, market)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "acc-1", "AES CORP", "0000874761", "8-K", "2026-01-01",
                            "https://www.sec.gov/aes1.txt", "Investor Day", "2026-09-15",
                            "investor day", "press_release", "snippet", "alert_candidate",
                            None, "2026-01-01T00:00:00+00:00", "US",
                        ),
                        (
                            "acc-2", "AES Corp", "0000874761", "8-K", "2026-02-01",
                            "https://www.sec.gov/aes2.txt", "Investor Day", "2026-10-15",
                            "investor day", "press_release", "snippet", "alert_candidate",
                            None, "2026-02-01T00:00:00+00:00", "US",
                        ),
                        (
                            "acc-3", "Other Co", "0000000002", "8-K", "2026-01-01",
                            "https://www.sec.gov/other.txt", "Investor Day", "2026-09-15",
                            "investor day", "press_release", "snippet", "alert_candidate",
                            None, "2026-01-01T00:00:00+00:00", "UK",
                        ),
                    ],
                )
                db.conn.commit()
                rows = db.distinct_detection_companies(market="US")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["cik"], "0000874761")
                self.assertEqual(rows[0]["company_name"], "AES Corp")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
