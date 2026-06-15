"""Read-only local debug UI for PopDay."""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import Database
from .parser import normalize_text, strip_tags
from .rules import ALERT_REQUIREMENTS


STYLE = """
:root {
  color-scheme: light;
  --bg: #f7f7f5;
  --text: #202124;
  --muted: #63676c;
  --line: #d9d9d4;
  --panel: #ffffff;
  --panel-soft: #fbfbfa;
  --accent: #2f6f9f;
  --ok: #137333;
  --warn: #9a6700;
  --danger: #b3261e;
  --ok-bg: #e6f4ea;
  --warn-bg: #fff4d8;
  --danger-bg: #fce8e6;
  --neutral-bg: #eef1f3;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
header {
  border-bottom: 1px solid var(--line);
  background: var(--panel);
  padding: 18px 24px;
  position: sticky;
  top: 0;
  z-index: 5;
}
main { max-width: 1240px; margin: 0 auto; padding: 24px; }
h1 { margin: 0; font-size: 22px; font-weight: 650; letter-spacing: 0; }
h2 { margin: 0 0 12px; font-size: 16px; font-weight: 650; letter-spacing: 0; }
p { margin: 4px 0 0; color: var(--muted); }
section { margin-bottom: 26px; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px; }
.tabs {
  background: var(--panel);
  border: 1px solid var(--line);
  display: flex;
  gap: 4px;
  margin: 0 0 22px;
  overflow-x: auto;
  padding: 0 10px;
}
.tab {
  color: var(--muted);
  display: inline-block;
  font-weight: 650;
  padding: 12px 11px 10px;
  text-decoration: none;
  white-space: nowrap;
}
.tab:hover { color: var(--text); }
.tab-active {
  border-bottom: 3px solid var(--accent);
  color: var(--text);
}
.summary-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.status-banner {
  background: var(--panel);
  border: 1px solid var(--line);
  border-left: 5px solid var(--accent);
  margin-bottom: 14px;
  padding: 20px;
}
.status-title {
  font-size: 26px;
  font-weight: 750;
  line-height: 1.2;
}
.status-meta {
  color: var(--muted);
  margin-top: 6px;
}
.summary-card {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  padding: 14px;
}
.summary-label {
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
  text-transform: uppercase;
}
.summary-value {
  font-size: 22px;
  font-weight: 650;
  margin-top: 4px;
}
.summary-note {
  color: var(--muted);
  margin-top: 6px;
}
.tab-footer {
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
  margin-top: 20px;
  padding-top: 10px;
}
.page-note {
  color: var(--muted);
  margin: -6px 0 14px;
}
.pill {
  border-radius: 999px;
  display: inline-block;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  padding: 5px 8px;
  white-space: nowrap;
}
.pill-ok { background: var(--ok-bg); color: var(--ok); }
.pill-warn { background: var(--warn-bg); color: var(--warn); }
.pill-danger { background: var(--danger-bg); color: var(--danger); }
.pill-neutral { background: var(--neutral-bg); color: var(--muted); }
.snippet-box {
  background: var(--panel);
  border: 1px solid var(--line);
  font-size: 15px;
  margin-bottom: 14px;
  padding: 14px;
}
.text-preview {
  background: var(--panel);
  border: 1px solid var(--line);
  max-height: 520px;
  overflow: auto;
  padding: 14px;
  white-space: pre-wrap;
}
.button-link {
  background: #202124;
  border-radius: 4px;
  color: #fff;
  display: inline-block;
  font-weight: 650;
  margin: 0 8px 12px 0;
  padding: 8px 11px;
  text-decoration: none;
}
.secondary-link {
  background: transparent;
  border: 1px solid var(--line);
  color: var(--text);
}
.manual-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.manual-card {
  background: var(--panel);
  border: 1px solid var(--line);
  padding: 12px;
}
.manual-card h3 {
  font-size: 13px;
  margin: 0 0 6px;
}
.manual-card p {
  color: var(--text);
  font-size: 13px;
  margin: 0;
}
.rule-helper {
  background: var(--panel);
  border: 1px solid var(--line);
  margin-bottom: 12px;
  padding: 12px;
}
.rule-helper h3 {
  font-size: 13px;
  margin: 0 0 6px;
}
.rule-helper p {
  color: var(--text);
  font-size: 13px;
  margin: 0 0 6px;
}
.rule-helper p:last-child { margin-bottom: 0; }
.section-head {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 12px;
}
.section-head h2 { margin: 0; }
table {
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--line);
}
th, td {
  border-bottom: 1px solid var(--line);
  padding: 9px 10px;
  text-align: left;
  vertical-align: top;
}
th { color: var(--muted); font-size: 12px; font-weight: 650; text-transform: uppercase; }
tbody tr:nth-child(even) { background: var(--panel-soft); }
tbody tr:hover { background: #eef5f8; }
tr:last-child td { border-bottom: 0; }
ul {
  margin: 0;
  padding: 12px 18px;
  background: var(--panel);
  border: 1px solid var(--line);
}
li { margin: 5px 0; }
.status-alert_candidate { color: var(--ok); font-weight: 650; }
.status-dismissed { color: var(--warn); font-weight: 650; }
.empty {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  color: var(--muted);
  padding: 18px;
}
.url { max-width: 280px; overflow-wrap: anywhere; }
.action-stack { display: flex; flex-wrap: wrap; gap: 7px; }
.inline-action {
  border: 1px solid var(--line);
  border-radius: 4px;
  color: var(--text);
  display: inline-block;
  font-weight: 650;
  padding: 6px 8px;
  text-decoration: none;
}
.inline-action-primary {
  background: #202124;
  border-color: #202124;
  color: #fff;
}
form { margin: 0; }
.rule-form {
  align-items: end;
  background: var(--panel-soft);
  border: 1px solid var(--line);
  display: grid;
  gap: 10px;
  grid-template-columns: 160px minmax(180px, 1fr) minmax(240px, 1.4fr) auto;
  margin-bottom: 10px;
  padding: 12px;
}
label {
  color: var(--muted);
  display: grid;
  font-size: 12px;
  font-weight: 650;
  gap: 4px;
  text-transform: uppercase;
}
input, select {
  border: 1px solid var(--line);
  border-radius: 4px;
  color: var(--text);
  font: inherit;
  min-height: 36px;
  padding: 7px 8px;
  text-transform: none;
  width: 100%;
}
input:focus, select:focus {
  border-color: var(--accent);
  outline: 2px solid rgba(47, 111, 159, 0.18);
}
button {
  background: #202124;
  border: 0;
  border-radius: 4px;
  color: #fff;
  cursor: pointer;
  font: inherit;
  font-weight: 650;
  min-height: 36px;
  padding: 7px 11px;
}
button:hover, .button-link:hover, .inline-action:hover { opacity: 0.86; }
.delete-button {
  background: transparent;
  border: 1px solid var(--line);
  color: var(--danger);
  min-width: 70px;
}
.build-version {
  margin-top: 30px;
  color: #9aa0a6;
  font-size: 11px;
  text-align: right;
}
@media (max-width: 800px) {
  main { padding: 16px; }
  .grid { grid-template-columns: 1fr; }
  .summary-grid { grid-template-columns: 1fr; }
  .manual-grid { grid-template-columns: 1fr; }
  table { display: block; overflow-x: auto; }
  .rule-form { grid-template-columns: 1fr; }
}
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _display_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("_", " ").replace(":", ": ")
    return " ".join(word.upper() if word in {"sec", "edgar"} else word for word in text.split()).capitalize()


def _pill(text: object, tone: str = "neutral") -> str:
    return f'<span class="pill pill-{tone}">{_e(text)}</span>'


def _status_tone(value: str) -> str:
    lowered = value.lower()
    if lowered in {"available", "alert_candidate", "alert ready", "yes"}:
        return "ok"
    if lowered in {"not yet available", "dismissed", "no"}:
        return "warn"
    if lowered == "error":
        return "danger"
    return "neutral"


def _rules_table(db: Database) -> str:
    rows = db.rules()
    body = "\n".join(
        "<tr>"
        f"<td>{_e(_display_text(row['rule_type']))}</td>"
        f"<td>{_e(row['phrase'])}</td>"
        f"<td>{_e(row['description'])}</td>"
        f"<td>{_pill('yes' if row['active'] else 'no', _status_tone('yes' if row['active'] else 'no'))}</td>"
        "<td>"
        '<form method="post" action="/rules/delete">'
        f'<input type="hidden" name="rule_type" value="{_e(row["rule_type"])}">'
        f'<input type="hidden" name="phrase" value="{_e(row["phrase"])}">'
        '<button class="delete-button" type="submit">Delete</button>'
        "</form>"
        "</td>"
        "</tr>"
        for row in rows
    )
    add_form = """
    <form class="rule-form" method="post" action="/rules/add">
      <label>Type
        <select name="rule_type">
          <option value="include">Include</option>
          <option value="routine_context">Routine context</option>
        </select>
      </label>
      <label>Phrase
        <input name="phrase" required placeholder="e.g. healthcare day">
      </label>
      <label>Description
        <input name="description" placeholder="Optional description">
      </label>
      <button type="submit">Add Rule</button>
    </form>
    <div class="rule-helper">
      <h3>How routine context is used</h3>
      <p><strong>Include</strong> phrases are the positive phrases PopDay looks for, such as investor day or analyst day.</p>
      <p><strong>Routine context</strong> phrases are ordinary events, such as earnings calls or annual meetings. They do not automatically block an alert.</p>
      <p>They help PopDay explain weaker matches and avoid treating a passing mention of an ordinary meeting as an Investor Day announcement.</p>
    </div>
    """
    return (
        add_form +
        "<table><thead><tr><th>Type</th><th>Phrase</th><th>Description</th>"
        f"<th>Active</th><th>Action</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _recipients_table(db: Database) -> str:
    rows = db.alert_recipients()
    body = "\n".join(
        "<tr>"
        f"<td>{_e(row['email'])}</td>"
        f"<td>{_pill('yes' if row['active'] else 'no', _status_tone('yes' if row['active'] else 'no'))}</td>"
        f"<td>{_e(_friendly_datetime(row['created_timestamp']))}</td>"
        "<td>"
        f'<form method="post" action="/recipients/{"unsubscribe" if row["active"] else "reactivate"}">'
        f'<input type="hidden" name="email" value="{_e(row["email"])}">'
        f'<button class="{"delete-button" if row["active"] else ""}" type="submit">{"Unsubscribe" if row["active"] else "Reactivate"}</button>'
        "</form>"
        "</td>"
        "</tr>"
        for row in rows
    )
    if not body:
        body = '<tr><td colspan="4">No alert recipients configured.</td></tr>'
    add_form = """
    <form class="rule-form" method="post" action="/recipients/add">
      <label>Email
        <input name="email" type="email" required placeholder="person@example.com">
      </label>
      <button type="submit">Add Recipient</button>
    </form>
    """
    return (
        add_form +
        "<table><thead><tr><th>Email</th><th>Active</th><th>Added</th>"
        f"<th>Action</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _requirements_list() -> str:
    items = "\n".join(f"<li>{_e(requirement)}</li>" for requirement in ALERT_REQUIREMENTS)
    return f"<ul>{items}</ul>"


def _tabs(active: str) -> str:
    tabs = [
        ("summary", "Summary"),
        ("announcements", "Investor Days"),
        ("rules", "Rules"),
        ("recipients", "Email Alerts"),
        ("health", "System Health"),
        ("candidates", "Candidates"),
        ("filings", "Filings"),
        ("help", "Help"),
    ]
    return '<nav class="tabs">' + "\n".join(
        f'<a class="tab {"tab-active" if key == active else ""}" href="/?tab={key}">{label}</a>'
        for key, label in tabs
    ) + "</nav>"


def _tab_footer(active: str) -> str:
    descriptions = {
        "summary": "This page shows the current alert state and the latest EDGAR check.",
        "announcements": "This page shows every qualifying Investor Day announcement PopDay has found.",
        "rules": "This page controls the phrases PopDay uses when deciding what to inspect.",
        "recipients": "This page controls who receives PopDay alert emails.",
        "health": "This page shows recent scheduled runs and low-level scan counts.",
        "candidates": "This page shows filings that matched or were dismissed, with public SEC source links.",
        "filings": "This page shows recently processed SEC filings so duplicate runs can be checked.",
        "help": "This page briefly explains how PopDay works.",
    }
    return f'<div class="tab-footer">{_e(descriptions.get(active, ""))}</div>'


def _day_suffix(day: int) -> str:
    if 10 <= day % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _friendly_date(value: datetime) -> str:
    day = value.day
    return f"{day}{_day_suffix(day)} {value.strftime('%B %Y')}"


def _friendly_datetime(value: str) -> str:
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


def _friendly_dt(value: datetime) -> str:
    day = value.day
    return (
        f"{value.strftime('%A')} {day}{_day_suffix(day)} "
        f"{value.strftime('%B %Y')} {value.strftime('%H:%M %Z')}"
    )


def _next_scheduled_run(now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    schedule = [(4, 30), (8, 0)]
    for day_offset in range(8):
        candidate_day = now + timedelta(days=day_offset)
        if candidate_day.weekday() not in {1, 2, 3, 4, 5}:
            continue
        for hour, minute in schedule:
            candidate = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                return candidate
    return now


def _friendly_date_string(value: str) -> str:
    if not value:
        return ""
    try:
        fmt = "%Y%m%d" if value.isdigit() and len(value) == 8 else "%Y-%m-%d"
        parsed = datetime.strptime(value, fmt)
    except ValueError:
        return value
    return _friendly_date(parsed)


def _sec_detail_url(filing_url: str) -> str:
    marker = "/Archives/edgar/data/"
    if marker not in filing_url:
        return filing_url
    tail = filing_url.split(marker, 1)[1]
    parts = tail.split("/")
    cik, accession_no_dashes = parts[0], parts[1]
    accession = parts[-1].replace(".txt", "")
    if len(parts) < 3:
        accession_no_dashes = accession.replace("-", "")
    else:
        accession = accession_no_dashes
        if len(parts) >= 3 and parts[2].endswith(".txt"):
            accession = parts[2].replace(".txt", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik}/{accession_no_dashes}/{accession}-index.htm"
    )


def _clean_filing_preview(raw: str, snippet: str) -> str:
    plain = strip_tags(raw)
    plain = normalize_text(plain)
    if not plain:
        return "No readable filing text could be extracted."

    if snippet:
        anchor = snippet[:80].lower()
        idx = plain.lower().find(anchor)
        if idx != -1:
            start = max(0, idx - 1200)
            end = min(len(plain), idx + 3500)
            return plain[start:end]

    return plain[:5000]


def _filing_preview(db_path: str, detection_id: int) -> str:
    from .config import load_config
    from .edgar_fetch import EdgarClient

    db = Database(db_path)
    try:
        row = db.detection(detection_id)
        if not row:
            return "<section><h2>Filing Text</h2><div class=\"empty\">Candidate not found.</div></section>"
    finally:
        db.close()

    config = load_config()
    client = EdgarClient(config.sec_user_agent, config.request_delay_seconds)
    try:
        raw = client.get_text(row["filing_url"])
        preview = _clean_filing_preview(raw, row["snippet"] or "")
    except Exception as exc:
        preview = f"Could not fetch filing text: {exc}"

    sec_url = _sec_detail_url(row["filing_url"])
    return (
        "<section><h2>Filing Text</h2>"
        '<p class="page-note">Cleaned local preview of the source filing around the match.</p>'
        f'<a class="button-link secondary-link" href="/?tab=candidates">Back to candidates</a>'
        f'<a class="button-link" href="{_e(sec_url)}">Open SEC filing page</a>'
        f"<div class=\"snippet-box\"><strong>{_e(row['company_name'])}</strong><br>"
        f"Form: {_e(row['form_type'])}<br>"
        f"Filed: {_e(_friendly_date_string(row['filing_date']))}<br>"
        f"Status: {_pill(_display_text(row['status']), _status_tone(row['status']))}<br>"
        f"Matched phrase: {_e(row['matched_phrase'])}<br>"
        f"Reason: {_e(_display_text(row['dismissal_reason'] or 'alert ready'))}</div>"
        f"<div class=\"snippet-box\"><strong>PopDay snippet</strong><br>{_e(row['snippet'])}</div>"
        f"<div class=\"text-preview\">{_e(preview)}</div>"
        "</section>"
    )


def _help() -> str:
    cards = [
        (
            "Schedule",
            "launchd runs PopDay Tuesday-Saturday at 04:30 and 08:00, scanning the previous SEC business day.",
        ),
        (
            "Source",
            "PopDay reads SEC EDGAR daily indexes, then fetches only 8-K and 6-K filings.",
        ),
        (
            "Include Rules",
            "These are event phrases PopDay looks for, such as investor day or analyst day.",
        ),
        (
            "Routine Context",
            "These phrases help explain dismissals. They are not automatic blockers.",
        ),
        (
            "Date Rule",
            "No future event date nearby means no alert.",
        ),
        (
            "Duplicate Rule",
            "Previously processed filings and already alerted events are skipped.",
        ),
        (
            "Email Rule",
            "Email is sent only for qualifying future event announcements.",
        ),
        (
            "No Commentary",
            "Alerts contain only company, event date, and SEC source link.",
        ),
    ]
    body = "\n".join(
        '<div class="manual-card">'
        f"<h3>{_e(title)}</h3>"
        f"<p>{_e(text)}</p>"
        "</div>"
        for title, text in cards
    )
    return f'<div class="manual-grid">{body}</div>'


def _summary(db: Database, db_path: str) -> str:
    rows = _launchd_health_rows(db_path)
    latest = rows[0] if rows else None
    if latest:
        index_status = latest["index_status"] or "unknown"
        alerts_sent = latest["alerts_sent"] or "0"
        filing_date = _friendly_date_string(latest.get("filing_date", ""))
        started = _friendly_datetime(latest["started"])
    else:
        index_status = "no runs yet"
        alerts_sent = "0"
        filing_date = ""
        started = "none"

    latest_alert = db.latest_sent_alert()
    if latest_alert:
        status_title = (
            f"{latest_alert['company_name']} announced "
            f"{latest_alert['event_type'] or 'an Investor Day'}"
        )
        status_meta = f"Event date: {latest_alert['event_date']}"
    else:
        status_title = f"No qualifying EDGAR Investor Day alerts as of {_friendly_date(datetime.now().astimezone())}"
        status_meta = "This does not cover announcements that were not filed on EDGAR as an 8-K or 6-K."

    cards = [
        (
            "Investor Days Found",
            str(db.investor_day_announcement_count()),
            "Qualifying EDGAR announcements stored by PopDay.",
        ),
        (
            "Latest EDGAR Index",
            _pill(index_status, _status_tone(index_status)),
            f"Scanned: {filing_date or 'none'}; last run: {started}",
        ),
        ("Alerts Sent", alerts_sent, "Emails sent by the latest recorded run."),
    ]
    return (
        '<div class="status-banner">'
        f'<div class="status-title">{_e(status_title)}</div>'
        f'<div class="status-meta">{_e(status_meta)}</div>'
        '</div>'
        '<div class="summary-grid">' + "\n".join(
        '<div class="summary-card">'
        f'<div class="summary-label">{_e(label)}</div>'
        f'<div class="summary-value">{value if str(value).startswith("<span") else _e(value)}</div>'
        f'<div class="summary-note">{_e(note)}</div>'
        '</div>'
        for label, value, note in cards
        ) + "</div>"
    )


def _announcements_table(db: Database) -> str:
    rows = db.investor_day_announcements()
    total = len(rows)
    if not rows:
        return (
            '<div class="summary-card" style="margin-bottom: 12px;">'
            '<div class="summary-label">Investor Days Found</div>'
            '<div class="summary-value">0</div>'
            '<div class="summary-note">No qualifying Investor Day announcements have been stored yet.</div>'
            '</div>'
        )
    body = "\n".join(
        "<tr>"
        f"<td>{_e(row['company_name'])}</td>"
        f"<td>{_e(row['event_type'] or 'Investor Day')}</td>"
        f"<td>{_e(_friendly_date_string(row['event_date']))}</td>"
        f"<td>{_e(row['source_type'])}</td>"
        f"<td>{_e(row['form_type'] or row['source_label'])}</td>"
        f"<td>{_e(_friendly_date_string(row['filing_date']))}</td>"
        f"<td>{_e(row['matched_phrase'] or '')}</td>"
        f"<td>{_pill('sent' if row['alert_sent'] else 'not sent', _status_tone('yes' if row['alert_sent'] else 'no'))}</td>"
        f"<td class=\"url\"><a class=\"inline-action\" href=\"{_e(_sec_detail_url(row['source_url']) if row['source_type'] == 'EDGAR' else row['source_url'])}\">Source</a></td>"
        "</tr>"
        for row in rows
    )
    return (
        '<div class="summary-card" style="margin-bottom: 12px;">'
        '<div class="summary-label">Investor Days Found</div>'
        f'<div class="summary-value">{total}</div>'
        '<div class="summary-note">Known Investor Day announcements stored by PopDay.</div>'
        '</div>'
        "<table><thead><tr><th>Company</th><th>Event</th><th>Event Date</th>"
        "<th>Source Type</th><th>Source</th><th>Announced/File Date</th><th>Matched Phrase</th><th>Email</th><th>Link</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _processed_table(db: Database) -> str:
    rows = db.recent_processed()
    if not rows:
        return '<div class="empty">No processed filings yet.</div>'
    body = "\n".join(
        "<tr>"
        f"<td>{_e(_friendly_datetime(row['processed_timestamp']))}</td>"
        f"<td>{_e(row['form_type'])}</td>"
        f"<td>{_e(row['company_name'])}</td>"
        f"<td>{_e(row['accession_number'])}</td>"
        f"<td>{_e(_friendly_date_string(row['filing_date']))}</td>"
        "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr><th>Processed</th><th>Form</th><th>Company</th>"
        f"<th>Accession</th><th>Filed</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _candidates_table(db: Database) -> str:
    rows = db.recent_candidates()
    if not rows:
        return '<div class="empty">No candidate matches yet.</div>'
    body = "\n".join(
        "<tr>"
        f"<td>{_e(_friendly_datetime(row['created_timestamp']))}</td>"
        f"<td>{_pill(_display_text(row['status']), _status_tone(row['status']))}</td>"
        f"<td>{_e(row['company_name'])}</td>"
        f"<td>{_e(row['matched_phrase'])}</td>"
        f"<td>{_e(_friendly_date_string(row['event_date']))}</td>"
        f"<td>{_e(_display_text(row['matched_location']))}</td>"
        f"<td>{_e(_display_text(row['dismissal_reason'] or 'alert ready'))}</td>"
        f"<td class=\"url\"><div class=\"action-stack\">"
        f"<a class=\"inline-action inline-action-primary\" href=\"/candidate?id={_e(row['id'])}\">Read text</a>"
        f"<a class=\"inline-action\" href=\"{_e(_sec_detail_url(row['filing_url']))}\">SEC page</a>"
        "</div></td>"
        "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr><th>Seen</th><th>Status</th><th>Company</th>"
        "<th>Phrase</th><th>Event Date</th><th>Location</th><th>Reason</th>"
        f"<th>Source</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _launchd_health_rows(db_path: str) -> list[dict[str, str]]:
    log_path = Path(db_path).parent / "logs" / "popday.launchd.out.log"
    if not log_path.exists():
        return []

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
    runs: list[dict[str, str]] = []
    current: dict[str, str] | None = None

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


def _launchd_health_table(db_path: str) -> str:
    rows = _launchd_health_rows(db_path)
    if not rows:
        return '<div class="empty">No system health runs recorded yet.</div>'
    body = "\n".join(
        "<tr>"
        f"<td>{_e(_friendly_datetime(row['started']))}</td>"
        f"<td>{_e(_friendly_date_string(row['filing_date']))}</td>"
        f"<td>{_pill(row['index_status'], _status_tone(row['index_status']))}</td>"
        f"<td>{_e(row['filings_parsed'])}</td>"
        f"<td>{_e(row['eight_k_sanity_count'])}</td>"
        f"<td>{_e(row['alerts_sent'])}</td>"
        "</tr>"
        for row in rows
    )
    next_run = _friendly_dt(_next_scheduled_run())
    return (
        f'<div class="summary-card" style="margin-bottom: 12px;">'
        f'<div class="summary-label">Next scheduled run</div>'
        f'<div class="summary-value">{_e(next_run)}</div>'
        '</div>'
        "<table><thead><tr><th>Recent run</th><th>Filing Date</th><th>EDGAR Index</th><th>Filings Checked</th>"
        f"<th>8-K Count</th><th>Alerts Sent</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _build_version() -> str:
    updated_at = datetime.fromtimestamp(Path(__file__).stat().st_mtime).astimezone()
    day = updated_at.day
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{updated_at.strftime('%H:%M')} {updated_at.strftime('%a')} {day}{suffix} {updated_at.strftime('%b %Y')}"


def render_debug_page(db_path: str, active_tab: str = "summary") -> str:
    if active_tab == "manual":
        active_tab = "help"
    if active_tab not in {"summary", "announcements", "rules", "recipients", "health", "candidates", "filings", "help"}:
        active_tab = "summary"
    db = Database(db_path)
    try:
        if active_tab == "summary":
            content = (
                "<section><h2>Summary</h2>"
                '<p class="page-note">The current PopDay alert state at a glance.</p>'
                f"{_summary(db, db_path)}</section>"
            )
        elif active_tab == "announcements":
            content = (
                "<section><h2>Investor Days</h2>"
                '<p class="page-note">Running list of qualifying Investor Day announcements PopDay has found.</p>'
                f"{_announcements_table(db)}</section>"
            )
        elif active_tab == "rules":
            content = (
                '<div class="grid"><section><h2>Rules</h2>'
                '<p class="page-note">Add or remove phrases used by the detector.</p>'
                f"{_rules_table(db)}</section><section><h2>Alert Requirements</h2>"
                '<p class="page-note">All requirements must be true before an email is sent.</p>'
                f"{_requirements_list()}</section></div>"
            )
        elif active_tab == "recipients":
            content = (
                "<section><h2>Email Alerts</h2>"
                '<p class="page-note">People who receive PopDay alert emails.</p>'
                f"{_recipients_table(db)}</section>"
            )
        elif active_tab == "health":
            content = (
                "<section><h2>System Health</h2>"
                '<p class="page-note">Recent scheduled runs and the next expected run.</p>'
                f"{_launchd_health_table(db_path)}</section>"
            )
        elif active_tab == "candidates":
            content = (
                "<section><h2>Candidate Matches</h2>"
                '<p class="page-note">Matches PopDay inspected, including dismissed false positives.</p>'
                f"{_candidates_table(db)}</section>"
            )
        elif active_tab == "filings":
            content = (
                "<section><h2>Processed Filings</h2>"
                '<p class="page-note">Recent SEC filings PopDay has already scanned.</p>'
                f"{_processed_table(db)}</section>"
            )
        else:
            content = (
                "<section><h2>Help</h2>"
                '<p class="page-note">Short explanation of the PopDay logic.</p>'
                f"{_help()}</section>"
            )
    finally:
        db.close()

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PopDay</title>
  <style>{STYLE}</style>
</head>
<body>
  <header>
    <h1>PopDay</h1>
    <p>Local monitor for EDGAR Investor Day alerts.</p>
  </header>
  <main>
    {_tabs(active_tab)}
    {content}
    {_tab_footer(active_tab)}
    <div class="build-version">build {_build_version()}</div>
  </main>
</body>
</html>"""


