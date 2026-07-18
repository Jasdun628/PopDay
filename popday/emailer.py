"""Plain email alert delivery."""

from __future__ import annotations

import html
import re
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from .config import Config
from .date_extract import format_human_date
from .filing_parser import split_sentences
from .unsubscribe import unsubscribe_url


def _sec_readable_url(filing_url: str) -> str:
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
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik}/{accession_no_dashes}/{accession}-index.htm"
    )


def _mailto_unsubscribe(config: Config, recipient: str) -> str:
    target = config.email_from or config.smtp_username or recipient
    subject = quote("Unsubscribe PopDay")
    body = quote(f"Please unsubscribe {recipient} from PopDay alert emails.")
    return f"mailto:{target}?subject={subject}&body={body}"


def _unsubscribe_link(config: Config, recipient: str) -> str:
    if config.unsubscribe_base_url and config.unsubscribe_secret:
        return unsubscribe_url(config.unsubscribe_base_url, recipient, config.unsubscribe_secret)
    return _mailto_unsubscribe(config, recipient)


def _normalize_excerpt(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _excerpt_sentences(value: str) -> list[str]:
    text = _normalize_excerpt(value)
    if not text:
        return []
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if sentences and sentences[0][:1].islower():
        sentences = sentences[1:]
    return sentences


def _trim_sentence(value: str, limit: int = 220) -> str:
    text = _normalize_excerpt(value)
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return f"{clipped}..."


def _focus_announcement_sentence(sentence: str) -> str:
    text = _normalize_excerpt(sentence)
    lowered = text.lower()
    starts: list[int] = []
    for cue in ("also announced", "announced it will", "announced that it will", "will host", "will hold"):
        idx = lowered.find(cue)
        if idx != -1:
            starts.append(idx)
    if not starts:
        return text
    start = min(starts)
    prefix = text[:start].rstrip()
    if prefix:
        words = prefix.split()
        if words:
            start = max(0, start - len(words[-1]) - 1)
    return text[start:].strip(" ,;:-")


_NUGGET_CONTEXT_WINDOW = 2  # sentences either side of the Key Excerpt anchor
_NUGGET_SIMILARITY_LIMIT = 0.9  # token-overlap backstop vs Key Excerpt

_CONTEXT_MARKER_KEYWORDS = (
    "spin-off", "spinoff", "spin off", "inaugural", "first-time", "first ever",
    "headquartered", "headquarters", "based in", "outlook", "guidance",
)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _token_overlap_ratio(a: str, b: str) -> float:
    """Fraction of the smaller sentence's tokens also present in the other -
    a simple, dependency-free stand-in for a full similarity metric, used
    only as a backstop guard against near-duplicate text."""
    tokens_a, tokens_b = _tokenize(a), _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))


def _has_context_marker(sentence: str) -> bool:
    """True if a sentence carries a number/date/named-context signal - the
    kind of thing worth a second, distinct nugget (a figure, a date, a
    location, a spin-off/parent mention, an inaugural/first-time flag)."""
    if re.search(r"\d", sentence):
        return True
    lowered = sentence.lower()
    return any(marker in lowered for marker in _CONTEXT_MARKER_KEYWORDS)


def _find_anchor_index(context_sentences: list[str], snippet: str) -> int | None:
    """Locate the Key Excerpt's source sentence within the wider context.

    snippet is drawn from the same source text via filing_parser.best_nugget
    (which itself calls split_sentences), so under normal truncation-free
    conditions it is byte-identical to one of context_sentences here (both
    ultimately come from the same split_sentences() call over the same
    text) - substring containment is the fallback for the truncated case
    (best_nugget clips long sentences and appends "...").
    """
    normalized_snippet = _normalize_excerpt(snippet).rstrip(".")
    if not normalized_snippet:
        return None
    snippet_prefix = (
        normalized_snippet[:-3].rstrip() if normalized_snippet.endswith("...") else normalized_snippet
    )
    if not snippet_prefix:
        return None
    for idx, sentence in enumerate(context_sentences):
        normalized_sentence = _normalize_excerpt(sentence).rstrip(".")
        if normalized_sentence == normalized_snippet or snippet_prefix in normalized_sentence:
            return idx
    return None


