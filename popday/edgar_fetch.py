"""SEC EDGAR fetching with conservative rate limiting and retries."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date


SEC_BASE = "https://www.sec.gov"
TARGET_FORMS = {"8-K", "6-K"}


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


class EdgarClient:
    def __init__(self, user_agent: str, delay_seconds: float = 0.65):
        self.user_agent = user_agent
        self.delay_seconds = max(delay_seconds, 0.1)
        self._last_request = 0.0

    def _request(self, url: str, *, attempts: int = 4) -> bytes:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

        headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
        }
        request = urllib.request.Request(url, headers=headers)
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    self._last_request = time.monotonic()
                    return response.read()
            except urllib.error.HTTPError as exc:
                self._last_request = time.monotonic()
                if exc.code not in {403, 429, 500, 502, 503, 504} or attempt == attempts - 1:
                    raise
                time.sleep(min(2**attempt, 16))
            except urllib.error.URLError:
                self._last_request = time.monotonic()
                if attempt == attempts - 1:
                    raise
                time.sleep(min(2**attempt, 16))
        raise RuntimeError(f"Unable to fetch {url}")

    def get_text(self, url: str) -> str:
        return self._request(url).decode("utf-8", errors="replace")

    def get_json(self, url: str) -> dict:
        return json.loads(self.get_text(url))

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
        cik, company_name, form_type, filing_date, filename = [part.strip() for part in parts]
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

    def filings_for_date(self, run_date: date, max_companies: int | None = None) -> list[Filing]:
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
        return filings