def serve_debug_ui(db_path: str, host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _redirect_to(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/unsubscribe":
                query = parse_qs(parsed.query)
                email = query.get("email", [""])[0].strip().lower()
                token = query.get("token", [""])[0].strip()
                from .config import load_config
                from .unsubscribe import valid_unsubscribe_token

                config = load_config()
                ok = bool(
                    email
                    and token
                    and config.unsubscribe_secret
                    and valid_unsubscribe_token(email, token, config.unsubscribe_secret)
                )
                if ok:
                    db = Database(db_path)
                    try:
                        db.unsubscribe_alert_recipient(email)
                    finally:
                        db.close()
                    message = f"{email} has been unsubscribed from PopDay alert emails."
                    status = 200
                else:
                    message = "This unsubscribe link is invalid or expired."
                    status = 403
                content = (
                    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                    f"<title>PopDay</title><style>{STYLE}</style></head><body>"
                    "<header><h1>PopDay</h1><p>Email alerts</p></header>"
                    f"<main><section><h2>Unsubscribe</h2><p>{_e(message)}</p></section></main>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if path == "/candidate":
                query = parse_qs(parsed.query)
                try:
                    detection_id = int(query.get("id", ["0"])[0])
                except ValueError:
                    detection_id = 0
                content = (
                    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                    f"<title>PopDay</title><style>{STYLE}</style></head><body>"
                    "<header><h1>PopDay</h1><p>Local monitor for EDGAR Investor Day alerts.</p></header>"
                    f"<main>{_tabs('candidates')}{_filing_preview(db_path, detection_id)}"
                    f"{_tab_footer('candidates')}<div class=\"build-version\">build {_build_version()}</div></main>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

            if path not in {"/", "/debug"}:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            active_tab = parse_qs(parsed.query).get("tab", ["summary"])[0]
            content = render_debug_page(db_path, active_tab).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            data = parse_qs(body)
            db = Database(db_path)
            try:
                if path == "/rules/add":
                    rule_type = data.get("rule_type", [""])[0]
                    phrase = data.get("phrase", [""])[0].strip()
                    description = data.get("description", [""])[0].strip()
                    if rule_type in {"include", "routine_context"} and phrase:
                        if not description:
                            description = (
                                "Qualifying investor-event phrase."
                                if rule_type == "include"
                                else "Context-sensitive routine phrase; not an automatic exclusion."
                            )
                        db.add_rule(rule_type, phrase, description)
                    self._redirect_to("/?tab=rules")
                    return
                if path == "/rules/delete":
                    rule_type = data.get("rule_type", [""])[0]
                    phrase = data.get("phrase", [""])[0]
                    if rule_type in {"include", "routine_context"} and phrase:
                        db.delete_rule(rule_type, phrase)
                    self._redirect_to("/?tab=rules")
                    return
                if path == "/recipients/add":
                    email = data.get("email", [""])[0].strip()
                    if "@" in email and "." in email:
                        db.add_alert_recipient(email)
                    self._redirect_to("/?tab=recipients")
                    return
                if path == "/recipients/delete":
                    email = data.get("email", [""])[0].strip()
                    if email:
                        db.delete_alert_recipient(email)
                    self._redirect_to("/?tab=recipients")
                    return
                if path == "/recipients/unsubscribe":
                    email = data.get("email", [""])[0].strip()
                    if email:
                        db.unsubscribe_alert_recipient(email)
                    self._redirect_to("/?tab=recipients")
                    return
                if path == "/recipients/reactivate":
                    email = data.get("email", [""])[0].strip()
                    if email:
                        db.reactivate_alert_recipient(email)
                    self._redirect_to("/?tab=recipients")
                    return
            finally:
                db.close()

            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"PopDay debug UI running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()
