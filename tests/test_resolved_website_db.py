"""Tests for the resolved_company_websites table (popday/db.py) backing the
heuristic auto-resolver."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from popday.db import Database


class ResolvedWebsiteDbTests(unittest.TestCase):
    def _db(self, tmpdir: str) -> Database:
        return Database(str(Path(tmpdir) / "popday.sqlite3"))

    def test_no_row_until_attempted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                self.assertFalse(db.has_resolved_website_row("commercial metals co"))
                self.assertEqual(db.resolved_websites_by_key(), {})
            finally:
                db.close()

    def test_upsert_then_lookup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.upsert_resolved_website(
                    company_key="commercial metals co",
                    company_name="Commercial Metals Co",
                    cik="0000022444",
                    resolved_website="https://www.commercialmetals.com/",
                )
                self.assertTrue(db.has_resolved_website_row("commercial metals co"))
                self.assertEqual(
                    db.resolved_websites_by_key(),
                    {"commercial metals co": "https://www.commercialmetals.com/"},
                )
            finally:
                db.close()

    def test_empty_result_still_records_row_but_no_lookup_entry(self):
        """A checked-but-unresolved attempt must gate re-attempting (row
        exists) without ever appearing as a resolvable link - same contract
        as the EDGAR companies table."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.upsert_resolved_website(
                    company_key="no confident match co",
                    company_name="No Confident Match Co",
                    resolved_website="",
                )
                self.assertTrue(db.has_resolved_website_row("no confident match co"))
                self.assertEqual(db.resolved_websites_by_key(), {})
            finally:
                db.close()

    def test_works_without_a_cik(self):
        """UK companies have no CIK - the resolved cache is keyed by
        company_key precisely so it still covers them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.upsert_resolved_website(
                    company_key="some uk plc",
                    company_name="Some UK plc",
                    resolved_website="https://www.someukplc.co.uk/",
                )
                self.assertEqual(
                    db.resolved_websites_by_key(),
                    {"some uk plc": "https://www.someukplc.co.uk/"},
                )
            finally:
                db.close()

    def test_upsert_overwrites_previous_value_for_same_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.upsert_resolved_website(
                    company_key="example co",
                    company_name="Example Co",
                    resolved_website="https://old.example.com/",
                )
                db.upsert_resolved_website(
                    company_key="example co",
                    company_name="Example Co",
                    resolved_website="https://new.example.com/",
                )
                self.assertEqual(
                    db.resolved_websites_by_key(), {"example co": "https://new.example.com/"}
                )
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
