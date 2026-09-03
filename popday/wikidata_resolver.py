"""Company website resolution via Wikidata's structured data (P856 "official
website"), keyed by an entity search on company name.

Replaces the heuristic domain-guessing approach (popday/website_resolver.py,
retired after a production incident: a guessed domain for "Kyivstar Group
Ltd." turned out to be squatted by a cloaking redirect gate leading to an
unrelated scam site - a live adversarial page can defeat content-based
verification on a retry even when it failed it moments before). Wikidata is
curated, human-reviewed structured data, not a guess - the trustworthy
replacement. Still validated (never a taxonomy-junk or malformed URL) before
ever being returned, and still "" - never a wrong link - when nothing
resolves. See resolve_company_website()'s priority order in
popday/company_websites.py: curated > this cache > EDGAR (last resort).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .company_websites import _is_usable_website

API_URL = "https://www.wikidata.org/w/api.php"
OFFICIAL_WEBSITE_PROPERTY = "P856"
TICKER_SYMBOL_PROPERTY = "P249"
INSTANCE_OF_PROPERTY = "P31"

# A short/ambiguous company name (e.g. "Brunswick", "Harrow", "Dana") often
# collides with a far more "notable" unrelated Wikidata entity that happens
# to have a P856 too - a US city government, a UK school, a singer, all
# confirmed false positives during testing. Requiring the matched entity's
# "instance of" claim to be one of these business classes is the gate that
# separates them: every real company checked in testing carried at least
# one of these; none of the false positives did.
_BUSINESS_ENTITY_TYPES = {
    "Q4830453",  # business
    "Q891723",   # public company
    "Q783794",   # company
    "Q6881511",  # enterprise
}

# Deliberately narrow: only unambiguous legal-entity/registration tokens.
# Brand-carrying words like "Holdings" or "Group" are NOT here - stripping
# those can turn a specific company into an ambiguous generic search term
# (testing found "YETI Holdings" resolves correctly on Wikidata; bare "YETI"
# does not). "de" catches a trailing SEC state-of-incorporation tag like
# "/DE/" once punctuation is stripped per-token.
_SEARCH_SUFFIX_TOKENS = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "llc", "llp", "lp", "plc", "nv", "sa", "ag", "se", "spa",
    "gmbh", "de",
}


def _clean_search_name(company_name: object) -> str:
    """Trailing legal-suffix tokens stripped for a cleaner Wikidata search
    query - but never a token preceded by "&"/"and", since a phrase like
    "Merck & Co" is often the company's actual distinguishing name, not a
    generic suffix (testing found bare "Merck" alone resolves to the wrong,
    unrelated Merck KGaA / Merck Group on Wikidata; "Merck & Co" resolves to
    the intended Merck & Co., Inc.)."""
    tokens = str(company_name or "").split()
    while tokens:
        bare = re.sub(r"[^a-z0-9]", "", tokens[-1].lower())
        if not bare:
            tokens.pop()
            continue
        if bare in _SEARCH_SUFFIX_TOKENS:
            preceding = tokens[-2].strip(".,").lower() if len(tokens) >= 2 else ""
            if preceding in {"&", "and"}:
                break
            tokens.pop()
            continue
        break
    cleaned = " ".join(tokens).strip().rstrip(",.")
    return cleaned or str(company_name or "").strip()


def _get_json(params: dict[str, str], user_agent: str, *, timeout: float = 10.0) -> dict[str, Any]:
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def search_candidate_qids(company_name: str, user_agent: str, *, limit: int = 5) -> list[str]:
    """Wikidata entity IDs matching a company name, in Wikidata's own
    relevance order. Raises on a hard fetch failure - callers must not
    swallow this into an indistinguishable "no match" the way they would a
    genuinely empty result (same honest-failure contract as
    popday/company_websites.py's fetch_edgar_website)."""
    payload = _get_json(
        {
            "action": "wbsearchentities",
            "search": company_name,
            "language": "en",
            "type": "item",
            "limit": str(limit),
            "format": "json",
        },
        user_agent,
    )
    return [str(item["id"]) for item in payload.get("search", []) if item.get("id")]


def fetch_entity_claims(qid: str, user_agent: str) -> dict[str, Any]:
    """Raw claims dict for one Wikidata entity. Raises on a hard fetch
    failure, same contract as search_candidate_qids."""
    payload = _get_json(
        {"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"},
        user_agent,
    )
    return payload.get("entities", {}).get(qid, {}).get("claims", {}) or {}


def _claim_value(claims: dict[str, Any], property_id: str) -> str:
    for statement in claims.get(property_id) or []:
        try:
            value = statement["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _claim_entity_ids(claims: dict[str, Any], property_id: str) -> set[str]:
    """QIDs referenced by an entity-valued claim (e.g. P31 "instance of") -
    a different datavalue shape than the plain-string claims _claim_value
    reads (item claims carry {"id": "Q..."}, not a bare string)."""
    ids: set[str] = set()
    for statement in claims.get(property_id) or []:
        try:
            qid = statement["mainsnak"]["datavalue"]["value"]["id"]
        except (KeyError, TypeError):
            continue
        if isinstance(qid, str) and qid:
            ids.add(qid)
    return ids


def _is_business_entity(claims: dict[str, Any]) -> bool:
    """True only if this Wikidata entity is itself classified as some kind
    of business/company (see _BUSINESS_ENTITY_TYPES). The critical
    disambiguating gate: a short/ambiguous company name search can rank an
    unrelated but more "notable" entity (a city, a school, a person) above
    the actual company, and that entity can have its own unrelated P856."""
    return bool(_claim_entity_ids(claims, INSTANCE_OF_PROPERTY) & _BUSINESS_ENTITY_TYPES)


def select_official_website(claims: dict[str, Any]) -> str:
    """P856 if present, not junk (reuses the same taxonomy-junk filter the
    EDGAR fill source uses), and the entity is actually a business - ""
    otherwise. Never a guess: a malformed URL, a filtered one, or one that
    belongs to a same-named-but-unrelated entity is treated identically to
    no URL at all."""
    if not _is_business_entity(claims):
        return ""
    website = _claim_value(claims, OFFICIAL_WEBSITE_PROPERTY)
    return website if website and _is_usable_website(website) else ""


def resolve_website_wikidata(
    company_name: str, user_agent: str, *, ticker: str = ""
) -> str:
    """Search Wikidata by company name, optionally disambiguating multiple
    candidates by ticker symbol (P249), and return the first candidate's
    verified official website - "" if search finds nothing, no candidate
    has a usable P856, or any fetch fails. Never raises - a network or API
    failure must never block the alert this runs ahead of (see
    popday/cli.py's _ensure_resolved_website).

    Searches with the suffix-cleaned name first (Wikidata's search often
    misses on a full SEC-style legal name like "Cytokinetics Inc" - testing
    found it needs "Cytokinetics"), falling back to the untouched raw name
    if that finds nothing, since cleaning is a heuristic and could in
    principle be too aggressive for a name this codebase hasn't seen yet.
    """
    cleaned = _clean_search_name(company_name)
    queries = [cleaned] if cleaned == str(company_name or "").strip() else [cleaned, str(company_name)]

    candidates: list[str] = []
    for query in queries:
        try:
            candidates = search_candidate_qids(query, user_agent)
        except Exception:
            continue
        if candidates:
            break
    if not candidates:
        return ""

    fetched: dict[str, dict[str, Any]] = {}

    def _claims(qid: str) -> dict[str, Any]:
        if qid not in fetched:
            try:
                fetched[qid] = fetch_entity_claims(qid, user_agent)
            except Exception:
                fetched[qid] = {}
        return fetched[qid]

    # An exact ticker match is the strongest signal available (disambiguates
    # e.g. "Merck" between the unrelated US and German companies) - checked
    # first, but only wins if that entity also has a usable website.
    if ticker:
        for qid in candidates:
            if _claim_value(_claims(qid), TICKER_SYMBOL_PROPERTY).upper() == ticker.strip().upper():
                website = select_official_website(_claims(qid))
                if website:
                    return website

    # No ticker, or no ticker-matched candidate had a website: fall through
    # to Wikidata's own relevance ranking, taking the first candidate that
    # actually has a usable P856 (not just the top search hit - a highly
    # notable but website-less entity, e.g. a stock-index redirect page,
    # can rank above the actual company page).
    for qid in candidates:
        website = select_official_website(_claims(qid))
        if website:
            return website
    return ""
