#!/usr/bin/env python3
"""Generate the small Mac Mini PopDay status JSON for PythonAnywhere."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import socket
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_DIR = Path("/Users/jasondunne/PopDayRuntime")
DEFAULT_BACKUP_ROOT = Path("/Users/jasondunne/PopDayBackups")
DEFAULT_OUTPUT = DEFAULT_RUNTIME_DIR / "status" / "popday_status.json"


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _log_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return _iso(dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc))


def _read_tail(path: Path, line_count: int = 30) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-line_count:]


def _launchd_rows(out_log: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _read_tail(out_log, 600):
        if line.startswith("PopDay launchd run started: "):
            if current:
                rows.append(current)
            current = {
                "started_at": line.replace("PopDay launchd run started: ", "", 1),
                "filing_date_scanned": "",
                "edgar_index_status": "",
                "filings_parsed": "",
                "eight_k_sanity_count": "",
                "qualifying_alerts_sent": "",
            }
        elif current and line.startswith("Filing date scanned: "):
            current["filing_date_scanned"] = line.replace("Filing date scanned: ", "", 1)
        elif current and line.startswith("EDGAR index status: "):
            current["edgar_index_status"] = line.replace("EDGAR index status: ", "", 1)
        elif current and line.startswith("Filings parsed: "):
            current["filings_parsed"] = line.replace("Filings parsed: ", "", 1)
        elif current and line.startswith("8-K sanity count: "):
            current["eight_k_sanity_count"] = line.replace("8-K sanity count: ", "", 1)
        elif current and line.startswith("Qualifying PopDay alerts sent: "):
            current["qualifying_alerts_sent"] = line.replace("Qualifying PopDay alerts sent: ", "", 1)
    if current:
        rows.append(current)
    return rows


def _count(con: sqlite3.Connection, table: str, where: str = "") -> int | None:
    try:
        sql = f"SELECT count(*) FROM {table} {where}"
        return int(con.execute(sql).fetchone()[0])
    except sqlite3.Error:
        return None


def _latest_alert(con: sqlite3.Connection) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    try:
        rows.extend(
            dict(row)
            for row in con.execute(
                """
                SELECT company_name, event_type, event_date, filing_url AS source_url,
                       alert_sent_timestamp, 'EDGAR' AS source_type
                FROM detections
                WHERE alert_sent = 1 AND alert_sent_timestamp IS NOT NULL
                ORDER BY alert_sent_timestamp DESC
                LIMIT 5
                """
            ).fetchall()
        )
    except sqlite3.Error:
        pass
    try:
        rows.extend(
            dict(row)
            for row in con.execute(
                """
                SELECT company_name, event_type, event_date, source_url,
                       alert_sent_timestamp, source_type
                FROM known_announcements
                WHERE alert_sent = 1 AND alert_sent_timestamp IS NOT NULL
                ORDER BY alert_sent_timestamp DESC
                LIMIT 5
                """
            ).fetchall()
        )
    except sqlite3.Error:
        pass
    if not rows:
        return None
    return max(rows, key=lambda row: str(row.get("alert_sent_timestamp") or ""))


def _database_status(db_path: Path) -> dict[str, Any]:
    status: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "counts": {},
        "latest_alert": None,
        "error": "",
    }
    if not db_path.exists():
        status["error"] = "database_missing"
        return status
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        status["counts"] = {
            "processed_filings": _count(con, "processed_filings"),
            "detections": _count(con, "detections"),
            "known_announcements": _count(con, "known_announcements"),
            "active_recipients": _count(con, "alert_recipients", "WHERE active = 1"),
        }
        status["latest_alert"] = _latest_alert(con)
    finally:
        con.close()
    return status


def _backup_status(backup_root: Path) -> dict[str, Any]:
    backups = sorted(path for path in backup_root.glob("*") if path.is_dir())
    latest = backups[-1] if backups else None
    return {
        "backup_root": str(backup_root),
        "retained_backups": len(backups),
        "latest_backup_at": latest.name if latest else None,
        "latest_backup_path": str(latest) if latest else None,
        "live_database_backed_up": bool(latest and (latest / "popday.sqlite3").exists()),
        "latest_manifest_path": str(latest / "manifest.txt") if latest and (latest / "manifest.txt").exists() else None,
    }


def _git_commit(source_repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _days_since(value: str | None, now: dt.datetime) -> int | None:
    parsed = _parse_iso(value)
    if not parsed:
        return None
    return max(0, (now.date() - parsed.date()).days)


def _health(
    *,
    latest_run: dict[str, str] | None,
    db_status: dict[str, Any],
    err_log: Path,
    now: dt.datetime,
) -> dict[str, str]:
    if not db_status["exists"]:
        return {"level": "BROKEN", "summary": "BROKEN: live Mac Mini database is missing."}
    if not latest_run:
        return {"level": "BROKEN", "summary": "BROKEN: no Mac Mini scan runs found in the launchd log."}

    started = _parse_iso(latest_run.get("started_at"))
    if not started:
        return {"level": "BROKEN", "summary": "BROKEN: latest scan time could not be parsed."}

    age_hours = (now - started).total_seconds() / 3600
    index_status = (latest_run.get("edgar_index_status") or "").strip().lower()
    err_size = err_log.stat().st_size if err_log.exists() else 0

    if age_hours > 42:
        return {"level": "BROKEN", "summary": "BROKEN: no successful Mac Mini scan in more than 42 hours."}
    if index_status == "error":
        return {"level": "BROKEN", "summary": "BROKEN: latest Mac Mini scan reported an EDGAR error."}
    if err_size > 0:
        return {"level": "BROKEN", "summary": "BROKEN: Mac Mini launchd error log is not empty."}
    if age_hours > 18:
        return {"level": "STALE", "summary": "STALE: latest Mac Mini scan is older than expected."}
    if "not yet" in index_status:
        return {"level": "STALE", "summary": "STALE: SEC EDGAR index was not yet available, but the scanner did not crash."}
    if index_status == "available":
        alerts = latest_run.get("qualifying_alerts_sent") or "0"
        if alerts == "0":
            return {
                "level": "LIVE",
                "summary": "Healthy. No qualifying PopDay alerts found in latest scan.",
            }
        return {"level": "LIVE", "summary": "LIVE: latest Mac Mini scan succeeded and sent alerts."}
    return {"level": "STALE", "summary": "STALE: latest Mac Mini scan has an unclear EDGAR status."}


def build_status(args: argparse.Namespace) -> dict[str, Any]:
    runtime_dir = args.runtime_dir
    db_path = args.db_path or runtime_dir / "popday.sqlite3"
    out_log = runtime_dir / "logs" / "popday.launchd.out.log"
    err_log = runtime_dir / "logs" / "popday.launchd.err.log"
    now = _utc_now()

    rows = _launchd_rows(out_log)
    latest_run = rows[-1] if rows else None
    db_status = _database_status(db_path)
    latest_alert = db_status.get("latest_alert") or {}
    backup = _backup_status(args.backup_root)
    health = _health(latest_run=latest_run, db_status=db_status, err_log=err_log, now=now)

    return {
        "generated_at": _iso(now),
        "scanner_host": "Mac Mini",
        "source_repo": str(args.source_repo),
        "source_git_commit": _git_commit(args.source_repo),
        "runtime_dir": str(runtime_dir),
        "database_path": str(db_path),
        "latest_scan_started_at": latest_run.get("started_at") if latest_run else None,
        "latest_scan_completed_at": None,
        "filing_date_scanned": latest_run.get("filing_date_scanned") if latest_run else None,
        "edgar_index_status": latest_run.get("edgar_index_status") if latest_run else None,
        "filings_parsed": latest_run.get("filings_parsed") if latest_run else None,
        "eight_k_sanity_count": latest_run.get("eight_k_sanity_count") if latest_run else None,
        "qualifying_alerts_sent": latest_run.get("qualifying_alerts_sent") if latest_run else None,
        "last_alert_company": latest_alert.get("company_name"),
        "last_alert_date": latest_alert.get("event_date"),
        "last_alert_sent_at": latest_alert.get("alert_sent_timestamp"),
        "last_alert_filing_url": latest_alert.get("source_url"),
        "days_since_last_alert": _days_since(latest_alert.get("alert_sent_timestamp"), now),
        "latest_log_tail": _read_tail(out_log, 24),
        "latest_error_log_tail": _read_tail(err_log, 12),
        "out_log_path": str(out_log),
        "out_log_modified_at": _log_mtime(out_log),
        "error_log_path": str(err_log),
        "error_log_size_bytes": err_log.stat().st_size if err_log.exists() else 0,
        "error_log_modified_at": _log_mtime(err_log),
        "database_counts": db_status["counts"],
        "last_backup_at": backup["latest_backup_at"],
        "last_backup_path": backup["latest_backup_path"],
        "live_database_backed_up": backup["live_database_backed_up"],
        "retained_backups": backup["retained_backups"],
        "backup_status": backup,
        "sync_status": "generated_on_mac_mini",
        "health": health,
        "architecture_note": "Scanner runs on Mac Mini. This page shows last synced Mac Mini status.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write PopDay status JSON for PythonAnywhere.")
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--source-repo", type=Path, default=Path.cwd())
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    status = build_status(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, args.output)
    print(f"PopDay status JSON written: {args.output}")
    print(f"{status['health']['level']}: {status['health']['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
