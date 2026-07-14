"""UK announcement detection tests, including against the real Glencore fixture."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest import mock

from popday.detector import detect_in_uk_announcement
from popday.sources import Announcement
from popday.sources.investegate import InvestegateClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CANARY_HTML = (FIXTURES / "investegate_canary_glencore_9184165.html").read_text(encoding="utf-8")

INCLUDE = ["capital markets day", "investor day", "analyst day", "cmd"]
ROUTINE = ["earnings call", "annual meeting"]


def _announcement(headline: str, raw_text: str) -> Announcement:
    return Announcement(
        source="investegate",
        market="UK",
        dedup_key="9184165",
        company_name="Glencore",
        company_identifier="GLEN",
        headline=headline,
        wire_or_form="RNS",
        announced_at="2025-10-21T08:00:07+00:00",
        detail_url="https://www.investegate.co.uk/announcement/rns/glencore--glen/notice-of-capital-markets-day/9184165",
        raw_text=raw_text,
    )


class RealFixtureDetectionTests(unittest.TestCase):
    def test_glencore_canary_detects_event_and_date(self):
        client = InvestegateClient("PopDay/0.1 test@example.com", skip_robots_check=True)
        with mock.patch.object(client, "get_html", return_value=CANARY_HTML):
            text = client.fetch_detail_text("fake://detail")
        self.assertIn("Capital Markets Day", text)

        announcement = _announcement("Notice of Capital Markets Day", text)
        detections = detect_in_uk_announcement(
            announcement, date(2025, 10, 21), INCLUDE, ROUTINE
        )
        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection.status, "alert_candidate")
        self.assertEqual(detection.event_date, "2025-12-03")
        self.assertEqual(detection.event_type, "Capital Markets Day")
        self.assertEqual(detection.market, "UK")
        self.assertEqual(detection.ticker, "GLEN")
        self.assertEqual(detection.filing.accession_number, "9184165")
        record = detection.to_record()
        self.assertEqual(record["market"], "UK")
        self.assertEqual(record["ticker"], "GLEN")


class SyntheticDetectionTests(unittest.TestCase):
    def test_tbd_when_headline_matches_but_no_date(self):
        announcement = _announcement(
            "Notice of Capital Markets Day",
            "Glencore plc will host a Capital Markets Day for analysts and investors. Details to follow.",
        )
        detections = detect_in_uk_announcement(announcement, date(2025, 10, 21), INCLUDE, ROUTINE)
        self.assertEqual(detections[0].status, "alert_candidate_tbd")
        self.assertEqual(detections[0].matched_location, "headline")

    def test_prior_year_reference_is_not_a_new_event(self):
        announcement = _announcement(
            "Annual Report",
            "Slides from our 2024 Capital Markets Day on 3 December 2025 remain available.",
        )
        detections = detect_in_uk_announcement(announcement, date(2025, 10, 21), INCLUDE, ROUTINE)
        self.assertEqual(detections[0].status, "dismissed")

    def test_cmd_word_boundary_in_body(self):
        announcement = _announcement(
            "Trading Update",
            "The board recommends approval. We recommend shareholders read the circular.",
        )
        detections = detect_in_uk_announcement(announcement, date(2025, 10, 21), INCLUDE, ROUTINE)
        self.assertEqual(detections[0].status, "dismissed")
        self.assertEqual(detections[0].dismissal_reason, "no_qualifying_phrase_found")

    def test_no_phrase_dismissed(self):
        announcement = _announcement(
            "Transaction in Own Shares",
            "The company purchased 100,000 ordinary shares on 21 October 2025.",
        )
        detections = detect_in_uk_announcement(announcement, date(2025, 10, 21), INCLUDE, ROUTINE)
        self.assertEqual(detections[0].status, "dismissed")
        self.assertEqual(detections[0].dismissal_reason, "no_qualifying_phrase_found")

    def test_past_event_without_announcement_cue_dismissed(self):
        announcement = _announcement(
            "Results Presentation",
            "A replay of the Capital Markets Day held its session on 3 November 2025 is available.",
        )
        detections = detect_in_uk_announcement(announcement, date(2025, 11, 5), INCLUDE, ROUTINE)
        self.assertEqual(detections[0].status, "dismissed")


if __name__ == "__main__":
    unittest.main()
