"""Heuristic company-website auto-resolution.

Generates a candidate domain from a company name and verifies it before ever
storing it - a live page, not a parked/for-sale placeholder, whose content
plausibly mentions the company. A wrong link is worse than no link (see
HANDOFF.md); if nothing survives verification this returns "" and the caller
leaves the company unlinked, exactly like the EDGAR fill source in
popday/company_websites.py. This only ever fills a gap - see
resolve_company_website()'s priority order (curated > EDGAR > this).
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

RESOLUTION_METHOD = "heuristic_domain_guess"

# Legal-entity / generic words that carry no brand meaning on their own -
# stripped from the end of a company name before guessing a domain so
# "Commercial Metals Co" and "Commercial Metals Company" guess the same
# domain, and so a bare "The" never becomes part of a candidate.
_SUFFIX_WORDS = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "plc",
    "ltd", "limited", "llc", "lp", "llp", "sa", "nv", "ag", "se", "spa",
    "gmbh", "holdings", "holding", "group", "trust", "the", "de", "class",
}

_PARKED_MARKERS = (
    "domain is for sale",
    "this domain may be for sale",
    "buydomains",
    "hugedomains",
    "parkingcrew",
    "sedo domain parking",
    "domain parking",
    "future home of",
    "this web page is parked",
    "godaddy.com/domainfor sale",
)

# Real company homepages carry substantial visible text (nav, footer, copy).
# A page below this threshold is either a parked placeholder or a
# client-side redirect/bot-gate whose only "content" is script - the exact
# shape of the domain-squatter incident below.
_MIN_VISIBLE_TEXT_CHARS = 80

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _visible_text(html: str) -> str:
    """Plain text a human reader would actually see - script/style content
    stripped first. Content-matching and the parked-domain check both run
    against this, never raw HTML: a domain-squatter redirect page can
    legitimately contain its own hostname inside a <script> block (see
    "Kyivstar Group Ltd." -> a JS window.location.replace() gate that
    echoed "kyivstar.com" in its own redirect URL and would otherwise have
    passed as a "content match" for zero real content)."""
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", html)
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", without_scripts)).strip()


def _words(company_name: object) -> list[str]:
    text = re.sub(r"[^a-z0-9\s]", " ", str(company_name or "").lower())
    words = [w for w in text.split() if w]
    while words and words[-1] in _SUFFIX_WORDS:
        words.pop()
    return words


def domain_candidates(company_name: object) -> list[str]:
    """Ordered, deduplicated .com domain guesses, most confident first.

    Empty if nothing brand-like survives suffix stripping (e.g. the name was
    just "Inc") - never guess off an empty base. Deliberately narrow: only
    the full (concatenated and hyphenated) name is tried, never a single
    generic word, to keep the false-positive rate low before verification
    even runs.
    """
    words = _words(company_name)
    if not words:
        return []
    candidates = [f"{''.join(words)}.com"]
    if len(words) > 1:
        hyphenated = f"{'-'.join(words)}.com"
        if hyphenated != candidates[0]:
            candidates.append(hyphenated)
    return candidates


def _looks_parked(visible_text_lower: str) -> bool:
    return any(marker in visible_text_lower for marker in _PARKED_MARKERS)


def _content_matches_company(visible_text_lower: str, company_name: object) -> bool:
    """Light confidence signal beyond "the domain is live": the page's
    visible text must mention the company's most distinctive name word. A
    short/generic anchor (e.g. "SES", "3M") is exactly the case where a
    coincidental concatenation is most likely to already be someone else's
    registered domain (see the "SES S.a." -> sessa.com incident this
    guarded against in testing) - so it's rejected outright rather than
    waved through on liveness alone. "Omit rather than show wrong data"."""
    words = _words(company_name)
    if not words:
        return False
    anchor = max(words, key=len)
    if len(anchor) < 4:
        return False
    return anchor in visible_text_lower


class WebsiteCandidateChecker:
    """One HTTP HEAD-then-GET verification per candidate domain. Never
    raises - a DNS failure, timeout, or non-HTML response just means "not
    verified", exactly like a fetch failure anywhere else in this
    codebase (see popday/edgar_fetch.py's honest-failure convention)."""

    def __init__(self, user_agent: str, *, timeout: float = 8.0):
        self.user_agent = user_agent
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def verify(self, domain: str, company_name: object) -> str:
        url = f"https://{domain}/"
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                content_type = response.headers.get("Content-Type", "")
                if status != 200 or "html" not in content_type.lower():
                    return ""
                body = response.read(65536).decode("utf-8", errors="replace")
                final_url = response.geturl()
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return ""

        visible = _visible_text(body)
        if len(visible) < _MIN_VISIBLE_TEXT_CHARS:
            return ""
        visible_lower = visible.lower()
        if _looks_parked(visible_lower) or not _content_matches_company(visible_lower, company_name):
            return ""

        parsed = urlparse(final_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}/"


def resolve_website_heuristic(
    company_name: object,
    user_agent: str,
    *,
    delay_seconds: float = 0.0,
) -> str:
    """Try each domain candidate in order, verify, return the first
    confident match - "" if none survive. Never raises."""
    checker = WebsiteCandidateChecker(user_agent)
    for i, domain in enumerate(domain_candidates(company_name)):
        if delay_seconds and i:
            time.sleep(delay_seconds)
        url = checker.verify(domain, company_name)
        if url:
            return url
    return ""
