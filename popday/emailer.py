"""Plain email alert delivery."""

from __future__ import annotations

import html
import re
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from .config import Config
from .date_extract import format_human_date
from .unsubscribe import unsubscribe_url


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


def _main_nugget(alert: object) -> str:
    snippet = getattr(alert, "snippet", "") or ""
    event_label = str(getattr(alert, "event_label", "") or "").lower()
    cue_phrases = [
        "will host",
        "will hold",
        "will present",
        "plans to host",
        "scheduled for",
        "to be held",
    ]
    for sentence in _excerpt_sentences(snippet):
        lowered = sentence.lower()
        if event_label and event_label in lowered:
            return _trim_sentence(_focus_announcement_sentence(sentence))
        if any(cue in lowered for cue in cue_phrases):
            return _trim_sentence(_focus_announcement_sentence(sentence))
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

    for index, alert in enumerate(alerts):
        event_date = format_human_date(alert.event_date)
        source_label = getattr(alert, "source_label", "Source")
        lines.append("-" * 56)
        lines.append(f"ALERT {index + 1}")
        lines.append("-" * 56)
        lines.append(f"Company: {alert.company_name}")
        lines.append(f"Event:   {alert.event_label}")
        lines.append(f"Date:    {event_date}")
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
        lines.append(source_label.upper())
        lines.append(alert.filing_url)
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

    for index, alert in enumerate(alerts):
        event_date = format_human_date(alert.event_date)
        source_label = html.escape(getattr(alert, "source_label", "Source").upper())
        company_name = html.escape(str(alert.company_name))
        event_label = html.escape(str(alert.event_label))
        filing_url = html.escape(str(alert.filing_url))
        event_url_raw = str(getattr(alert, "event_url", "") or "").strip()
        event_url = html.escape(event_url_raw)
        parts.extend(
            [
                "<hr style=\"border:none;border-top:1px solid #bbb;margin:18px 0 14px;\">",
                f"<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">ALERT {index + 1}</div>",
                f"<p><strong>Company:</strong> {company_name}<br>",
                f"<strong>Event:</strong> {event_label}<br>",
                f"<strong>Date:</strong> {html.escape(event_date)}",
            ]
        )
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
        parts.append(f"<div style=\"font-size:13px;font-weight:700;letter-spacing:0.04em;color:#444;\">{source_label}</div>")
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
