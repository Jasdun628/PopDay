"""Future event date extraction."""

from __future__ import annotations

import re
from datetime import date, datetime


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


def format_human_date(value: date) -> str:
    return value.strftime("%-d %B %Y")
