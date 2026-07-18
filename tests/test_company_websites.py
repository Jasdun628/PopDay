"""Tests for popday/company_websites.py - EDGAR self-reported website capture."""

from __future__ import annotations

import unittest
from unittest import mock

from popday.company_websites import (
    fetch_edgar_website,
    normalize_cik,
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


if __name__ == "__main__":
    unittest.main()
