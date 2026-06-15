"""Plain email alert delivery."""

from __future__ import annotations

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


def build_alert_body(alerts: list[object], unsubscribe_link: str | None = None) -> str:
    lines: list[str] = []
    for index, alert in enumerate(alerts):
        event_date = format_human_date(alert.event_date)
        article = "an" if alert.event_label.lower().startswith(("analyst", "investor")) else "a"
        source_label = getattr(alert, "source_label", "Source")
        lines.append(
            f"{alert.company_name} has announced {article} {alert.event_label} on {event_date}.\n\n"
            f"{source_label}:\n{alert.filing_url}"
        )
        if index != len(alerts) - 1:
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
