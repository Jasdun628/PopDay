"""SEC EDGAR discovery adapter (US market) - thin wrap, not a rewrite.

The real EDGAR machinery lives in popday.edgar_fetch (EdgarClient, Filing,
the blocked/unavailable error types, the EFTS control probe). This module
gives it the same adapter face as popday.sources.investegate - SOURCE_ID,
scan() -> list[Announcement], probe_canary() - for orchestration-level code
that wants to treat markets uniformly.

Deliberate limitation, documented honestly: the US scan pipeline inside
popday/cli.py still consumes Filing objects directly (it needs the raw SGML
envelope parse, exhibits, acceptance datetimes - far richer than the common
Announcement shape). Rewiring that battle-tested path through Announcement
would be regression risk for zero user value, so cli.py keeps using
EdgarClient natively and this wrap exists for the uniform-interface layer
(coverage/canary plumbing, future convergence).
"""

from __future__ import annotations

from datetime import date

from ..edgar_fetch import (  # noqa: F401 - re-exported as the canonical import site
    EdgarBlockedError,
    EdgarClient,
    EdgarUnavailableError,
    Filing,
    TARGET_FORMS,
)
from . import Announcement

SOURCE_ID = "edgar"
MARKET = "US"


def announcement_from_filing(filing: Filing) -> Announcement:
    return Announcement(
        source=SOURCE_ID,
        market=MARKET,
        dedup_key=filing.accession_number,
        company_name=filing.company_name,
        company_identifier=filing.cik,
        headline="",  # EDGAR discovery is phrase-search based; filings carry no headline
        wire_or_form=filing.form_type,
        announced_at=filing.acceptance_datetime or filing.filing_date,
        detail_url=filing.filing_url,
        raw_text="",
    )


class EdgarSource:
    """Adapter face over EdgarClient matching InvestegateClient's contract."""

    def __init__(self, user_agent: str, delay_seconds: float = 0.65):
        self.client = EdgarClient(user_agent, delay_seconds)

    @property
    def stats(self):
        return self.client.stats

    def scan(self, run_date: date, include_phrases: list[str]) -> list[Announcement]:
        filings = self.client.search_filings_for_phrases(run_date, include_phrases)
        return [announcement_from_filing(filing) for filing in filings]

    def probe_canary(self) -> bool:
        return self.client.discovery_control_probe()
