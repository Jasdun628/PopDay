"""Tests for popday/company_websites.py - EDGAR self-reported website capture."""

from __future__ import annotations

import unittest
from unittest import mock

from popday.company_websites import (
    fetch_edgar_website,
    normalize_cik,
    resolve_company_website,
    select_edgar_website,
)


class NormalizeCikTests(unittest.TestCase):
    def test_zero_pads_numeric_cik(self):
        self.assertEqual(normalize_cik("1005516"), "0001005516")
        self.assertEqual(normalize_cik(1005516), "0001005516")

    def test_already_padded_cik_unchanged(self):
        self.assertEqual(normalize_cik("0001005516"), "0001005516")

    def test_empty_cik(self):
        self.assertEqual(normalize_cik(""), "")
        self.assertEqual(normalize_cik(None), "")


class SelectEdgarWebsiteTests(unittest.TestCase):
    def test_prefers_website_over_investor_website(self):
        payload = {
            "website": "https://www.example.com",
            "investorWebsite": "https://investor.example.com",
        }
        self.assertEqual(select_edgar_website(payload), "https://www.example.com")

    def test_falls_back_to_investor_website_when_website_empty(self):
        payload = {"website": "", "investorWebsite": "https://investor.example.com"}
        self.assertEqual(select_edgar_website(payload), "https://investor.example.com")

    def test_neither_field_present_returns_empty(self):
        self.assertEqual(select_edgar_website({}), "")

    def test_junk_taxonomy_host_is_filtered_out(self):
        # Reuses detector.py's IR-link junk-host filter - an EDGAR-reported
        # xbrl.org/sec.gov artifact must never be stored as a website.
        payload = {"website": "http://www.xbrl.org/2003/role/presentationLinkbaseRef"}
        self.assertEqual(select_edgar_website(payload), "")

    def test_junk_website_falls_back_to_usable_investor_website(self):
        payload = {
            "website": "https://www.sec.gov/some/path",
            "investorWebsite": "https://investor.example.com",
        }
        self.assertEqual(select_edgar_website(payload), "https://investor.example.com")

    def test_non_http_scheme_rejected(self):
        self.assertEqual(select_edgar_website({"website": "ftp://example.com"}), "")

    def test_malformed_url_without_host_rejected(self):
        self.assertEqual(select_edgar_website({"website": "not-a-url"}), "")


class FetchEdgarWebsiteTests(unittest.TestCase):
    def test_fetches_submissions_json_for_normalized_cik(self):
        client = mock.Mock()
        client.get_json.return_value = {"website": "https://www.example.com"}
        result = fetch_edgar_website(client, "1005516")
        client.get_json.assert_called_once_with(
            "https://data.sec.gov/submissions/CIK0001005516.json"
        )
        self.assertEqual(result, "https://www.example.com")

    def test_propagates_hard_failures(self):
        from popday.edgar_fetch import EdgarBlockedError

        client = mock.Mock()
        client.get_json.side_effect = EdgarBlockedError("url", 403)
        with self.assertRaises(EdgarBlockedError):
            fetch_edgar_website(client, "1005516")


class ResolveCompanyWebsiteTests(unittest.TestCase):
    """Priority order: curated > EDGAR > heuristic auto-resolve > "" -
    manual corrections must always win, and the auto-resolver must never
    override an EDGAR-sourced link either."""

    def test_curated_wins_over_everything(self):
        result = resolve_company_website(
            "Example Co",
            "0000000001",
            {"Example Co": "https://curated.example.com/"},
            {"0000000001": "https://edgar.example.com/"},
            {"example co": "https://resolved.example.com/"},
        )
        self.assertEqual(result, "https://curated.example.com/")

    def test_edgar_wins_over_resolved_when_curated_empty(self):
        result = resolve_company_website(
            "Example Co",
            "0000000001",
            {},
            {"0000000001": "https://edgar.example.com/"},
            {"example co": "https://resolved.example.com/"},
        )
        self.assertEqual(result, "https://edgar.example.com/")

    def test_resolved_fills_gap_when_curated_and_edgar_empty(self):
        result = resolve_company_website(
            "Example Co", "0000000001", {}, {}, {"example co": "https://resolved.example.com/"}
        )
        self.assertEqual(result, "https://resolved.example.com/")

    def test_resolved_lookup_works_without_a_cik(self):
        """UK companies pass cik=None - the resolved layer must still match
        by company name."""
        result = resolve_company_website(
            "Some UK plc", None, {}, {}, {"some uk plc": "https://someukplc.co.uk/"}
        )
        self.assertEqual(result, "https://someukplc.co.uk/")

    def test_nothing_resolved_anywhere_returns_empty(self):
        self.assertEqual(resolve_company_website("Example Co", "0000000001", {}, {}, {}), "")
        self.assertEqual(resolve_company_website("Example Co", "0000000001", {}, {}, None), "")


if __name__ == "__main__":
    unittest.main()
