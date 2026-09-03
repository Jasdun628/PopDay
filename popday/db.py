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
    processed_timestamp TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'US'
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
    market TEXT NOT NULL DEFAULT 'US',
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
    market TEXT NOT NULL DEFAULT 'US',
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
    detected_json TEXT,
    market TEXT NOT NULL DEFAULT 'US'
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
    event_day_close_date TEXT,
    event_day_close REAL,
    event_day_move_pct REAL,
    price_data_source TEXT,
    price_data_timestamp TEXT,
    status TEXT NOT NULL,
    notes TEXT,
    market TEXT NOT NULL DEFAULT 'US'
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    started_utc TEXT NOT NULL,
    finished_utc TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    discovery_source TEXT,
    filings_seen INTEGER NOT NULL DEFAULT 0,
    filings_parsed INTEGER NOT NULL DEFAULT 0,
    alerts_sent INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    efts_total_hits INTEGER NOT NULL DEFAULT 0,
    discovery_control TEXT NOT NULL DEFAULT '',
    run_kind TEXT NOT NULL DEFAULT 'scheduled',
    source TEXT NOT NULL DEFAULT 'edgar'
);

CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL NOT NULL,
    currency TEXT NOT NULL,
    market TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'yfinance',
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS ticker_mappings (
    market TEXT NOT NULL,
    local_symbol TEXT NOT NULL,
    yahoo_symbol TEXT NOT NULL,
    manual_override TEXT,
    notes TEXT,
    PRIMARY KEY (market, local_symbol)
);

CREATE TABLE IF NOT EXISTS ops_alerts (
    alert_key TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    opened_utc TEXT,
    last_sent_utc TEXT,
    detail TEXT
);

-- One row per CIK (the stable EDGAR identity; company_name casing varies
-- across filings for the same CIK - see HANDOFF.md). edgar_website is
-- EDGAR's self-reported website field, captured once per company and never
-- overwriting a curated (DEFAULT_COMPANY_WEBSITES / config.json) link. NULL
-- means "checked, EDGAR had nothing usable" - distinct from "never checked"
-- (no row at all), which is what gates a repeat fetch.
CREATE TABLE IF NOT EXISTS companies (
    cik TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'US',
    edgar_website TEXT,
    edgar_website_checked_utc TEXT NOT NULL
);

