"""Cached daily price-reaction enrichment for qualifying announcements.

This module is intentionally separate from the scan path. It never affects
alert qualification or email sending.
"""

from __future__ import annotations

import csv
import io
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .db import Database, utc_now


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
PRICE_DATA_SOURCE = "yahoo_chart_daily_json"

COMPANY_TICKER_OVERRIDES = {
    "barnes & noble education, inc.": "BNED",
    "climb global solutions, inc.": "CLMB",
    "harmonic inc.": "HLIT",
    "radian group inc.": "RDN",
    "samsara inc.": "IOT",
    "slb limited/nv": "SLB",
}


@dataclass(frozen=True)
class PriceBar:
    date: date
    open: float
    high: float
    low: float
    close: float


def _normalize_cik(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.zfill(10)


def _company_key(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_acceptance_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fetch_text(url: str, *, user_agent: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def _ticker_rank(ticker: str) -> tuple[int, int]:
    """Rank a CIK's listings so common stock beats warrants/units/rights.

    SPACs and dual-listed companies share one CIK across several symbols
    (e.g. BBCQ common vs BBCQW warrants). Warrant prices exaggerate moves, so
    prefer symbols without a W/U/R class suffix, then shorter symbols (DRD ADR
    over the thinner OTC DRDGF).
    """
    suffix_penalty = 1 if len(ticker) >= 4 and ticker[-1] in "WUR" else 0
    return (suffix_penalty, len(ticker))


def fetch_cik_ticker_map(*, user_agent: str) -> dict[str, str]:
    data = json.loads(_fetch_text(SEC_COMPANY_TICKERS_URL, user_agent=user_agent))
    mapping: dict[str, str] = {}
    for item in data.values():
        cik = _normalize_cik(item.get("cik_str"))
        ticker = str(item.get("ticker") or "").strip().upper()
        if cik and ticker:
            existing = mapping.get(cik)
            if existing is None or _ticker_rank(ticker) < _ticker_rank(existing):
                mapping[cik] = ticker
    return mapping


def _stooq_symbol(ticker: str) -> str:
    return ticker.strip().lower().replace(".", "-") + ".us"


def fetch_stooq_daily_bars(ticker: str, *, user_agent: str) -> list[PriceBar]:
    query = urllib.parse.urlencode({"s": _stooq_symbol(ticker), "i": "d"})
    text = _fetch_text(f"{STOOQ_DAILY_URL}?{query}", user_agent=user_agent)
    return parse_daily_bars(text)


def fetch_yahoo_daily_bars(ticker: str, *, user_agent: str) -> list[PriceBar]:
    start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int((datetime.now(timezone.utc) + timedelta(days=3)).timestamp())
    query = urllib.parse.urlencode(
        {"period1": start, "period2": end, "interval": "1d", "events": "history"}
    )
    text = _fetch_text(
        f"{YAHOO_CHART_URL}{urllib.parse.quote(ticker.upper())}?{query}",
        user_agent=user_agent,
    )
    payload = json.loads(text)
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    bars: list[PriceBar] = []
    for index, stamp in enumerate(timestamps):
        try:
            values = (opens[index], highs[index], lows[index], closes[index])
        except IndexError:
            continue
        if any(value is None for value in values):
            continue
        bars.append(
            PriceBar(
                date=datetime.fromtimestamp(int(stamp), timezone.utc).date(),
                open=float(values[0]),
                high=float(values[1]),
                low=float(values[2]),
                close=float(values[3]),
            )
        )
    bars.sort(key=lambda bar: bar.date)
    return bars


def fetch_daily_bars(ticker: str, *, user_agent: str) -> tuple[list[PriceBar], str]:
    try:
        bars = fetch_stooq_daily_bars(ticker, user_agent=user_agent)
        if bars:
            return bars, "stooq_daily_csv"
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    return fetch_yahoo_daily_bars(ticker, user_agent=user_agent), "yahoo_chart_daily_json"


def parse_daily_bars(text: str) -> list[PriceBar]:
    bars: list[PriceBar] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            if not row.get("Date") or row.get("Close") in {"", "N/D"}:
                continue
            bars.append(
                PriceBar(
                    date=datetime.strptime(str(row["Date"]), "%Y-%m-%d").date(),
                    open=float(str(row["Open"])),
                    high=float(str(row["High"])),
                    low=float(str(row["Low"])),
                    close=float(str(row["Close"])),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda bar: bar.date)
    return bars


def resolve_ticker(announcement: dict[str, Any], cik_tickers: dict[str, str]) -> str:
    ticker = str(announcement.get("ticker") or "").strip().upper()
    if ticker:
        return ticker
    cik = _normalize_cik(announcement.get("cik"))
    if cik and cik in cik_tickers:
        return cik_tickers[cik]
    return COMPANY_TICKER_OVERRIDES.get(_company_key(announcement.get("company_name")), "")


def reaction_anchor_date(announcement: dict[str, Any]) -> date | None:
    accepted = _parse_acceptance_datetime(announcement.get("acceptance_datetime"))
    if accepted:
        if accepted.time() >= time(16, 0):
            return accepted.date() + timedelta(days=1)
        return accepted.date()
    return _parse_date(announcement.get("filing_date"))


def _first_bar_on_or_after(bars: list[PriceBar], target: date) -> PriceBar | None:
    for bar in bars:
        if bar.date >= target:
            return bar
    return None


def _last_bar_before(bars: list[PriceBar], target: date) -> PriceBar | None:
    before = [bar for bar in bars if bar.date < target]
    return before[-1] if before else None


def _daily_return_volatility_pct(bars: list[PriceBar]) -> float | None:
    if len(bars) < 3:
        return None
    returns = [
        (bars[index].close - bars[index - 1].close) / bars[index - 1].close
        for index in range(1, len(bars))
        if bars[index - 1].close
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * 100


def _pct(current: float | None, base: float | None) -> float | None:
    if current is None or base in {None, 0}:
        return None
    return 100 * (current - base) / base


def compute_price_reaction(
    announcement: dict[str, Any],
    *,
    ticker: str,
    bars: list[PriceBar],
    price_data_source: str = PRICE_DATA_SOURCE,
    timestamp: str | None = None,
) -> dict[str, Any]:
    key = f"{announcement.get('source_table') or 'announcement'}:{announcement.get('source_id') or _company_key(announcement.get('company_name'))}"
    base = {
        "announcement_key": key,
        "source_table": announcement.get("source_table") or "announcement",
        "source_id": announcement.get("source_id"),
        "company_name": announcement.get("company_name") or "",
        "cik": announcement.get("cik") or None,
        "ticker": ticker or None,
        "event_date": announcement.get("event_date") or None,
        "filing_date": announcement.get("filing_date") or None,
        "acceptance_datetime": announcement.get("acceptance_datetime") or None,
        "reaction_date": None,
        "previous_close_date": None,
        "previous_close": None,
        "reaction_open": None,
        "reaction_high": None,
        "reaction_low": None,
        "reaction_close": None,
        "announcement_move_pct": None,
        "intraday_range_pct": None,
        "latest_close_date": None,
        "latest_close": None,
        "interval_return_pct": None,
        "interval_daily_volatility_pct": None,
        "event_day_close_date": None,
        "event_day_close": None,
        "event_day_move_pct": None,
        "price_data_source": price_data_source,
        "price_data_timestamp": timestamp or utc_now(),
        "status": "ok",
        "notes": None,
    }
    if not ticker:
        return base | {"status": "missing_ticker", "notes": "No ticker found from CIK or override."}
    anchor = reaction_anchor_date(announcement)
    if not anchor:
        return base | {"status": "missing_announcement_date", "notes": "No filing or acceptance date."}
    if not bars:
        return base | {"status": "missing_prices", "notes": f"No daily price rows for {ticker}."}
    reaction_bar = _first_bar_on_or_after(bars, anchor)
    if not reaction_bar:
        return base | {"status": "missing_reaction_day", "notes": f"No trading day on or after {anchor}."}
    previous_bar = _last_bar_before(bars, reaction_bar.date)
    if not previous_bar:
        return base | {"status": "missing_previous_close", "notes": "No previous trading close found."}
    latest_bar = bars[-1]
    interval_bars = [bar for bar in bars if previous_bar.date <= bar.date <= latest_bar.date]
    # Close on the actual Investor Day (first trading day on/after the event date). Only
    # available once the event has happened; upcoming events leave these None.
    event_day = _parse_date(announcement.get("event_date"))
    event_day_bar = _first_bar_on_or_after(bars, event_day) if event_day else None
    event_day_prev = _last_bar_before(bars, event_day_bar.date) if event_day_bar else None
    return base | {
        "reaction_date": reaction_bar.date.isoformat(),
        "previous_close_date": previous_bar.date.isoformat(),
        "previous_close": previous_bar.close,
        "reaction_open": reaction_bar.open,
        "reaction_high": reaction_bar.high,
        "reaction_low": reaction_bar.low,
        "reaction_close": reaction_bar.close,
        "announcement_move_pct": _pct(reaction_bar.close, previous_bar.close),
        "intraday_range_pct": (
            100 * (reaction_bar.high - reaction_bar.low) / previous_bar.close
            if previous_bar.close
            else None
        ),
        "latest_close_date": latest_bar.date.isoformat(),
        "latest_close": latest_bar.close,
        "interval_return_pct": _pct(latest_bar.close, previous_bar.close),
        "interval_daily_volatility_pct": _daily_return_volatility_pct(interval_bars),
        "event_day_close_date": event_day_bar.date.isoformat() if event_day_bar else None,
        "event_day_close": event_day_bar.close if event_day_bar else None,
        "event_day_move_pct": (
            _pct(event_day_bar.close, event_day_prev.close)
            if event_day_bar and event_day_prev else None
        ),
        "status": "ok",
    }


def refresh_price_reactions(db: Database, *, user_agent: str) -> list[dict[str, Any]]:
    announcements = [dict(row) for row in db.investor_day_announcements()]
    cik_tickers = fetch_cik_ticker_map(user_agent=user_agent)
    bars_by_ticker: dict[str, list[PriceBar]] = {}
    source_by_ticker: dict[str, str] = {}
    refreshed: list[dict[str, Any]] = []
    timestamp = utc_now()
    for announcement in announcements:
        ticker = resolve_ticker(announcement, cik_tickers)
        bars: list[PriceBar] = []
        price_data_source = PRICE_DATA_SOURCE
        if ticker:
            if ticker not in bars_by_ticker:
                try:
                    bars_by_ticker[ticker], source_by_ticker[ticker] = fetch_daily_bars(
                        ticker,
                        user_agent=user_agent,
                    )
                except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                    row = compute_price_reaction(
                        announcement,
                        ticker=ticker,
                        bars=[],
                        price_data_source=price_data_source,
                        timestamp=timestamp,
                    ) | {
                        "status": "fetch_error",
                        "notes": f"Could not fetch daily prices for {ticker}: {exc}",
                    }
                    db.upsert_price_reaction(row)
                    refreshed.append(row)
                    continue
            bars = bars_by_ticker[ticker]
            price_data_source = source_by_ticker.get(ticker, PRICE_DATA_SOURCE)
        row = compute_price_reaction(
            announcement,
            ticker=ticker,
            bars=bars,
            price_data_source=price_data_source,
            timestamp=timestamp,
        )
        db.upsert_price_reaction(row)
        refreshed.append(row)
    return refreshed
