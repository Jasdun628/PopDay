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
    acceptance_datetime TEXT,
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
    acceptance_datetime TEXT,
    filing_url TEXT NOT NULL,
    event_type TEXT,
    event_date TEXT,
    matched_phrase TEXT,
    matched_location TEXT,
    snippet TEXT,
    items_json TEXT NOT NULL DEFAULT '[]',
    event_url TEXT,
    evidence_url TEXT,
    evidence_label TEXT,
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

CREATE TABLE IF NOT EXISTS hype_tracking (
    candidate_id INTEGER PRIMARY KEY,
    cik TEXT NOT NULL,
    announcement_date TEXT NOT NULL,
    event_date TEXT NOT NULL,
    qualifying_count INTEGER DEFAULT 0,
    hype_status TEXT DEFAULT 'pending',
    hype_definition_version TEXT DEFAULT 'v1-abstract-guess',
    provisional INTEGER NOT NULL DEFAULT 1,
    last_checked TEXT,
    detected_json TEXT
);

CREATE TABLE IF NOT EXISTS price_reactions (
    announcement_key TEXT PRIMARY KEY,
    source_table TEXT NOT NULL,
    source_id INTEGER,
    company_name TEXT NOT NULL,
    cik TEXT,
    ticker TEXT,
    event_date TEXT,
    filing_date TEXT,
    acceptance_datetime TEXT,
    reaction_date TEXT,
    previous_close_date TEXT,
    previous_close REAL,
    reaction_open REAL,
    reaction_high REAL,
    reaction_low REAL,
    reaction_close REAL,
    announcement_move_pct REAL,
    intraday_range_pct REAL,
    latest_close_date TEXT,
    latest_close REAL,
    interval_return_pct REAL,
    interval_daily_volatility_pct REAL,
    price_data_source TEXT,
    price_data_timestamp TEXT,
    status TEXT NOT NULL,
    notes TEXT
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
        detection_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(detections)").fetchall()
        }
        processed_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(processed_filings)").fetchall()
        }
        if "acceptance_datetime" not in processed_columns:
            self.conn.execute("ALTER TABLE processed_filings ADD COLUMN acceptance_datetime TEXT")
        if "acceptance_datetime" not in detection_columns:
            self.conn.execute("ALTER TABLE detections ADD COLUMN acceptance_datetime TEXT")
        if "items_json" not in detection_columns:
            self.conn.execute(
                "ALTER TABLE detections ADD COLUMN items_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "event_url" not in detection_columns:
            self.conn.execute("ALTER TABLE detections ADD COLUMN event_url TEXT")
        if "evidence_url" not in detection_columns:
            self.conn.execute("ALTER TABLE detections ADD COLUMN evidence_url TEXT")
        if "evidence_label" not in detection_columns:
            self.conn.execute("ALTER TABLE detections ADD COLUMN evidence_label TEXT")
        hype_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(hype_tracking)").fetchall()
        }
        if "hype_definition_version" not in hype_columns:
            self.conn.execute(
                "ALTER TABLE hype_tracking ADD COLUMN hype_definition_version TEXT DEFAULT 'v1-abstract-guess'"
            )
        if "provisional" not in hype_columns:
            self.conn.execute(
                "ALTER TABLE hype_tracking ADD COLUMN provisional INTEGER NOT NULL DEFAULT 1"
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
            (accession_number, cik, company_name, form_type, filing_date, acceptance_datetime,
             filing_url, processed_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filing.accession_number,
                filing.cik,
                filing.company_name,
                filing.form_type,
                filing.filing_date,
                getattr(filing, "acceptance_datetime", "") or None,
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

    def detections_missing_acceptance_datetime(
        self,
        limit: int | None = None,
        *,
        qualifying_only: bool = False,
    ) -> list[sqlite3.Row]:
        query = """
            SELECT id, accession_number, filing_url
            FROM detections
            WHERE (acceptance_datetime IS NULL OR acceptance_datetime = '')
              AND filing_url IS NOT NULL
              AND filing_url != ''
        """
        if qualifying_only:
            query += " AND status = 'alert_candidate'"
        query += " ORDER BY filing_date DESC, created_timestamp DESC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        return self.conn.execute(query, params).fetchall()

    def set_detection_acceptance_datetime(
        self,
        detection_id: int,
        acceptance_datetime: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE detections
            SET acceptance_datetime = ?
            WHERE id = ?
            """,
            (acceptance_datetime, detection_id),
        )
        self.conn.commit()

    def sync_processed_acceptance_datetime(
        self,
        accession_number: str,
        acceptance_datetime: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE processed_filings
            SET acceptance_datetime = ?
            WHERE accession_number = ?
            """,
            (acceptance_datetime, accession_number),
        )
        self.conn.commit()

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
            SELECT accession_number, company_name, form_type, filing_date, acceptance_datetime,
                   processed_timestamp
            FROM processed_filings
            ORDER BY processed_timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def recent_candidates(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT d.id, d.created_timestamp, d.company_name, d.form_type, d.filing_date, d.event_type, d.event_date,
                   d.matched_phrase, d.matched_location, d.status, d.dismissal_reason, d.filing_url,
                   d.acceptance_datetime, d.event_url, d.evidence_url, d.evidence_label,
                   h.qualifying_count, h.hype_status, h.hype_definition_version, h.provisional
            FROM detections d
            LEFT JOIN hype_tracking h ON h.candidate_id = d.id
            ORDER BY d.filing_date DESC, d.created_timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def detection(self, detection_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT id, accession_number, company_name, cik, form_type, filing_date, filing_url,
                   acceptance_datetime, event_type, event_date, matched_phrase, matched_location,
                   snippet, items_json, status, dismissal_reason, event_url, evidence_url,
                   evidence_label, alert_sent, created_timestamp
            FROM detections
            WHERE id = ?
            """,
            (detection_id,),
        ).fetchone()

    def latest_sent_alert(self) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT company_name, event_type, event_date, filing_url, acceptance_datetime,
                   alert_sent_timestamp
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
            SELECT d.id, d.company_name, d.event_type, d.event_date, d.form_type, d.filing_url AS source_url,
                   'SEC filing' AS source_label, snippet, d.event_url,
                   d.evidence_url, d.evidence_label, alert_sent_timestamp,
                   h.qualifying_count, h.hype_status, h.provisional
            FROM detections d
            LEFT JOIN hype_tracking h ON h.candidate_id = d.id
            WHERE d.alert_sent = 1 AND d.alert_sent_timestamp = ?
              AND d.status = 'alert_candidate'
              AND d.event_type IS NOT NULL
              AND d.event_date IS NOT NULL
            ORDER BY id
            """,
            (latest_timestamp,),
        ).fetchall()
        known_rows = self.conn.execute(
            """
            SELECT id, company_name, event_type, event_date, '' AS form_type, source_url,
                   source_label, '' AS snippet, '' AS event_url,
                   source_url AS evidence_url, source_label AS evidence_label, alert_sent_timestamp,
                   NULL AS qualifying_count, NULL AS hype_status, NULL AS provisional
            FROM known_announcements
            WHERE alert_sent = 1 AND alert_sent_timestamp = ?
            ORDER BY id
            """,
            (latest_timestamp,),
        ).fetchall()
        return [dict(row) for row in [*detection_rows, *known_rows]]

    def hype_watch_candidates(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, accession_number, company_name, cik, filing_date, acceptance_datetime,
                   event_date, event_type, filing_url
            FROM detections
            WHERE status = 'alert_candidate'
              AND filing_date IS NOT NULL
              AND event_date IS NOT NULL
            ORDER BY event_date, created_timestamp
            """
        ).fetchall()

    def upsert_hype_tracking(
        self,
        *,
        candidate_id: int,
        cik: str,
        announcement_date: str,
        event_date: str,
        qualifying_count: int,
        hype_status: str,
        hype_definition_version: str,
        provisional: bool,
        last_checked: str,
        detected_json: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO hype_tracking
            (candidate_id, cik, announcement_date, event_date, qualifying_count, hype_status,
             hype_definition_version, provisional, last_checked, detected_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                cik = excluded.cik,
                announcement_date = excluded.announcement_date,
                event_date = excluded.event_date,
                qualifying_count = excluded.qualifying_count,
                hype_status = excluded.hype_status,
                hype_definition_version = excluded.hype_definition_version,
                provisional = excluded.provisional,
                last_checked = excluded.last_checked,
                detected_json = excluded.detected_json
            """,
            (
                candidate_id,
                cik,
                announcement_date,
                event_date,
                qualifying_count,
                hype_status,
                hype_definition_version,
                int(provisional),
                last_checked,
                detected_json,
            ),
        )
        self.conn.commit()

    def hype_tracking_for_candidate(self, candidate_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT candidate_id, cik, announcement_date, event_date, qualifying_count,
                   hype_status, hype_definition_version, provisional, last_checked, detected_json
            FROM hype_tracking
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

    def all_hype_tracking_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT candidate_id, cik, announcement_date, event_date, qualifying_count,
                   hype_status, hype_definition_version, provisional, last_checked, detected_json
            FROM hype_tracking
            ORDER BY event_date, candidate_id
            """
        ).fetchall()

    def investor_day_announcements(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT d.id AS source_id, 'detections' AS source_table,
                   d.company_name, d.cik, d.ticker, d.event_type, d.event_date,
                   d.form_type, d.filing_date,
                   d.acceptance_datetime, d.filing_url AS source_url, d.evidence_url, d.evidence_label,
                   d.accession_number, d.matched_phrase, d.matched_location, d.alert_sent,
                   d.alert_sent_timestamp, d.created_timestamp, 'EDGAR' AS source_type,
                   'SEC filing' AS source_label, h.qualifying_count AS hype_count
            FROM detections d
            LEFT JOIN hype_tracking h ON h.candidate_id = d.id
            WHERE d.event_type IS NOT NULL
              AND (
                (d.status = 'alert_candidate' AND d.event_date IS NOT NULL)
                OR d.status = 'alert_candidate_tbd'
              )
            ORDER BY d.event_date IS NULL, d.event_date DESC, d.created_timestamp DESC
            """
        ).fetchall()
        known_rows = self.conn.execute(
            """
            SELECT id AS source_id, 'known_announcements' AS source_table,
                   company_name, NULL AS cik, NULL AS ticker, event_type, event_date, NULL AS form_type,
                   announcement_date AS filing_date, NULL AS acceptance_datetime,
                   source_url, NULL AS accession_number,
                   source_url AS evidence_url, source_label AS evidence_label,
                   NULL AS matched_phrase, NULL AS matched_location, alert_sent,
                   alert_sent_timestamp, created_timestamp, source_type, source_label,
                   NULL AS hype_count
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

    def upsert_price_reaction(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO price_reactions
            (announcement_key, source_table, source_id, company_name, cik, ticker,
             event_date, filing_date, acceptance_datetime, reaction_date, previous_close_date,
             previous_close, reaction_open, reaction_high, reaction_low, reaction_close,
             announcement_move_pct, intraday_range_pct, latest_close_date, latest_close,
             interval_return_pct, interval_daily_volatility_pct, price_data_source,
             price_data_timestamp, status, notes)
            VALUES
            (:announcement_key, :source_table, :source_id, :company_name, :cik, :ticker,
             :event_date, :filing_date, :acceptance_datetime, :reaction_date, :previous_close_date,
             :previous_close, :reaction_open, :reaction_high, :reaction_low, :reaction_close,
             :announcement_move_pct, :intraday_range_pct, :latest_close_date, :latest_close,
             :interval_return_pct, :interval_daily_volatility_pct, :price_data_source,
             :price_data_timestamp, :status, :notes)
            ON CONFLICT(announcement_key) DO UPDATE SET
                source_table = excluded.source_table,
                source_id = excluded.source_id,
                company_name = excluded.company_name,
                cik = excluded.cik,
                ticker = excluded.ticker,
                event_date = excluded.event_date,
                filing_date = excluded.filing_date,
                acceptance_datetime = excluded.acceptance_datetime,
                reaction_date = excluded.reaction_date,
                previous_close_date = excluded.previous_close_date,
                previous_close = excluded.previous_close,
                reaction_open = excluded.reaction_open,
                reaction_high = excluded.reaction_high,
                reaction_low = excluded.reaction_low,
                reaction_close = excluded.reaction_close,
                announcement_move_pct = excluded.announcement_move_pct,
                intraday_range_pct = excluded.intraday_range_pct,
                latest_close_date = excluded.latest_close_date,
                latest_close = excluded.latest_close,
                interval_return_pct = excluded.interval_return_pct,
                interval_daily_volatility_pct = excluded.interval_daily_volatility_pct,
                price_data_source = excluded.price_data_source,
                price_data_timestamp = excluded.price_data_timestamp,
                status = excluded.status,
                notes = excluded.notes
            """,
            row,
        )
        self.conn.commit()

    def price_reaction_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT announcement_key, source_table, source_id, company_name, cik, ticker,
                   event_date, filing_date, acceptance_datetime, reaction_date,
                   previous_close_date, previous_close, reaction_open, reaction_high,
                   reaction_low, reaction_close, announcement_move_pct, intraday_range_pct,
                   latest_close_date, latest_close, interval_return_pct,
                   interval_daily_volatility_pct, price_data_source, price_data_timestamp,
                   status, notes
            FROM price_reactions
            ORDER BY COALESCE(filing_date, '') DESC, company_name
            """
        ).fetchall()

    def investor_day_announcement_count(self) -> int:
        return len(self.investor_day_announcements())

    def research_hype_events(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT d.id AS candidate_id, d.company_name, d.ticker, d.cik, d.event_type,
                   d.event_date, COALESCE(h.announcement_date, d.filing_date) AS announcement_date,
                   d.acceptance_datetime, d.filing_url AS source_url, d.evidence_url, d.evidence_label,
                   d.created_timestamp, 'EDGAR' AS source_type,
                   h.qualifying_count AS investor_comms_count,
                   h.detected_json, h.last_checked, h.hype_definition_version, h.provisional
            FROM detections d
            LEFT JOIN hype_tracking h ON h.candidate_id = d.id
            WHERE d.status = 'alert_candidate'
              AND d.event_type IS NOT NULL
              AND d.event_date IS NOT NULL
            ORDER BY d.event_date DESC, d.created_timestamp DESC
            """
        ).fetchall()
        known_rows = self.conn.execute(
            """
            SELECT NULL AS candidate_id, company_name, NULL AS ticker, NULL AS cik, event_type,
                   event_date, announcement_date, NULL AS acceptance_datetime, source_url,
                   source_url AS evidence_url, source_label AS evidence_label,
                   created_timestamp, source_type,
                   NULL AS investor_comms_count, NULL AS detected_json, NULL AS last_checked,
                   NULL AS hype_definition_version, NULL AS provisional
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
            existing_count = existing.get("investor_comms_count")
            item_count = item.get("investor_comms_count")
            if item_count is not None and (
                existing_count is None or int(item_count) > int(existing_count)
            ):
                deduped[key] = item
            elif existing["source_type"] != "EDGAR" and item["source_type"] == "EDGAR":
                deduped[key] = item
        return list(deduped.values())

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
