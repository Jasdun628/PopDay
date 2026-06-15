"""Optional V2 stock reaction enrichment.

This module is intentionally separate from V1 alerts. It is not imported by the
scan path and never affects email content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


@dataclass(frozen=True)
class StockReaction:
    ticker: str
    previous_close_before_filing: float
    next_close_after_filing: float
    reaction_pct: float
    reaction_computed_timestamp: str
    price_data_source: str = "yfinance"


def compute_reaction(ticker: str, filing_date: date) -> StockReaction:
    import yfinance as yf

    start = filing_date - timedelta(days=7)
    end = filing_date + timedelta(days=7)
    history = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    if history.empty:
        raise RuntimeError(f"No price history returned for {ticker}")

    before = history[history.index.date < filing_date]
    after = history[history.index.date > filing_date]
    if before.empty or after.empty:
        raise RuntimeError(f"Could not find previous and next closes around {filing_date}")

    previous_close = float(before.iloc[-1]["Close"])
    next_close = float(after.iloc[0]["Close"])
    reaction_pct = 100 * (next_close - previous_close) / previous_close
    return StockReaction(
        ticker=ticker,
        previous_close_before_filing=previous_close,
        next_close_after_filing=next_close,
        reaction_pct=reaction_pct,
        reaction_computed_timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
