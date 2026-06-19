"""Flask web app for PopDay — public investor day announcement tracker."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for

from popday.config import load_config
from popday.db import Database


app = Flask(__name__)

_admin_password = os.environ.get("POPDAY_ADMIN_PASSWORD", "")
app.secret_key = hashlib.sha256(f"popday:{_admin_password}".encode()).digest()


def _day_suffix(day: int) -> str:
    if 10 <= day % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _friendly_date(value: str) -> str:
    if not value:
        return ""
    try:
        fmt = "%Y%m%d" if value.isdigit() and len(value) == 8 else "%Y-%m-%d"
        parsed = datetime.strptime(value, fmt)
    except ValueError:
        return value
    day = parsed.day
    return f"{day}{_day_suffix(day)} {parsed.strftime('%B %Y')}"


def _sec_filing_url(filing_url: str) -> str:
    """Convert raw SEC .txt URL to the human-readable filing index page."""
    marker = "/Archives/edgar/data/"
    if marker not in filing_url:
        return filing_url
    tail = filing_url.split(marker, 1)[1]
    parts = tail.split("/")
    if len(parts) < 3:
        return filing_url
    cik = parts[0]
    accession_no_dashes = parts[1]
    accession = parts[-1].replace(".txt", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik}/{accession_no_dashes}/{accession}-index.htm"
    )


def _get_db() -> Database:
    config = load_config()
    return Database(config.db_path)


def _prepare_row(row: dict, today: date) -> dict:
    event_date_raw = row.get("event_date") or ""
    try:
        event_date_obj = date.fromisoformat(event_date_raw) if event_date_raw else None
    except ValueError:
        event_date_obj = None

    source_url = row.get("source_url") or ""
    link = _sec_filing_url(source_url) if row.get("source_type") == "EDGAR" else source_url

    return {
        "company_name": row["company_name"],
        "event_type": row["event_type"] or "Investor Day",
        "event_date_display": _friendly_date(event_date_raw),
        "event_date_raw": event_date_raw,
        "is_future": event_date_obj is not None and event_date_obj >= today,
        "source_label": row.get("form_type") or row.get("source_label") or "Source",
        "filing_date_display": _friendly_date(row.get("filing_date") or ""),
        "source_url": link,
    }


@app.route("/")
def index():
    return _render_main_ui(request.args.get("tab", "summary"), is_admin=False)


@app.route("/status")
def public_status():
    return redirect(url_for("index"))


@app.route("/unsubscribe")
def unsubscribe():
    email = request.args.get("email", "").strip().lower()
    token = request.args.get("token", "").strip()
    config = load_config()

    if not (email and token and config.unsubscribe_secret):
        return render_template(
            "unsubscribe.html",
            message="This unsubscribe link is invalid.",
            success=False,
        ), 403

    from popday.unsubscribe import valid_unsubscribe_token
    if not valid_unsubscribe_token(email, token, config.unsubscribe_secret):
        return render_template(
            "unsubscribe.html",
            message="This unsubscribe link is invalid or expired.",
            success=False,
        ), 403

    db = _get_db()
    try:
        db.unsubscribe_alert_recipient(email)
    finally:
        db.close()

    return render_template(
        "unsubscribe.html",
        message=f"{email} has been unsubscribed from PopDay alerts.",
        success=True,
    )


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------

ADMIN_TABS = [
    ("summary", "Summary"),
    ("announcements", "Investor Days"),
    ("rules", "Rules"),
    ("recipients", "Email Alerts"),
    ("health", "System Health"),
    ("candidates", "Candidates"),
    ("filings", "Filings"),
    ("help", "Help"),
]

PUBLIC_TABS = [
    ("summary", "Summary"),
    ("announcements", "Investor Days"),
    ("health", "System Health"),
    ("candidates", "Candidates"),
    ("filings", "Filings"),
    ("help", "Help"),
]

_VALID_ADMIN_TABS = {key for key, _ in ADMIN_TABS}
_VALID_PUBLIC_TABS = {key for key, _ in PUBLIC_TABS}


def _friendly_datetime_str(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value).astimezone()
    except ValueError:
        return value
    day = parsed.day
    return (
        f"{parsed.strftime('%A')} {day}{_day_suffix(day)} "
        f"{parsed.strftime('%B %Y')} {parsed.strftime('%H:%M %Z')}"
    )


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _friendly_datetime_value(value: object) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return "unknown"
    local = parsed.astimezone()
    day = local.day
    return (
        f"{local.strftime('%A')} {day}{_day_suffix(day)} "
        f"{local.strftime('%B %Y')} {local.strftime('%H:%M %Z')}"
    )


def _status_path() -> Path:
    return Path(os.environ.get("POPDAY_STATUS_JSON", "status/popday_status.json"))


def _status_age_note(updated_at: datetime | None) -> str:
    if not updated_at:
        return "No synced status file has been received."
    age_seconds = max(0, (datetime.now(timezone.utc) - updated_at).total_seconds())
    age_minutes = int(age_seconds // 60)
    if age_minutes < 90:
        return f"{age_minutes} minute{'s' if age_minutes != 1 else ''} ago."
    age_hours = int(age_minutes // 60)
    if age_hours < 48:
        return f"{age_hours} hour{'s' if age_hours != 1 else ''} ago."
    age_days = int(age_hours // 24)
    return f"{age_days} day{'s' if age_days != 1 else ''} ago."


def _stale_front_door_summary(updated_at: datetime, *, broken: bool) -> str:
    age = _status_age_note(updated_at).rstrip(".")
    level = "BROKEN" if broken else "STALE"
    return (
        f"{level}: PopDay is not current. "
        f"The browser is showing data last refreshed {age}; "
        "Codex needs to refresh the Mac Mini to PythonAnywhere copy."
    )


def _level_class(level: object) -> str:
    normalized = str(level or "unknown").strip().lower()
    if normalized in {"live", "healthy"}:
        return "live"
    if normalized == "stale":
        return "stale"
    if normalized == "broken":
        return "broken"
    return "unknown"


def _fallback_status(message: str) -> dict:
    return {
        "health": {"level": "BROKEN", "summary": message},
        "public_level": "BROKEN",
        "level_class": "broken",
        "architecture_note": "Scanner runs on Mac Mini. This page shows last synced Mac Mini status.",
        "generated_at_display": "unknown",
        "status_file_updated_at_display": "missing",
        "status_file_age_note": "No synced Mac Mini status file has been received.",
        "latest_scan_started_at_display": "unknown",
        "next_expected_scan_display": _next_scheduled_run(),
        "filing_date_scanned": None,
        "edgar_index_status": None,
        "filings_parsed": None,
        "eight_k_sanity_count": None,
        "qualifying_alerts_sent": None,
        "last_alert_company": None,
        "last_alert_date": None,
        "days_since_last_alert": None,
        "last_alert_filing_url": None,
        "live_database_backed_up": False,
        "last_backup_at": None,
        "retained_backups": 0,
        "database_counts": {},
    }


def _load_public_status() -> dict:
    path = _status_path()
    if not path.exists():
        return _fallback_status(f"BROKEN: no synced Mac Mini status file found at {path}.")
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _fallback_status(f"BROKEN: synced Mac Mini status file could not be read: {exc}.")

    status.setdefault("health", {})
    status["health"].setdefault("level", "UNKNOWN")
    status["health"].setdefault("summary", "Status file loaded, but no health summary was provided.")
    status.setdefault(
        "architecture_note",
        "Scanner runs on Mac Mini. This page shows last synced Mac Mini status.",
    )
    status.setdefault("database_counts", {})

    updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    age_hours = (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600
    level = str(status["health"].get("level") or "UNKNOWN").upper()

    if age_hours > 42:
        level = "BROKEN"
        status["health"]["summary"] = _stale_front_door_summary(updated_at, broken=True)
    elif age_hours > 18 and level == "LIVE":
        level = "STALE"
        status["health"]["summary"] = _stale_front_door_summary(updated_at, broken=False)

    status["public_level"] = "LIVE / HEALTHY" if level == "LIVE" else level
    status["level_class"] = _level_class(level)
    status["front_door_note"] = (
        "This is the PopDay browser front door. If it is stale, Codex should refresh it."
    )
    status["generated_at_display"] = _friendly_datetime_value(status.get("generated_at"))
    status["status_file_updated_at_display"] = _friendly_datetime_value(updated_at.isoformat())
    status["status_file_age_note"] = _status_age_note(updated_at)
    status["latest_scan_started_at_display"] = _friendly_datetime_value(
        status.get("latest_scan_started_at")
    )
    status["next_expected_scan_display"] = _next_scheduled_run()
    return status


def _admin_display_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("_", " ").replace(":", ": ")
    return " ".join(
        word.upper() if word in {"sec", "edgar"} else word for word in text.split()
    ).capitalize()


def _hype_pill_tone(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in {"building", "hyped"}:
        return "ok"
    if status in {"quiet", "non_hyped"}:
        return "neutral"
    return "warn"


def _hype_display(value: object, provisional: object) -> str:
    label = _admin_display_text(value)
    if label and bool(provisional):
        return f"{label} (provisional)"
    return label


def _announcement_sort_settings() -> tuple[str, str]:
    sort_key = request.args.get("sort", "filed").strip().lower()
    direction = request.args.get("direction", "desc").strip().lower()
    if sort_key not in {"filed", "event"}:
        sort_key = "filed"
    if direction not in {"asc", "desc"}:
        direction = "desc"
    return sort_key, direction


def _sort_announcements(items: list[dict], sort_key: str, direction: str) -> list[dict]:
    reverse = direction == "desc"
    items.sort(
        key=lambda item: (
            item["company_name"].casefold(),
            item.get("event_date_raw") or "",
            item.get("filing_date_raw") or "",
        )
    )
    if sort_key == "event":
        items.sort(key=lambda item: item.get("event_date_raw") or "", reverse=reverse)
    else:
        items.sort(key=lambda item: item.get("filing_date_raw") or "", reverse=reverse)
    return items


def _next_scheduled_run() -> str:
    now = datetime.now().astimezone()
    for day_offset in range(8):
        day = now + timedelta(days=day_offset)
        if day.weekday() not in {1, 2, 3, 4, 5}:
            continue
        for hour, minute in [(4, 30), (8, 0)]:
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                d = candidate.day
                return (
                    f"{candidate.strftime('%A')} {d}{_day_suffix(d)} "
                    f"{candidate.strftime('%B %Y')} {candidate.strftime('%H:%M %Z')}"
                )
    d = now.day
    return f"{now.strftime('%A')} {d}{_day_suffix(d)} {now.strftime('%B %Y')} {now.strftime('%H:%M %Z')}"


def _launchd_health_rows_from_lines(lines: list[str]) -> list[dict]:
    runs: list[dict] = []
    current: dict | None = None
    for line in lines:
        if line.startswith("PopDay launchd run started: "):
            if current:
                runs.append(current)
            current = {
                "started": line.replace("PopDay launchd run started: ", "", 1),
                "filing_date": "",
                "index_status": "",
                "filings_parsed": "",
                "eight_k_sanity_count": "",
                "alerts_sent": "",
            }
        elif current and line.startswith("Filing date scanned: "):
            current["filing_date"] = line.replace("Filing date scanned: ", "", 1)
        elif current and line.startswith("EDGAR index status: "):
            current["index_status"] = line.replace("EDGAR index status: ", "", 1)
        elif current and line.startswith("Filings parsed: "):
            current["filings_parsed"] = line.replace("Filings parsed: ", "", 1)
        elif current and line.startswith("8-K sanity count: "):
            current["eight_k_sanity_count"] = line.replace("8-K sanity count: ", "", 1)
        elif current and line.startswith("Qualifying PopDay alerts sent: "):
            current["alerts_sent"] = line.replace("Qualifying PopDay alerts sent: ", "", 1)
    if current:
        runs.append(current)
    return list(reversed(runs[-12:]))


def _check_admin():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login", next=request.full_path))
    return None


def _build_admin_context(db: Database, tab: str) -> dict:
    status = _load_public_status()
    synced_health_rows = _launchd_health_rows_from_lines(status.get("latest_log_tail") or [])
    ctx: dict = {"status": status}
    if tab == "summary":
        latest_run = synced_health_rows[0] if synced_health_rows else None
        latest_alert = db.latest_sent_alert()
        ctx.update(
            announcement_count=db.investor_day_announcement_count(),
            latest_alert=dict(latest_alert) if latest_alert else None,
            next_run=_next_scheduled_run(),
            latest_run_started=_friendly_datetime_str(latest_run["started"]) if latest_run else "",
            latest_run_filing_date=_friendly_date(latest_run["filing_date"]) if latest_run and latest_run["filing_date"] else "—",
            latest_run_index_status=latest_run["index_status"] if latest_run else "—",
            latest_run_alerts_sent=latest_run["alerts_sent"] if latest_run else "0",
        )
    elif tab == "announcements":
        sort_key, direction = _announcement_sort_settings()
        announcements = [
            {
                "company_name": r["company_name"],
                "event_type": r["event_type"] or "Investor Day",
                "event_date": _friendly_date(r["event_date"]) if dict(r).get("event_date") else "—",
                "event_date_raw": dict(r).get("event_date") or "",
                "source_type": dict(r).get("source_type") or "",
                "source": dict(r).get("form_type") or dict(r).get("source_label") or "Source",
                "filing_date": _friendly_date(r["filing_date"]) if dict(r).get("filing_date") else "—",
                "filing_date_raw": dict(r).get("filing_date") or "",
                "matched_phrase": dict(r).get("matched_phrase") or "",
                "alert_sent": bool(dict(r).get("alert_sent")),
                "source_url": (
                    _sec_filing_url(r["source_url"])
                    if dict(r).get("source_type") == "EDGAR"
                    else (dict(r).get("source_url") or "")
                ),
            }
            for r in db.investor_day_announcements()
        ]
        ctx.update(
            announcements=_sort_announcements(announcements, sort_key, direction),
            announcement_sort=sort_key,
            announcement_direction=direction,
        )
    elif tab == "rules":
        from popday.rules import ALERT_REQUIREMENTS
        ctx["rules"] = [dict(r) for r in db.rules()]
        ctx["alert_requirements"] = list(ALERT_REQUIREMENTS)
    elif tab == "recipients":
        ctx["recipients"] = [
            {
                "email": r["email"],
                "active": bool(r["active"]),
                "created": _friendly_datetime_str(r["created_timestamp"]),
            }
            for r in db.alert_recipients()
        ]
        sent_rows = db.conn.execute(
            """
            SELECT company_name, event_type, event_date, alert_sent_timestamp
            FROM detections WHERE alert_sent = 1
            ORDER BY alert_sent_timestamp DESC LIMIT 20
            """
        ).fetchall()
        ctx["sent_alerts"] = [
            {
                "company_name": r["company_name"],
                "event_type": r["event_type"] or "Investor Day",
                "event_date": r["event_date"] or "—",
                "sent_at": _friendly_datetime_str(r["alert_sent_timestamp"]),
            }
            for r in sent_rows
        ]
    elif tab == "health":
        ctx["health_rows"] = [
            {
                "started": _friendly_datetime_str(r["started"]),
                "filing_date": _friendly_date(r["filing_date"]) if dict(r).get("filing_date") else "—",
                "index_status": dict(r).get("index_status") or "—",
                "filings_parsed": dict(r).get("filings_parsed") or "—",
                "eight_k_sanity_count": dict(r).get("eight_k_sanity_count") or "—",
                "alerts_sent": dict(r).get("alerts_sent") or "0",
            }
            for r in synced_health_rows
        ]
        ctx["next_run"] = _next_scheduled_run()
    elif tab == "candidates":
        ctx["candidates"] = [
            {
                "id": r["id"],
                "created": _friendly_datetime_str(r["created_timestamp"]),
                "status": r["status"] or "",
                "status_display": _admin_display_text(r["status"]),
                "company_name": r["company_name"],
                "matched_phrase": dict(r).get("matched_phrase") or "",
                "event_date": _friendly_date(r["event_date"]) if dict(r).get("event_date") else "—",
                "matched_location": _admin_display_text(dict(r).get("matched_location")),
                "reason": _admin_display_text(dict(r).get("dismissal_reason") or "alert ready"),
                "sec_url": _sec_filing_url(r["filing_url"]) if dict(r).get("filing_url") else "",
                "hype_status": dict(r).get("hype_status") or "",
                "hype_status_display": _hype_display(
                    dict(r).get("hype_status"),
                    dict(r).get("provisional"),
                ),
                "hype_count": dict(r).get("qualifying_count"),
                "hype_tone": _hype_pill_tone(dict(r).get("hype_status")),
                "hype_provisional": bool(dict(r).get("provisional")),
            }
            for r in db.recent_candidates()
        ]
    elif tab == "filings":
        ctx["filings"] = [
            {
                "processed": _friendly_datetime_str(r["processed_timestamp"]),
                "form_type": r["form_type"],
                "company_name": r["company_name"],
                "accession_number": r["accession_number"],
                "filing_date": _friendly_date(r["filing_date"]) if dict(r).get("filing_date") else "—",
            }
            for r in db.recent_processed()
        ]
    return ctx


def _render_main_ui(tab: str, *, is_admin: bool) -> str:
    valid_tabs = _VALID_ADMIN_TABS if is_admin else _VALID_PUBLIC_TABS
    if tab not in valid_tabs:
        tab = "summary"
    config = load_config()
    db = Database(config.db_path)
    try:
        ctx = _build_admin_context(db, tab)
    finally:
        db.close()
    return render_template(
        "admin.html",
        active_tab=tab,
        tabs=ADMIN_TABS if is_admin else PUBLIC_TABS,
        is_admin=is_admin,
        **ctx,
    )


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    admin_pw = os.environ.get("POPDAY_ADMIN_PASSWORD", "")
    if not admin_pw:
        return render_template(
            "admin_login.html",
            error="Admin access is not configured. Set the POPDAY_ADMIN_PASSWORD environment variable.",
            disabled=True,
        )
    error = None
    next_path = request.args.get("next", "")
    if not next_path.startswith("/admin/"):
        next_path = url_for("admin_tab", tab="summary")
    if request.method == "POST":
        provided = request.form.get("password", "")
        if secrets.compare_digest(provided.encode(), admin_pw.encode()):
            session["admin_authenticated"] = True
            return redirect(next_path)
        error = "Incorrect password."
    return render_template("admin_login.html", error=error, disabled=False)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin_index():
    auth = _check_admin()
    if auth is not None:
        return auth
    return redirect(url_for("admin_tab", tab="summary"))


@app.route("/admin/<tab>")
def admin_tab(tab):
    if tab == "candidates":
        return _render_main_ui("candidates", is_admin=False)
    auth = _check_admin()
    if auth is not None:
        return auth
    return _render_main_ui(tab, is_admin=True)


@app.route("/admin/rules/add", methods=["POST"])
def admin_rules_add():
    auth = _check_admin()
    if auth is not None:
        return auth
    rule_type = request.form.get("rule_type", "")
    phrase = request.form.get("phrase", "").strip()
    description = request.form.get("description", "").strip()
    if rule_type in {"include", "routine_context"} and phrase:
        if not description:
            description = (
                "Qualifying investor-event phrase."
                if rule_type == "include"
                else "Context-sensitive routine phrase; not an automatic exclusion."
            )
        db = _get_db()
        try:
            db.add_rule(rule_type, phrase, description)
        finally:
            db.close()
    return redirect(url_for("admin_tab", tab="rules"))


@app.route("/admin/rules/delete", methods=["POST"])
def admin_rules_delete():
    auth = _check_admin()
    if auth is not None:
        return auth
    rule_type = request.form.get("rule_type", "")
    phrase = request.form.get("phrase", "")
    if rule_type in {"include", "routine_context"} and phrase:
        db = _get_db()
        try:
            db.delete_rule(rule_type, phrase)
        finally:
            db.close()
    return redirect(url_for("admin_tab", tab="rules"))


@app.route("/admin/recipients/add", methods=["POST"])
def admin_recipients_add():
    auth = _check_admin()
    if auth is not None:
        return auth
    email = request.form.get("email", "").strip().lower()
    if "@" in email and "." in email:
        db = _get_db()
        try:
            db.add_alert_recipient(email)
        finally:
            db.close()
    return redirect(url_for("admin_tab", tab="recipients"))


@app.route("/admin/recipients/unsubscribe", methods=["POST"])
def admin_recipients_unsubscribe():
    auth = _check_admin()
    if auth is not None:
        return auth
    email = request.form.get("email", "").strip().lower()
    if email:
        db = _get_db()
        try:
            db.unsubscribe_alert_recipient(email)
        finally:
            db.close()
    return redirect(url_for("admin_tab", tab="recipients"))


@app.route("/admin/recipients/reactivate", methods=["POST"])
def admin_recipients_reactivate():
    auth = _check_admin()
    if auth is not None:
        return auth
    email = request.form.get("email", "").strip().lower()
    if email:
        db = _get_db()
        try:
            db.reactivate_alert_recipient(email)
        finally:
            db.close()
    return redirect(url_for("admin_tab", tab="recipients"))


@app.route("/admin/candidate/<int:detection_id>")
def admin_candidate(detection_id):
    auth = _check_admin()
    if auth is not None:
        return auth
    config = load_config()
    db = Database(config.db_path)
    try:
        row = db.detection(detection_id)
    finally:
        db.close()
    if not row:
        return redirect(url_for("admin_tab", tab="candidates"))

    from popday.edgar_fetch import EdgarClient
    from popday.filing_parser import normalize_text, parse_sec_filing

    sec_url = _sec_filing_url(row["filing_url"])
    preview = ""
    fetch_error = ""
    items: list[str] = []
    try:
        client = EdgarClient(config.sec_user_agent, config.request_delay_seconds)
        raw = client.get_text(row["filing_url"])
        parsed = parse_sec_filing(raw)
        items = parsed.items
        plain = normalize_text("\n\n".join(parsed.press_releases) or parsed.cover_text)
        snippet = row["snippet"] or ""
        if snippet:
            anchor = snippet[:80].lower()
            idx = plain.lower().find(anchor)
            if idx != -1:
                start = max(0, idx - 1200)
                end = min(len(plain), idx + 3500)
                preview = plain[start:end]
        if not preview:
            preview = plain[:5000]
        if not preview:
            preview = "No readable filing text could be extracted."
    except Exception as exc:
        fetch_error = str(exc)

    return render_template(
        "admin_candidate.html",
        row=dict(row),
        preview=preview,
        fetch_error=fetch_error,
        sec_url=sec_url,
        status_display=_admin_display_text(row["status"]),
        reason_display=_admin_display_text(row["dismissal_reason"] or "alert ready"),
        filing_date_display=_friendly_date(row["filing_date"]) if dict(row).get("filing_date") else "—",
        items=items or json.loads(row["items_json"] or "[]"),
    )


if __name__ == "__main__":
    app.run(debug=True)
