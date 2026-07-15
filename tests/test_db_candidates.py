import tempfile
import unittest
from pathlib import Path

from popday.db import Database


class CandidateOrderingTests(unittest.TestCase):
    def test_candidates_default_to_newest_filing_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            try:
                db.conn.executemany(
                    """
                    INSERT INTO detections
                    (id, accession_number, company_name, cik, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            1,
                            "older-hype",
                            "Hype Co",
                            "0000000001",
                            "8-K",
                            "20260617",
                            "https://www.sec.gov/hype.txt",
                            "Investor Day",
                            "2026-09-15",
                            "investor day",
                            "press_release",
                            "snippet",
                            "alert_candidate",
                            None,
                            "2026-06-17T01:00:00+00:00",
                        ),
                        (
                            2,
                            "newer-dismissed",
                            "Dismissed Co",
                            "0000000002",
                            "8-K",
                            "20260619",
                            "https://www.sec.gov/dismissed.txt",
                            None,
                            None,
                            "",
                            "",
                            "",
                            "dismissed",
                            "no_event_date",
                            "2026-06-19T03:00:00+00:00",
                        ),
                    ],
                )
                db.upsert_hype_tracking(
                    candidate_id=1,
                    cik="0000000001",
                    announcement_date="2026-06-17",
                    event_date="2026-09-15",
                    qualifying_count=0,
                    hype_status="quiet",
                    hype_definition_version="v1-abstract-guess",
                    provisional=True,
                    last_checked="2026-06-19",
                    detected_json="[]",
                )

                rows = db.recent_candidates(limit=2)

                self.assertEqual(rows[0]["company_name"], "Dismissed Co")
                self.assertEqual(rows[0]["filing_date"], "20260619")
                self.assertEqual(rows[1]["company_name"], "Hype Co")
                self.assertEqual(rows[1]["hype_status"], "quiet")
            finally:
                db.close()

    def test_investor_day_announcements_include_evidence_link(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (id, accession_number, company_name, cik, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, evidence_url, evidence_label, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "evidence-alert",
                        "Evidence Co",
                        "0000000001",
                        "8-K",
                        "20260617",
                        "https://www.sec.gov/filing-index.htm",
                        "Investor Day",
                        "2026-09-15",
                        "investor day",
                        "press_release",
                        "Evidence Co will host an Investor Day.",
                        "alert_candidate",
                        None,
                        "https://www.sec.gov/exhibit-991.htm",
                        "Exhibit 99.1",
                        "2026-06-17T01:00:00+00:00",
                    ),
                )
                db.upsert_hype_tracking(
                    candidate_id=1,
                    cik="0000000001",
                    announcement_date="2026-06-17",
                    event_date="2026-09-15",
                    qualifying_count=4,
                    hype_status="hyped",
                    hype_definition_version="v1-abstract-guess",
                    provisional=True,
                    last_checked="2026-06-19",
                    detected_json="[]",
                )

                rows = db.investor_day_announcements()

                self.assertEqual(rows[0]["evidence_url"], "https://www.sec.gov/exhibit-991.htm")
                self.assertEqual(rows[0]["evidence_label"], "Exhibit 99.1")
                self.assertEqual(rows[0]["hype_count"], 4)
            finally:
                db.close()

    def test_investor_day_announcements_include_acceptance_datetime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (id, accession_number, company_name, cik, form_type, filing_date,
                     acceptance_datetime, filing_url, event_type, event_date, matched_phrase,
                     matched_location, snippet, status, dismissal_reason, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "acceptance-alert",
                        "Acceptance Co",
                        "0000000001",
                        "8-K",
                        "20260617",
                        "2026-06-17T16:32:05-04:00",
                        "https://www.sec.gov/filing-index.htm",
                        "Investor Day",
                        "2026-09-15",
                        "investor day",
                        "press_release",
                        "Acceptance Co will host an Investor Day.",
                        "alert_candidate",
                        None,
                        "2026-06-17T01:00:00+00:00",
                    ),
                )

                rows = db.investor_day_announcements()

                self.assertEqual(rows[0]["acceptance_datetime"], "2026-06-17T16:32:05-04:00")
            finally:
                db.close()

    def test_research_hype_events_include_existing_hype_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (id, accession_number, company_name, cik, ticker, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, evidence_url, evidence_label, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "research-alert",
                        "Research Co",
                        "0000000001",
                        "RSH",
                        "8-K",
                        "20260617",
                        "https://www.sec.gov/filing-index.htm",
                        "Investor Day",
                        "2026-09-15",
                        "investor day",
                        "press_release",
                        "Research Co will host an Investor Day.",
                        "alert_candidate",
                        None,
                        "https://www.sec.gov/exhibit-991.htm",
                        "Exhibit 99.1",
                        "2026-06-17T01:00:00+00:00",
                    ),
                )
                db.upsert_hype_tracking(
                    candidate_id=1,
                    cik="0000000001",
                    announcement_date="2026-06-17",
                    event_date="2026-09-15",
                    qualifying_count=2,
                    hype_status="hyped",
                    hype_definition_version="v1-abstract-guess",
                    provisional=True,
                    last_checked="2026-06-19",
                    detected_json='[{"filing_date":"2026-07-01","form":"8-K","item_codes":["7.01"]}]',
                )

                rows = db.research_hype_events()

                self.assertEqual(rows[0]["company_name"], "Research Co")
                self.assertEqual(rows[0]["ticker"], "RSH")
                self.assertEqual(rows[0]["investor_comms_count"], 2)
                self.assertIn("7.01", rows[0]["detected_json"])
            finally:
                db.close()

    def test_research_hype_events_falls_back_to_price_reaction_ticker(self):
        """The US detection path has never set detections.ticker directly
        (only the UK path does) - research_hype_events must fall back to the
        ticker price_reactions already resolved and cached for this company
        after every scan, rather than showing a real gap as missing data
        (found 2026-07-15: Ticker showed blank on every single US row)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (id, accession_number, company_name, cik, ticker, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, evidence_url, evidence_label, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        2,
                        "no-ticker-alert",
                        "No Ticker Co",
                        "0000000002",
                        "",  # never populated on the US path
                        "8-K",
                        "20260617",
                        "https://www.sec.gov/filing-index.htm",
                        "Investor Day",
                        "2026-09-20",
                        "investor day",
                        "press_release",
                        "No Ticker Co will host an Investor Day.",
                        "alert_candidate",
                        None,
                        "https://www.sec.gov/exhibit-991.htm",
                        "Exhibit 99.1",
                        "2026-06-17T01:00:00+00:00",
                    ),
                )
                db.conn.execute(
                    """
                    INSERT INTO price_reactions
                    (announcement_key, source_table, company_name, ticker, status)
                    VALUES ('us|no-ticker-co', 'detections', 'No Ticker Co', 'NTC', 'ok')
                    """
                )
                db.conn.commit()

                rows = db.research_hype_events()

                self.assertEqual(rows[0]["company_name"], "No Ticker Co")
                self.assertEqual(rows[0]["ticker"], "NTC")
            finally:
                db.close()

    def test_investor_day_announcements_surfaces_known_announcement_overrides(self):
        """known_announcements rows have no real CIK (press-release-only
        events that never touched EDGAR) - ticker_override/cik_override are
        the only way resolve_ticker can ever find them. Regression test for
        the Sandisk Corp row, which showed blank everywhere until this."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            try:
                db.conn.execute(
                    """
                    INSERT INTO known_announcements
                    (company_name, event_type, event_date, announcement_date, source_url,
                     source_label, source_type, notes, created_timestamp, cik_override,
                     ticker_override)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "Sandisk Corp",
                        "Investor Day",
                        "2026-08-13",
                        "2026-07-09",
                        "https://www.sandisk.com/company/newsroom",
                        "Sandisk newsroom",
                        "press_release",
                        "Press-release only, never filed with the SEC.",
                        "2026-07-14T07:31:21+00:00",
                        "0002023554",
                        None,
                    ),
                )
                db.conn.commit()

                rows = db.investor_day_announcements()

                self.assertEqual(rows[0]["company_name"], "Sandisk Corp")
                self.assertEqual(rows[0]["cik"], "0002023554")
            finally:
                db.close()

    def test_latest_sent_alert_batch_handles_hype_join(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(str(Path(tmpdir) / "popday.sqlite3"))
            try:
                db.conn.execute(
                    """
                    INSERT INTO detections
                    (id, accession_number, company_name, cik, form_type, filing_date, filing_url,
                     event_type, event_date, matched_phrase, matched_location, snippet, status,
                     dismissal_reason, alert_sent, alert_sent_timestamp, created_timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "sent-alert",
                        "Hype Co",
                        "0000000001",
                        "8-K",
                        "20260617",
                        "https://www.sec.gov/hype.txt",
                        "Investor Day",
                        "2026-09-15",
                        "investor day",
                        "press_release",
                        "Hype Co will host an Investor Day on September 15, 2026.",
                        "alert_candidate",
                        None,
                        1,
                        "2026-06-19T08:00:00+00:00",
                        "2026-06-17T01:00:00+00:00",
                    ),
                )
                db.upsert_hype_tracking(
                    candidate_id=1,
                    cik="0000000001",
                    announcement_date="2026-06-17",
                    event_date="2026-09-15",
                    qualifying_count=0,
                    hype_status="quiet",
                    hype_definition_version="v1-abstract-guess",
                    provisional=True,
                    last_checked="2026-06-19",
                    detected_json="[]",
                )

                rows = db.latest_sent_alert_batch()

                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["event_date"], "2026-09-15")
                self.assertEqual(rows[0]["hype_status"], "quiet")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
