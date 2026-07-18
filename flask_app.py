"""Flask web app for PopDay — public investor day announcement tracker."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for

from popday.company_websites import resolve_company_website
from popday.config import DEFAULT_COMPANY_WEBSITES, load_config
from popday.db import Database


app = Flask(__name__)

_admin_password = os.environ.get("POPDAY_ADMIN_PASSWORD", "")
app.secret_key = hashlib.sha256(f"popday:{_admin_password}".encode()).digest()


def _day_suffix(day: int) -> str:
    if 10 <= day % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


# Month abbreviations Jason prefers: Sept (never September), Sep is not used.
_MONTH_ABBR = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sept", "Oct", "Nov", "Dec",
)


def _abbrev_month_year(value) -> str:
    """Abbreviated month + year, e.g. 'Sept 2026'."""
    return f"{_MONTH_ABBR[value.month]} {value.year}"


# Weekday abbreviations Jason prefers: Weds (not Wed), Thurs, Tues, etc.
_WEEKDAY_ABBR = ("Mon", "Tues", "Weds", "Thurs", "Fri", "Sat", "Sun")


def _abbrev_weekday(value) -> str:
    """Abbreviated weekday, e.g. 'Weds'."""
    return _WEEKDAY_ABBR[value.weekday()]


_PRICE_SOURCE_LABELS = {
    "yahoo_chart_daily_json": "Yahoo (daily)",
    "stooq_csv": "Stooq (daily)",
}


# Corporate suffixes: SEC/RNS source data casing is inconsistent (e.g. all
# caps "COMMERCIAL METALS Co"), so these render in one canonical casing.
_COMPANY_NAME_SUFFIX_EXCEPTIONS = {
    "inc": "Inc", "inc.": "Inc.", "co": "Co", "co.": "Co.",
    "corp": "Corp", "corp.": "Corp.", "llc": "LLC", "ltd": "Ltd",
    "ltd.": "Ltd.", "plc": "plc", "lp": "LP", "llp": "LLP",
    "nv": "N.V.", "sa": "S.A.", "ag": "AG", "se": "SE",
}


def _titleize_word(word: str) -> str:
    return "-".join(part[:1].upper() + part[1:].lower() if part else part for part in word.split("-"))


def _display_company_token(token: str) -> str:
    key = token.lower()
    if key in _COMPANY_NAME_SUFFIX_EXCEPTIONS:
        return _COMPANY_NAME_SUFFIX_EXCEPTIONS[key]
    if token.isupper() and token.isalpha() and len(token) <= 5:
        return token  # likely acronym (e.g. "ADI") - preserve as-is
    return _titleize_word(token)


def _display_company_name(raw: object) -> str:
    """Display-only casing normalization; never mutates the stored raw name."""
    name = str(raw or "").strip()
    if not name:
        return name
    words = []
    for word in name.split(" "):
        # Compound tokens like "LIMITED/NV" are two casing decisions, not one.
        words.append("/".join(_display_company_token(part) for part in word.split("/")))
    return " ".join(words)


def _price_source_label(value: object) -> str:
    """Friendly, short label for the price data source."""
    raw = str(value or "").strip()
    if not raw:
        return "—"
    return _PRICE_SOURCE_LABELS.get(raw, raw)


def _interval_return_average(rows) -> str:
    """Average (mean) interval return across a group of Price Reaction rows."""
    values = [row["interval_return_raw"] for row in rows if row.get("interval_return_raw") is not None]
    if not values:
        return "—"
    return _pct_text(sum(values) / len(values))


def _event_day_return_average(rows) -> str:
    """Average (mean) Investor Day close move across a group of Price Reaction rows."""
    values = [row["event_day_move_raw"] for row in rows if row.get("event_day_move_raw") is not None]
    if not values:
        return "—"
    return _pct_text(sum(values) / len(values))


def _friendly_date(value: str) -> str:
    if not value:
        return ""
    try:
        fmt = "%Y%m%d" if value.isdigit() and len(value) == 8 else "%Y-%m-%d"
        parsed = datetime.strptime(value, fmt)
    except ValueError:
        return value
    day = parsed.day
    return f"{day}{_day_suffix(day)} {_abbrev_month_year(parsed)}"


def _sec_filing_url(filing_url: str) -> str:
    """Convert raw SEC .txt URL to the human-readable filing index page."""
    marker = "/Archives/edgar/data/"
    if marker not in filing_url or not filing_url.endswith(".txt"):
        return filing_url
    tail = filing_url.split(marker, 1)[1]
    parts = tail.split("/")
    if len(parts) < 2:
        return filing_url
    cik = parts[0]
    accession = parts[-1].replace(".txt", "")
    accession_no_dashes = parts[1] if len(parts) >= 3 else accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik}/{accession_no_dashes}/{accession}-index.htm"
    )


def _company_website(
    company_name: object,
    company_websites: dict[str, str] | None = None,
    cik: object = None,
    edgar_websites: dict[str, str] | None = None,
) -> str:
    """Curated (config.json override, then DEFAULT_COMPANY_WEBSITES) always
    wins; EDGAR's self-reported website (keyed by CIK, captured at scan time
    - see popday/company_websites.py) only fills a gap curation hasn't
    reached; otherwise empty, same as always - unlinked plain text, never a
    guessed link."""
    return resolve_company_website(
        company_name, cik, company_websites or DEFAULT_COMPANY_WEBSITES, edgar_websites
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
        "company_name": _display_company_name(row["company_name"]),
        "company_url": _company_website(row["company_name"]),
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
    return _render_main_ui(request.args.get("tab", "price_reaction"), is_admin=False)


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
    ("price_reaction", "Price Reaction"),
    ("announcements", "Investor Days"),
    ("research", "Research / Hype"),
    ("rules", "Rules"),
    ("recipients", "Email Alerts"),
    ("candidates", "Scan Log"),
    ("summary", "System Health"),
    ("help", "Help"),
]

PUBLIC_TABS = [
    ("price_reaction", "Price Reaction"),
    ("announcements", "Investor Days"),
    ("research", "Research / Hype"),
    ("candidates", "Scan Log"),
    ("summary", "System Health"),
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
        f"{_abbrev_weekday(parsed)} {day}{_day_suffix(day)} "
        f"{_abbrev_month_year(parsed)} {parsed.strftime('%H:%M %Z')}"
    )


def _friendly_source_datetime_str(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    zone = parsed.tzname() or ""
    if zone in {"UTC-04:00", "UTC-05:00"}:
        zone = "ET"
    day = parsed.day
    return (
        f"{_abbrev_weekday(parsed)} {day}{_day_suffix(day)} "
        f"{_abbrev_month_year(parsed)} {parsed.strftime('%H:%M')} {zone}".strip()
    )


def _friendly_source_datetime_parts(value: str) -> tuple[str, str]:
    """Split an EDGAR acceptance datetime into (date line, time line)."""
    if not value:
        return "", ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value, ""
    zone = parsed.tzname() or ""
    if zone in {"UTC-04:00", "UTC-05:00"}:
        zone = "ET"
    day = parsed.day
    date_line = f"{_abbrev_weekday(parsed)} {day}{_day_suffix(day)} {_abbrev_month_year(parsed)}"
    time_line = f"{parsed.strftime('%H:%M')} {zone}".strip()
    return date_line, time_line


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
        return "—"
    local = parsed.astimezone()
    day = local.day
    return (
        f"{_abbrev_weekday(local)} {day}{_day_suffix(day)} "
        f"{_abbrev_month_year(local)} {local.strftime('%H:%M %Z')}"
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
        "Codex needs to refresh PopDay's status."
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
        "architecture_note": "Scanner runs on PythonAnywhere scheduled tasks.",
        "generated_at_display": "—",
        "status_file_updated_at_display": "missing",
        "status_file_age_note": "No PopDay status file was found.",
        "latest_scan_started_at_display": "—",
        "next_expected_scan_display": _next_scheduled_run(),
        "filing_date_scanned": None,
        "edgar_index_status": None,
        "filings_parsed": None,
        "eight_k_sanity_count": None,
        "qualifying_alerts_sent": None,
        "last_alert_company": None,
        "last_alert_cik": None,
        "last_alert_date": None,
        "days_since_last_alert": None,
        "last_alert_filing_url": None,
        "missing_company_websites": None,
        "live_database_backed_up": False,
        "last_backup_at": None,
        "retained_backups": 0,
        "database_counts": {},
        "scan_alert": {
            "active": True,
            "level": "danger",
            "headline": "Status unavailable — cannot confirm scan health.",
            "detail": message,
            "last_success_display": "—",
        },
    }


def _load_public_status() -> dict:
    path = _status_path()
    if not path.exists():
        return _fallback_status(f"BROKEN: no PopDay status file found at {path}.")
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _fallback_status(f"BROKEN: PopDay status file could not be read: {exc}.")

    status.setdefault("health", {})
    status["health"].setdefault("level", "UNKNOWN")
    status["health"].setdefault("summary", "Status file loaded, but no health summary was provided.")
    status.setdefault(
        "architecture_note",
        "Scanner runs on PythonAnywhere scheduled tasks.",
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
    status["generated_at_display"] = _friendly_datetime_value(status.get("generated_at"))
    status["status_file_updated_at_display"] = _friendly_datetime_value(updated_at.isoformat())
    status["status_file_age_note"] = _status_age_note(updated_at)
    status["latest_scan_started_at_display"] = _friendly_datetime_value(
        status.get("latest_scan_started_at")
    )
    status["next_expected_scan_display"] = _next_scheduled_run()
    _scan_health = status.get("scan_health") or {}
    # Highest-severity banner wins: a hard scan failure (red) outranks the output
    # canary, which itself can be red (discovery down) or amber (candidates stale).
    # Evaluated per discovery source when the status file carries the per-source
    # block (UK extension), so a UK outage banners even while US runs green and
    # vice versa; older status files fall back to the global view unchanged.
    per_source: dict = status.get("sources") or {}
    if per_source:
        source_banner_labels = {"edgar": "", "investegate": "UK scan: "}
        level_rank = {"danger": 2, "warning": 1}
        best: dict | None = None
        for source_id in sorted(per_source.keys()):
            entry = per_source[source_id] or {}
            candidate = _scan_alert(entry.get("scan_health") or {}) or _coverage_alert(
                entry.get("coverage_health") or {}
            )
            if not candidate:
                continue
            prefix = source_banner_labels.get(source_id, f"{source_id}: ")
            if prefix:
                candidate = candidate | {
                    "headline": f"{prefix}{candidate.get('headline', '')}"
                }
            if best is None or level_rank.get(candidate.get("level"), 0) > level_rank.get(
                best.get("level"), 0
            ):
                best = candidate
        status["scan_alert"] = best or {"active": False}
    else:
        status["scan_alert"] = _scan_alert(_scan_health) or _coverage_alert(
            status.get("coverage_health") or {}
        ) or {"active": False}
    status["last_successful_scan_display"] = (
        _friendly_datetime_value(_scan_health.get("last_success_utc"))
        if _scan_health.get("last_success_utc")
        else "never"
    )
    status["last_scan_source"] = _scan_health.get("last_run_source") or "—"
    status["recovered_days"] = [
        _friendly_date(str(d)) for d in (_scan_health.get("recovered_dates") or [])
    ]
    _coverage = status.get("coverage_health") or {}
    status["last_candidate_display"] = (
        _friendly_datetime_value(_coverage.get("last_candidate_utc"))
        if _coverage.get("last_candidate_utc")
        else "never"
    )
    return status


def _scan_alert(scan_health: dict) -> dict | None:
    """Blunt scan-health banner (red) shown on every tab, or None if healthy.

    Process check: a silently-dead scan is impossible to hide — if the last run
    failed, or no scan has succeeded in over 26 hours, every tab says why.
    """
    if not scan_health.get("table_present"):
        return None

    run_status = str(scan_health.get("last_run_status") or "").strip().lower()
    error = str(scan_health.get("last_run_error") or "").strip()
    last_success = _parse_datetime(scan_health.get("last_success_utc"))
    last_success_display = (
        _friendly_datetime_value(scan_health.get("last_success_utc"))
        if scan_health.get("last_success_utc")
        else "never"
    )
    age_hours = (
        (datetime.now(timezone.utc) - last_success).total_seconds() / 3600
        if last_success
        else None
    )

    if run_status == "failed":
        detail = f" {error}" if error else ""
        return {
            "active": True,
            "level": "danger",
            "headline": "Scan failed — data may be out of date.",
            "detail": f"The last automated PopDay scan did not complete.{detail}",
            "last_success_display": last_success_display,
        }
    if last_success is None:
        return {
            "active": True,
            "level": "danger",
            "headline": "No successful scan recorded yet.",
            "detail": "PopDay has not completed a successful scan on this database.",
            "last_success_display": last_success_display,
        }
    if age_hours is not None and age_hours > 26:
        return {
            "active": True,
            "level": "danger",
            "headline": "Scan is overdue — data may be out of date.",
            "detail": f"The last successful PopDay scan was about {int(age_hours)} hours ago.",
            "last_success_display": last_success_display,
        }
    return None


def _coverage_alert(coverage_health: dict) -> dict | None:
    """Output-canary banner, or None. Catches a scan that runs green but finds
    nothing: 'broken' (discovery down) is red, 'stale' (no new candidates) amber.
    """
    level = str(coverage_health.get("level") or "").strip().lower()
    message = str(coverage_health.get("message") or "").strip()
    if level == "broken":
        return {
            "active": True,
            "level": "danger",
            "headline": "PopDay may have stopped finding filings.",
            "detail": message or "Recent scans discovered no filings.",
            "last_success_display": None,
        }
    if level == "stale":
        return {
            "active": True,
            "level": "warn",
            "headline": "No new candidates recently — worth a look.",
            "detail": message or "No new investor-day candidate in a while.",
            "last_success_display": None,
        }
    return None


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


def _matched_phrase_display(value: object) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    return f'"{text[:1].upper()}{text[1:]}"'


def _parse_date_for_delta(value: object) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _unknown_text(value: object) -> str:
    text = str(value or "").strip()
    return text or "—"


def _money_text(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _money_text_for_market(value: object, market: object) -> str:
    """Currency symbol per row market: $ for US rows, £ for UK rows."""
    text = _money_text(value)
    if str(market or "US") == "UK" and text.startswith("$"):
        return f"£{text[1:]}"
    return text


_VALID_MARKETS = {"US", "UK"}


def _current_market() -> str | None:
    """The ?market= filter (All when absent/invalid), carried across links."""
    value = str(request.args.get("market") or "").strip().upper()
    return value if value in _VALID_MARKETS else None


def _pct_text(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _research_payload(value: object) -> list[dict]:
    try:
        payload = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _research_item_count(payload: list[dict], item_code: str) -> int | None:
    if payload is None:
        return None
    total = 0
    for filing in payload:
        codes = filing.get("item_codes") or []
        if any(str(code).strip() == item_code for code in codes):
            total += 1
    return total


def _research_presentation_count(payload: list[dict]) -> int | None:
    if payload is None:
        return None
    hints = ("presentation", "deck", "slides", "investor-day")
    total = 0
    for filing in payload:
        combined = " ".join(
            str(filing.get(key) or "")
            for key in ("primary_document", "source_url")
        ).lower()
        if any(hint in combined for hint in hints):
            total += 1
    return total


def _latest_known_hype_filing(payload: list[dict]) -> str:
    dated = [filing for filing in payload if filing.get("filing_date")]
    if not dated:
        return "—"
    latest = max(dated, key=lambda filing: str(filing.get("filing_date") or ""))
    date_label = _friendly_date(str(latest.get("filing_date") or ""))
    form = str(latest.get("form") or "").strip()
    codes = ", ".join(str(code) for code in latest.get("item_codes") or [])
    return " ".join(part for part in [date_label, form, codes] if part).strip() or "—"


def _announcement_sort_settings() -> tuple[str, str]:
    sort_key = request.args.get("sort", "filed").strip().lower()
    direction = request.args.get("direction", "desc").strip().lower()
    if sort_key not in {"filed", "event", "hype"}:
        sort_key = "filed"
    if direction not in {"asc", "desc"}:
        direction = "desc"
    return sort_key, direction


def _research_sort_settings() -> tuple[str, str]:
    sort_key = request.args.get("sort", "investor_comms").strip().lower()
    direction = request.args.get("direction", "desc").strip().lower()
    valid = {
        "company", "ticker", "event_type", "ad", "id", "days", "raw",
        "investor_comms", "item_701", "item_801", "presentation",
        "latest", "source",
    }
    if sort_key not in valid:
        sort_key = "investor_comms"
    if direction not in {"asc", "desc"}:
        direction = "desc"
    return sort_key, direction


def _research_number_sort_value(value: object) -> int | None:
    return int(value) if value is not None else None


def _sort_research_rows(items: list[dict], sort_key: str, direction: str) -> list[dict]:
    def missing_last_number(item: dict, key: str) -> tuple[bool, int]:
        value = item.get(key)
        number = -1 if value is None else int(value)
        return value is None, -number if direction == "desc" else number

    text_keys = {
        "company": "company_name",
        "ticker": "ticker",
        "event_type": "event_type",
        "ad": "ad_date_raw",
        "id": "id_date_raw",
        "latest": "latest_filing",
        "source": "source_label",
    }
    number_keys = {
        "days": "days_sort",
        "raw": "raw_hype_count_sort",
        "investor_comms": "investor_comms_count_sort",
        "item_701": "item_701_count_sort",
        "item_801": "item_801_count_sort",
        "presentation": "presentation_count_sort",
    }
    items.sort(key=lambda item: (item["company_name"].casefold(), item.get("id_date_raw") or ""))
    if sort_key == "investor_comms" and direction == "desc":
        items.sort(
            key=lambda item: (
                item["investor_comms_count_sort"] is None,
                -(item["investor_comms_count_sort"] or -1),
                item["raw_hype_count_sort"] is None,
                -(item["raw_hype_count_sort"] or -1),
            )
        )
    elif sort_key in number_keys:
        items.sort(key=lambda item: missing_last_number(item, number_keys[sort_key]))
    else:
        key = text_keys[sort_key]
        items.sort(key=lambda item: str(item.get(key) or "").casefold(), reverse=direction == "desc")
    return items


def _sort_announcements(items: list[dict], sort_key: str, direction: str) -> list[dict]:
    reverse = direction == "desc"
    items.sort(
        key=lambda item: (
            item["company_name"].casefold(),
            item.get("event_date_raw") or "",
            item.get("filing_date_raw") or "",
        )
    )
    if sort_key == "hype":
        items.sort(key=lambda item: item.get("hype_count_sort", -1), reverse=reverse)
    elif sort_key == "event":
        items.sort(key=lambda item: item.get("event_date_raw") or "", reverse=reverse)
    else:
        items.sort(key=lambda item: item.get("filing_date_raw") or "", reverse=reverse)
    return items


def _is_upcoming_announcement(item: dict, today: date | None = None) -> bool:
    event_date = item.get("event_date_raw") or ""
    if not event_date:
        return False
    try:
        parsed = date.fromisoformat(str(event_date))
    except ValueError:
        return False
    return parsed >= (today or date.today())


def _next_scheduled_run() -> str:
    # PythonAnywhere's schedule API has no weekday selector, so (unlike the
    # old Mac Mini launchd job, which skipped Sun/Mon) these tasks fire every
    # day at 04:00 and 07:00 UTC.
    now_utc = datetime.now(timezone.utc)
    for day_offset in range(4):
        day = now_utc + timedelta(days=day_offset)
        for hour, minute in [(4, 0), (7, 0)]:
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now_utc:
                local = candidate.astimezone()
                d = local.day
                return (
                    f"{_abbrev_weekday(local)} {d}{_day_suffix(d)} "
                    f"{_abbrev_month_year(local)} {local.strftime('%H:%M %Z')}"
                )
    local = now_utc.astimezone()
    d = local.day
    return f"{_abbrev_weekday(local)} {d}{_day_suffix(d)} {_abbrev_month_year(local)} {local.strftime('%H:%M %Z')}"


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


def _build_admin_context(
    db: Database,
    tab: str,
    *,
    company_websites: dict[str, str],
    edgar_websites: dict[str, str] | None = None,
    market: str | None = None,
) -> dict:
    edgar_websites = edgar_websites or {}
    status = _load_public_status()
    if status.get("last_alert_company"):
        status["last_alert_company_url"] = _company_website(
            status.get("last_alert_company"),
            company_websites,
            status.get("last_alert_cik"),
            edgar_websites,
        )
    synced_health_rows = _launchd_health_rows_from_lines(status.get("latest_log_tail") or [])
    ctx: dict = {"status": status, "active_market": market}
    if tab == "summary":
        latest_run = synced_health_rows[0] if synced_health_rows else None
        ctx.update(
            announcement_count=db.investor_day_announcement_count(),
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
                "company_name": _display_company_name(r["company_name"]),
                "company_url": _company_website(
                    r["company_name"], company_websites, dict(r).get("cik"), edgar_websites
                ),
                "event_type": r["event_type"] or "Investor Day",
                "event_date": _friendly_date(r["event_date"]) if dict(r).get("event_date") else "Date TBD",
                "event_date_raw": dict(r).get("event_date") or "",
                "is_tbd": not dict(r).get("event_date"),
                "source_type": dict(r).get("source_type") or "",
                "filing_date": _friendly_date(r["filing_date"]) if dict(r).get("filing_date") else "—",
                "filing_date_raw": dict(r).get("filing_date") or "",
                "hype_count": (
                    int(dict(r).get("hype_count"))
                    if dict(r).get("hype_count") is not None
                    else None
                ),
                "hype_count_sort": (
                    int(dict(r).get("hype_count"))
                    if dict(r).get("hype_count") is not None
                    else -1
                ),
                "matched_phrase": _matched_phrase_display(dict(r).get("matched_phrase")),
                "alert_sent": bool(dict(r).get("alert_sent")),
                "source_url": dict(r).get("evidence_url")
                or (
                    _sec_filing_url(r["source_url"])
                    if dict(r).get("source_type") == "EDGAR"
                    else (dict(r).get("source_url") or "")
                ),
                "source_link_label": dict(r).get("evidence_label") or "Source",
            }
            for r in db.investor_day_announcements(market)
        ]
        sorted_announcements = _sort_announcements(announcements, sort_key, direction)
        # A date-TBD announcement is a future event with no firm date yet, so it belongs
        # under Upcoming rather than Legacy.
        upcoming_announcements = [
            item for item in sorted_announcements
            if item.get("is_tbd") or _is_upcoming_announcement(item)
        ]
        legacy_announcements = [
            item for item in sorted_announcements
            if not item.get("is_tbd") and not _is_upcoming_announcement(item)
        ]
        ctx.update(
            announcements=sorted_announcements,
            upcoming_announcements=upcoming_announcements,
            legacy_announcements=legacy_announcements,
            announcement_sort=sort_key,
            announcement_direction=direction,
        )
    elif tab == "research":
        sort_key, direction = _research_sort_settings()
        research_rows = []
        for r in db.research_hype_events(market):
            row = dict(r)
            ad_raw = row.get("announcement_date") or ""
            id_raw = row.get("event_date") or ""
            ad_date = _parse_date_for_delta(ad_raw)
            id_date = _parse_date_for_delta(id_raw)
            days = (id_date - ad_date).days if ad_date and id_date else None
            has_hype_data = row.get("investor_comms_count") is not None
            payload = _research_payload(row.get("detected_json")) if has_hype_data else None
            item_701_count = _research_item_count(payload, "7.01") if payload is not None else None
            item_801_count = _research_item_count(payload, "8.01") if payload is not None else None
            presentation_count = (
                _research_presentation_count(payload) if payload is not None else None
            )
            source_url = row.get("evidence_url") or row.get("source_url") or ""
            source_label = row.get("evidence_label") or row.get("source_type") or "Source"
            research_rows.append(
                {
                    "company_name": _display_company_name(row.get("company_name") or ""),
                    "company_url": _company_website(
                        row.get("company_name"), company_websites, row.get("cik"), edgar_websites
                    ),
                    "ticker": _unknown_text(row.get("ticker")),
                    "event_type": row.get("event_type") or "Investor Day",
                    "ad_date": _friendly_date(str(ad_raw)) if ad_raw else "—",
                    "ad_date_raw": str(ad_raw),
                    "id_date": _friendly_date(str(id_raw)) if id_raw else "—",
                    "id_date_raw": str(id_raw),
                    "days": days if days is not None else "—",
                    "days_sort": days,
                    "raw_hype_count": "—",
                    "raw_hype_count_sort": None,
                    "investor_comms_count": (
                        int(row["investor_comms_count"]) if has_hype_data else "—"
                    ),
                    "investor_comms_count_sort": (
                        int(row["investor_comms_count"]) if has_hype_data else None
                    ),
                    "item_701_count": item_701_count if item_701_count is not None else "—",
                    "item_701_count_sort": item_701_count,
                    "item_801_count": item_801_count if item_801_count is not None else "—",
                    "item_801_count_sort": item_801_count,
                    "presentation_count": (
                        presentation_count if presentation_count is not None else "—"
                    ),
                    "presentation_count_sort": presentation_count,
                    "latest_filing": _latest_known_hype_filing(payload) if payload else "—",
                    "source_url": source_url,
                    "source_label": source_label,
                }
            )
        sorted_research_rows = _sort_research_rows(research_rows, sort_key, direction)
        upcoming_research_rows = [
            item for item in sorted_research_rows if _is_upcoming_announcement(
                {"event_date_raw": item.get("id_date_raw") or ""}
            )
        ]
        legacy_research_rows = [
            item for item in sorted_research_rows if not _is_upcoming_announcement(
                {"event_date_raw": item.get("id_date_raw") or ""}
            )
        ]
        ctx.update(
            research_rows=sorted_research_rows,
            upcoming_research_rows=upcoming_research_rows,
            legacy_research_rows=legacy_research_rows,
            research_sort=sort_key,
            research_direction=direction,
            research_unknown_note=(
                "Research counts are shown only where PopDay has enough data. Unknown means "
                "the event has not been backfilled yet. Upcoming means the Investor Day has "
                "not passed; Legacy means it has passed."
            ),
        )
    elif tab == "price_reaction":
        price_reaction_rows = [
            {
                "company_name": _display_company_name(r["company_name"]),
                "company_url": _company_website(
                    r["company_name"], company_websites, dict(r).get("cik"), edgar_websites
                ),
                "ticker": _unknown_text(r["ticker"]),
                "event_date_raw": dict(r).get("event_date") or "",
                "event_date": _friendly_date(r["event_date"]) if dict(r).get("event_date") else "Date TBD",
                "filing_date": _friendly_date(r["filing_date"]) if dict(r).get("filing_date") else "—",
                "accepted": (_friendly_source_datetime_parts(r["acceptance_datetime"])[0] if dict(r).get("acceptance_datetime") else "—"),
                "accepted_time": (_friendly_source_datetime_parts(r["acceptance_datetime"])[1] if dict(r).get("acceptance_datetime") else ""),
                "reaction_date": _friendly_date(r["reaction_date"]) if dict(r).get("reaction_date") else "—",
                "market": dict(r).get("market") or "US",
                "previous_close": _money_text_for_market(r["previous_close"], dict(r).get("market")),
                "reaction_close": _money_text_for_market(r["reaction_close"], dict(r).get("market")),
                "event_day_close": _money_text_for_market(r["event_day_close"], dict(r).get("market")) if dict(r).get("event_day_close") is not None else "—",
                "event_day_move_pct": _pct_text(r["event_day_move_pct"]) if dict(r).get("event_day_move_pct") is not None else "",
                "event_day_move_raw": dict(r).get("event_day_move_pct"),
                "event_day_direction": (
                    "up" if (dict(r).get("event_day_move_pct") or 0) > 0
                    else ("down" if (dict(r).get("event_day_move_pct") or 0) < 0 else "")
                ),
                "announcement_move_pct": _pct_text(r["announcement_move_pct"]),
                "intraday_range_pct": _pct_text(r["intraday_range_pct"]),
                "latest_close_date": _friendly_date(r["latest_close_date"]) if dict(r).get("latest_close_date") else "—",
                "latest_close": _money_text_for_market(r["latest_close"], dict(r).get("market")),
                "interval_return_pct": _pct_text(r["interval_return_pct"]),
                "interval_return_raw": r["interval_return_pct"],
                "interval_daily_volatility_pct": _pct_text(r["interval_daily_volatility_pct"]),
                "status": _admin_display_text(r["status"]),
                "notes": r["notes"] or "",
                "source": _price_source_label(r["price_data_source"]),
                "updated": _friendly_datetime_str(r["price_data_timestamp"]) if dict(r).get("price_data_timestamp") else "—",
            }
            for r in db.price_reaction_rows(market)
        ]
        upcoming_pr = [item for item in price_reaction_rows if _is_upcoming_announcement(item)]
        legacy_pr = [item for item in price_reaction_rows if not _is_upcoming_announcement(item)]
        ctx["price_reaction_rows"] = price_reaction_rows
        ctx["upcoming_price_reaction_rows"] = upcoming_pr
        ctx["legacy_price_reaction_rows"] = legacy_pr
        ctx["upcoming_price_reaction_avg"] = _interval_return_average(upcoming_pr)
        ctx["legacy_price_reaction_avg"] = _interval_return_average(legacy_pr)
        ctx["upcoming_price_reaction_event_avg"] = _event_day_return_average(upcoming_pr)
        ctx["legacy_price_reaction_event_avg"] = _event_day_return_average(legacy_pr)
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
            SELECT company_name, cik, event_type, event_date, alert_sent_timestamp
            FROM detections WHERE alert_sent = 1
            ORDER BY alert_sent_timestamp DESC LIMIT 20
            """
        ).fetchall()
        ctx["sent_alerts"] = [
            {
                "company_name": _display_company_name(r["company_name"]),
                "company_url": _company_website(
                    r["company_name"], company_websites, r["cik"], edgar_websites
                ),
                "event_type": r["event_type"] or "Investor Day",
                "event_date": r["event_date"] or "—",
                "sent_at": _friendly_datetime_str(r["alert_sent_timestamp"]),
            }
            for r in sent_rows
        ]
    elif tab == "candidates":
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
        ctx["candidates"] = [
            {
                "id": r["id"],
                "created": _friendly_datetime_str(r["created_timestamp"]),
                "filing_date": _friendly_date(r["filing_date"]) if dict(r).get("filing_date") else "—",
                "status": r["status"] or "",
                "status_display": _admin_display_text(r["status"]),
                "company_name": _display_company_name(r["company_name"]),
                "company_url": _company_website(
                    r["company_name"], company_websites, dict(r).get("cik"), edgar_websites
                ),
                "matched_phrase": _matched_phrase_display(dict(r).get("matched_phrase")),
                "event_date": _friendly_date(r["event_date"]) if dict(r).get("event_date") else "—",
                "matched_location": _admin_display_text(dict(r).get("matched_location")),
                "reason": _admin_display_text(dict(r).get("dismissal_reason") or "alert ready"),
                "sec_url": _sec_filing_url(r["filing_url"]) if dict(r).get("filing_url") else "",
                "event_url": dict(r).get("event_url") or "",
                "evidence_url": dict(r).get("evidence_url") or "",
                "evidence_label": dict(r).get("evidence_label") or "Evidence",
                "hype_status": dict(r).get("hype_status") or "",
                "hype_status_display": _hype_display(
                    dict(r).get("hype_status"),
                    dict(r).get("provisional"),
                ),
                "hype_count": dict(r).get("qualifying_count"),
                "hype_tone": _hype_pill_tone(dict(r).get("hype_status")),
                "hype_provisional": bool(dict(r).get("provisional")),
            }
            for r in db.recent_candidates(market=market)
        ]
    return ctx


def _render_main_ui(tab: str, *, is_admin: bool) -> str:
    valid_tabs = _VALID_ADMIN_TABS if is_admin else _VALID_PUBLIC_TABS
    if tab not in valid_tabs:
        tab = "summary"
    config = load_config()
    market = _current_market()
    db = Database(config.db_path)
    try:
        ctx = _build_admin_context(
            db,
            tab,
            company_websites=config.company_websites,
            edgar_websites=db.company_websites_by_cik(),
            market=market,
        )
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
        edgar_websites = db.company_websites_by_cik()
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
        row=dict(row)
        | {
            "company_url": _company_website(
                row["company_name"], config.company_websites, row["cik"], edgar_websites
            )
        },
        preview=preview,
        fetch_error=fetch_error,
        sec_url=sec_url,
        status_display=_admin_display_text(row["status"]),
        reason_display=_admin_display_text(row["dismissal_reason"] or "alert ready"),
        filing_date_display=_friendly_date(row["filing_date"]) if dict(row).get("filing_date") else "—",
        items=items or json.loads(row["items_json"] or "[]"),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5057, debug=True)
