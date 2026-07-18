"""Configuration loading for PopDay."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_USER_AGENT = "PopDay/0.1 contact@example.com"

# UK (Investegate) defaults - per-market where behaviour differs, shared where
# it doesn't (hype_threshold, email settings). All overridable in config.json.
DEFAULT_UK_INCLUDE_PHRASES = [
    "capital markets day",
    "capital markets event",
    "investor day",
    "investor seminar",
    "analyst day",
    "teach-in",
    "capital markets update",
    "cmd",  # matched on word boundaries only, never as a substring
]
DEFAULT_UK_EXCLUDE_PHRASES = [
    # Known headline false positives: Investor Meet Company is a webcast
    # platform whose name appears on routine results presentations, and a
    # bare "investor presentation" headline is routine IR housekeeping.
    "investor meet company",
    "investor presentation",
    "investor relations website",
]
# Routine RNS headline types that never count as pre-event hype disclosures
# (the UK analogue of restricting US hype to voluntary 8-K items 2.02/7.01/
# 8.01). Provisional list - matched case-insensitively as substrings.
DEFAULT_UK_ROUTINE_HEADLINES = [
    "transaction in own shares",
    "director/pdmr shareholding",
    "holding(s) in company",
    "total voting rights",
    "block listing",
    "result of agm",
    "result of gm",
    "notice of agm",
    "agm statement",  # debatable; excluded for v1
    "second price monitoring extn",
    "price monitoring extension",
    "form 8",
    "rule 8",
    "annual financial report on nsm",
    "doc re.",
]

DEFAULT_COMPANY_WEBSITES = {
    "Barnes & Noble Education, Inc.": "https://bned.com/",
    "Climb Global Solutions, Inc.": "https://www.climbglobalsolutions.com/",
    "HARMONIC INC.": "https://www.harmonicinc.com/",
    "Radian Group Inc.": "https://www.radian.com/",
    "Samsara Inc.": "https://www.samsara.com/",
    "SLB LIMITED/NV": "https://www.slb.com/",
    # Added 7 Jul 2026 for newly-surfaced Investor Day companies (URLs verified).
    "Advanced Drainage Systems, Inc.": "https://www.adspipe.com/",
    # SPAC — links to its merger target Pasqal, whose analyst day this was.
    "Bleichroeder Acquisition Corp. II": "https://www.pasqal.com/",
    "Caseys General Stores Inc": "https://www.caseys.com/",
    "COMMERCIAL METALS Co": "https://www.cmc.com/",
    "Drdgold Ltd": "https://www.drdgold.com/",
    "Dynatrace, Inc.": "https://www.dynatrace.com/",
    "Graham Corp": "https://www.grahamcorp.com/",
    "MoonLake Immunotherapeutics": "https://www.moonlaketx.com/",
    "Resideo Technologies, Inc.": "https://www.resideo.com/",
    "Scotts Miracle-gro Co": "https://investor.scotts.com/",
    "Sandisk Corp": "https://www.sandisk.com/",
    # Added 15 Jul 2026 for newly-surfaced Investor Day companies (URLs
    # verified live - www.adiglobal.com confirmed via its own page metadata,
    # the investor.adiglobal.com subdomain the filing itself named returns a
    # Cloudflare bot-challenge to unattended clients, not usable as a link).
    "Ligand Pharmaceuticals Inc": "https://www.ligand.com/",
    "ADI Global Distribution Inc.": "https://www.adiglobal.com/",
    # Added 17 Jul 2026 for newly-surfaced Investor Day companies (URLs verified live).
    "ASML Holding NV": "https://www.asml.com/",
    "Hanover Insurance Group, Inc.": "https://www.hanover.com/",
    "Smith & Wesson Brands, Inc.": "https://www.smith-wesson.com/",
    # Added 18 Jul 2026 for a newly-surfaced Investor Day company (URL verified live).
    "BOS Better Online Solutions Ltd": "https://www.boscom.com",
}


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
    # Placeholder pending Cabrera et al. results section.
    hype_threshold: int
    hype_definition_version: str
    hype_provisional: bool
    unsubscribe_base_url: str | None
    unsubscribe_secret: str | None
    company_websites: dict[str, str]
    # UK market extension (per-market config; hype_threshold/email are shared)
    uk_user_agent: str
    uk_request_delay_seconds: float
    uk_include_phrases: list[str]
    uk_exclude_phrases: list[str]
    uk_routine_headlines: list[str]
    # When set (PA production), every real scan regenerates this status JSON
    # so the front door's freshness clock tracks scans, not deploys.
    status_json_path: str

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
        # POPDAY_CONFIG_JSON lets scheduled jobs that run outside the repo
        # (e.g. the auto-deploy clone, which has no config.json) point every
        # bare load_config() call at the real config without touching cwd.
        candidate = Path(os.environ.get("POPDAY_CONFIG_JSON", "config.json"))
    else:
        candidate = Path(path)
    if not candidate.exists():
        return {}
    with candidate.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _pick(settings: dict[str, Any], key: str, env_key: str, default: Any = None) -> Any:
    return os.environ.get(env_key, settings.get(key, default))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _company_websites(settings: dict[str, Any]) -> dict[str, str]:
    configured = settings.get("company_websites")
    websites = dict(DEFAULT_COMPANY_WEBSITES)
    if not isinstance(configured, dict):
        return websites
    for company_name, url in configured.items():
        name = str(company_name or "").strip()
        link = str(url or "").strip()
        if name and link:
            websites[name] = link
    return websites


def _phrase_list(settings: dict[str, Any], key: str, default: list[str]) -> list[str]:
    configured = settings.get(key)
    if not isinstance(configured, list):
        return list(default)
    phrases = [str(item).strip().lower() for item in configured if str(item).strip()]
    return phrases or list(default)


def load_config(path: str | None = None) -> Config:
    settings = _read_json(path)
    sec_user_agent = str(
        _pick(settings, "sec_user_agent", "POPDAY_SEC_USER_AGENT", DEFAULT_USER_AGENT)
    )
    return Config(
        db_path=str(_pick(settings, "db_path", "POPDAY_DB_PATH", "popday.sqlite3")),
        sec_user_agent=sec_user_agent,
        smtp_host=_pick(settings, "smtp_host", "POPDAY_SMTP_HOST"),
        smtp_port=int(_pick(settings, "smtp_port", "POPDAY_SMTP_PORT", 587)),
        smtp_username=_pick(settings, "smtp_username", "POPDAY_SMTP_USERNAME"),
        smtp_password=_pick(settings, "smtp_password", "POPDAY_SMTP_PASSWORD"),
        email_from=_pick(settings, "email_from", "POPDAY_EMAIL_FROM"),
        email_to=_pick(settings, "email_to", "POPDAY_EMAIL_TO"),
        request_delay_seconds=float(
            _pick(settings, "request_delay_seconds", "POPDAY_REQUEST_DELAY_SECONDS", 0.65)
        ),
        hype_threshold=int(_pick(settings, "hype_threshold", "POPDAY_HYPE_THRESHOLD", 2)),
        hype_definition_version=str(
            _pick(
                settings,
                "hype_definition_version",
                "POPDAY_HYPE_DEFINITION_VERSION",
                "v1-abstract-guess",
            )
        ),
        hype_provisional=_as_bool(
            _pick(settings, "hype_provisional", "POPDAY_HYPE_PROVISIONAL", True),
            default=True,
        ),
        unsubscribe_base_url=_pick(settings, "unsubscribe_base_url", "POPDAY_UNSUBSCRIBE_BASE_URL"),
        unsubscribe_secret=_pick(settings, "unsubscribe_secret", "POPDAY_UNSUBSCRIBE_SECRET"),
        company_websites=_company_websites(settings),
        uk_user_agent=str(
            _pick(settings, "uk_user_agent", "POPDAY_UK_USER_AGENT", sec_user_agent)
        ),
        uk_request_delay_seconds=float(
            _pick(settings, "uk_request_delay_seconds", "POPDAY_UK_REQUEST_DELAY_SECONDS", 2.0)
        ),
        uk_include_phrases=_phrase_list(settings, "uk_include_phrases", DEFAULT_UK_INCLUDE_PHRASES),
        uk_exclude_phrases=_phrase_list(settings, "uk_exclude_phrases", DEFAULT_UK_EXCLUDE_PHRASES),
        uk_routine_headlines=_phrase_list(
            settings, "uk_routine_headlines", DEFAULT_UK_ROUTINE_HEADLINES
        ),
        status_json_path=str(
            _pick(settings, "status_json_path", "POPDAY_STATUS_JSON_PATH", "") or ""
        ),
    )
