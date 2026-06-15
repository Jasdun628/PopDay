"""Plain email alert delivery."""

from __future__ import annotations

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
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _trim_sentence(value: str, limit: int = 220) -> str:
    text = _normalize_excerpt(value)
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return f"{clipped}..."


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
            return _trim_sentence(sentence)
        if any(cue in lowered for cue in cue_phrases):
            return _trim_sentence(sentence)
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
    if alert_count == 1:
        lines.append("PopDay found 1 new investor-event announcement.")
    else:
        lines.append(f"PopDay found {alert_count} new investor-event announcements.")
    lines.append("")

    for index, alert in enumerate(alerts):
        event_date = format_human_date(alert.event_date)
        source_label = getattr(alert, "source_label", "Source")
        lines.append(f"Company: {alert.company_name}")
        lines.append(f"Event: {alert.event_label}")
        lines.append(f"Date: {event_date}")
        nugget = _main_nugget(alert)
        if nugget:
            lines.append(f"Main nugget: {nugget}")
        excerpt = _key_excerpt(alert)
        if excerpt:
            lines.append("Key excerpt:")
            lines.append(excerpt)
        lines.append(f"{source_label}:")
        lines.append(alert.filing_url)
        if index != len(alerts) - 1:
            lines.append("")
            lines.append("---")
            lines.append("")

    if unsubscribe_link:
        lines.append("")
        lines.append(f"Unsubscribe:\n{unsubscribe_link}")
    return "\n".join(lines)


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
