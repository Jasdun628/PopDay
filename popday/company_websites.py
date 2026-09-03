"""EDGAR self-reported company website capture.

Automatic extraction from filing *content* was scrapped after the XBRL-junk
incident (a wrong link is worse than no link - see HANDOFF.md). This module
uses a narrower, more trustworthy source instead: the `website` /
`investorWebsite` fields EDGAR's own submissions.json exposes per CIK. It
only ever fills a gap - a curated link (DEFAULT_COMPANY_WEBSITES / the
config.json override) always wins, see flask_app.py's `_company_website()`.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .detector import _is_taxonomy_junk_url
from .edgar_fetch import EdgarClient

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def normalize_cik(cik: object) -> str:
    text = str(cik or "").strip()
    return text.zfill(10) if text.isdigit() else text


def _is_usable_website(url: str) -> bool:
    """Basic http(s) validation plus the IR-link fix's junk-host filter,
    reused here so an EDGAR-reported xbrl.org/sec.gov artifact can never be
    stored as if it were a company's own website."""
    text = str(url or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return not _is_taxonomy_junk_url(text)


def select_edgar_website(payload: dict[str, Any]) -> str:
    """Pick the best usable website from an EDGAR submissions.json payload.

    Prefers the main `website` field over `investorWebsite` - main domains
    have proven more robust to unattended clients than investor-relations
    subdomains (the ADI Global Cloudflare bot-challenge precedent - see
    DEFAULT_COMPANY_WEBSITES in popday/config.py). Falls back to
    investorWebsite if `website` is empty or junk. Returns "" (never a wrong
    link) if neither field survives the filter.
    """
    for field in ("website", "investorWebsite"):
        candidate = str(payload.get(field) or "").strip()
        if candidate and _is_usable_website(candidate):
            return candidate
    return ""


def fetch_edgar_website(client: EdgarClient, cik: str) -> str:
    """One data.sec.gov submissions.json fetch, resolved to a usable website
    or "". Raises EdgarBlockedError/EdgarUnavailableError on a hard fetch
    failure (via the shared EdgarClient retry/backoff logic) - callers must
    log that, never swallow it into an indistinguishable "" result."""
    payload = client.get_json(SUBMISSIONS_URL.format(cik=normalize_cik(cik)))
    return select_edgar_website(payload)


def company_key(company_name: object) -> str:
    """Case/whitespace-only normalisation for matching a company_name against
    a curated-link dict - punctuation is deliberately significant (see
    flask_app.py's _company_website(), the primary caller)."""
    return " ".join(str(company_name or "").strip().lower().split())


def resolve_company_website(
    company_name: object,
    cik: object,
    curated_websites: dict[str, str] | None,
    edgar_websites: dict[str, str] | None,
    resolved_websites: dict[str, str] | None = None,
) -> str:
    """The one company_url resolution order used everywhere it's rendered:
    curated (config.json override, then DEFAULT_COMPANY_WEBSITES) always
    wins; the resolved-websites cache (popday/wikidata_resolver.py - curated
    Wikidata structured data, keyed by company name since it also covers UK
    companies with no CIK) fills a gap curation hasn't reached; EDGAR's
    self-reported website (by CIK) is the last resort, kept only because
    it's a harmless, already-fetched value on the rare filer that sets it
    (near-zero coverage in practice - see HANDOFF.md); otherwise "" - never
    a guessed link. Kept independent of Flask so non-web callers (e.g. the
    missing-link count in scripts/generate_status_json.py) share this exact
    logic.
    """
    lookup = {company_key(name): url for name, url in (curated_websites or {}).items()}
    key = company_key(company_name)
    curated = lookup.get(key, "")
    if curated:
        return curated
    if resolved_websites:
        resolved = resolved_websites.get(key, "")
        if resolved:
            return resolved
    if cik and edgar_websites:
        return edgar_websites.get(normalize_cik(cik), "")
    return ""
