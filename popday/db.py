"""SQLite persistence for processed filings and detections."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .rules import default_rules


SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_filings (
    accession_number TEXT PRIMARY KEY,
    cik TEXT NOT NULL,
    company_name TEXT NOT NULL,
    form_type TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    filing_url TEXT NOT NULL,
    processed_timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number TEXT NOT NULL,
    company_name TEXT NOT NULL,
    cik TEXT NOT NULL,
    form_type TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    filing_url TEXT NOT NULL,
    event_type TEXT,
    event_date TEXT,
    matched_phrase TEXT,
    matched_location TEXT,
    snippet TEXT,
    status TEXT NOT NULL,
    dismissal_reason TEXT,
    alert_sent INTEGER NOT NULL DEFAULT 0,
    alert_sent_timestamp TEXT,
    ticker TEXT,
    previous_close_before_filing REAL,
    next_close_after_filing REAL,
    reaction_pct REAL,
    reaction_computed_timestamp TEXT,
    price_data_source TEXT,
    created_timestamp TEXT NOT NULL,
    UNIQUE(accession_number, event_type, event_date, matched_phrase, snippet)
);

CREATE TABLE IF NOT EXISTS rules (
    rule_type TEXT NOT NULL,
    phrase TEXT NOT NULL,
    description TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(rule_type, phrase)
);

CREATE TABLE IF NOT EXISTS alert_recipients (
    email TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 1,
    created_timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS known_announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_date TEXT NOT NULL,
    announcement_date TEXT,
    source_url TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_type TEXT NOT NULL,
    notes TEXT,
    alert_sent INTEGER NOT NULL DEFAULT 0,
    alert_sent_timestamp TEXT,
    created_timestamp TEXT NOT NULL,
    UNIQUE(company_name, event_type, event_date, source_url)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True) if Path(path).parent != Path(".") else None
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.init()

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.seed_rules()
        self.conn.commit()

    def _migrate(self) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(known_announcements)").fetchall()
        }
        if "alert_sent" not in columns:
            self.conn.execute(
                "ALTER TABLE known_announcements ADD COLUMN alert_sent INTEGER NOT NULL DEFAULT 0"
            )
        if "alert_sent_timestamp" not in columns:
            self.conn.execute(
                "ALTER TABLE known_announcements ADD COLUMN alert_sent_timestamp TEXT"
            )

    def close(self) -> None:
        self.conn.close()

    def seed_rules(self) -> None:
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO rules (rule_type, phrase, description, active)
            VALUES (:rule_type, :phrase, :description, :active)
            """,
            [asdict(rule) | {"active": int(rule.active)} for rule in default_rules()],
        )

    def seed_recipients(self, recipients: list[str]) -> None:
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO alert_recipients (email, active, created_timestamp)
            VALUES (?, 1, ?)
            """,
            [(recipient.lower(), utc_now()) for recipient in recipients],
        )
        self.conn.commit()

    def already_processed(self, accession_number: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM processed_filings WHERE accession_number = ?",
            (accession_number,),
        ).fetchone()
        return row is not None

    def mark_processed(self, filing: Any) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO processed_filings
            (accession_number, cik, company_name, form_type, filing_date, filing_url, processed_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filing.accession_number,
                filing.cik,
                filing.company_name,
                filing.form_type,
                filing.filing_date,
                filing.filing_url,
                utc_now(),
            ),
        )
        self.conn.commit()

    def detection_already_alerted(self, company_name: str, event_type: str, event_date: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM detections
            WHERE company_name = ? AND event_type = ? AND event_date = ? AND alert_sent = 1
            """,
            (company_name, event_type, event_date),
        ).fetchone()
        return row is not None

    def insert_detection(self, detection: Any) -> int:
        payload = detection.to_record()
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(["?"] * len(payload))
        cursor = self.conn.execute(
            f"INSERT OR IGNORE INTO detections ({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )
        self.conn.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        row = self.conn.execute(
            """
            SELECT id FROM detections
            WHERE accession_number = ? AND event_type IS ? AND event_date IS ?
              AND matched_phrase IS ? AND snippet IS ?
            ORDER BY id DESC LIMIT 1
            """,
            (
                payload["accession_number"],
                payload["event_type"],
                payload["event_date"],
                payload["matched_phrase"],
                payload["snippet"],
            ),
        ).fetchone()
        return int(row["id"]) if row else 0

    def mark_alert_sent(self, detection_ids: list[int]) -> None:
        if not detection_ids:
            return
        self.conn.executemany(
            """
            UPDATE detections
            SET alert_sent = 1, alert_sent_timestamp = ?
            WHERE id = ?
            """,
            [(utc_now(), detection_id) for detection_id in detection_ids],
        )
        self.conn.commit()

    def rules(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT rule_type, phrase, description, active FROM rules ORDER BY rule_type, phrase"
        ).fetchall()

    def active_phrases(self, rule_type: str) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT phrase FROM rules
            WHERE rule_type = ? AND active = 1
            ORDER BY phrase
            """,
            (rule_type,),
        ).fetchall()
        return [str(row["phrase"]) for row in rows]

    def add_rule(self, rule_type: str, phrase: str, description: str) -> None:
        self.conn.execute(
            """
            INSERT INTO rules (rule_type, phrase, description, active)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(rule_type, phrase) DO UPDATE SET
                description = excluded.description,
                active = 1
            """,
            (rule_type, phrase.strip().lower(), description.strip()),
        )
        self.conn.commit()

    def delete_rule(self, rule_type: str, phrase: str) -> None:
        self.conn.execute(
            "DELETE FROM rules WHERE rule_type = ? AND phrase = ?",
            (rule_type, phrase),
        )
        self.conn.commit()

    def recent_processed(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT accession_number, company_name, form_type, filing_date, processed_timestamp
            FROM processed_filings
            ORDER BY processed_timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def recent_candidates(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, created_timestamp, company_name, form_type, filing_date, event_type, event_date,
                   matched_phrase, matched_location, status, dismissal_reason, filing_url
            FROM detections
            ORDER BY created_timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def detection(self, detection_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT id, accession_number, company_name, cik, form_type, filing_date, filing_url,
                   event_type, event_date, matched_phrase, matched_location, snippet, status,
                   dismissal_reason, alert_sent, created_timestamp
            FROM detections
            WHERE id = ?
            """,
            (detection_id,),
        ).fetchone()

    def latest_sent_alert(self) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT company_name, event_type, event_date, filing_url, alert_sent_timestamp
            FROM detections
            WHERE alert_sent = 1
            ORDER BY alert_sent_timestamp DESC, created_timestamp DESC
            LIMIT 1
            """
        ).fetchone()

    def latest_sent_alert_batch(self) -> list[dict[str, Any]]:
        latest_detection_ts = self.conn.execute(
            """
            SELECT alert_sent_timestamp
            FROM detections
            WHERE alert_sent = 1 AND alert_sent_timestamp IS NOT NULL
            ORDER BY alert_sent_timestamp DESC, created_timestamp DESC
            LIMIT 1
            """
        ).fetchone()
        latest_known_ts = self.conn.execute(
            """
            SELECT alert_sent_timestamp
            FROM known_announcements
            WHERE alert_sent = 1 AND alert_sent_timestamp IS NOT NULL
            ORDER BY alert_sent_timestamp DESC, created_timestamp DESC
            LIMIT 1
            """
        ).fetchone()
        timestamps = [
            str(row["alert_sent_timestamp"])
            for row in (latest_detection_ts, latest_known_ts)
            if row and row["alert_sent_timestamp"]
        ]
        if not timestamps:
            return []
        latest_timestamp = max(timestamps)

        detection_rows = self.conn.execute(
            """
            SELECT id, company_name, event_type, event_date, filing_url AS source_url,
                   'SEC filing' AS source_label, snippet, alert_sent_timestamp
            FROM detections
            WHERE alert_sent = 1 AND alert_sent_timestamp = ?
              AND status = 'alert_candidate'
              AND event_type IS NOT NULL
              AND event_date IS NOT NULL
            ORDER BY id
            """,
            (latest_timestamp,),
        ).fetchall()
        known_rows = self.conn.execute(
            """
            SELECT id, company_name, event_type, event_date, source_url,
                   source_label, '' AS snippet, alert_sent_timestamp
            FROM known_announcements
            WHERE alert_sent = 1 AND alert_sent_timestamp = ?
            ORDER BY id
            """,
            (latest_timestamp,),
        ).fetchall()
        return [dict(row) for row in [*detection_rows, *known_rows]]

    def investor_day_announcements(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT company_name, event_type, event_date, form_type, filing_date, filing_url AS source_url,
                   accession_number, matched_phrase, matched_location, alert_sent,
                   alert_sent_timestamp, created_timestamp, 'EDGAR' AS source_type,
                   'SEC filing' AS source_label
            FROM detections
            WHERE status = 'alert_candidate'
              AND event_type IS NOT NULL
              AND event_date IS NOT NULL
            ORDER BY event_date DESC, created_timestamp DESC
            """
        ).fetchall()
        known_rows = self.conn.execute(
            """
            SELECT company_name, event_type, event_date, NULL AS form_type,
                   announcement_date AS filing_date, source_url, NULL AS accession_number,
                   NULL AS matched_phrase, NULL AS matched_location, alert_sent,
                   alert_sent_timestamp, created_timestamp, source_type, source_label
            FROM known_announcements
            ORDER BY event_date DESC, created_timestamp DESC
            """
        ).fetchall()
        deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in [*rows, *known_rows]:
            item = dict(row)
            key = (
                str(item["company_name"]).strip().lower(),
                str(item["event_type"]).strip().lower(),
                str(item["event_date"]).strip(),
            )
            existing = deduped.get(key)
            if not existing:
                deduped[key] = item
                continue
            existing["alert_sent"] = int(bool(existing["alert_sent"]) or bool(item["alert_sent"]))
            existing["alert_sent_timestamp"] = (
                existing["alert_sent_timestamp"] or item["alert_sent_timestamp"]
            )
            if existing["source_type"] != "EDGAR" and item["source_type"] == "EDGAR":
                deduped[key] = item | {
                    "alert_sent": existing["alert_sent"],
                    "alert_sent_timestamp": existing["alert_sent_timestamp"],
                }
            elif not existing["matched_phrase"] and item["matched_phrase"]:
                existing["matched_phrase"] = item["matched_phrase"]
        return sorted(
            deduped.values(),
            key=lambda row: (row["event_date"] or "", row["created_timestamp"] or ""),
            reverse=True,
        )

    def investor_day_announcement_count(self) -> int:
        return len(self.investor_day_announcements())

    def add_known_announcement(
        self,
        *,
        company_name: str,
        event_type: str,
        event_date: str,
        announcement_date: str,
        source_url: str,
        source_label: str,
        source_type: str,
        notes: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO known_announcements
            (company_name, event_type, event_date, announcement_date, source_url,
             source_label, source_type, notes, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_name, event_type, event_date, source_url) DO UPDATE SET
                announcement_date = excluded.announcement_date,
                source_label = excluded.source_label,
                source_type = excluded.source_type,
                notes = excluded.notes
            """,
            (
                company_name.strip(),
                event_type.strip(),
                event_date.strip(),
                announcement_date.strip(),
                source_url.strip(),
                source_label.strip(),
                source_type.strip(),
                notes.strip() if notes else None,
                utc_now(),
            ),
        )
        self.conn.commit()

    def unsent_known_announcements(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, company_name, event_type, event_date, announcement_date,
                   source_url, source_label, source_type
            FROM known_announcements
            WHERE alert_sent = 0
            ORDER BY announcement_date, company_name
            """
        ).fetchall()

    def mark_known_alert_sent(self, announcement_ids: list[int]) -> None:
        if not announcement_ids:
            return
        self.conn.executemany(
            """
            UPDATE known_announcements
            SET alert_sent = 1, alert_sent_timestamp = ?
            WHERE id = ?
            """,
            [(utc_now(), announcement_id) for announcement_id in announcement_ids],
        )
        self.conn.commit()

    def alert_recipients(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT email, active, created_timestamp
            FROM alert_recipients
            ORDER BY email
            """
        ).fetchall()

    def active_alert_recipients(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT email FROM alert_recipients
            WHERE active = 1
            ORDER BY email
            """
        ).fetchall()
        return [str(row["email"]) for row in rows]

    def add_alert_recipient(self, email: str) -> None:
        self.conn.execute(
            """
            INSERT INTO alert_recipients (email, active, created_timestamp)
            VALUES (?, 1, ?)
            ON CONFLICT(email) DO UPDATE SET active = 1
            """,
            (email.strip().lower(), utc_now()),
        )
        self.conn.commit()

    def unsubscribe_alert_recipient(self, email: str) -> None:
        self.conn.execute(
            "UPDATE alert_recipients SET active = 0 WHERE email = ?",
            (email.strip().lower(),),
        )
        self.conn.commit()

    def reactivate_alert_recipient(self, email: str) -> None:
        self.conn.execute(
            "UPDATE alert_recipients SET active = 1 WHERE email = ?",
            (email.strip().lower(),),
        )
        self.conn.commit()

    def delete_alert_recipient(self, email: str) -> None:
        self.conn.execute("DELETE FROM alert_recipients WHERE email = ?", (email,))
        self.conn.commit()
