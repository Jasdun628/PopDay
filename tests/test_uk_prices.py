"""Ticker mapping, GBp conversion, and capture-window tests (UK Phase 2)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date

from popday.db import Database
from popday.prices import (
    map_epic_to_yahoo,
    normalize_close,
    resolve_yahoo_symbol,
    uk_price_universe,
)


class TickerMappingTests(unittest.TestCase):
    def test_plain_epic(self):
        self.assertEqual(map_epic_to_yahoo("VOD"), "VOD.L")

    def test_trailing_dot_stripped(self):
        self.assertEqual(map_epic_to_yahoo("NG."), "NG.L")

    def test_internal_dot_becomes_dash(self):
        self.assertEqual(map_epic_to_yahoo("BT.A"), "BT-A.L")

    def test_lowercase_normalised(self):
        self.assertEqual(map_epic_to_yahoo("vod"), "VOD.L")

    def test_empty_is_empty(self):
        self.assertEqual(map_epic_to_yahoo(""), "")
        self.assertEqual(map_epic_to_yahoo("."), "")


class GbpConversionTests(unittest.TestCase):
    def test_gbp_pence_divided_by_100(self):
        close, currency = normalize_close(116.75, "GBp")
        self.assertAlmostEqual(close, 1.1675)
        self.assertEqual(currency, "GBP")

    def test_whole_gbp_untouched(self):
        close, currency = normalize_close(11.675, "GBP")
        self.assertAlmostEqual(close, 11.675)
        self.assertEqual(currency, "GBP")

    def test_usd_never_converted(self):
        close, currency = normalize_close(50.0, "USD")
        self.assertAlmostEqual(close, 50.0)
        self.assertEqual(currency, "USD")


class MappingTableAndUniverseTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_manual_override_wins_and_derived_recorded(self):
        self.assertEqual(resolve_yahoo_symbol(self.db, "NG."), "NG.L")
        row = self.db.get_ticker_mapping("UK", "NG.")
        self.assertEqual(row["yahoo_symbol"], "NG.L")
        self.db.conn.execute(
            "UPDATE ticker_mappings SET manual_override = 'NGX.L' "
            "WHERE market = 'UK' AND local_symbol = 'NG.'"
        )
        self.db.conn.commit()
        self.assertEqual(resolve_yahoo_symbol(self.db, "NG."), "NGX.L")

    def _insert_uk_event(self, filing_date: str, event_date: str, ticker: str = "GLEN"):
        self.db.conn.execute(
            """
            INSERT INTO detections
            (accession_number, company_name, cik, form_type, filing_date, filing_url,
             event_type, event_date, status, created_timestamp, market, ticker)
            VALUES (?, 'Glencore', '', 'RNS', ?, 'https://example.test/a', 'Capital Markets Day',
                    ?, 'alert_candidate', '2026-01-01T00:00:00+00:00', 'UK', ?)
            """,
            (f"9{filing_date.replace('-', '')}", filing_date, event_date, ticker),
        )
        self.db.conn.commit()

    def test_universe_window_matches_paper_windows(self):
        self._insert_uk_event("2026-07-01", "2026-07-20")
        # inside [announcement-14d, event+14d]
        self.assertEqual(len(uk_price_universe(self.db, date(2026, 7, 10))), 1)
        self.assertEqual(len(uk_price_universe(self.db, date(2026, 6, 20))), 1)
        self.assertEqual(len(uk_price_universe(self.db, date(2026, 8, 3))), 1)
        # outside
        self.assertEqual(len(uk_price_universe(self.db, date(2026, 6, 10))), 0)
        self.assertEqual(len(uk_price_universe(self.db, date(2026, 8, 10))), 0)


if __name__ == "__main__":
    unittest.main()
