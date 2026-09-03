"""Tests for the weekly digest feature: the alert_recipients frequency
column, the digest DB queries (active_digest_recipients, alerts_sent_since,
record_digest_sent), send_digest_email's subject/validation, and cli.py's
send_digest() dispatcher end to end against a real temp database.
"""

from __future__ import annotations

import argparse
import smtplib
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest import mock

from popday import cli
from popday.config import Config
from popday.db import Database
from popday.emailer import send_digest_email


def _make_config(**overrides) -> Config:
    fields = dict(
        db_path=":memory:",
        sec_user_agent="PopDay/0.1 test@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user@example.com",
        smtp_password="secret",
        email_from="alerts@example.com",
        email_to="ops@example.com",
        request_delay_seconds=0.1,
        hype_threshold=1,
        hype_definition_version="v1",
        hype_provisional=False,
        unsubscribe_base_url=None,
        unsubscribe_secret=None,
        company_websites={},
        uk_user_agent="PopDay/0.1 test@example.com",
        uk_request_delay_seconds=0.1,
        uk_include_phrases=[],
        uk_exclude_phrases=[],
        uk_routine_headlines=[],
        status_json_path="",
    )
    fields.update(overrides)
    return Config(**fields)


@dataclass(frozen=True)
class _Alert:
    company_name: str = "Example Inc."
    event_label: str = "Investor Day"
    event_date: date = date(2026, 9, 15)
    filing_url: str = ""
    source_label: str = "SEC filing"
    form_type: str = "8-K"
    event_url: str = ""
    evidence_url: str = ""
    evidence_label: str = ""
    snippet: str = "Example Inc. will host an Investor Day."
    hype_status: str = "quiet"
    hype_count: int = 0
    hype_provisional: bool = True
    context_text: str = ""
    company_url_missing: bool = False


class AlertRecipientFrequencyDbTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmpdir.name) / "popday.sqlite3"))

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_new_recipient_defaults_to_immediate(self):
        self.db.add_alert_recipient("new@example.com")
        rows = self.db.alert_recipients()
        self.assertEqual(rows[0]["frequency"], "immediate")
        self.assertIsNone(rows[0]["last_digest_sent_utc"])

    def test_active_alert_recipients_excludes_weekly(self):
        self.db.add_alert_recipient("immediate@example.com")
        self.db.add_alert_recipient("weekly@example.com")
        self.db.set_alert_recipient_frequency("weekly@example.com", "weekly")
        self.assertEqual(self.db.active_alert_recipients(), ["immediate@example.com"])

    def test_active_digest_recipients_only_weekly_and_active(self):
        self.db.add_alert_recipient("weekly@example.com")
        self.db.set_alert_recipient_frequency("weekly@example.com", "weekly")
        self.db.add_alert_recipient("unsubscribed@example.com")
        self.db.set_alert_recipient_frequency("unsubscribed@example.com", "weekly")
        self.db.unsubscribe_alert_recipient("unsubscribed@example.com")
        self.db.add_alert_recipient("immediate@example.com")

        emails = [row["email"] for row in self.db.active_digest_recipients()]
        self.assertEqual(emails, ["weekly@example.com"])

    def test_record_digest_sent_updates_timestamp(self):
        self.db.add_alert_recipient("weekly@example.com")
        self.db.set_alert_recipient_frequency("weekly@example.com", "weekly")
        self.db.record_digest_sent("weekly@example.com", "2026-09-01T00:00:00+00:00")
        row = self.db.active_digest_recipients()[0]
        self.assertEqual(row["last_digest_sent_utc"], "2026-09-01T00:00:00+00:00")


class AlertsSentSinceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmpdir.name) / "popday.sqlite3"))

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def _insert_detection(self, *, accession, alert_sent_timestamp, status="alert_candidate"):
        self.db.conn.execute(
            """
            INSERT INTO detections (
                accession_number, cik, company_name, form_type, filing_date,
                event_type, event_date, filing_url, market, status, alert_sent,
                alert_sent_timestamp, created_timestamp
            ) VALUES (?, '0000000001', 'Example Inc.', '8-K', '2026-09-01',
                      'investor_day', '2026-09-15', 'https://example.com/filing',
                      'US', ?, 1, ?, '2026-09-01T00:00:00+00:00')
            """,
            (accession, status, alert_sent_timestamp),
        )
        self.db.conn.commit()

    def _insert_known_announcement(self, *, name, alert_sent_timestamp):
        self.db.conn.execute(
            """
            INSERT INTO known_announcements (
                company_name, event_type, event_date, source_url, source_label,
                market, alert_sent, alert_sent_timestamp, created_timestamp, source_type
            ) VALUES (?, 'investor_day', '2026-09-20', 'https://example.com', 'Manual',
                      'US', 1, ?, '2026-09-01T00:00:00+00:00', 'Manual')
            """,
            (name, alert_sent_timestamp),
        )
        self.db.conn.commit()

    def test_only_returns_rows_sent_after_the_cutoff(self):
        self._insert_detection(accession="0000000000-26-000001", alert_sent_timestamp="2026-08-01T00:00:00+00:00")
        self._insert_detection(accession="0000000000-26-000002", alert_sent_timestamp="2026-09-02T00:00:00+00:00")
        rows = self.db.alerts_sent_since("2026-09-01T00:00:00+00:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["alert_sent_timestamp"], "2026-09-02T00:00:00+00:00")

    def test_excludes_dismissed_detections(self):
        self._insert_detection(
            accession="0000000000-26-000003",
            alert_sent_timestamp="2026-09-02T00:00:00+00:00",
            status="dismissed",
        )
        rows = self.db.alerts_sent_since("2026-09-01T00:00:00+00:00")
        self.assertEqual(rows, [])

    def test_includes_both_detections_and_known_announcements(self):
        self._insert_detection(accession="0000000000-26-000004", alert_sent_timestamp="2026-09-02T00:00:00+00:00")
        self._insert_known_announcement(name="Known Co", alert_sent_timestamp="2026-09-03T00:00:00+00:00")
        rows = self.db.alerts_sent_since("2026-09-01T00:00:00+00:00")
        companies = sorted(row["company_name"] for row in rows)
        self.assertEqual(companies, ["Example Inc.", "Known Co"])


class SendDigestEmailTests(unittest.TestCase):
    def test_subject_is_singular_for_one_alert(self):
        config = _make_config()
        with mock.patch.object(smtplib, "SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            send_digest_email(config, [_Alert()], recipient="reader@example.com")
        sent_message = smtp.send_message.call_args[0][0]
        self.assertEqual(sent_message["Subject"], "PopDay Weekly Digest: 1 new investor-event announcement")
        self.assertEqual(sent_message["To"], "reader@example.com")

    def test_subject_is_plural_for_multiple_alerts(self):
        config = _make_config()
        with mock.patch.object(smtplib, "SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            send_digest_email(config, [_Alert(), _Alert(company_name="Other Co")], recipient="reader@example.com")
        sent_message = smtp.send_message.call_args[0][0]
        self.assertEqual(sent_message["Subject"], "PopDay Weekly Digest: 2 new investor-event announcements")

    def test_raises_when_email_not_configured(self):
        config = _make_config(smtp_password=None)
        with self.assertRaises(RuntimeError):
            send_digest_email(config, [_Alert()], recipient="reader@example.com")

    def test_raises_when_no_alerts(self):
        config = _make_config()
        with self.assertRaises(RuntimeError):
            send_digest_email(config, [], recipient="reader@example.com")


class SendDigestCliTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "popday.sqlite3")
        db = Database(self.db_path)
        db.add_alert_recipient("weekly@example.com")
        db.set_alert_recipient_frequency("weekly@example.com", "weekly")
        db.add_alert_recipient("no-news@example.com")
        db.set_alert_recipient_frequency("no-news@example.com", "weekly")
        db.record_digest_sent("no-news@example.com", "2099-09-03T00:00:00+00:00")
        db.conn.execute(
            """
            INSERT INTO detections (
                accession_number, cik, company_name, form_type, filing_date,
                event_type, event_date, filing_url, market, status, alert_sent,
                alert_sent_timestamp, created_timestamp
            ) VALUES ('0000000000-26-000001', '0000000001', 'Example Inc.', '8-K',
                      '2026-09-01', 'investor_day', '2026-09-15',
                      'https://example.com/filing', 'US', 'alert_candidate', 1,
                      '2099-09-02T00:00:00+00:00', '2026-09-01T00:00:00+00:00')
            """
        )
        db.conn.commit()
        db.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_dry_run_sends_nothing_and_leaves_state_untouched(self):
        config = _make_config(db_path=self.db_path)
        args = argparse.Namespace(config=None, dry_run=True)
        with mock.patch.object(cli, "load_config", return_value=config), mock.patch.object(
            cli, "send_digest_email"
        ) as send_mock:
            exit_code = cli.send_digest(args)
        self.assertEqual(exit_code, 0)
        send_mock.assert_not_called()

    def test_real_send_only_emails_recipient_with_new_alerts(self):
        config = _make_config(db_path=self.db_path)
        args = argparse.Namespace(config=None, dry_run=False)
        with mock.patch.object(cli, "load_config", return_value=config), mock.patch.object(
            cli, "send_digest_email"
        ) as send_mock:
            exit_code = cli.send_digest(args)
        self.assertEqual(exit_code, 0)
        send_mock.assert_called_once()
        (sent_config, sent_alerts), kwargs = send_mock.call_args
        self.assertEqual(kwargs["recipient"], "weekly@example.com")
        self.assertEqual(len(sent_alerts), 1)

        db = Database(self.db_path)
        try:
            rows = {row["email"]: row["last_digest_sent_utc"] for row in db.active_digest_recipients()}
        finally:
            db.close()
        self.assertNotEqual(rows["weekly@example.com"], "2099-09-03T00:00:00+00:00")
        self.assertEqual(rows["no-news@example.com"], "2099-09-03T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
