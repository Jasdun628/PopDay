import tempfile
import unittest
from pathlib import Path

from popday.db import Database


class CandidateOrderingTests(unittest.TestCase):
    def test_hype_tracked_candidates_are_visible_first(self):
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

                self.assertEqual(rows[0]["company_name"], "Hype Co")
                self.assertEqual(rows[0]["hype_status"], "quiet")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
