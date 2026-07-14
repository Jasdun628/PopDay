"""Common interface for PopDay discovery source adapters.

Each market's discovery mechanism (SEC EDGAR for US, Investegate for UK) is
one adapter module under popday/sources/. Every adapter exposes:
  - a SOURCE_ID constant identifying it ('edgar', 'investegate')
  - a client class with scan(date, ...) -> list[Announcement]
  - a probe_canary() -> bool method for the discovery-instrument canary

Everything downstream of discovery (dedup, hype tracking, price capture, web
UI, email) works on the market-agnostic Announcement shape below, never on
any adapter-specific type (SEC's Filing, or anything Investegate-specific).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Announcement:
    """A single company announcement, from any discovery source.

    dedup_key: source-specific unique identifier - EDGAR's accession number,
    Investegate's trailing numeric announcement ID. Unique within a given
    `source`, not necessarily across sources.
    """

    source: str  # SOURCE_ID of the adapter that produced this
    market: str  # 'US' or 'UK'
    dedup_key: str
    company_name: str
    company_identifier: str  # CIK (US) or EPIC ticker (UK)
    headline: str
    wire_or_form: str  # form_type (US, e.g. '8-K') or wire source (UK, e.g. 'RNS')
    announced_at: str  # ISO 8601 UTC timestamp
    detail_url: str
    raw_text: str  # plain-text announcement body, populated for matches only
