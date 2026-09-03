"""Tests for UK Price Reaction math (popday/stock_reaction.py's UK path).

Reuses the exact same compute_price_reaction() methodology as US - these
tests focus on what's UK-specific: the London-close-aware reaction anchor,
EPIC->Yahoo ticker resolution, GBp/GBP currency handling via yfinance, and
that the `market` column actually persists (it silently defaulted to 'US'
before this feature, since neither compute_price_reaction() nor
upsert_price_reaction() referenced it).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd

from popday.db import Database
from popday.stock_reaction import (
    PriceBar,
    compute_price_reaction,
    fetch_uk_daily_bars,
    reaction_anchor_date_uk,
    refresh_price_reactions,
)


class ReactionAnchorDateUkTests(unittest.TestCase):
    def test_before_lse_close_in_bst_uses_same_day(self):
        # 16:00 BST (UTC+1) = 15:00 UTC, before the 16:30 London close.
        announcement = {"acceptance_datetime": "2026-06-17T15:00:00+00:00"}
        self.assertEqual(reaction_anchor_date_uk(announcement), date(2026, 6, 17))

    def test_after_lse_close_in_bst_uses_next_day(self):
        # 16:45 BST (UTC+1) = 15:45 UTC, after the 16:30 London close.
        announcement = {"acceptance_datetime": "2026-06-17T15:45:00+00:00"}
        self.assertEqual(reaction_anchor_date_uk(announcement), date(2026, 6, 18))

    def test_after_lse_close_in_gmt_uses_next_day(self):
        # Regression test: a fixed UTC cutoff (the US path's time(16, 0))
        # would get this wrong in winter - 16:45 GMT (UTC+0) is after the
        # 16:30 London close, but well before any 16:00/17:00 UTC boundary
        # a naive comparison might use.
        announcement = {"acceptance_datetime": "2026-12-17T16:45:00+00:00"}
        self.assertEqual(reaction_anchor_date_uk(announcement), date(2026, 12, 18))

    def test_before_lse_close_in_gmt_uses_same_day(self):
        announcement = {"acceptance_datetime": "2026-12-17T16:00:00+00:00"}
        self.assertEqual(reaction_anchor_date_uk(announcement), date(2026, 12, 17))

    def test_no_acceptance_datetime_falls_back_to_filing_date(self):
        announcement = {"acceptance_datetime": None, "filing_date": "20261217"}
        self.assertEqual(reaction_anchor_date_uk(announcement), date(2026, 12, 17))


class ComputePriceReactionMarketTests(unittest.TestCase):
    def test_defaults_to_us_market(self):
        row = compute_price_reaction({"company_name": "No Market Co"}, ticker="", bars=[])
        self.assertEqual(row["market"], "US")

    def test_uk_market_propagated(self):
        row = compute_price_reaction(
            {"company_name": "UK Co", "market": "UK"}, ticker="", bars=[]
        )
        self.assertEqual(row["market"], "UK")

    def test_anchor_date_override_wins_over_default_computation(self):
        # Without the override, this acceptance time (05:00) would anchor to
        # the same day under the default US reaction_anchor_date() too - so
        # use a deliberately different override date to prove it's actually
        # used, not just coincidentally consistent.
        announcement = {
            "company_name": "Override Co",
            "acceptance_datetime": "2026-06-17T05:00:00+00:00",
        }
        bars = [
            PriceBar(date(2026, 6, 19), 10.0, 10.5, 9.8, 10.2),
            PriceBar(date(2026, 6, 20), 10.2, 10.6, 10.0, 10.4),
        ]
        row = compute_price_reaction(
            announcement, ticker="TST", bars=bars, anchor_date=date(2026, 6, 20)
        )
        self.assertEqual(row["reaction_date"], "2026-06-20")


class FetchUkDailyBarsTests(unittest.TestCase):
    def _fake_history(self, rows: dict) -> "pd.DataFrame":
        index = pd.to_datetime(list(rows.keys()))
        return pd.DataFrame(
            {
                "Open": [v[0] for v in rows.values()],
                "High": [v[1] for v in rows.values()],
                "Low": [v[2] for v in rows.values()],
                "Close": [v[3] for v in rows.values()],
            },
            index=index,
        )

    def test_gbp_pence_normalized_to_pounds(self):
        history = self._fake_history(
            {"2026-06-17": (1680.0, 1700.0, 1670.0, 1690.0)}
        )
        fake_ticker = mock.Mock()
        fake_ticker.history.return_value = history
        fake_ticker.history_metadata = {"currency": "GBp"}
        fake_yfinance = mock.Mock()
        fake_yfinance.Ticker.return_value = fake_ticker

        with mock.patch.dict("sys.modules", {"yfinance": fake_yfinance}):
            bars = fetch_uk_daily_bars("DGE.L")

        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0].close, 16.90)
        self.assertAlmostEqual(bars[0].open, 16.80)

    def test_unresolvable_currency_yields_no_bars(self):
        # Never guess: missing or non-GBp/GBP currency metadata means the
        # data is unusable, exactly like popday/prices.py's capture path.
        history = self._fake_history({"2026-06-17": (10.0, 10.5, 9.8, 10.2)})
        fake_ticker = mock.Mock()
        fake_ticker.history.return_value = history
        fake_ticker.history_metadata = {"currency": "USD"}
        fake_yfinance = mock.Mock()
        fake_yfinance.Ticker.return_value = fake_ticker

        with mock.patch.dict("sys.modules", {"yfinance": fake_yfinance}):
            bars = fetch_uk_daily_bars("WEIRD.L")
        self.assertEqual(bars, [])

    def test_empty_history_yields_no_bars(self):
        fake_ticker = mock.Mock()
        fake_ticker.history.return_value = pd.DataFrame()
        fake_yfinance = mock.Mock()
        fake_yfinance.Ticker.return_value = fake_ticker

        with mock.patch.dict("sys.modules", {"yfinance": fake_yfinance}):
            bars = fetch_uk_daily_bars("NODATA.L")
        self.assertEqual(bars, [])


class RefreshPriceReactionsUkIntegrationTests(unittest.TestCase):
    """End-to-end through refresh_price_reactions() with a real temp DB -
    catches wiring bugs (market silently dropping to 'US', EPIC not
    resolving to a Yahoo symbol) that pure unit tests would miss."""

    def _db(self, tmpdir: str) -> Database:
        return Database(str(Path(tmpdir) / "popday.sqlite3"))

    def test_uk_detection_produces_a_uk_price_reaction_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (accession_number, cik, company_name, form_type, filing_date,
                     acceptance_datetime, filing_url, event_type,
                     event_date, matched_phrase, matched_location, snippet, status,
                     market, ticker, created_timestamp)
                    VALUES
                    ('uk-1', '', 'Diageo plc', 'RNS', '2026-06-17',
                     '2026-06-17T15:00:00+00:00', 'https://example.com',
                     'Investor Day', '2026-09-15', 'investor day', 'body', 'snippet',
                     'alert_candidate', 'UK', 'DGE', '2026-06-17T15:05:00+00:00')
                    """
                )
                db.conn.commit()

                bars = [
                    PriceBar(date(2026, 6, 16), 16.5, 16.8, 16.4, 16.6),
                    PriceBar(date(2026, 6, 17), 16.7, 16.9, 16.5, 16.85),
                    PriceBar(date(2026, 6, 18), 16.9, 17.2, 16.8, 17.1),
                ]
                with mock.patch(
                    "popday.stock_reaction.fetch_uk_daily_bars", return_value=bars
                ) as fetch, mock.patch(
                    "popday.stock_reaction.fetch_cik_ticker_map", return_value={}
                ):
                    refresh_price_reactions(db, user_agent="PopDay/0.1 test")

                fetch.assert_called_once_with("DGE.L")
                rows = db.price_reaction_rows(market="UK")
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["market"], "UK")
                self.assertEqual(row["ticker"], "DGE.L")
                self.assertEqual(row["reaction_date"], "2026-06-17")
                self.assertEqual(row["previous_close_date"], "2026-06-16")
                self.assertAlmostEqual(row["previous_close"], 16.6)
                self.assertAlmostEqual(row["reaction_close"], 16.85)
                self.assertEqual(row["status"], "ok")

                # EPIC -> Yahoo symbol mapping must be recorded, auditable.
                mapping = db.get_ticker_mapping("UK", "DGE")
                self.assertEqual(mapping["yahoo_symbol"], "DGE.L")
            finally:
                db.close()

    def test_us_rows_unaffected_by_uk_processing(self):
        """Adding UK handling must not change a single thing about how US
        rows are resolved or computed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = self._db(tmpdir)
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (accession_number, cik, company_name, form_type, filing_date,
                     acceptance_datetime, filing_url, event_type,
                     event_date, matched_phrase, matched_location, snippet, status,
                     market, ticker, created_timestamp)
                    VALUES
                    ('us-1', '0000000099', 'Example US Co', '8-K', '2026-06-17',
                     '2026-06-17T15:30:00-04:00', 'https://example.com',
                     'Investor Day', '2026-09-15', 'investor day', 'body', 'snippet',
                     'alert_candidate', 'US', 'EXUS', '2026-06-17T15:35:00+00:00')
                    """
                )
                db.conn.commit()

                us_bars = [
                    PriceBar(date(2026, 6, 16), 9.8, 10.2, 9.7, 10.0),
                    PriceBar(date(2026, 6, 17), 10.1, 10.3, 9.9, 10.1),
                    PriceBar(date(2026, 6, 18), 10.4, 11.2, 10.2, 11.0),
                ]
                with mock.patch(
                    "popday.stock_reaction.fetch_daily_bars",
                    return_value=(us_bars, "yahoo_chart_daily_json"),
                ), mock.patch(
                    "popday.stock_reaction.fetch_cik_ticker_map", return_value={}
                ), mock.patch(
                    "popday.stock_reaction.fetch_uk_daily_bars"
                ) as uk_fetch:
                    refresh_price_reactions(db, user_agent="PopDay/0.1 test")

                uk_fetch.assert_not_called()
                us_rows = db.price_reaction_rows(market="US")
                self.assertEqual(len(us_rows), 1)
                self.assertEqual(us_rows[0]["market"], "US")
                self.assertEqual(us_rows[0]["ticker"], "EXUS")
                self.assertEqual(us_rows[0]["status"], "ok")
                self.assertEqual(db.price_reaction_rows(market="UK"), [])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