def _main_nugget(alert: object) -> str:
    """A distinct nugget from context near the Key Excerpt - not a
    re-derivation of the same sentence. On a short press release with
    nothing else worth surfacing, this is correctly empty rather than a
    near-duplicate of Key Excerpt (2026-07-14: both fields were printing
    near-identical text on short releases)."""
    context_text = str(getattr(alert, "context_text", "") or "")
    snippet = str(getattr(alert, "snippet", "") or "")
    if not context_text or not snippet:
        return ""

    context_sentences = [
        cleaned for raw in split_sentences(context_text) if (cleaned := _normalize_excerpt(raw))
    ]
    if not context_sentences:
        return ""

    anchor_idx = _find_anchor_index(context_sentences, snippet)
    if anchor_idx is None:
        return ""

    key_excerpt = _key_excerpt(alert)
    lo = max(0, anchor_idx - _NUGGET_CONTEXT_WINDOW)
    hi = min(len(context_sentences), anchor_idx + _NUGGET_CONTEXT_WINDOW + 1)
    for idx in range(lo, hi):
        if idx == anchor_idx:
            continue
        candidate = context_sentences[idx]
        if not _has_context_marker(candidate):
            continue
        if key_excerpt and _token_overlap_ratio(candidate, key_excerpt) > _NUGGET_SIMILARITY_LIMIT:
            continue
        return _trim_sentence(_focus_announcement_sentence(candidate))
    return ""


def _key_excerpt(alert: object, max_sentences: int = 3, limit: int = 420) -> str:
    snippet = getattr(alert, "snippet", "") or ""
    sentences = _excerpt_sentences(snippet)
    if not sentences:
        return ""
    excerpt = " ".join(sentences[:max_sentences])
    if len(excerpt) <= limit:
        return excerpt
    clipped = excerpt[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return f"{clipped}..."


def _recovered_note(alert: object) -> str:
    """Label for alerts found while sweeping scan days missed during downtime."""
    recovered_from = str(getattr(alert, "recovered_from", "") or "").strip()
    if not recovered_from:
        return ""
    try:
        from datetime import datetime as _dt

        human = format_human_date(_dt.strptime(recovered_from, "%Y-%m-%d").date())
    except ValueError:
        human = recovered_from
    return f"Recovered from downtime (missed scan day {human})"


def _missing_website_note(alert: object) -> str:
    """Informational only (see popday/company_websites.py) - never blocks
    the alert, just flags that this company still shows as plain text on
    the site until a link is curated or EDGAR later reports one."""
    if not getattr(alert, "company_url_missing", False):
        return ""
    return "No website on file for this company yet - it will show as plain text until curated."


_MARKET_SECTION_LABELS = {
    "US": "US - SEC EDGAR",
    "UK": "UK - RNS (via Investegate)",
}


def _grouped_by_market(alerts: list[object]) -> list[tuple[str, list[object]]]:
    """Alerts grouped by market, US first. A single-market batch returns one
    group, and callers render no section headers for it - so US-only emails
    (the only kind that existed before the UK extension) are unchanged."""
    groups: dict[str, list[object]] = {}
    for alert in alerts:
        market = str(getattr(alert, "market", "US") or "US")
        groups.setdefault(market, []).append(alert)
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] != "US", kv[0]))
    return ordered


