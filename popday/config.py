"""Configuration loading for PopDay."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_USER_AGENT = "PopDay/0.1 contact@example.com"


@dataclass(frozen=True)
class Config:
    db_path: str
    sec_user_agent: str
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    email_from: str | None
    email_to: str | None
    request_delay_seconds: float
    unsubscribe_base_url: str | None
    unsubscribe_secret: str | None

    @property
    def email_configured(self) -> bool:
        return bool(
            self.smtp_host
            and self.smtp_port
            and self.smtp_username
            and self.smtp_password
            and self.email_from
            and self.email_to
        )

    @property
    def email_recipients(self) -> list[str]:
        if not self.email_to:
            return []
        return [
            recipient.strip()
            for recipient in self.email_to.replace(";", ",").split(",")
            if recipient.strip()
        ]

    @property
    def sec_user_agent_has_placeholder_contact(self) -> bool:
        return "contact@example.com" in self.sec_user_agent.lower()


def _read_json(path: str | None) -> dict[str, Any]:
    if not path:
        candidate = Path("config.json")
    else:
        candidate = Path(path)
    if not candidate.exists():
        return {}
    with candidate.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _pick(settings: dict[str, Any], key: str, env_key: str, default: Any = None) -> Any:
    return os.environ.get(env_key, settings.get(key, default))


def load_config(path: str | None = None) -> Config:
    settings = _read_json(path)
    return Config(
        db_path=str(_pick(settings, "db_path", "POPDAY_DB_PATH", "popday.sqlite3")),
        sec_user_agent=str(
            _pick(settings, "sec_user_agent", "POPDAY_SEC_USER_AGENT", DEFAULT_USER_AGENT)
        ),
        smtp_host=_pick(settings, "smtp_host", "POPDAY_SMTP_HOST"),
        smtp_port=int(_pick(settings, "smtp_port", "POPDAY_SMTP_PORT", 587)),
        smtp_username=_pick(settings, "smtp_username", "POPDAY_SMTP_USERNAME"),
        smtp_password=_pick(settings, "smtp_password", "POPDAY_SMTP_PASSWORD"),
        email_from=_pick(settings, "email_from", "POPDAY_EMAIL_FROM"),
        email_to=_pick(settings, "email_to", "POPDAY_EMAIL_TO"),
        request_delay_seconds=float(
            _pick(settings, "request_delay_seconds", "POPDAY_REQUEST_DELAY_SECONDS", 0.65)
        ),
        unsubscribe_base_url=_pick(settings, "unsubscribe_base_url", "POPDAY_UNSUBSCRIBE_BASE_URL"),
        unsubscribe_secret=_pick(settings, "unsubscribe_secret", "POPDAY_UNSUBSCRIBE_SECRET"),
    )
