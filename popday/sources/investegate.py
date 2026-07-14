"""Investegate (investegate.co.uk) discovery adapter for UK-listed companies.

Source mechanics (verified 2026-07-14):
- Homepage / day-archive pages are server-rendered HTML tables, one calendar
  day each: https://www.investegate.co.uk/ (today) or
  https://www.investegate.co.uk/today-announcements/YYYY-MM-DD (any day back
  to 1999, per the announcement-archive calendar). Both accept
  ?perPage=300&page=N (valid perPage values: 50/100/200/300, read from the
  page's own <select class="table-length-selector">); ~700-900 rows/day fits
  comfortably in page=1..3 at perPage=300.
- Row fields: timestamp (Europe/London, "%d %b %Y %I:%M %p"), wire source
  (RNS/PRN/MFN/BZW/...), company name + EPIC ticker, headline, detail URL of
  the form /announcement/{wire}/{company-slug}--{ticker-slug}/{headline-slug}/{id}.
- Dedup key: the trailing numeric ID (e.g. 9668735) - the UK equivalent of an
  EDGAR accession number.
- robots.txt (checked once at client construction) disallows /tp001,
  /ui/login, /ui/register, /callback, /search-company, and some ad/
  email-click paths - none of which this adapter uses.
- Terms are private-investor personal use, no redistribution: this adapter
  extracts nuggets/dates only. fetch_detail_text() is used solely to run
  trigger-phrase/date extraction on matched headlines; the resulting raw text
  must never be rendered verbatim on the public web UI (link out to the
  Investegate detail page instead - see HANDOFF.md).
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from . import Announcement

SOURCE_ID = "investegate"
BASE = "https://www.investegate.co.uk"
LONDON_TZ = ZoneInfo("Europe/London")
UTC_TZ = ZoneInfo("UTC")

# Pinned canary: a real, historical FTSE-100 Capital Markets Day announcement
# (Glencore plc, "Notice of Capital Markets Day", filed 21 Oct 2025 09:00 UK,
# event 3 Dec 2025 1pm UK). Verified 2026-07-14 to parse correctly via this
# adapter. If a live day comes back with zero headline matches, fetching this
# fixed URL and confirming it still parses as expected distinguishes a
# genuinely quiet day from the site having changed under us / this adapter
# being silently broken.
CANARY_URL = f"{BASE}/announcement/rns/glencore--glen/notice-of-capital-markets-day/9184165"
CANARY_EXPECTED_DEDUP_KEY = "9184165"
CANARY_EXPECTED_HEADLINE = "Notice of Capital Markets Day"
CANARY_EXPECTED_COMPANY_SUBSTRING = "Glencore"

# Startup robots.txt check: every path prefix this adapter actually fetches.
_NEEDED_PATH_PREFIXES = ("/", "/today-announcements/", "/announcement/", "/announcement-archive")

# 200-status "block" pages (Cloudflare challenges etc.) must be detected by
# body content, not just HTTP status - a 200 challenge page is still a block,
# never a silent success.
_CHALLENGE_SIGNATURES = (
    "checking your browser",
    "cf-browser-verification",
    "attention required! | cloudflare",
    "just a moment...",
)


class InvestegateBlockedError(RuntimeError):
    """Raised on 403 / 429-after-backoff / a detected bot-challenge page.

    A block is always a failed run, never a silent success.
    """

    def __init__(self, url: str, status: int, reason: str = ""):
        super().__init__(f"Investegate refused {url} (HTTP {status}). {reason}".strip())
        self.url = url
        self.status = status
        self.reason = reason


class InvestegateRobotsDisallowedError(RuntimeError):
    """Raised at client construction if robots.txt now disallows a path this
    adapter relies on. Fetching must stop, not silently proceed."""


@dataclass
class FetchStats:
    requests: int = 0
    retries: int = 0
    blocked: int = 0
    sources_used: list[str] = field(default_factory=list)


def _headline_matches(headline: str, include_phrases: list[str], exclude_phrases: list[str]) -> bool:
    lowered = headline.lower()
    if any(excl.lower() in lowered for excl in exclude_phrases):
        return False
    return any(
        re.search(rf"\b{re.escape(phrase.lower())}\b", lowered) for phrase in include_phrases
    )


class InvestegateClient:
    def __init__(self, user_agent: str, delay_seconds: float = 2.0, *, skip_robots_check: bool = False):
        self.user_agent = user_agent
        # Brief mandates max 1 request / 2 seconds - never allow a lower delay.
        self.delay_seconds = max(delay_seconds, 2.0)
        self._last_request = 0.0
        self.stats = FetchStats()
        if not skip_robots_check:
            self._check_robots()

    # ------------------------------------------------------------------ HTTP

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-GB,en;q=0.9",
        }

    def _check_robots(self) -> None:
        try:
            body = self._request(f"{BASE}/robots.txt", attempts=1)
        except Exception:
            return  # robots.txt itself unreachable - don't hard-fail startup on that
        disallowed: set[str] = set()
        for line in body.decode("utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("disallow:"):
                path = stripped.split(":", 1)[1].strip().rstrip("*")
                if path:
                    disallowed.add(path)
        for needed in _NEEDED_PATH_PREFIXES:
            for blocked in disallowed:
                if needed.startswith(blocked):
                    raise InvestegateRobotsDisallowedError(
                        f"robots.txt now disallows '{blocked}', which this adapter needs for '{needed}'."
                    )

    def _request(self, url: str, *, attempts: int = 4) -> bytes:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        request = urllib.request.Request(url, headers=self._headers())
        for attempt in range(attempts):
            self.stats.requests += 1
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    self._last_request = time.monotonic()
                    return response.read()
            except urllib.error.HTTPError as exc:
                self._last_request = time.monotonic()
                if exc.code in (403, 429):
                    self.stats.blocked += 1
                    if attempt == attempts - 1:
                        raise InvestegateBlockedError(url, exc.code) from exc
                    self.stats.retries += 1
                    time.sleep(min(2**attempt, 30) * 2)
                    continue
                if exc.code == 404:
                    raise
                if attempt == attempts - 1:
                    raise
                self.stats.retries += 1
                time.sleep(min(2**attempt, 16))
            except urllib.error.URLError:
                self._last_request = time.monotonic()
                if attempt == attempts - 1:
                    raise
                self.stats.retries += 1
                time.sleep(min(2**attempt, 16))
        raise RuntimeError(f"Unable to fetch {url}")

    def get_html(self, url: str) -> str:
        body = self._request(url)
        text = body.decode("utf-8", errors="replace")
        lowered = text.lower()
        if any(sig in lowered for sig in _CHALLENGE_SIGNATURES):
            raise InvestegateBlockedError(url, 200, "response body matches a bot-challenge signature")
        return text

    # ------------------------------------------------------------- Discovery

    @staticmethod
    def _day_url(run_date: date, page: int) -> str:
        params: dict[str, object] = {"perPage": 300}
        if page > 1:
            params["page"] = page
        return f"{BASE}/today-announcements/{run_date.isoformat()}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _rows_from_html(html: str) -> list[Announcement]:
        soup = BeautifulSoup(html, "html.parser")
        rows: list[Announcement] = []
        for tr in soup.select("table.table-investegate tbody tr"):
            cells = tr.find_all("td")
            if len(cells) < 4:
                continue
            time_text = cells[0].get_text(strip=True)
            wire_link = cells[1].find("a")
            wire = wire_link.get_text(strip=True) if wire_link else ""
            company_href = ""
            company_text = ""
            for link in cells[2].find_all("a"):
                href = link.get("href") or ""
                if "/company/" in href:
                    company_href = href
                    company_text = link.get_text(strip=True)
            headline_link = cells[3].find("a", class_="announcement-link")
            if not headline_link:
                continue
            detail_url = headline_link.get("href", "")
            headline = headline_link.get_text(strip=True)
            numeric_id = detail_url.rstrip("/").rsplit("/", 1)[-1]
            if not numeric_id.isdigit():
                continue
            ticker = company_href.rstrip("/").rsplit("/", 1)[-1] if company_href else ""
            # Company link text is "Name (TICKER)" - strip the trailing
            # parenthetical since the ticker is already captured separately.
            company_name = re.sub(r"\s*\([^)]*\)\s*$", "", company_text).strip() or company_text
            try:
                dt_naive = datetime.strptime(time_text, "%d %b %Y %I:%M %p")
            except ValueError:
                continue
            dt_utc = dt_naive.replace(tzinfo=LONDON_TZ).astimezone(UTC_TZ)
            rows.append(
                Announcement(
                    source=SOURCE_ID,
                    market="UK",
                    dedup_key=numeric_id,
                    company_name=company_name,
                    company_identifier=ticker,
                    headline=headline,
                    wire_or_form=wire,
                    announced_at=dt_utc.isoformat(timespec="seconds"),
                    detail_url=detail_url,
                    raw_text="",
                )
            )
        return rows

    def index_for_date(self, run_date: date, *, max_pages: int = 4) -> list[Announcement]:
        """Every announcement row for one UK calendar day (no headline
        filtering, no detail fetch) - the full day's index, deduplicated on
        the numeric ID. Walks pages until a short page signals the end."""
        seen: dict[str, Announcement] = {}
        for page in range(1, max_pages + 1):
            html = self.get_html(self._day_url(run_date, page))
            rows = self._rows_from_html(html)
            for row in rows:
                seen.setdefault(row.dedup_key, row)
            if len(rows) < 300:
                break
        self.stats.sources_used.append("index")
        return list(seen.values())

    def fetch_detail_text(self, detail_url: str) -> str:
        """Full plain-text body of one announcement detail page.

        Only called for headline-matched rows, per Investegate's terms
        (personal use, no redistribution) - the pipeline extracts nuggets and
        dates from this text but never renders it verbatim on the web UI.
        """
        html = self.get_html(detail_url)
        soup = BeautifulSoup(html, "html.parser")
        # The RNS wire body is a nested <body> tag embedded verbatim inside
        # the outer page by the distributor - take the LAST <body>, not the
        # first (the outer page's own <body>).
        bodies = soup.find_all("body")
        target = bodies[-1] if bodies else soup
        return target.get_text(" ", strip=True)

    def scan(
        self,
        run_date: date,
        include_phrases: list[str],
        exclude_phrases: list[str] | None = None,
        *,
        max_pages: int = 4,
    ) -> list[Announcement]:
        """Headline-matched announcements for one UK day, with full detail
        text fetched (only) for matches - mirrors EdgarClient.
        search_filings_for_phrases()'s contract: returns candidates, not the
        full firehose.
        """
        exclude_phrases = exclude_phrases or []
        index = self.index_for_date(run_date, max_pages=max_pages)
        matches = [
            row for row in index if _headline_matches(row.headline, include_phrases, exclude_phrases)
        ]
        return [replace(row, raw_text=self.fetch_detail_text(row.detail_url)) for row in matches]

    # --------------------------------------------------------------- Canary

    def probe_canary(self) -> bool:
        """True if the pinned historical Glencore CMD announcement still
        parses as expected. Called only when a live day comes back with zero
        headline matches, to distinguish "genuinely quiet day" from "the
        site changed and this adapter is silently broken".
        """
        try:
            html = self.get_html(CANARY_URL)
        except InvestegateBlockedError:
            return False
        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.find(id="main-title")
        title_text = title_el.get_text(strip=True) if title_el else ""
        if CANARY_EXPECTED_HEADLINE not in title_text:
            return False
        if CANARY_EXPECTED_DEDUP_KEY not in html:
            return False
        if CANARY_EXPECTED_COMPANY_SUBSTRING not in html:
            return False
        return True