def build_alert_body(alerts: list[object], unsubscribe_link: str | None = None) -> str:
    lines: list[str] = []
    alert_count = len(alerts)
    lines.append("POPDAY ALERT")
    lines.append("=" * 12)
    lines.append("")
    if alert_count == 1:
        lines.append("PopDay found 1 new investor-event announcement.")
    else:
        lines.append(f"PopDay found {alert_count} new investor-event announcements.")
    lines.append("")

    groups = _grouped_by_market(alerts)
    ordered_alerts: list[object] = []
    section_starts: dict[int, str] = {}
    if len(groups) > 1:
        for market, group in groups:
            section_starts[len(ordered_alerts)] = _MARKET_SECTION_LABELS.get(market, market)
            ordered_alerts.extend(group)
    else:
        ordered_alerts = list(alerts)

    alerts = ordered_alerts
    for index, alert in enumerate(alerts):
        if index in section_starts:
            lines.append("=" * 56)
            lines.append(section_starts[index])
            lines.append("=" * 56)
            lines.append("")
        event_date = format_human_date(alert.event_date)
        source_label = getattr(alert, "source_label", "Source")
        lines.append("-" * 56)
        lines.append(f"ALERT {index + 1}")
        lines.append("-" * 56)
        lines.append(f"Company: {alert.company_name}")
        lines.append(f"Event:   {alert.event_label}")
        lines.append(f"Date:    {event_date}")
        recovered_note = _recovered_note(alert)
        if recovered_note:
            lines.append(f"Note:    {recovered_note}")
        missing_website_note = _missing_website_note(alert)
        if missing_website_note:
            lines.append(f"Note:    {missing_website_note}")
        nugget = _main_nugget(alert)
        if nugget:
            lines.append("")
            lines.append("MAIN NUGGET")
            lines.append(nugget)
        excerpt = _key_excerpt(alert)
        if excerpt:
            lines.append("")
            lines.append("KEY EXCERPT")
            lines.append(excerpt)
        lines.append("")
        event_url = str(getattr(alert, "event_url", "") or "").strip()
        if event_url:
            lines.append("COMPANY EVENT / IR LINK")
            lines.append(event_url)
            lines.append("")
        evidence_url = str(getattr(alert, "evidence_url", "") or "").strip()
        evidence_label = str(getattr(alert, "evidence_label", "") or "").strip() or "Evidence"
        if not evidence_url:
            evidence_url = _sec_readable_url(str(alert.filing_url))
            evidence_label = "SEC filing page"
        lines.append(f"EVIDENCE: {evidence_label}")
        lines.append(evidence_url)
        lines.append("")
        readable_filing_url = _sec_readable_url(str(alert.filing_url))
        form_type = str(getattr(alert, "form_type", "") or "").strip()
        filing_label = f"{source_label.upper()}: {form_type}" if form_type else f"{source_label.upper()} PAGE"
        lines.append(filing_label)
        lines.append(readable_filing_url)
        if index != len(alerts) - 1:
            lines.append("")

    if unsubscribe_link:
        lines.append("")
        lines.append("-" * 56)
        lines.append("UNSUBSCRIBE")
        lines.append(unsubscribe_link)
    return "\n".join(lines)


