"""UK daily-close price capture (Phase 2 of the UK market extension).

Scope guard: CAPTURE ONLY. This module fetches and stores daily closing
prices for UK companies with detected events - no run-up math, no signals,
no reaction calculations. That analysis is a separate future project.

Mechanics:
- Universe: UK alert candidates whose window [announcement - 10 trading
  days, event + 10 trading days] is still active, matching the Cabrera et
  al. CAAR windows. Trading days are approximated as 14 calendar days -
  capture-only, so erring generous (a few extra days of closes) is safe and
  the exact trading-day arithmetic can live with the future analysis code.
- Ticker mapping EPIC -> Yahoo: strip a trailing '.', replace internal '.'
  with '-', append '.L' (VOD -> VOD.L, NG. -> NG.L, BT.A -> BT-A.L). Stored
  in ticker_mappings with a manual_override column an operator can set for
  the odd symbol the rule gets wrong; unresolvable tickers are logged and
  skipped, never fatal.
- GBp gotcha: Yahoo quotes LSE equities in pence (currency 'GBp'). Those
  closes are divided by 100 and stored as 'GBP'. Anything already quoted in
  a whole currency (GBP, USD, EUR) is stored as reported. Never FX-convert.
- yfinance pinned in requirements.txt; fetch failures are logged, non-fatal,
  and simply retried on the next scheduled run (upserts heal gaps).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .config import Config
from .db import Database

# 10 trading days ~= 14 calendar days (2 weekends); see module docstring.
WINDOW_CALENDAR_DAYS = 14


def map_epic_to_yahoo(epic: str) -> str:
    """Derive the Yahoo Finance symbol for an LSE EPIC ticker."""
    cleaned = str(epic or "").strip().upper().rstrip(".")
    if not cleaned:
        return ""
    return f"{cleaned.replace('.', '-')}.L"


def resolve_yahoo_symbol(db: Database, epic: str) -> str:
    """Mapping-table lookup with manual override, deriving and recording on
    first sight so every symbol PopDay ever used is auditable in one table."""
    epic = str(epic or "").strip()
    if not epic:
        return ""
    row = db.get_ticker_mapping("UK", epic)
    if row is not None:
        override = str(row["manual_override"] or "").strip()
        return override or str(row["yahoo_symbol"] or "")
    derived = map_epic_to_yahoo(epic)
    if derived:
        db.save_ticker_mapping(
            market="UK", local_symbol=epic, yahoo_symbol=derived, notes="derived"
        )
    return derived


def normalize_close(close: float, currency: str) -> tuple[float, str]:
    """Apply the GBp/GBP pence correction; never FX-convert."""
    if str(currency or "").strip() == "GBp":
        return close / 100.0, "GBP"
    return close, str(currency or "").strip() or "unknown"


def uk_price_universe(db: Database, as_of: date) -> list[dict[str, Any]]:
    """UK events whose capture window includes as_of, with their EPICs."""
    rows = db.conn.execute(
        """
        SELECT id, company_name, ticker, filing_date, event_date
        FROM detections
        WHERE market = 'UK'
          AND status = 'alert_candidate'
          AND ticker IS NOT NULL AND ticker != ''
          AND filing_date IS NOT NULL
          AND event_date IS NOT NULL
        """
    ).fetchall()
    universe: list[dict[str, Any]] = []
    for row in rows:
        try:
            announced = date.fromisoformat(str(row["filing_date"]))
            event = date.fromisoformat(str(row["event_date"]))
        except ValueError:
            continue
        window_start = announced - timedelta(days=WINDOW_CALENDAR_DAYS)
        window_end = event + timedelta(days=WINDOW_CALENDAR_DAYS)
        if window_start <= as_of <= window_end:
            universe.append(
                {
                    "detection_id": int(row["id"]),
                    "company_name": str(row["company_name"]),
                    "epic": str(row["ticker"]),
                    "window_start": window_start,
                    "window_end": window_end,
                }
            )
    return universe


def capture_uk_prices(
    config: Config,
    db: Database,
    *,
    as_of: date | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Fetch and upsert daily closes for the active UK universe.

    Fetches ~1 month of daily history per ticker and upserts every close
    that falls inside the event's capture window, so a run that was missed
    (or a ticker that failed) heals itself on the next run. Returns a
    per-ticker summary; failures are captured in the summary, never raised.
    """
    today = as_of or date.today()
    results: list[dict[str, Any]] = []
    universe = uk_price_universe(db, today)
    if not universe:
        return results

    import yfinance  # imported lazily: only the capture task needs it

    fetched: dict[str, tuple[list[tuple[str, float]], str]] = {}
    for item in universe:
        epic = item["epic"]
        symbol = resolve_yahoo_symbol(db, epic)
        summary: dict[str, Any] = {
            "company_name": item["company_name"],
            "epic": epic,
            "yahoo_symbol": symbol,
            "stored": 0,
            "error": "",
        }
        if not symbol:
            summary["error"] = "unresolvable ticker"
            results.append(summary)
            continue
        try:
            if symbol not in fetched:
                ticker = yfinance.Ticker(symbol)
                history = ticker.history(period="1mo", interval="1d")
                currency = str(
                    (getattr(ticker, "history_metadata", None) or {}).get("currency") or ""
                )
                closes: list[tuple[str, float]] = []
                for index, row in history.iterrows():
                    close = row.get("Close")
                    if close is None or close != close:  # NaN guard
                        continue
                    closes.append((index.date().isoformat(), float(close)))
                fetched[symbol] = (closes, currency)
            closes, currency = fetched[symbol]
            if not closes:
                summary["error"] = "no price data returned"
                results.append(summary)
                continue
            for close_date, raw_close in closes:
                day = date.fromisoformat(close_date)
                if not (item["window_start"] <= day <= item["window_end"]):
                    continue
                normalized, stored_currency = normalize_close(raw_close, currency)
                if not dry_run:
                    db.upsert_price(
                        ticker=symbol,
                        date=close_date,
                        close=round(normalized, 6),
                        currency=stored_currency,
                        market="UK",
                        source="yfinance",
                    )
                summary["stored"] += 1
            summary["currency"] = normalize_close(0.0, currency)[1]
        except Exception as exc:  # noqa: BLE001 - price capture is best-effort
            summary["error"] = f"{type(exc).__name__}: {exc}"
        results.append(summary)
    return results
