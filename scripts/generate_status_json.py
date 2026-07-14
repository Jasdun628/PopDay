#!/usr/bin/env python3
"""Generate the small PopDay status JSON that drives the System Health tab."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import socket
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_DIR = Path("/Users/jasondunne/PopDayRuntime")
DEFAULT_BACKUP_ROOT = Path("/Users/jasondunne/PopDayBackups")
DEFAULT_OUTPUT = DEFAULT_RUNTIME_DIR / "status" / "popday_status.json"
_PA_BACKUP_FILE_RE = re.compile(r"^popday\.sqlite3\.(\d{8}T\d{6}Z)\.bak$")

# Output-based canary thresholds. Process checks (did a scan run?) miss the
# failure where a scan runs green but silently finds nothing — the shape of the
# June 2026 outage. These watch the output instead.
COVERAGE_DISCOVERY_RUN_WINDOW = 6  # recent completed runs inspected for discovery
CANDIDATE_STALE_DAYS = 8  # amber if no new investor-day candidate in this many days


def _assess_coverage(
    *,
    recent_filings_seen: list[int],
    days_since_candidate: int | None,
    discovery_control: str = "",
    discovery_source: str = "",
) -> dict[str, str]:
    """Pure coverage verdict from discovery volume, candidate freshness, and
    the discovery instrument's own health.

    filings_seen changed meaning with the EFTS switch: it now counts
    phrase-matching filings (routinely zero on quiet days), not every 8-K
    filed (hundreds). Zeros alone are therefore NOT evidence of breakage on
    EFTS runs - only the control probe can distinguish "instrument broken"
    from "genuinely quiet". The UK investegate-index source counts headline
    matches the same way (sparse; zero is normal), so it gets the same
    treatment; only the legacy daily-index source, where hundreds of raw
    filings are always expected, treats an all-zero window as breakage.

    - broken: control probe says the instrument is broken, or an all-zero
      window on the daily-index source (where hundreds are always expected).
    - stale: no new candidate in a while, or an all-zero phrase-match window
      with no control verdict recorded (unverified quiet - worth a glance).
    """
    considered = len(recent_filings_seen)
    all_zero = considered >= COVERAGE_DISCOVERY_RUN_WINDOW and sum(recent_filings_seen) == 0
    control = str(discovery_control or "").strip().lower()
    source = str(discovery_source or "").strip().lower()

    if all_zero:
        if control == "broken":
            return {
                "level": "broken",
                "message": (
                    "The discovery control probe failed: the discovery instrument "
                    "returned zero hits even for a known-good historical query. "
                    "The instrument is broken - recent green runs cannot be trusted."
                ),
            }
        if source and source not in ("efts", "investegate-index"):
            return {
                "level": "broken",
                "message": (
                    f"PopDay discovered no filings across its last {considered} scans "
                    "on the daily-index source, where hundreds are expected - "
                    "discovery may be broken (the June 2026 outage signature)."
                ),
            }
        if control != "ok":
            return {
                "level": "stale",
                "message": (
                    f"Zero filings matched across the last {considered} scans and no "
                    "control-probe verdict is recorded - probably a quiet stretch, "
                    "but worth one manual check."
                ),
            }
        # control == "ok": verified quiet; fall through to candidate freshness.
    if days_since_candidate is not None and days_since_candidate > CANDIDATE_STALE_DAYS:
        return {
            "level": "stale",
            "message": (
                f"No new investor-day candidate in {days_since_candidate} days - "
                "worth checking the scanner is still matching filings."
            ),
        }
    return {"level": "ok", "message": ""}


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


def _parse_launchd_lines(lines: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
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


def _launchd_rows(out_log: Path) -> list[dict[str, str]]:
    return _parse_launchd_lines(_read_tail(out_log, 600))


def _synth_log_lines_from_scan_runs(con: sqlite3.Connection, limit: int = 12) -> list[str]:
    """Recreate the launchd-log text format from scan_runs rows, oldest first.

    This used to be a tail of the Mac Mini's launchd stdout log. Now that
    scans can run on PythonAnywhere (no such log file exists there), rebuild
    the same lines from scan_runs - the actual source of truth for every
    field this used to scrape out of text - so the format stays identical
    and every downstream consumer (this script's own latest_run fields, and
    flask_app.py's Scan Log tab) keeps working unchanged regardless of which
    host ran the scan.
    """
    try:
        # US/edgar rows only: these lines feed the existing single-table
        # "Recent scan runs" view whose format predates the UK extension.
        # UK (investegate) run health is reported via the per-source
        # `sources` block instead of being mixed into this stream.
        rows = con.execute(
            "SELECT started_utc, run_date, status, discovery_source, discovery_control, "
            "filings_seen, filings_parsed, alerts_sent FROM scan_runs "
            "WHERE run_kind != 'backfill' AND source = 'edgar' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        try:
            rows = con.execute(
                "SELECT started_utc, run_date, status, discovery_source, discovery_control, "
                "filings_seen, filings_parsed, alerts_sent FROM scan_runs "
                "WHERE run_kind != 'backfill' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.Error:
            return []
    lines: list[str] = []
    for row in reversed(rows):
        if row["status"] == "failed" or row["discovery_control"] == "broken":
            index_status = "error"
        elif row["status"] in ("ok", "dry-run"):
            index_status = "available"
        else:
            index_status = "unknown"
        lines.append(f"PopDay launchd run started: {row['started_utc']}")
        lines.append(f"Filing date scanned: {row['run_date'] or 'unknown'}")
        lines.append(f"EDGAR index status: {index_status}")
        parsed = row["filings_parsed"]
        lines.append(f"Filings parsed: {parsed if parsed is not None else 0}")
        # Total 8-K count isn't stored in scan_runs (only phrase-matching
        # filings_seen is) - not available when reconstructing from the DB.
        lines.append("8-K sanity count: n/a")
        alerts = row["alerts_sent"]
        lines.append(f"Qualifying PopDay alerts sent: {alerts if alerts is not None else 0}")
    return lines


def _count(con: sqlite3.Connection, table: str, where: str = "") -> int | None:
    try:
        sql = f"SELECT count(*) FROM {table} {where}"
        return int(con.execute(sql).fetchone()[0])
    except sqlite3.Error:
        return None


def _scan_sources(con: sqlite3.Connection) -> list[str]:
    """Distinct discovery sources that have ever run. Pre-UK databases have
    no source column; treat everything there as 'edgar'."""
    try:
        rows = con.execute("SELECT DISTINCT source FROM scan_runs ORDER BY source").fetchall()
        return [str(row["source"]) for row in rows] or ["edgar"]
    except sqlite3.Error:
        return ["edgar"]


def _scan_health(con: sqlite3.Connection, source: str | None = None) -> dict[str, Any]:
    """Authoritative dead-man's-switch health from the scan_runs table.

    Mirrors popday.db.Database.latest_scan_health() but is kept as a raw query
    so this deploy script stays independent of the popday package import path.
    Returns table_present=False if the scan_runs table has not been created yet
    (older runtimes), so callers can fall back to log-based health. Passing a
    source filters to one discovery source ('edgar' / 'investegate') so a
    healthy run from one can never mask an outage in the other; on databases
    predating the source column the filter is ignored.
    """
    empty: dict[str, Any] = {
        "table_present": False,
        "last_success_utc": None,
        "last_run_utc": None,
        "last_run_status": None,
        "last_run_source": None,
        "last_run_error": None,
        "recovered_dates": [],
    }
    source_filter = " AND source = ?" if source else ""
    params: tuple = (source,) if source else ()
    try:
        last_ok = con.execute(
            f"SELECT finished_utc FROM scan_runs WHERE status = 'ok'{source_filter} "
            "ORDER BY id DESC LIMIT 1",
            params,
        ).fetchone()
        last_any = con.execute(
            "SELECT started_utc, finished_utc, status, discovery_source, error "
            f"FROM scan_runs WHERE 1=1{source_filter} ORDER BY id DESC LIMIT 1",
            params,
        ).fetchone()
    except sqlite3.Error:
        if source:
            return _scan_health(con, source=None)
        return empty
    if last_any is None:
        empty["table_present"] = True
        return empty
    return {
        "table_present": True,
        "last_success_utc": last_ok["finished_utc"] if last_ok else None,
        "last_run_utc": last_any["finished_utc"] or last_any["started_utc"],
        "last_run_status": last_any["status"],
        "last_run_source": last_any["discovery_source"],
        "last_run_error": last_any["error"],
        "recovered_dates": _recovered_dates(con),
    }


def _recovered_dates(con: sqlite3.Connection) -> list[str]:
    """Missed days the most recent run swept during downtime recovery.

    Backfill rows are written immediately before their triggering run's own
    row, so the latest run session's recoveries are the contiguous trailing
    group of run_kind='backfill' rows (skipping the newest row itself when it
    is the scheduled/manual main-date run). Older synced DBs without the
    run_kind column report none.
    """
    try:
        rows = con.execute(
            "SELECT run_date, status, run_kind FROM scan_runs "
            "ORDER BY id DESC LIMIT 40"
        ).fetchall()
    except sqlite3.Error:
        return []
    if not rows:
        return []
    start = 0 if rows[0]["run_kind"] == "backfill" else 1
    recovered: list[str] = []
    for row in rows[start:]:
        if row["run_kind"] != "backfill":
            break
        if row["status"] == "ok":
            recovered.append(row["run_date"])
    recovered.reverse()
    return recovered


def _coverage_health(
    con: sqlite3.Connection,
    now: dt.datetime,
    source: str | None = None,
    market: str | None = None,
) -> dict[str, Any]:
    """Output canary: is PopDay still discovering filings and producing candidates?

    Per-source/per-market when asked (the UK extension's requirement: a UK
    block must not be masked by a healthy US run, and vice versa). On
    databases predating the source/market columns the filters fall away.
    """
    base: dict[str, Any] = {
        "table_present": False,
        "last_candidate_utc": None,
        "days_since_candidate": None,
        "recent_runs_considered": 0,
        "recent_filings_seen": None,
        "level": "unknown",
        "message": "",
    }
    source_filter = " AND source = ?" if source else ""
    source_params: tuple = (source,) if source else ()
    market_filter = " AND market = ?" if market else ""
    market_params: tuple = (market,) if market else ()
    try:
        last_candidate = con.execute(
            "SELECT max(created_timestamp) FROM detections "
            f"WHERE status IN ('alert_candidate', 'alert_candidate_tbd'){market_filter}",
            market_params,
        ).fetchone()[0]
        rows = con.execute(
            "SELECT filings_seen FROM scan_runs WHERE finished_utc IS NOT NULL"
            f"{source_filter} ORDER BY id DESC LIMIT ?",
            source_params + (COVERAGE_DISCOVERY_RUN_WINDOW,),
        ).fetchall()
    except sqlite3.Error:
        if source or market:
            return _coverage_health(con, now)
        return base
    latest = None
    try:
        latest = con.execute(
            "SELECT COALESCE(discovery_source, ''), COALESCE(discovery_control, '') "
            f"FROM scan_runs WHERE finished_utc IS NOT NULL{source_filter} "
            "ORDER BY id DESC LIMIT 1",
            source_params,
        ).fetchone()
    except sqlite3.Error:
        # Older synced DB without the discovery_control column: assess on
        # volume + freshness only (control stays unknown).
        try:
            row = con.execute(
                "SELECT COALESCE(discovery_source, '') FROM scan_runs "
                "WHERE finished_utc IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            latest = (row[0], "") if row else None
        except sqlite3.Error:
            latest = None
    recent = [int(row[0] or 0) for row in rows]
    latest_source = str(latest[0] or "") if latest else ""
    latest_control = str(latest[1] or "") if latest else ""
    last_dt = _parse_iso(last_candidate)
    days = (now.date() - last_dt.date()).days if last_dt else None
    verdict = _assess_coverage(
        recent_filings_seen=recent,
        days_since_candidate=days,
        discovery_control=latest_control,
        discovery_source=latest_source,
    )
    base.update(
        table_present=True,
        last_candidate_utc=last_candidate,
        days_since_candidate=days,
        recent_runs_considered=len(recent),
        recent_filings_seen=sum(recent),
        level=verdict["level"],
        message=verdict["message"],
    )
    return base


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


def _database_status(db_path: Path, now: dt.datetime) -> dict[str, Any]:
    status: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "counts": {},
        "latest_alert": None,
        "scan_health": None,
        "coverage_health": None,
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
        status["scan_health"] = _scan_health(con)
        # Per-source health so a UK outage is never masked by a healthy US
        # run (and vice versa). Keyed by discovery source; each entry pairs
        # the dead-man's-switch view with that source's own coverage canary.
        source_markets = {"edgar": "US", "investegate": "UK"}
        status["sources"] = {
            source: {
                "scan_health": _scan_health(con, source=source),
                "coverage_health": _coverage_health(
                    con, now, source=source, market=source_markets.get(source)
                ),
            }
            for source in _scan_sources(con)
        }
        status["coverage_health"] = _coverage_health(con, now)
    finally:
        con.close()
    return status


def _backup_status(backup_root: Path) -> dict[str, Any]:
    """Summarize backups under backup_root, for display only.

    Two conventions exist and this reads either: the Mac Mini's
    backup_popday_runtime.py writes a timestamped subdirectory per backup
    (with popday.sqlite3 and manifest.txt inside); PythonAnywhere's deploy
    script instead writes a flat popday.sqlite3.<timestamp>.bak file per
    backup directly under backup_root. Neither convention is changed here -
    this only fixes how existing backups of either shape are counted and
    reported.
    """
    dir_backups = sorted(path for path in backup_root.glob("*") if path.is_dir())
    if dir_backups:
        latest = dir_backups[-1]
        return {
            "backup_root": str(backup_root),
            "retained_backups": len(dir_backups),
            "latest_backup_at": latest.name,
            "latest_backup_path": str(latest),
            "live_database_backed_up": (latest / "popday.sqlite3").exists(),
            "latest_manifest_path": str(latest / "manifest.txt") if (latest / "manifest.txt").exists() else None,
        }

    file_backups = sorted(path for path in backup_root.glob("popday.sqlite3.*.bak") if path.is_file())
    if file_backups:
        latest = file_backups[-1]
        match = _PA_BACKUP_FILE_RE.match(latest.name)
        return {
            "backup_root": str(backup_root),
            "retained_backups": len(file_backups),
            "latest_backup_at": match.group(1) if match else latest.name,
            "latest_backup_path": str(latest),
            "live_database_backed_up": True,
            "latest_manifest_path": None,
        }

    return {
        "backup_root": str(backup_root),
        "retained_backups": 0,
        "latest_backup_at": None,
        "latest_backup_path": None,
        "live_database_backed_up": False,
        "latest_manifest_path": None,
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


_LEVEL_RANK = {"LIVE": 0, "STALE": 1, "BROKEN": 2}
_SOURCE_LABELS = {"edgar": "", "investegate": "UK (Investegate) "}


def _source_health_verdict(
    scan_health: dict[str, Any],
    coverage: dict[str, Any],
    latest_run: dict[str, str] | None,
    now: dt.datetime,
    label: str = "",
) -> dict[str, str] | None:
    """The scan_runs dead-man's-switch ladder for one discovery source.

    Returns None when this source has no run history (caller falls back to
    log-based health). `label` prefixes summaries for non-default sources so
    a UK-caused banner says so explicitly; the US/edgar summaries stay
    word-for-word identical to the pre-UK output.
    """
    if not (scan_health.get("table_present") and scan_health.get("last_run_status")):
        return None
    run_status = (scan_health.get("last_run_status") or "").strip().lower()
    error = (scan_health.get("last_run_error") or "").strip()
    last_success = _parse_iso(scan_health.get("last_success_utc"))
    success_age_h = (now - last_success).total_seconds() / 3600 if last_success else None

    if run_status == "failed":
        detail = f" {error}" if error else ""
        return {"level": "BROKEN", "summary": f"BROKEN: the latest {label}PopDay scan failed.{detail}"[:400]}
    if last_success is None:
        return {"level": "BROKEN", "summary": f"BROKEN: no successful {label}PopDay scan has ever been recorded."}
    if run_status == "running" and success_age_h is not None and success_age_h > 3:
        return {"level": "BROKEN", "summary": f"BROKEN: a {label}PopDay scan started but never finished."}
    if success_age_h is not None and success_age_h > 50:
        return {"level": "BROKEN", "summary": f"BROKEN: no successful {label}PopDay scan in over {int(success_age_h)} hours."}
    if success_age_h is not None and success_age_h > 26:
        return {"level": "STALE", "summary": f"STALE: last successful {label}PopDay scan was about {int(success_age_h)} hours ago."}
    # Output canary: a scan can run green yet silently find nothing.
    if coverage.get("level") == "broken":
        return {"level": "BROKEN", "summary": f"BROKEN: {label}{coverage.get('message')}"}
    if coverage.get("level") == "stale":
        return {"level": "STALE", "summary": f"STALE: {label}{coverage.get('message')}"}
    if run_status in {"ok", "running", "dry-run"}:
        source = scan_health.get("last_run_source") or "?"
        if run_status == "running":
            return {"level": "LIVE", "summary": "Healthy. A PopDay scan is currently running; the previous scan succeeded."}
        if run_status == "dry-run":
            # A manual dry-run after the last real success: harmless, and
            # last_success_utc (checked above) is still the honest anchor.
            return {"level": "LIVE", "summary": "Healthy. Latest activity was a manual dry-run; the last real scan succeeded."}
        alerts = latest_run.get("qualifying_alerts_sent") if latest_run else None
        if alerts and alerts not in {"0", ""}:
            return {"level": "LIVE", "summary": f"LIVE: latest PopDay scan succeeded (via {source}) and sent alerts."}
        return {"level": "LIVE", "summary": f"Healthy. Latest PopDay scan succeeded (via {source}); no qualifying alerts."}
    return None


def _health(
    *,
    latest_run: dict[str, str] | None,
    db_status: dict[str, Any],
    err_log: Path,
    now: dt.datetime,
) -> dict[str, str]:
    if not db_status["exists"]:
        return {"level": "BROKEN", "summary": "BROKEN: live PopDay database is missing."}

    # Authoritative source of truth: the scan_runs dead-man's switch,
    # evaluated PER SOURCE and merged worst-first, so a UK outage can never
    # hide behind a healthy US run (or vice versa). Falls back to the older
    # log-parsing logic only when no source has any run history yet.
    sources: dict[str, Any] = db_status.get("sources") or {}
    verdicts: list[dict[str, str]] = []
    for source_id in sorted(sources.keys()):
        entry = sources[source_id] or {}
        verdict = _source_health_verdict(
            entry.get("scan_health") or {},
            entry.get("coverage_health") or {},
            latest_run if source_id == "edgar" else None,
            now,
            label=_SOURCE_LABELS.get(source_id, f"{source_id} "),
        )
        if verdict:
            verdicts.append(verdict)
    if not verdicts:
        # No per-source block (older DB): use the global scan_runs view.
        verdict = _source_health_verdict(
            db_status.get("scan_health") or {},
            db_status.get("coverage_health") or {},
            latest_run,
            now,
        )
        if verdict:
            verdicts.append(verdict)
    if verdicts:
        return max(verdicts, key=lambda v: _LEVEL_RANK.get(v["level"], 0))

    if not latest_run:
        return {"level": "BROKEN", "summary": "BROKEN: no PopDay scan runs found."}

    started = _parse_iso(latest_run.get("started_at"))
    if not started:
        return {"level": "BROKEN", "summary": "BROKEN: latest scan time could not be parsed."}

    age_hours = (now - started).total_seconds() / 3600
    index_status = (latest_run.get("edgar_index_status") or "").strip().lower()
    err_size = err_log.stat().st_size if err_log.exists() else 0

    if age_hours > 42:
        return {"level": "BROKEN", "summary": "BROKEN: no successful PopDay scan in more than 42 hours."}
    if index_status == "error":
        return {"level": "BROKEN", "summary": "BROKEN: latest PopDay scan reported an EDGAR error."}
    if err_size > 0:
        return {"level": "BROKEN", "summary": "BROKEN: launchd error log is not empty."}
    if age_hours > 18:
        return {"level": "STALE", "summary": "STALE: latest PopDay scan is older than expected."}
    if "not yet" in index_status:
        return {"level": "STALE", "summary": "STALE: SEC EDGAR index was not yet available, but the scanner did not crash."}
    if index_status == "available":
        alerts = latest_run.get("qualifying_alerts_sent") or "0"
        if alerts == "0":
            return {
                "level": "LIVE",
                "summary": "Healthy. No qualifying PopDay alerts found in latest scan.",
            }
        return {"level": "LIVE", "summary": "LIVE: latest PopDay scan succeeded and sent alerts."}
    return {"level": "STALE", "summary": "STALE: latest PopDay scan has an unclear EDGAR status."}


def build_status(args: argparse.Namespace) -> dict[str, Any]:
    runtime_dir = args.runtime_dir
    db_path = args.db_path or runtime_dir / "popday.sqlite3"
    out_log = runtime_dir / "logs" / "popday.launchd.out.log"
    err_log = runtime_dir / "logs" / "popday.launchd.err.log"
    now = _utc_now()

    log_lines: list[str] = []
    if db_path.exists():
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            log_lines = _synth_log_lines_from_scan_runs(con, limit=12)
        finally:
            con.close()
    if not log_lines:
        # No scan_runs rows yet (fresh install) or a pre-scan_runs database:
        # fall back to the historical Mac Mini launchd log, if any.
        log_lines = _read_tail(out_log, 600)
    rows = _parse_launchd_lines(log_lines)
    latest_run = rows[-1] if rows else None
    db_status = _database_status(db_path, now)
    latest_alert = db_status.get("latest_alert") or {}
    backup = _backup_status(args.backup_root)
    health = _health(latest_run=latest_run, db_status=db_status, err_log=err_log, now=now)

    return {
        "generated_at": _iso(now),
        "scanner_host": "PythonAnywhere",
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
        "latest_log_tail": log_lines[-24:],
        "latest_error_log_tail": _read_tail(err_log, 12),
        "out_log_path": str(out_log),
        "out_log_modified_at": _log_mtime(out_log),
        "error_log_path": str(err_log),
        "error_log_size_bytes": err_log.stat().st_size if err_log.exists() else 0,
        "error_log_modified_at": _log_mtime(err_log),
        "database_counts": db_status["counts"],
        "scan_health": db_status.get("scan_health"),
        "coverage_health": db_status.get("coverage_health"),
        "last_backup_at": backup["latest_backup_at"],
        "last_backup_path": backup["latest_backup_path"],
        "live_database_backed_up": backup["live_database_backed_up"],
        "retained_backups": backup["retained_backups"],
        "backup_status": backup,
        "sync_status": "generated_on_pythonanywhere",
        "health": health,
        "architecture_note": "Scanner runs on PythonAnywhere scheduled tasks.",
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
