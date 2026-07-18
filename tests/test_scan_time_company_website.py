"""Tests for cli._ensure_company_website - the scan-time EDGAR website capture.

Capture happens once, the first time a company (by CIK) is discovered, never
on later filings from it, and a fetch failure must never raise (cosmetic
data must never block the scan).
"""

from __future__ import annotations

import unittest
from unittest import mock

from popday import cli
from popday.edgar_fetch import EdgarBlockedError, Filing


def _filing(cik="0001005516", company_name="BOS Better Online Solutions Ltd") -> Filing:
    return Filing(
        accession_number="0001-26-000001",
        cik=cik,
        company_name=company_name,
        form_type="8-K",
        filing_date="2026-07-18",
        filing_url="https://www.sec.gov/Archives/edgar/data/1005516/0001-26-000001.txt",
        primary_document="doc.htm",
    )


class EnsureCompanyWebsiteTests(unittest.TestCase):
    def test_captures_website_for_newly_discovered_company(self):
        db = mock.Mock()
        db.has_company_website_row.return_value = False
        client = mock.Mock()
        with mock.patch.object(cli, "fetch_edgar_website", return_value="https://www.boscom.com") as fetch:
            cli._ensure_company_website(db, client, _filing())
        fetch.assert_called_once_with(client, "0001005516")
        db.upsert_company_website.assert_called_once_with(
            cik="0001005516",
            company_name="BOS Better Online Solutions Ltd",
            edgar_website="https://www.boscom.com",
        )

    def test_skips_already_captured_company(self):
        db = mock.Mock()
        db.has_company_website_row.return_value = True
        client = mock.Mock()
        with mock.patch.object(cli, "fetch_edgar_website") as fetch:
            cli._ensure_company_website(db, client, _filing())
        fetch.assert_not_called()
        db.upsert_company_website.assert_not_called()

    def test_fetch_failure_is_logged_never_raised_and_never_stored(self):
        db = mock.Mock()
        db.has_company_website_row.return_value = False
        client = mock.Mock()
        with mock.patch.object(cli, "fetch_edgar_website", side_effect=EdgarBlockedError("url", 403)):
            cli._ensure_company_website(db, client, _filing())  # must not raise
        db.upsert_company_website.assert_not_called()

    def test_empty_cik_is_skipped(self):
        db = mock.Mock()
        client = mock.Mock()
        with mock.patch.object(cli, "fetch_edgar_website") as fetch:
            cli._ensure_company_website(db, client, _filing(cik=""))
        fetch.assert_not_called()
        db.has_company_website_row.assert_not_called()
        db.upsert_company_website.assert_not_called()

    def test_website_not_found_still_records_row_as_empty(self):
        db = mock.Mock()
        db.has_company_website_row.return_value = False
        client = mock.Mock()
        with mock.patch.object(cli, "fetch_edgar_website", return_value=""):
            cli._ensure_company_website(db, client, _filing())
        db.upsert_company_website.assert_called_once_with(
            cik="0001005516",
            company_name="BOS Better Online Solutions Ltd",
            edgar_website="",
        )


if __name__ == "__main__":
    unittest.main()
