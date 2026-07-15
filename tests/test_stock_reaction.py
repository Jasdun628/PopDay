import unittest
from datetime import date

from popday.stock_reaction import PriceBar, compute_price_reaction, reaction_anchor_date


class PriceReactionTests(unittest.TestCase):
    def test_acceptance_before_market_close_uses_same_day(self):
        announcement = {
            "source_table": "detections",
            "source_id": 1,
            "company_name": "Same Day Co",
            "filing_date": "20260617",
            "acceptance_datetime": "2026-06-17T15:30:00-04:00",
        }

        self.assertEqual(reaction_anchor_date(announcement), date(2026, 6, 17))

    def test_acceptance_after_market_close_uses_next_day(self):
        announcement = {
            "source_table": "detections",
            "source_id": 1,
            "company_name": "After Market Co",
            "filing_date": "20260617",
            "acceptance_datetime": "2026-06-17T16:24:37-04:00",
        }

        self.assertEqual(reaction_anchor_date(announcement), date(2026, 6, 18))

    def test_computes_cached_daily_price_reaction(self):
        announcement = {
            "source_table": "detections",
            "source_id": 7,
            "company_name": "Reaction Co",
            "cik": "0000000007",
            "event_date": "2026-09-15",
            "filing_date": "20260617",
            "acceptance_datetime": "2026-06-17T16:24:37-04:00",
        }
        bars = [
            PriceBar(date(2026, 6, 16), 9.8, 10.2, 9.7, 10.0),
            PriceBar(date(2026, 6, 17), 10.1, 10.3, 9.9, 10.1),
            PriceBar(date(2026, 6, 18), 10.4, 11.2, 10.2, 11.0),
            PriceBar(date(2026, 6, 19), 10.9, 11.5, 10.7, 11.4),
        ]

        row = compute_price_reaction(
            announcement,
            ticker="RCT",
            bars=bars,
            timestamp="2026-07-01T08:00:00+00:00",
        )

        self.assertEqual(row["announcement_key"], "detections:7")
        self.assertEqual(row["reaction_date"], "2026-06-18")
        self.assertEqual(row["previous_close_date"], "2026-06-17")
        self.assertAlmostEqual(row["announcement_move_pct"], 8.910891089108919)
        self.assertEqual(row["latest_close"], 11.4)
        self.assertEqual(row["status"], "ok")


if __name__ == "__main__":
    unittest.main()


class TickerPreferenceTests(unittest.TestCase):
    def test_common_stock_beats_warrant_regardless_of_order(self):
        from unittest import mock
        import json as _json
        from popday import stock_reaction as sr

        payload = _json.dumps({
            "0": {"cik_str": 999001, "ticker": "BBCQW", "title": "Spac W"},
            "1": {"cik_str": 999001, "ticker": "BBCQ", "title": "Spac"},
            "2": {"cik_str": 999002, "ticker": "DRDGF", "title": "DRDGOLD OTC"},
            "3": {"cik_str": 999002, "ticker": "DRD", "title": "DRDGOLD ADR"},
        })
        with mock.patch.object(sr, "_fetch_text", return_value=payload):
            mapping = sr.fetch_cik_ticker_map(user_agent="ua")
        self.assertEqual(mapping[sr._normalize_cik(999001)], "BBCQ")
        self.assertEqual(mapping[sr._normalize_cik(999002)], "DRD")


class ResolveTickerOverrideTests(unittest.TestCase):
    """known_announcements rows have no real CIK (they're press-release-only
    events that never touched EDGAR), so their ticker/cik_override columns
    are the only way resolve_ticker can ever find them - found 2026-07-15
    while auditing why the Sandisk Corp manual row showed blank everywhere."""

    def test_ticker_override_used_directly(self):
        from popday import stock_reaction as sr

        announcement = {"ticker": "SNDK", "cik": None, "company_name": "Sandisk Corp"}
        self.assertEqual(sr.resolve_ticker(announcement, cik_tickers={}), "SNDK")

    def test_cik_override_resolves_via_the_normal_cik_ticker_map(self):
        from popday import stock_reaction as sr

        announcement = {"ticker": None, "cik": "2023554", "company_name": "Sandisk Corp"}
        cik_tickers = {sr._normalize_cik("2023554"): "SNDK"}
        self.assertEqual(sr.resolve_ticker(announcement, cik_tickers), "SNDK")

    def test_no_override_and_no_map_hit_falls_back_to_hardcoded_overrides(self):
        from popday import stock_reaction as sr

        announcement = {"ticker": None, "cik": None, "company_name": "Radian Group Inc."}
        self.assertEqual(sr.resolve_ticker(announcement, cik_tickers={}), "RDN")

    def test_no_override_and_no_hardcoded_entry_resolves_to_blank(self):
        from popday import stock_reaction as sr

        announcement = {"ticker": None, "cik": None, "company_name": "Totally Unknown Co"}
        self.assertEqual(sr.resolve_ticker(announcement, cik_tickers={}), "")