def build_alert_html(alerts: list[object], unsubscribe_link: str | None = None) -> str:
    parts: list[str] = [
        "<html><body style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.5;color:#111;\">",
        "<div style=\"font-size:20px;font-weight:700;letter-spacing:0.02em;\">POPDAY ALERT</div>",
        "<hr style=\"border:none;border-top:2px solid #111;margin:8px 0 16px;\">",
    ]
    alert_count = len(alerts)
    if alert_count == 1:
        parts.append("<p>PopDay found 1 new investor-event announcement.</p>")
    else:
        parts.append(f"<p>PopDay found {alert_count} new investor-event announcements.</p>")

    groups = _grouped_by_market(alerts)
    ordered_alerts: list[object] = []
    section_starts: dict[int, str] = {}
    if len(groups) > 1:
        for market, group in groups:
            section_starts[len(ordered_alerts)] = _MARKET_SECTION_LABELS.get(market, market)
            ordered_alerts.extend(group)
    else:
        ordered_alerts = list(alerts)

    alerts = ordered_alerts
    for index, alert in enumerate(alerts):
        if index in section_starts:
            parts.append(
                "<div style=\"font-size:15px;font-weight:700;letter-spacing:0.04em;"
                "margin-top:20px;padding:6px 0;border-top:2px solid #111;"
                f"border-bottom:1px solid #bbb;\">{html.escape(section_starts[index])}</div>"
            )
        event_date = format_human_date(alert.event_date)
        source_label = html.escape(getattr(alert, "source_label", "Source").upper())
        company_name = html.escape(str(alert.company_name))
        event_label = html.escape(str(alert.event_label))
        readable_filing_url_raw = _sec_readable_url(str(alert.filing_url))
        filing_url = html.escape(readable_filing_url_raw)
        event_url_raw = str(getattr(alert, "event_url", "") or "").strip()
        event_url = html.escape(event_url_raw)
        evidence_url_raw = str(getattr(alert, "evidence_url", "") or "").strip()
        evidence_label_raw = str(getattr(alert, "evidence_label", "") or "").strip() or "Evidence"
        if not evidence_url_raw:
            evidence_url_raw = readable_filing_url_raw
            evidence_label_raw = "SEC filing page"
        evidence_url = html.escape(evidence_url_raw)
        evidence_label = html.escape(evidence_label_raw)
        parts.extend(
            [
                "<hr style=\"border:none;border-top:1px solid #bbb;margin:18px 0 14px;\">",
                f"<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">ALERT {index + 1}</div>",
                f"<p><strong>Company:</strong> {company_name}<br>",
                f"<strong>Event:</strong> {event_label}<br>",
                f"<strong>Date:</strong> {html.escape(event_date)}",
            ]
        )
        recovered_note = _recovered_note(alert)
        if recovered_note:
            parts.append(f"<br><em>{html.escape(recovered_note)}</em>")
        missing_website_note = _missing_website_note(alert)
        if missing_website_note:
            parts.append(f"<br><em>{html.escape(missing_website_note)}</em>")
        parts.append("</p>")
        nugget = _main_nugget(alert)
        if nugget:
            parts.append("<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">MAIN NUGGET</div>")
            parts.append(f"<p>{html.escape(nugget)}</p>")
        excerpt = _key_excerpt(alert)
        if excerpt:
            parts.append("<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">KEY EXCERPT</div>")
            parts.append(f"<p>{html.escape(excerpt)}</p>")
        if event_url_raw:
            parts.append("<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">COMPANY EVENT / IR LINK</div>")
            parts.append(f'<p><a href="{event_url}">{event_url}</a></p>')
        parts.append("<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">EVIDENCE</div>")
        parts.append(f'<p><a href="{evidence_url}">{evidence_label}</a></p>')
        form_type = str(getattr(alert, "form_type", "") or "").strip()
        filing_label = f"{source_label}: {form_type}" if form_type else f"{source_label} PAGE"
        parts.append(f"<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">{html.escape(filing_label)}</div>")
        parts.append(f'<p><a href="{filing_url}">{filing_url}</a></p>')

    if unsubscribe_link:
        safe_unsub = html.escape(unsubscribe_link)
        parts.extend(
            [
                "<hr style=\"border:none;border-top:1px solid #bbb;margin:18px 0 14px;\">",
                "<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">UNSUBSCRIBE</div>",
                f'<p><a href="{safe_unsub}">{safe_unsub}</a></p>',
            ]
        )
    parts.append("</body></html>")
    return "".join(parts)


def send_alert_email(config: Config, alerts: list[object], recipients: list[str] | None = None) -> None:
    if not config.email_configured:
        raise RuntimeError("Email is not configured. Set SMTP and email environment variables or config.json.")
    recipients = recipients or config.email_recipients
    if not recipients:
        raise RuntimeError("No alert recipients are configured.")

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config.smtp_username, config.smtp_password)
        for recipient in recipients:
            unsubscribe_link = _unsubscribe_link(config, recipient)
            message = EmailMessage()
            message["Subject"] = "PopDay alert: Investor Day announced"
            message["From"] = config.email_from
            message["To"] = recipient
            message["List-Unsubscribe"] = f"<{unsubscribe_link}>"
            message.set_content(build_alert_body(alerts, unsubscribe_link=unsubscribe_link))
            message.add_alternative(build_alert_html(alerts, unsubscribe_link=unsubscribe_link), subtype="html")
            smtp.send_message(message)


def send_privileged_format_test_email(
    config: Config,
    alerts: list[object],
    *,
    recipient: str,
) -> None:
    if not config.email_configured:
        raise RuntimeError("Email is not configured. Set SMTP and email environment variables or config.json.")
    if not recipient.strip():
        raise RuntimeError("No privileged test recipient is configured.")
    if not alerts:
        raise RuntimeError("No recent alert content is available to replay as a format test.")

    message = EmailMessage()
    message["Subject"] = "TEST PopDay alert: Investor Day announced"
    message["From"] = config.email_from
    message["To"] = recipient.strip().lower()
    message.set_content(build_alert_body(alerts))
    message.add_alternative(build_alert_html(alerts), subtype="html")

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)


def send_ops_alert_email(config: Config, subject: str, body: str) -> None:
    """Plain operational alarm - not a marketing alert, no unsubscribe link.
    Goes to config.email_recipients (Jason's own address), never the public
    alert_recipients list, and never blocked by anyone's unsubscribe state.
    """
    if not config.email_configured:
        raise RuntimeError("Email is not configured. Set SMTP and email environment variables or config.json.")
    recipients = config.email_recipients
    if not recipients:
        raise RuntimeError("No operator recipients are configured for ops alerts.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.email_from
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)


def send_test_email(config: Config, recipients: list[str] | None = None) -> None:
    if not config.email_configured:
        raise RuntimeError("Email is not configured. Set SMTP and email environment variables or config.json.")
    recipients = recipients or config.email_recipients
    if not recipients:
        raise RuntimeError("No alert recipients are configured.")

    message = EmailMessage()
    message["Subject"] = "PopDay test email"
    message["From"] = config.email_from
    message["To"] = ", ".join(recipients)
    message.set_content(
        "This is a PopDay test email.\n\n"
        "If you received this, SMTP sending is configured correctly."
    )

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(message)
