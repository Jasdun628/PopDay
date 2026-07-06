"""Future event date extraction."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta


MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Jan\\.?|Feb\\.?|Mar\\.?|Apr\\.?|Jun\\.?|Jul\\.?|Aug\\.?|Sep\\.?|Sept\\.?|Oct\\.?|Nov\\.?|Dec\\.?"
)

DATE_PATTERNS = [
    re.compile(rf"\b({MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(\d{{4}})\b", re.IGNORECASE),
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTHS})[,]?\s+(\d{{4}})\b", re.IGNORECASE),
]


def _parse_match(match: re.Match[str]) -> date | None:
    text = match.group(0).replace(".", "")
    formats = ["%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"]
    normalized = re.sub(r"(\d)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE)
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return None


def extract_future_date(text: str, run_date: date) -> date | None:
    candidates: list[date] = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = _parse_match(match)
            if parsed and parsed > run_date:
                candidates.append(parsed)
    return min(candidates) if candidates else None


def extract_event_date(
    text: str,
    run_date: date,
    past_grace_days: int = 4,
    anchor: int | None = None,
    window: int = 45,
) -> date | None:
    """Extract an investor-event date that is upcoming, today, or just-happened.

    Unlike extract_future_date (strictly future), this also accepts a date on
    the run date or within `past_grace_days` before it. Companies routinely file
    the 8-K on the day of — or a day or two after — an Investor Day, so a strict
    "future only" rule silently drops real, freshly-dated events (which are also
    the cleanest Price Reaction inputs). The grace window stays small so old
    backward references ("our Investor Day in September 2021") are still ignored.

    Precision guard: a same-day / recent date is only accepted when it sits
    within `window` characters of `anchor` (the matched phrase position). This
    stops us mistaking an unrelated press-release dateline or signature-block
    date ("Schiphol, July 1, 2026 - ... buyback") for an event date. Strictly
    future dates are trusted anywhere, matching the long-standing behaviour.

    Prefers the soonest upcoming/today date; falls back to the most recent date
    inside the grace window when nothing is upcoming.
    """
    earliest = run_date - timedelta(days=max(0, past_grace_days))
    upcoming: list[date] = []
    recent: list[date] = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = _parse_match(match)
            if not parsed or parsed < earliest:
                continue
            if parsed > run_date:
                upcoming.append(parsed)
            elif anchor is None or abs(match.start() - anchor) <= window:
                recent.append(parsed)
    if upcoming:
        return min(upcoming)
    return max(recent) if recent else None


# Abbreviated months per Jason's house style: Sept (never September), not Sep.
_MONTH_ABBR = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sept", "Oct", "Nov", "Dec",
)


def format_human_date(value: date) -> str:
    return f"{value.day} {_MONTH_ABBR[value.month]} {value.year}"
