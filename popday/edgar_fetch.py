"""SEC EDGAR fetching with conservative rate limiting, honest failures, and retries.

Design notes (July 2026 rework):
- SEC's edge now applies bot filtering to the daily-index paths. A 403 there can
  mean "you are blocked", NOT "index not published yet". We therefore never
  translate 403 into a soft success. Callers get an EdgarBlockedError.
- Primary discovery now uses the officially supported full-text search JSON API
  (efts.sec.gov). We query the include phrases directly, which turns ~500 heavy
  envelope downloads per day into a handful of small JSON calls. The daily
  master.idx remains as a fallback discovery path.
- Requests send gzip-friendly, browser-adjacent headers. `Accept-Encoding:
  identity` was both against SEC guidance and a bot-fingerprint tell.
"""

from __future__ import annotations

import gzip
import io
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date

SEC_BASE = "https://www.sec.gov"
EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
TARGET_FORMS = {"8-K", "6-K"}


class EdgarBlockedError(RuntimeError):
    """Raised when SEC EDGAR refuses the request (403/blocked), after retries."""

    def __init__(self, url: str, status: int, body_snippet: str = ""):
        super().__init__(f"SEC EDGAR refused {url} (HTTP {status}). {body_snippet}".strip())
        self.url = url
        self.status = status
        self.body_snippet = body_snippet


class EdgarUnavailableError(RuntimeError):
    """Raised for network failures / 5xx after retries."""


@dataclass(frozen=True)
class Filing:
    accession_number: str
    cik: str
    company_name: str
    form_type: str
    filing_date: str
    filing_url: str
    primary_document: str
    acceptance_datetime: str = ""
    matched_phrase: str = ""  # set when discovered via full-text search


@dataclass
class FetchStats:
    requests: int = 0
    retries: int = 0
    http_403: int = 0
    sources_used: list[str] = field(default_factory=list)


