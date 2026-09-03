"""Integration test for _scan_single_date's EDGAR website capture + the
company_url_missing alert flag, exercising the real scan loop (not just the
unit-level _ensure_company_website tests) so a bug like calling the EDGAR
fetch twice per filing, or computing the missing-link flag from stale data,
would actually be caught.
"""

from __future__ import annotations

import argparse
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from popday import cli
from popday.config import load_config
from popday.db import Database
from popday.edgar_fetch import EdgarClient, Filing

RAW_FILING = """
<SEC-HEADER>
ACCESSION NUMBER: 0000000000-26-000001
CONFORMED SUBMISSION TYPE: 8-K
COMPANY CONFORMED NAME: EXAMPLE INC
CENTRAL INDEX KEY: 0000000001
FILED AS OF DATE: 20260619
ITEM INFORMATION: 7.01
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-99.1
<FILENAME>d123dex991.htm
<DESCRIPTION>Press release
<TEXT>
<html><body>
<p>Example Inc. will host an Investor Day on September 15, 2026, offering
a detailed look at strategy and growth.</p>
</body></html>
</TEXT>
</DOCUMENT>
"""

_FILING = Filing(
    accession_number="0000000000-26-000001",
    cik="0000000001",
    company_name="Example Inc.",
    form_type="8-K",
    filing_date="2026-06-19",
    filing_url="https://www.sec.gov/Archives/edgar/data/1/0000000000-26-000001.txt",
    primary_document="example.htm",
)


class ScanSingleDateCompanyWebsiteTests(unittest.TestCase):
    def _run(self, *, curated: dict[str, str], edgar_website: str, resolved_website: str = ""):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "popday.sqlite3")
            db = Database(db_path)
            config = load_config()
            config = mock.Mock(
                sec_user_agent="PopDay/0.1 test@example.com",
                request_delay_seconds=0.0,
                company_websites=curated,
                hype_threshold=1,
                hype_definition_version="v1",
                hype_provisional=True,
            )
            args = argparse.Namespace(
                dry_run=False,
                reprocess=False,
                legacy_parser=False,
                max_companies=None,
            )
            # resolve_website_heuristic makes real outbound HTTP requests -
            # always mocked here so this test never depends on network
            # access or a specific domain's live content.
            with mock.patch.object(EdgarClient, "search_filings_for_phrases", return_value=[_FILING]), \
                 mock.patch.object(EdgarClient, "get_text", return_value=RAW_FILING), \
                 mock.patch.object(cli, "fetch_edgar_website", return_value=edgar_website) as fetch, \
                 mock.patch.object(
                     cli, "resolve_website_heuristic", return_value=resolved_website
                 ) as resolve_heuristic, \
                 mock.patch.object(cli, "refresh_price_reactions", return_value=[]), \
                 mock.patch.object(cli, "watch_hype_candidates", return_value=[]):
                outcome = cli._scan_single_date(
                    config, db, args, date(2026, 6, 19), run_kind="scheduled"
                )
            db.close()
            return outcome, fetch, resolve_heuristic

    def test_edgar_capture_runs_once_and_alert_flags_link_present(self):
        outcome, fetch, resolve_heuristic = self._run(
            curated={}, edgar_website="https://www.example.com/"
        )

        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(len(outcome.alerts), 1)
        fetch.assert_called_once()
        self.assertFalse(outcome.alerts[0].company_url_missing)
        # EDGAR already filled the gap - the heuristic resolver is a lower
        # priority layer and must never even be attempted here.
        resolve_heuristic.assert_not_called()

    def test_curated_link_means_edgar_not_needed_for_missing_flag(self):
        # The enriched filing's company_name comes from the raw SEC header
        # ("COMPANY CONFORMED NAME: EXAMPLE INC" in RAW_FILING above), not
        # the discovery-time Filing.company_name - curated lookup is
        # case/whitespace-normalized only, punctuation matters, so the key
        # here must match that raw header string exactly.
        outcome, fetch, resolve_heuristic = self._run(
            curated={"EXAMPLE INC": "https://curated.example.com/"}, edgar_website=""
        )

        self.assertEqual(len(outcome.alerts), 1)
        self.assertFalse(outcome.alerts[0].company_url_missing)
        resolve_heuristic.assert_not_called()

    def test_no_link_anywhere_tries_heuristic_resolver_then_flags_missing_if_it_fails(self):
        outcome, fetch, resolve_heuristic = self._run(
            curated={}, edgar_website="", resolved_website=""
        )

        self.assertEqual(len(outcome.alerts), 1)
        self.assertTrue(outcome.alerts[0].company_url_missing)
        fetch.assert_called_once()
        resolve_heuristic.assert_called_once()

    def test_heuristic_resolver_success_clears_the_missing_flag(self):
        outcome, fetch, resolve_heuristic = self._run(
            curated={}, edgar_website="", resolved_website="https://www.exampleinc.com/"
        )

        self.assertEqual(len(outcome.alerts), 1)
        self.assertFalse(outcome.alerts[0].company_url_missing)
        resolve_heuristic.assert_called_once()

    def test_captured_website_is_persisted_for_the_company(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "popday.sqlite3")
            db = Database(db_path)
            config = mock.Mock(
                sec_user_agent="PopDay/0.1 test@example.com",
                request_delay_seconds=0.0,
                company_websites={},
                hype_threshold=1,
                hype_definition_version="v1",
                hype_provisional=True,
            )
            args = argparse.Namespace(
                dry_run=False, reprocess=False, legacy_parser=False, max_companies=None
            )
            with mock.patch.object(EdgarClient, "search_filings_for_phrases", return_value=[_FILING]), \
                 mock.patch.object(EdgarClient, "get_text", return_value=RAW_FILING), \
                 mock.patch.object(cli, "fetch_edgar_website", return_value="https://www.example.com/"), \
                 mock.patch.object(cli, "resolve_website_heuristic", return_value=""), \
                 mock.patch.object(cli, "refresh_price_reactions", return_value=[]), \
                 mock.patch.object(cli, "watch_hype_candidates", return_value=[]):
                cli._scan_single_date(config, db, args, date(2026, 6, 19), run_kind="scheduled")

            self.assertEqual(
                db.company_websites_by_cik(), {"0000000001": "https://www.example.com/"}
            )
            db.close()

    def test_resolved_website_is_persisted_for_the_company(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "popday.sqlite3")
            db = Database(db_path)
            config = mock.Mock(
                sec_user_agent="PopDay/0.1 test@example.com",
                request_delay_seconds=0.0,
                company_websites={},
                hype_threshold=1,
                hype_definition_version="v1",
                hype_provisional=True,
            )
            args = argparse.Namespace(
                dry_run=False, reprocess=False, legacy_parser=False, max_companies=None
            )
            with mock.patch.object(EdgarClient, "search_filings_for_phrases", return_value=[_FILING]), \
                 mock.patch.object(EdgarClient, "get_text", return_value=RAW_FILING), \
                 mock.patch.object(cli, "fetch_edgar_website", return_value=""), \
                 mock.patch.object(
                     cli, "resolve_website_heuristic", return_value="https://www.exampleinc.com/"
                 ), \
                 mock.patch.object(cli, "refresh_price_reactions", return_value=[]), \
                 mock.patch.object(cli, "watch_hype_candidates", return_value=[]):
                cli._scan_single_date(config, db, args, date(2026, 6, 19), run_kind="scheduled")

            self.assertEqual(
                db.resolved_websites_by_key(), {"example inc": "https://www.exampleinc.com/"}
            )
            db.close()


if __name__ == "__main__":
    unittest.main()
