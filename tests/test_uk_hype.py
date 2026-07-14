"""UK hype pass and cross-market reclassify tests (Phase 1b)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date

from popday.config import load_config
from popday.db import Database
from popday.hype import reclassify_hype_tracking, update_uk_hype_from_index
from popday.sources import Announcement


def _index_row(dedup_key: str, ticker: str, headline: str) -> Announcement:
    return Announcement(
        source="investegate",
        market="UK",
        dedup_key=dedup_key,
        company_name="Glencore",
        company_identifier=ticker,
        headline=headline,
        wire_or_form="RNS",
        announced_at="2026-07-14T08:00:00+00:00",
        detail_url=f"https://www.investegate.co.uk/announcement/rns/x/y/{dedup_key}",
        raw_text="",
    )


class UkHypeTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        self.config = load_config("/nonexistent-config.json")  # defaults only
        self.db.conn.execute(
            """
            INSERT INTO detections
            (id, accession_number, company_name, cik, form_type, filing_date, filing_url,
             event_type, event_date, status, created_timestamp, market, ticker)
            VALUES (501, '9100000', 'Glencore', '', 'RNS', '2026-07-01',
                    'https://investegate.test/evt', 'Capital Markets Day', '2026-07-20',
                    'alert_candidate', '2026-07-01T09:00:00+00:00', 'UK', 'GLEN')
            """
        )
        self.db.conn.commit()

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_counts_non_routine_announcements_only(self):
        index = [
            _index_row("9200001", "GLEN", "Production Report"),
            _index_row("9200002", "GLEN", "Transaction in Own Shares"),  # routine
            _index_row("9200003", "GLEN", "Total Voting Rights"),  # routine
            _index_row("9200004", "OTHER", "Production Report"),  # different company
            _index_row("9100000", "GLEN", "Capital Markets Day"),  # the event itself
        ]
        updated = update_uk_hype_from_index(self.config, self.db, index, date(2026, 7, 14))
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["qualifying_count"], 1)
        row = self.db.hype_tracking_for_candidate(501)
        payload = json.loads(row["detected_json"])
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["accession_number"], "9200001")
        self.assertEqual(row["market"], "UK")

    def test_appends_across_days_with_dedup(self):
        index_day_one = [_index_row("9200001", "GLEN", "Production Report")]
        update_uk_hype_from_index(self.config, self.db, index_day_one, date(2026, 7, 14))
        index_day_two = [
            _index_row("9200001", "GLEN", "Production Report"),  # replayed (backfill)
            _index_row("9200009", "GLEN", "Contract Award"),
        ]
        updated = update_uk_hype_from_index(self.config, self.db, index_day_two, date(2026, 7, 15))
        self.assertEqual(updated[0]["qualifying_count"], 2)

    def test_outside_window_not_counted(self):
        index = [_index_row("9200001", "GLEN", "Production Report")]
        # After the event date: nothing counted, no crash.
        updated = update_uk_hype_from_index(self.config, self.db, index, date(2026, 7, 25))
        self.assertEqual(updated, [])

    def test_reclassify_preserves_market_and_filters(self):
        index = [_index_row("9200001", "GLEN", "Production Report")]
        update_uk_hype_from_index(self.config, self.db, index, date(2026, 7, 14))

        rows = reclassify_hype_tracking(self.config, self.db, as_of=date(2026, 7, 16))
        self.assertEqual(len(rows), 1)
        row = self.db.hype_tracking_for_candidate(501)
        self.assertEqual(row["market"], "UK", "reclassify must not reset market to US")

        self.assertEqual(
            reclassify_hype_tracking(
                self.config, self.db, as_of=date(2026, 7, 16), market="US", dry_run=True
            ),
            [],
        )
        self.assertEqual(
            len(
                reclassify_hype_tracking(
                    self.config, self.db, as_of=date(2026, 7, 16), market="UK", dry_run=True
                )
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