class EdgarClient:
    def __init__(self, user_agent: str, delay_seconds: float = 0.65):
        self.user_agent = user_agent
        self.delay_seconds = max(delay_seconds, 0.1)
        self._last_request = 0.0
        self.stats = FetchStats()

    # ------------------------------------------------------------------ HTTP

    def _headers(self, accept: str) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }

    def _request(self, url: str, *, attempts: int = 4, accept: str = "*/*") -> bytes:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

        request = urllib.request.Request(url, headers=self._headers(accept))
        last_403_body = ""
        for attempt in range(attempts):
            self.stats.requests += 1
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    self._last_request = time.monotonic()
                    raw = response.read()
                    if response.headers.get("Content-Encoding", "") == "gzip":
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    return raw
            except urllib.error.HTTPError as exc:
                self._last_request = time.monotonic()
                if exc.code == 403:
                    self.stats.http_403 += 1
                    try:
                        last_403_body = exc.read()[:300].decode("utf-8", errors="replace")
                    except Exception:
                        last_403_body = ""
                if exc.code == 404:
                    raise  # genuinely absent; let the caller decide
                if exc.code not in {403, 429, 500, 502, 503, 504} or attempt == attempts - 1:
                    if exc.code == 403:
                        raise EdgarBlockedError(url, 403, last_403_body) from exc
                    raise
                self.stats.retries += 1
                # SEC IP blocks are commonly ~10 minutes; long backoff with jitter
                # gives a real chance of riding one out inside a scheduled run.
                base = 90 if exc.code in {403, 429} else 2**attempt
                time.sleep(base * (attempt + 1) + random.uniform(0, 5))
            except urllib.error.URLError as exc:
                self._last_request = time.monotonic()
                if attempt == attempts - 1:
                    raise EdgarUnavailableError(str(exc.reason)) from exc
                self.stats.retries += 1
                time.sleep(min(2**attempt, 16) + random.uniform(0, 2))
        raise EdgarUnavailableError(f"Unable to fetch {url}")

    def get_text(self, url: str) -> str:
        return self._request(url).decode("utf-8", errors="replace")

    def get_json(self, url: str) -> dict:
        return json.loads(
            self._request(url, accept="application/json").decode("utf-8", errors="replace")
        )

    # ---------------------------------------------- Primary: full-text search

    def _efts_url(self, phrase: str, run_date: date) -> str:
        day = run_date.isoformat()
        params = {
            "q": f'"{phrase}"',
            "dateRange": "custom",
            "startdt": day,
            "enddt": day,
            "forms": ",".join(sorted(TARGET_FORMS)),
        }
        return f"{EFTS_BASE}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _filing_from_efts_hit(hit: dict, phrase: str) -> Filing | None:
        src = hit.get("_source", {})
        raw_id = hit.get("_id", "")  # e.g. "0001234567-26-000123:doc.htm"
        accession_dashed, _, doc = raw_id.partition(":")
        ciks = src.get("ciks") or []
        names = src.get("display_names") or []
        form = (src.get("root_forms") or [src.get("file_type", "")])[0]
        if form not in TARGET_FORMS:
            return None
        if not accession_dashed or not ciks:
            return None
        cik = str(ciks[0]).lstrip("0").zfill(10) if str(ciks[0]).strip("0") else str(ciks[0]).zfill(10)
        company = names[0].split("  (CIK")[0].strip() if names else ""
        envelope = f"{SEC_BASE}/Archives/edgar/data/{int(cik)}/{accession_dashed}.txt"
        return Filing(
            accession_number=accession_dashed,
            cik=cik,
            company_name=company,
            form_type=form,
            filing_date=src.get("file_date", ""),
            filing_url=envelope,
            primary_document=doc or f"{accession_dashed}.txt",
            acceptance_datetime=src.get("file_date", ""),
            matched_phrase=phrase,
        )

    def search_filings_for_phrases(
        self, run_date: date, phrases: list[str]
    ) -> list[Filing]:
        """Discover candidate filings via the EDGAR full-text search JSON API.

        One small JSON request per phrase; results deduplicated by accession.
        Raises EdgarBlockedError / EdgarUnavailableError on hard failure so the
        caller can fall back to the daily index (and never fake a green run).
        """
        seen: dict[str, Filing] = {}
        for phrase in phrases:
            payload = self.get_json(self._efts_url(phrase, run_date))
            for hit in payload.get("hits", {}).get("hits", []):
                filing = self._filing_from_efts_hit(hit, phrase)
                if filing and filing.accession_number not in seen:
                    seen[filing.accession_number] = filing
        self.stats.sources_used.append("efts")
        return list(seen.values())

    # ------------------------------------------------ Fallback: daily index

    def _daily_index_url(self, run_date: date) -> str:
        quarter = ((run_date.month - 1) // 3) + 1
        yyyymmdd = run_date.strftime("%Y%m%d")
        return (
            f"{SEC_BASE}/Archives/edgar/daily-index/{run_date.year}/"
            f"QTR{quarter}/master.{yyyymmdd}.idx"
        )

    def _filing_from_index_line(self, line: str) -> Filing | None:
        parts = line.split("|")
        if len(parts) != 5:
            return None
        cik, company_name, form_type, filing_date, filename = [
            part.strip() for part in parts
        ]
        if form_type not in TARGET_FORMS:
            return None
        accession_number = filename.rsplit("/", 1)[-1].replace(".txt", "")
        return Filing(
            accession_number=accession_number,
            cik=cik.zfill(10),
            company_name=company_name,
            form_type=form_type,
            filing_date=filing_date,
            filing_url=f"{SEC_BASE}/Archives/{filename}",
            primary_document=filename.rsplit("/", 1)[-1],
        )

    def filings_for_date(
        self, run_date: date, max_companies: int | None = None
    ) -> list[Filing]:
        raw_index = self.get_text(self._daily_index_url(run_date))
        filings: list[Filing] = []
        in_records = False
        for line in raw_index.splitlines():
            if line.startswith("-----"):
                in_records = True
                continue
            if not in_records:
                continue
            filing = self._filing_from_index_line(line)
            if filing:
                filings.append(filing)
                if max_companies and len(filings) >= max_companies:
                    break
        self.stats.sources_used.append("daily-index")
        return filings
