"""Tests for popday.sources.investegate against real saved fixtures.

Fixtures are real HTML pulled from investegate.co.uk on 2026-07-14 (see
tests/fixtures/). No network access - the underlying HTTP methods are
monkeypatched to return fixture content.
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from popday.sources import Announcement
from popday.sources.investegate import (
    CANARY_EXPECTED_COMPANY_SUBSTRING,
    CANARY_EXPECTED_DEDUP_KEY,
    CANARY_EXPECTED_HEADLINE,
    InvestegateBlockedError,
    InvestegateClient,
    _headline_matches,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INDEX_HTML = (FIXTURES / "investegate_index_2026-07-14.html").read_text(encoding="utf-8")
CANARY_HTML = (FIXTURES / "investegate_canary_glencore_9184165.html").read_text(encoding="utf-8")


def _client() -> InvestegateClient:
    return InvestegateClient("PopDay/0.1 test@example.com", skip_robots_check=True)


class IndexParsingTests(unittest.TestCase):
    def test_parses_real_index_rows(self):
        client = _client()
        rows = client._rows_from_html(INDEX_HTML)
        self.assertGreater(len(rows), 250)
        self.assertTrue(all(isinstance(r, Announcement) for r in rows))
        self.assertTrue(all(r.market == "UK" for r in rows))
        self.assertTrue(all(r.source == "investegate" for r in rows))
        self.assertTrue(all(r.dedup_key.isdigit() for r in rows))

    def test_dcc_row_fields_are_correct(self):
        client = _client()
        rows = client._rows_from_html(INDEX_HTML)
        dcc = next(r for r in rows if r.dedup_key == "9668806")
        self.assertIn("DCC", dcc.company_name)
        self.assertEqual(dcc.company_identifier, "DCC")
        self.assertEqual(dcc.wire_or_form, "RNS")
        self.assertEqual(dcc.headline, "Form 38.5a (EPT/RI)-DCC plc Amend")
        self.assertEqual(dcc.announced_at, "2026-07-14T16:48:00+00:00")  # 17:48 BST -> UTC

    def test_dedup_on_numeric_id(self):
        client = _client()
        rows = client._rows_from_html(INDEX_HTML + INDEX_HTML)  # duplicate content
        with mock.patch.object(client, "get_html", return_value=INDEX_HTML), \
             mock.patch.object(client, "_day_url", return_value="fake://page"):
            index = client.index_for_date(date(2026, 7, 14), max_pages=1)
        ids = [row.dedup_key for row in index]
        self.assertEqual(len(ids), len(set(ids)), "index_for_date must dedup by numeric id")


class HeadlineMatchingTests(unittest.TestCase):
    INCLUDE = [
        "capital markets day", "capital markets event", "investor day",
        "investor seminar", "analyst day", "teach-in", "capital markets update", "cmd",
    ]
    EXCLUDE = ["investor meet company", "investor presentation", "investor relations website"]

    def test_matches_capital_markets_day(self):
        self.assertTrue(_headline_matches("Notice of Capital Markets Day", self.INCLUDE, self.EXCLUDE))

    def test_matches_cmd_word_boundary_only(self):
        self.assertTrue(_headline_matches("2026 CMD Update", self.INCLUDE, self.EXCLUDE))
        self.assertFalse(_headline_matches("Recmd Trading Update", self.INCLUDE, self.EXCLUDE))

    def test_excludes_investor_meet_company(self):
        self.assertFalse(_headline_matches("Investor Meet Company Presentation", self.INCLUDE, self.EXCLUDE))

    def test_excludes_bare_investor_presentation(self):
        self.assertFalse(_headline_matches("Investor Presentation", self.INCLUDE, self.EXCLUDE))

    def test_no_match_on_unrelated_headline(self):
        self.assertFalse(_headline_matches("Transaction in Own Shares", self.INCLUDE, self.EXCLUDE))

    def test_case_insensitive(self):
        self.assertTrue(_headline_matches("NOTICE OF CAPITAL MARKETS DAY", self.INCLUDE, self.EXCLUDE))


class CanaryTests(unittest.TestCase):
    def test_canary_passes_against_real_fixture(self):
        client = _client()
        with mock.patch.object(client, "get_html", return_value=CANARY_HTML):
            self.assertTrue(client.probe_canary())

    def test_canary_fails_when_headline_changed(self):
        client = _client()
        broken_html = CANARY_HTML.replace(CANARY_EXPECTED_HEADLINE, "Something Else Entirely")
        with mock.patch.object(client, "get_html", return_value=broken_html):
            self.assertFalse(client.probe_canary())

    def test_canary_fails_on_block(self):
        client = _client()
        with mock.patch.object(client, "get_html", side_effect=InvestegateBlockedError("u", 403)):
            self.assertFalse(client.probe_canary())

    def test_canary_constants_are_internally_consistent(self):
        self.assertIn(CANARY_EXPECTED_DEDUP_KEY, CANARY_HTML)
        self.assertIn(CANARY_EXPECTED_COMPANY_SUBSTRING, CANARY_HTML)


class ScanIntegrationTests(unittest.TestCase):
    def test_scan_fetches_detail_only_for_matches(self):
        client = _client()
        detail_calls = []

        def fake_get_html(url):
            if "today-announcements" in url:
                return INDEX_HTML
            detail_calls.append(url)
            return CANARY_HTML

        with mock.patch.object(client, "get_html", side_effect=fake_get_html):
            results = client.scan(
                date(2026, 7, 14),
                include_phrases=["capital markets day", "notice of capital markets day"],
                exclude_phrases=[],
                max_pages=1,
            )
        # None of the fixture's real rows are CMD announcements, so scan()
        # should fetch zero detail pages and return zero matches.
        self.assertEqual(results, [])
        self.assertEqual(detail_calls, [])

    def test_scan_populates_raw_text_for_a_synthetic_match(self):
        client = _client()

        def fake_get_html(url):
            if "today-announcements" in url:
                return INDEX_HTML
            return CANARY_HTML

        with mock.patch.object(client, "get_html", side_effect=fake_get_html):
            # Force one row to look like a match by matching against a real
            # headline substring already present in the fixture, then confirm
            # raw_text gets populated from the (mocked) detail fetch.
            index = client.index_for_date(date(2026, 7, 14), max_pages=1)
            target_headline = index[0].headline
            results = client.scan(
                date(2026, 7, 14),
                include_phrases=[target_headline.lower()],
                exclude_phrases=[],
                max_pages=1,
            )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].raw_text)
        self.assertIn("Glencore", results[0].raw_text)


if __name__ == "__main__":
    unittest.main()
