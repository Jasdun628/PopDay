"""Regression tests for the July 2026 detection-coverage fixes.

These pin the three gaps that were dropping real Investor Days:
- same-day / just-happened events (strict "future only" date rule),
- forward announcements that also say "previously announced" (past-cue veto),
- plural phrases like "Investor Days" (EFTS exact-phrase discovery).
"""

from __future__ import annotations

import unittest
from datetime import date

from popday.date_extract import extract_event_date, extract_future_date
from popday.detector import detect_in_parsed_filing
from popday.edgar_fetch import EdgarClient, Filing
from popday.filing_parser import parse_sec_filing


def _raw(items: str, body: str, filed: str = "20260618", accession: str = "0000000000-26-000009") -> str:
    return f"""
<SEC-HEADER>
ACCESSION NUMBER: {accession}
CONFORMED SUBMISSION TYPE: 8-K
COMPANY CONFORMED NAME: EXAMPLE INC
CENTRAL INDEX KEY: 0000000001
FILED AS OF DATE: {filed}
ITEM INFORMATION: {items}
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-99.1
<FILENAME>d1dex991.htm
<DESCRIPTION>Press release
<TEXT>
<html><body>{body}</body></html>
</TEXT>
</DOCUMENT>
"""


def _filing(filed: str = "2026-06-18", accession: str = "0000000000-26-000009") -> Filing:
    return Filing(
        accession_number=accession,
        cik="0000000001",
        company_name="Example Inc.",
        form_type="8-K",
        filing_date=filed,
        filing_url=f"https://www.sec.gov/Archives/edgar/data/1/{accession}.txt",
        primary_document="example.htm",
    )


class ExtractEventDateTests(unittest.TestCase):
    def test_same_day_accepted(self):
        run = date(2026, 6, 18)
        self.assertEqual(extract_event_date("Investor Day on June 18, 2026", run), run)
        # strict future rule drops it
        self.assertIsNone(extract_future_date("Investor Day on June 18, 2026", run))

    def test_recent_past_within_grace_accepted(self):
        run = date(2026, 6, 22)
        self.assertEqual(
            extract_event_date("hosted its Investor Day on June 20, 2026", run),
            date(2026, 6, 20),
        )

    def test_old_backward_reference_rejected(self):
        run = date(2026, 6, 24)
        self.assertIsNone(extract_event_date("at our Investor Day in April 9, 2026", run))

    def test_prefers_soonest_upcoming(self):
        run = date(2026, 7, 1)
        self.assertEqual(
            extract_event_date("Investor Days on July 13, 2026, and July 14, 2026", run),
            date(2026, 7, 13),
        )


class PhraseVariantTests(unittest.TestCase):
    def test_plural_variant_added(self):
        self.assertEqual(
            EdgarClient._phrase_query_variants("investor day"),
            ["investor day", "investor days"],
        )
        self.assertEqual(
            EdgarClient._phrase_query_variants("teach-in"),
            ["teach-in", "teach-ins"],
        )

    def test_no_duplicate_when_already_plural(self):
        self.assertEqual(EdgarClient._phrase_query_variants("investor days"), ["investor days"])


class DetectorCoverageTests(unittest.TestCase):
    def test_future_announcement_survives_previously_announced(self):
        # The Resideo case: a clear forward-looking event that also says
        # "previously announced" must NOT be vetoed by the past cue.
        parsed = parse_sec_filing(
            _raw(
                "7.01",
                "<p>As previously announced, Example Inc. will host Investor Days "
                "in New York City on July 13, 2026, and July 14, 2026.</p>",
                filed="20260701",
            )
        )
        detections = detect_in_parsed_filing(_filing("2026-07-01"), parsed, date(2026, 7, 1))
        self.assertEqual(detections[0].status, "alert_candidate")
        self.assertEqual(detections[0].event_date, "2026-07-13")

    def test_same_day_investor_day_detected(self):
        parsed = parse_sec_filing(
            _raw(
                "7.01",
                "<p>Example Inc. HOSTS 2026 INVESTOR DAY. The Company will host its "
                "Investor Day on June 18, 2026 to unveil its fiscal 2030 outlook.</p>",
                filed="20260618",
            )
        )
        detections = detect_in_parsed_filing(_filing("2026-06-18"), parsed, date(2026, 6, 18))
        self.assertEqual(detections[0].status, "alert_candidate")
        self.assertEqual(detections[0].event_date, "2026-06-18")

    def test_dateline_far_from_phrase_is_not_an_event(self):
        # A recent date far from the phrase (an unrelated dateline / earnings
        # date) must not be attached to a passing capital-markets-day mention.
        parsed = parse_sec_filing(
            _raw(
                "8.01",
                "<p>On July 1, 2026, Example Inc. reported quarterly earnings of "
                "$2.00 per share and reaffirmed full-year guidance, consistent "
                "with the strategic themes from its capital markets day.</p>",
                filed="20260701",
            )
        )
        detections = detect_in_parsed_filing(_filing("2026-07-01"), parsed, date(2026, 7, 1))
        self.assertEqual(detections[0].status, "dismissed")

    def test_phrase_named_after_earlier_year_is_dismissed(self):
        # "2025 Capital Markets Day" is a past-event reference even if a recent
        # date sits next to it.
        parsed = parse_sec_filing(
            _raw(
                "8.01",
                "<p>The buyback of Euro 3.5 billion announced during the 2025 "
                "Capital Markets Day ran until June 26, 2026.</p>",
                filed="20260629",
            )
        )
        detections = detect_in_parsed_filing(_filing("2026-06-29"), parsed, date(2026, 6, 29))
        self.assertEqual(detections[0].status, "dismissed")

    def test_current_year_named_event_is_kept(self):
        # ...but "2026 Investor Day" in the current year is a real event.
        parsed = parse_sec_filing(
            _raw(
                "7.01",
                "<p>Example Inc. hosts its 2026 Investor Day on June 18, 2026 in "
                "Hilliard, Ohio.</p>",
                filed="20260618",
            )
        )
        detections = detect_in_parsed_filing(_filing("2026-06-18"), parsed, date(2026, 6, 18))
        self.assertEqual(detections[0].status, "alert_candidate")

    def test_genuine_backward_reference_still_dismissed(self):
        parsed = parse_sec_filing(
            _raw(
                "2.02",
                "<p>As outlined at the Company's April 9, 2026 Investor Day, management "
                "remains focused on executing its three-year plan.</p>",
                filed="20260624",
            )
        )
        detections = detect_in_parsed_filing(_filing("2026-06-24"), parsed, date(2026, 6, 24))
        self.assertEqual(detections[0].status, "dismissed")


if __name__ == "__main__":
    unittest.main()