-- Keyed by company_key (see popday/company_websites.py's normalisation),
-- not CIK - UK companies have no CIK and still need a cache slot. The
-- lowest-priority fill layer under curated and EDGAR (resolve_company_website()
-- in popday/company_websites.py); NULL resolved_website means "attempted, no
-- confident domain match" - distinct from "never attempted" (no row), which
-- is what gates a repeat attempt so we don't re-guess the same company on
-- every scan.
CREATE TABLE IF NOT EXISTS resolved_company_websites (
    company_key TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    cik TEXT,
    resolved_website TEXT,
    resolution_method TEXT NOT NULL DEFAULT 'heuristic_domain_guess',
    resolved_checked_utc TEXT NOT NULL
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
        # known_announcements rows have no CIK by definition (they're
        # press-release-only events that never touched EDGAR), so the normal
        # CIK->ticker lookup can never resolve them. These let a manually-
        # confirmed ticker or CIK be attached per row - same pattern as the
        # UK ticker_mappings table's manual-override column.
        if "ticker_override" not in columns:
            self.conn.execute("ALTER TABLE known_announcements ADD COLUMN ticker_override TEXT")
        if "cik_override" not in columns:
            self.conn.execute("ALTER TABLE known_announcements ADD COLUMN cik_override TEXT")
        detection_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(detections)").fetchall()
        }
        processed_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(processed_filings)").fetchall()
        }
        scan_run_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(scan_runs)").fetchall()
        }
        if scan_run_columns and "efts_total_hits" not in scan_run_columns:
            self.conn.execute(
                "ALTER TABLE scan_runs ADD COLUMN efts_total_hits INTEGER NOT NULL DEFAULT 0"
            )
        if scan_run_columns and "discovery_control" not in scan_run_columns:
            self.conn.execute(
                "ALTER TABLE scan_runs ADD COLUMN discovery_control TEXT NOT NULL DEFAULT ''"
            )
        if scan_run_columns and "run_kind" not in scan_run_columns:
            self.conn.execute(
                "ALTER TABLE scan_runs ADD COLUMN run_kind TEXT NOT NULL DEFAULT 'scheduled'"
            )
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
        price_reaction_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(price_reactions)").fetchall()
        }
        for column in ("event_day_close_date", "event_day_close", "event_day_move_pct"):
            if column not in price_reaction_columns:
                col_type = "TEXT" if column.endswith("_date") else "REAL"
                self.conn.execute(f"ALTER TABLE price_reactions ADD COLUMN {column} {col_type}")
        # UK market extension (Jul 2026): market tag on every announcement-ish
        # table, defaulting to 'US' so existing rows are untouched; scan_runs
        # gains a source tag ('edgar' / 'investegate') so coverage gaps,
        # health, and the heartbeat are all tracked per discovery source.
        market_tables = {
            "detections": detection_columns,
            "processed_filings": processed_columns,
            "known_announcements": columns,
            "hype_tracking": hype_columns,
            "price_reactions": price_reaction_columns,
        }
        for table, table_columns in market_tables.items():
            if table_columns and "market" not in table_columns:
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN market TEXT NOT NULL DEFAULT 'US'"
                )
        if scan_run_columns and "source" not in scan_run_columns:
            self.conn.execute(
                "ALTER TABLE scan_runs ADD COLUMN source TEXT NOT NULL DEFAULT 'edgar'"
            )
        # Daily-index rows historically stored filing_date as YYYYMMDD while
        # EFTS rows use ISO; compact strings sort above every ISO date, which
        # froze the Scan Log on 17 Jun 2026. Normalise everything to ISO.
        for table in ("detections", "processed_filings"):
            self.conn.execute(
                f"UPDATE {table} SET filing_date = "
                "substr(filing_date, 1, 4) || '-' || substr(filing_date, 5, 2) "
                "|| '-' || substr(filing_date, 7, 2) "
                "WHERE filing_date GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'"
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

    def start_scan_run(self, run_date, run_kind: str = "scheduled", source: str = "edgar") -> int:
        cursor = self.conn.execute(
            "INSERT INTO scan_runs (run_date, started_utc, status, run_kind, source) "
            "VALUES (?, ?, 'running', ?, ?)",
            (str(run_date), utc_now(), run_kind, source),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def set_scan_run_alerts_sent(self, scan_run_id: int, alerts_sent: int) -> None:
        """Backfilled days defer email; count is filled in once the combined
        end-of-run email actually goes out."""
        self.conn.execute(
            "UPDATE scan_runs SET alerts_sent = ? WHERE id = ?",
            (alerts_sent, scan_run_id),
        )
        self.conn.commit()

    def covered_ok_dates(self, since, source: str = "edgar") -> set:
        """Business-day coverage truth: run_dates with at least one ok run.

        Per-source since the UK extension - a healthy US run must never mark
        a UK day as covered, or a UK outage would silently self-heal nothing.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT run_date FROM scan_runs "
            "WHERE status = 'ok' AND run_date >= ? AND source = ?",
            (str(since), source),
        ).fetchall()
        return {
            datetime.strptime(row["run_date"], "%Y-%m-%d").date() for row in rows
        }

    def earliest_ok_run_date(self, source: str = "edgar"):
        row = self.conn.execute(
            "SELECT MIN(run_date) AS d FROM scan_runs WHERE status = 'ok' AND source = ?",
            (source,),
        ).fetchone()
        if not row or not row["d"]:
            return None
        return datetime.strptime(row["d"], "%Y-%m-%d").date()

    def scan_sources_with_ok_runs(self) -> list[str]:
        """Sources that have ever completed a successful run.

        The heartbeat and per-source health checks iterate over these, so a
        source that has never launched (e.g. UK pre-cutover) can never raise
        a false alarm, while every launched source is watched independently.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT source FROM scan_runs WHERE status = 'ok' ORDER BY source"
        ).fetchall()
        return [str(row["source"]) for row in rows]

    def finish_scan_run(
        self,
        scan_run_id: int,
        *,
        status: str,
        filings_seen: int,
        filings_parsed: int,
        alerts_sent: int,
        source: str,
        error: str,
        efts_total_hits: int = 0,
        discovery_control: str = "",
    ) -> None:
        self.conn.execute(
            """
            UPDATE scan_runs
               SET finished_utc = ?, status = ?, discovery_source = ?,
                   filings_seen = ?, filings_parsed = ?, alerts_sent = ?, error = ?,
                   efts_total_hits = ?, discovery_control = ?
             WHERE id = ?
            """,
            (
                utc_now(),
                status,
                source,
                filings_seen,
                filings_parsed,
                alerts_sent,
                error,
                efts_total_hits,
                discovery_control,
                scan_run_id,
            ),
        )
        self.conn.commit()

    def latest_scan_health(self, source: str | None = None) -> dict[str, Any]:
        """Dead-man's-switch data for the health tab / status JSON.

        With source=None (default, original behaviour) this reports across
        all sources; pass 'edgar' / 'investegate' for per-source health so a
        healthy run from one source can never mask an outage in the other.
        """
        source_filter = " AND source = ?" if source else ""
        params: tuple = (source,) if source else ()
        last_ok = self.conn.execute(
            f"SELECT * FROM scan_runs WHERE status = 'ok'{source_filter} ORDER BY id DESC LIMIT 1",
            params,
        ).fetchone()
        last_any = self.conn.execute(
            f"SELECT * FROM scan_runs WHERE 1=1{source_filter} ORDER BY id DESC LIMIT 1",
            params,
        ).fetchone()
        return {
            "last_success_utc": last_ok["finished_utc"] if last_ok else None,
            "last_run_utc": (last_any["finished_utc"] or last_any["started_utc"]) if last_any else None,
            "last_run_status": last_any["status"] if last_any else None,
            "last_run_source": last_any["discovery_source"] if last_any else None,
            "last_run_error": last_any["error"] if last_any else None,
            "last_run_control": (last_any["discovery_control"] if "discovery_control" in last_any.keys() else "") if last_any else None,
        }

    def check_and_update_ops_alert(
        self, alert_key: str, *, is_broken: bool, detail: str, cooldown_hours: float = 12.0
    ) -> str:
        """Decide whether an operational alarm email is due, tracking state so
        PopDay never spams the same ongoing outage every run.

        Returns one of:
          'new_failure'   - just went from healthy to broken: send an email now.
          'still_failing' - been broken longer than cooldown_hours since the last
                            email: send a reminder now (so a multi-day outage is
                            never forgotten after the first alert).
          'recovered'     - was broken, is now healthy: send one all-clear email.
          'none'          - no email needed this call.
        """
        row = self.conn.execute(
            "SELECT * FROM ops_alerts WHERE alert_key = ?", (alert_key,)
        ).fetchone()
        now = utc_now()
        now_dt = datetime.now(timezone.utc)

        if is_broken:
            if row is None or not row["active"]:
                self.conn.execute(
                    """
                    INSERT INTO ops_alerts (alert_key, active, opened_utc, last_sent_utc, detail)
                    VALUES (?, 1, ?, ?, ?)
                    ON CONFLICT(alert_key) DO UPDATE SET
                        active = 1, opened_utc = excluded.opened_utc,
                        last_sent_utc = excluded.last_sent_utc, detail = excluded.detail
                    """,
                    (alert_key, now, now, detail),
                )
                self.conn.commit()
                return "new_failure"

            last_sent = row["last_sent_utc"]
            last_sent_dt = None
            if last_sent:
                try:
                    last_sent_dt = datetime.fromisoformat(last_sent)
                    if last_sent_dt.tzinfo is None:
                        last_sent_dt = last_sent_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    last_sent_dt = None
            stale = (
                last_sent_dt is None
                or (now_dt - last_sent_dt).total_seconds() > cooldown_hours * 3600
            )
            self.conn.execute(
                "UPDATE ops_alerts SET detail = ? WHERE alert_key = ?", (detail, alert_key)
            )
            if stale:
                self.conn.execute(
                    "UPDATE ops_alerts SET last_sent_utc = ? WHERE alert_key = ?",
                    (now, alert_key),
                )
            self.conn.commit()
            return "still_failing" if stale else "none"

        # is_broken == False
        if row is not None and row["active"]:
            self.conn.execute(
                "UPDATE ops_alerts SET active = 0, last_sent_utc = ? WHERE alert_key = ?",
                (now, alert_key),
            )
            self.conn.commit()
            return "recovered"
        return "none"

    def already_processed(self, accession_number: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM processed_filings WHERE accession_number = ?",
            (accession_number,),
        ).fetchone()
        return row is not None

    def mark_processed(self, filing: Any, market: str = "US") -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO processed_filings
            (accession_number, cik, company_name, form_type, filing_date, acceptance_datetime,
             filing_url, processed_timestamp, market)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                market,
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

    def filing_has_sent_alert(self, accession_number: str) -> bool:
        """True if any detection for this filing was already emailed as an alert.

        `--reprocess` uses this to leave already-alerted filings completely
        untouched, so re-detection never disturbs real alert history.
        """
        row = self.conn.execute(
            "SELECT 1 FROM detections WHERE accession_number = ? AND alert_sent = 1 LIMIT 1",
            (accession_number,),
        ).fetchone()
        return row is not None

    def delete_detections_for_accession(self, accession_number: str) -> int:
        """Remove stored detections for one filing so it can be re-detected.

        Used by `--reprocess` to re-run improved detection over already-processed
        filings without leaving stale duplicate rows behind.
        """
        cursor = self.conn.execute(
            "DELETE FROM detections WHERE accession_number = ?",
            (accession_number,),
        )
        self.conn.commit()
        return cursor.rowcount

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

    def recent_candidates(self, limit: int = 30, market: str | None = None) -> list[sqlite3.Row]:
        market_filter = " WHERE d.market = ?" if market else ""
        params: tuple = (market, limit) if market else (limit,)
        return self.conn.execute(
            f"""
            SELECT d.id, d.created_timestamp, d.company_name, d.cik, d.form_type, d.filing_date, d.event_type, d.event_date,
                   d.matched_phrase, d.matched_location, d.status, d.dismissal_reason, d.filing_url,
                   d.acceptance_datetime, d.event_url, d.evidence_url, d.evidence_label, d.market,
                   h.qualifying_count, h.hype_status, h.hype_definition_version, h.provisional
            FROM detections d
            LEFT JOIN hype_tracking h ON h.candidate_id = d.id{market_filter}
            ORDER BY d.filing_date DESC, d.created_timestamp DESC
            LIMIT ?
            """,
            params,
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
        # US-only by design: this feeds the EDGAR submissions-JSON watcher,
        # which looks companies up by CIK on data.sec.gov. UK candidates have
        # an EPIC ticker in the cik column and are watched by the Investegate
        # scan's own in-index hype pass instead.
        return self.conn.execute(
            """
            SELECT id, accession_number, company_name, cik, filing_date, acceptance_datetime,
                   event_date, event_type, filing_url
            FROM detections
            WHERE status = 'alert_candidate'
              AND filing_date IS NOT NULL
              AND event_date IS NOT NULL
              AND market = 'US'
            ORDER BY event_date, created_timestamp
            """
        ).fetchall()

    def uk_open_hype_events(self, as_of: str) -> list[sqlite3.Row]:
        """UK alert candidates whose hype window is still worth watching:
        announced, with the event upcoming or no more than 7 days past."""
        return self.conn.execute(
            """
            SELECT id, accession_number, company_name, cik, ticker, filing_date,
                   event_date, event_type, filing_url
            FROM detections
            WHERE status = 'alert_candidate'
              AND market = 'UK'
              AND filing_date IS NOT NULL
              AND event_date IS NOT NULL
              AND date(event_date) >= date(?, '-7 days')
            ORDER BY event_date, created_timestamp
            """,
            (as_of,),
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
        market: str = "US",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO hype_tracking
            (candidate_id, cik, announcement_date, event_date, qualifying_count, hype_status,
             hype_definition_version, provisional, last_checked, detected_json, market)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                cik = excluded.cik,
                announcement_date = excluded.announcement_date,
                event_date = excluded.event_date,
                qualifying_count = excluded.qualifying_count,
                hype_status = excluded.hype_status,
                hype_definition_version = excluded.hype_definition_version,
                provisional = excluded.provisional,
                last_checked = excluded.last_checked,
                detected_json = excluded.detected_json,
                market = excluded.market
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
                market,
            ),
        )
        self.conn.commit()

    def hype_tracking_for_candidate(self, candidate_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT candidate_id, cik, announcement_date, event_date, qualifying_count,
                   hype_status, hype_definition_version, provisional, last_checked,
                   detected_json, market
            FROM hype_tracking
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

    def all_hype_tracking_rows(self, market: str | None = None) -> list[sqlite3.Row]:
        market_filter = " WHERE market = ?" if market else ""
        params: tuple = (market,) if market else ()
        return self.conn.execute(
            f"""
            SELECT candidate_id, cik, announcement_date, event_date, qualifying_count,
                   hype_status, hype_definition_version, provisional, last_checked,
                   detected_json, market
            FROM hype_tracking{market_filter}
            ORDER BY event_date, candidate_id
            """,
            params,
        ).fetchall()

    # ------------------------------------------------------------ UK prices

    def upsert_price(
        self,
        *,
        ticker: str,
        date: str,
        close: float,
        currency: str,
        market: str,
        source: str = "yfinance",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO prices (ticker, date, close, currency, market, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                close = excluded.close,
                currency = excluded.currency,
                market = excluded.market,
                source = excluded.source,
                fetched_at = excluded.fetched_at
            """,
            (ticker, date, close, currency, market, source, utc_now()),
        )
        self.conn.commit()

    def get_ticker_mapping(self, market: str, local_symbol: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT market, local_symbol, yahoo_symbol, manual_override, notes "
            "FROM ticker_mappings WHERE market = ? AND local_symbol = ?",
            (market, local_symbol),
        ).fetchone()

    def save_ticker_mapping(
        self, *, market: str, local_symbol: str, yahoo_symbol: str, notes: str = ""
    ) -> None:
        """Record a derived mapping. Never overwrites manual_override - that
        column is the operator's escape hatch and only a human sets it."""
        self.conn.execute(
            """
            INSERT INTO ticker_mappings (market, local_symbol, yahoo_symbol, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(market, local_symbol) DO UPDATE SET
                yahoo_symbol = excluded.yahoo_symbol,
                notes = excluded.notes
            """,
            (market, local_symbol, yahoo_symbol, notes),
        )
        self.conn.commit()

    def investor_day_announcements(self, market: str | None = None) -> list[dict[str, Any]]:
        market_filter = " AND d.market = ?" if market else ""
        params: tuple = (market,) if market else ()
        rows = self.conn.execute(
            f"""
            SELECT d.id AS source_id, 'detections' AS source_table,
                   d.company_name, d.cik, d.ticker, d.event_type, d.event_date,
                   d.form_type, d.filing_date,
                   d.acceptance_datetime, d.filing_url AS source_url, d.evidence_url, d.evidence_label,
                   d.accession_number, d.matched_phrase, d.matched_location, d.alert_sent,
                   d.alert_sent_timestamp, d.created_timestamp,
                   CASE WHEN d.market = 'UK' THEN 'Investegate' ELSE 'EDGAR' END AS source_type,
                   CASE WHEN d.market = 'UK' THEN 'RNS announcement' ELSE 'SEC filing' END AS source_label,
                   d.market, h.qualifying_count AS hype_count
            FROM detections d
            LEFT JOIN hype_tracking h ON h.candidate_id = d.id
            WHERE d.event_type IS NOT NULL
              AND (
                (d.status = 'alert_candidate' AND d.event_date IS NOT NULL)
                OR d.status = 'alert_candidate_tbd'
              ){market_filter}
            ORDER BY d.event_date IS NULL, d.event_date DESC, d.created_timestamp DESC
            """,
            params,
        ).fetchall()
        known_market_filter = " WHERE market = ?" if market else ""
        known_rows = self.conn.execute(
            f"""
            SELECT id AS source_id, 'known_announcements' AS source_table,
                   company_name, cik_override AS cik, ticker_override AS ticker, event_type, event_date, NULL AS form_type,
                   announcement_date AS filing_date, NULL AS acceptance_datetime,
                   source_url, NULL AS accession_number,
                   source_url AS evidence_url, source_label AS evidence_label,
                   NULL AS matched_phrase, NULL AS matched_location, alert_sent,
                   alert_sent_timestamp, created_timestamp, source_type, source_label,
                   market, NULL AS hype_count
            FROM known_announcements{known_market_filter}
            ORDER BY event_date DESC, created_timestamp DESC
            """,
            params,
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
             interval_return_pct, interval_daily_volatility_pct,
             event_day_close_date, event_day_close, event_day_move_pct, price_data_source,
             price_data_timestamp, status, notes)
            VALUES
            (:announcement_key, :source_table, :source_id, :company_name, :cik, :ticker,
             :event_date, :filing_date, :acceptance_datetime, :reaction_date, :previous_close_date,
             :previous_close, :reaction_open, :reaction_high, :reaction_low, :reaction_close,
             :announcement_move_pct, :intraday_range_pct, :latest_close_date, :latest_close,
             :interval_return_pct, :interval_daily_volatility_pct,
             :event_day_close_date, :event_day_close, :event_day_move_pct, :price_data_source,
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
                event_day_close_date = excluded.event_day_close_date,
                event_day_close = excluded.event_day_close,
                event_day_move_pct = excluded.event_day_move_pct,
                price_data_source = excluded.price_data_source,
                price_data_timestamp = excluded.price_data_timestamp,
                status = excluded.status,
                notes = excluded.notes
            """,
            row,
        )
        self.conn.commit()

    def price_reaction_rows(self, market: str | None = None) -> list[sqlite3.Row]:
        market_filter = " WHERE market = ?" if market else ""
        params: tuple = (market,) if market else ()
        return self.conn.execute(
            f"""
            SELECT announcement_key, source_table, source_id, company_name, cik, ticker,
                   event_date, filing_date, acceptance_datetime, reaction_date,
                   previous_close_date, previous_close, reaction_open, reaction_high,
                   reaction_low, reaction_close, announcement_move_pct, intraday_range_pct,
                   latest_close_date, latest_close, interval_return_pct,
                   interval_daily_volatility_pct, event_day_close_date, event_day_close,
                   event_day_move_pct, price_data_source, price_data_timestamp,
                   status, notes, market
            FROM price_reactions{market_filter}
            ORDER BY COALESCE(filing_date, '') DESC, company_name
            """,
            params,
        ).fetchall()

    def investor_day_announcement_count(self) -> int:
        return len(self.investor_day_announcements())

    def research_hype_events(self, market: str | None = None) -> list[dict[str, Any]]:
        market_filter = " AND d.market = ?" if market else ""
        params: tuple = (market,) if market else ()
        rows = self.conn.execute(
            f"""
            SELECT d.id AS candidate_id, d.company_name,
                   COALESCE(NULLIF(d.ticker, ''), pr.ticker) AS ticker,
                   d.cik, d.event_type,
                   d.event_date, COALESCE(h.announcement_date, d.filing_date) AS announcement_date,
                   d.acceptance_datetime, d.filing_url AS source_url, d.evidence_url, d.evidence_label,
                   d.created_timestamp,
                   CASE WHEN d.market = 'UK' THEN 'Investegate' ELSE 'EDGAR' END AS source_type,
                   d.market, h.qualifying_count AS investor_comms_count,
                   h.detected_json, h.last_checked, h.hype_definition_version, h.provisional
            FROM detections d
            LEFT JOIN hype_tracking h ON h.candidate_id = d.id
            -- Ticker was never populated on the US detection path (only the
            -- UK path sets it directly); price_reactions already resolves
            -- and caches a ticker per company (via stock_reaction.py's SEC
            -- CIK->ticker lookup) after every scan, so reuse it here rather
            -- than re-deriving ticker resolution a second time.
            LEFT JOIN price_reactions pr ON pr.company_name = d.company_name
            WHERE d.status = 'alert_candidate'
              AND d.event_type IS NOT NULL
              AND d.event_date IS NOT NULL{market_filter}
            ORDER BY d.event_date DESC, d.created_timestamp DESC
            """,
            params,
        ).fetchall()
        known_market_filter = " WHERE k.market = ?" if market else ""
        known_rows = self.conn.execute(
            f"""
            SELECT NULL AS candidate_id, k.company_name, pr.ticker AS ticker, NULL AS cik,
                   k.event_type, k.event_date, k.announcement_date, NULL AS acceptance_datetime,
                   k.source_url, k.source_url AS evidence_url, k.source_label AS evidence_label,
                   k.created_timestamp, k.source_type,
                   k.market, NULL AS investor_comms_count, NULL AS detected_json, NULL AS last_checked,
                   NULL AS hype_definition_version, NULL AS provisional
            FROM known_announcements k
            LEFT JOIN price_reactions pr ON pr.company_name = k.company_name
            {known_market_filter}
            ORDER BY k.event_date DESC, k.created_timestamp DESC
            """,
            params,
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

    # ------------------------------------------------------- company websites

    def has_company_website_row(self, cik: str) -> bool:
        """True once a company's EDGAR website has been checked (successfully
        or not) - gates the scan-time capture so it fires only the first time
        a company is discovered, never on every later filing from it."""
        row = self.conn.execute(
            "SELECT 1 FROM companies WHERE cik = ?", (cik,)
        ).fetchone()
        return row is not None

    def upsert_company_website(
        self, *, cik: str, company_name: str, edgar_website: str, market: str = "US"
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO companies (cik, company_name, market, edgar_website, edgar_website_checked_utc)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cik) DO UPDATE SET
                company_name = excluded.company_name,
                market = excluded.market,
                edgar_website = excluded.edgar_website,
                edgar_website_checked_utc = excluded.edgar_website_checked_utc
            """,
            (cik, company_name, market, edgar_website or None, utc_now()),
        )
        self.conn.commit()

    def company_websites_by_cik(self) -> dict[str, str]:
        """EDGAR-resolved websites, keyed by CIK - the fallback layer under
        curated links wherever company_url is rendered."""
        rows = self.conn.execute(
            "SELECT cik, edgar_website FROM companies "
            "WHERE edgar_website IS NOT NULL AND edgar_website != ''"
        ).fetchall()
        return {str(row["cik"]): str(row["edgar_website"]) for row in rows}

    # ---------------------------------------------- resolved (auto) websites

    def has_resolved_website_row(self, company_key: str) -> bool:
        """True once a company's heuristic auto-resolve has been attempted
        (successfully or not) - gates the fetch so it fires only the first
        time, never re-guessing the same company on every later scan."""
        row = self.conn.execute(
            "SELECT 1 FROM resolved_company_websites WHERE company_key = ?", (company_key,)
        ).fetchone()
        return row is not None

    def upsert_resolved_website(
        self,
        *,
        company_key: str,
        company_name: str,
        cik: str = "",
        resolved_website: str,
        resolution_method: str = "heuristic_domain_guess",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO resolved_company_websites
                (company_key, company_name, cik, resolved_website, resolution_method, resolved_checked_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_key) DO UPDATE SET
                company_name = excluded.company_name,
                cik = excluded.cik,
                resolved_website = excluded.resolved_website,
                resolution_method = excluded.resolution_method,
                resolved_checked_utc = excluded.resolved_checked_utc
            """,
            (company_key, company_name, cik or None, resolved_website or None, resolution_method, utc_now()),
        )
        self.conn.commit()

    def resolved_websites_by_key(self) -> dict[str, str]:
        """Heuristically auto-resolved websites, keyed by company_key - the
        fallback layer under curated links and EDGAR wherever company_url is
        rendered."""
        rows = self.conn.execute(
            "SELECT company_key, resolved_website FROM resolved_company_websites "
            "WHERE resolved_website IS NOT NULL AND resolved_website != ''"
        ).fetchall()
        return {str(row["company_key"]): str(row["resolved_website"]) for row in rows}

    def distinct_detection_companies(self, market: str = "US") -> list[sqlite3.Row]:
        """Every distinct CIK ever detected in this market - the backfill
        target universe for the EDGAR website fill (one row per company, most
        recently seen name wins so a later, better-cased filing is preferred)."""
        return self.conn.execute(
            """
            SELECT cik, company_name
            FROM (
                SELECT cik, company_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY cik ORDER BY created_timestamp DESC
                       ) AS rn
                FROM detections
                WHERE market = ? AND cik IS NOT NULL AND cik != ''
            )
            WHERE rn = 1
            ORDER BY company_name
            """,
            (market,),
        ).fetchall()
