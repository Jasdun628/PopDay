"""Investor-event detection logic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .date_extract import extract_future_date
from .db import utc_now
from .edgar_fetch import Filing
from .parser import Section
from .rules import INCLUDE_PHRASES, ROUTINE_PHRASES


ANNOUNCEMENT_CUES = [
    "will host",
    "will hold",
    "will conduct",
    "will present",
    "to host",
    "to hold",
    "to be held",
    "scheduled for",
    "announced",
    "plans to host",
]

PAST_CUES = [
    "hosted",
    "held its",
    "was held",
    "previously announced",
    "replay",
    "presentation from",
]


@dataclass(frozen=True)
class Detection:
    filing: Filing
    event_type: str | None
    event_date: str | None
    matched_phrase: str | None
    matched_location: str | None
    snippet: str
    status: str
    dismissal_reason: str | None
    alert_sent: bool = False

    def to_record(self) -> dict[str, object]:
        return {
            "accession_number": self.filing.accession_number,
            "company_name": self.filing.company_name,
            "cik": self.filing.cik,
            "form_type": self.filing.form_type,
            "filing_date": self.filing.filing_date,
            "filing_url": self.filing.filing_url,
            "event_type": self.event_type,
            "event_date": self.event_date,
            "matched_phrase": self.matched_phrase,
            "matched_location": self.matched_location,
            "snippet": self.snippet,
            "status": self.status,
            "dismissal_reason": self.dismissal_reason,
            "alert_sent": int(self.alert_sent),
            "alert_sent_timestamp": None,
            "created_timestamp": utc_now(),
        }


def _window(text: str, start: int, end: int, radius: int = 300) -> str:
    left = max(start - radius, 0)
    right = min(end + radius, len(text))
    snippet = text[left:right]
    return re.sub(r"\s+", " ", snippet).strip()


def _has_any(text: str, phrases: list[str]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def _event_type(phrase: str) -> str:
    return " ".join(word.capitalize() if word != "r&d" else "R&D" for word in phrase.split())


def detect_in_sections(
    filing: Filing,
    sections: list[Section],
    run_date: date,
    include_phrases: list[str] | None = None,
    routine_phrases: list[str] | None = None,
) -> list[Detection]:
    include_phrases = include_phrases if include_phrases is not None else INCLUDE_PHRASES
    routine_phrases = routine_phrases if routine_phrases is not None else ROUTINE_PHRASES
    detections: list[Detection] = []
    seen_snippets: set[str] = set()

    for section in sections:
        lowered = section.text.lower()
        for phrase in include_phrases:
            match = re.search(rf"\b{re.escape(phrase)}\b", lowered)
            if not match:
                continue

            snippet = _window(section.text, match.start(), match.end())
            snippet_key = f"{phrase}:{snippet[:220]}"
            if snippet_key in seen_snippets:
                continue
            seen_snippets.add(snippet_key)

            phrase_context = snippet.lower()
            event_date = extract_future_date(snippet, run_date)
            high_signal = not section.location.startswith("full_body:")
            announcement_context = _has_any(phrase_context, ANNOUNCEMENT_CUES)
            past_context = _has_any(phrase_context, PAST_CUES)

            if event_date and (high_signal or announcement_context) and not past_context:
                detections.append(
                    Detection(
                        filing=filing,
                        event_type=_event_type(phrase),
                        event_date=event_date.isoformat(),
                        matched_phrase=phrase,
                        matched_location=section.location,
                        snippet=snippet,
                        status="alert_candidate",
                        dismissal_reason=None,
                    )
                )
                continue

            reason = "missing_future_event_date"
            if event_date and past_context:
                reason = "appears_to_reference_past_or_replay"
            elif event_date and not high_signal and not announcement_context:
                reason = "low_confidence_full_body_match_without_announcement_context"
            elif _has_any(phrase_context, routine_phrases):
                reason = "routine_phrase_context_without_clear_future_event"

            detections.append(
                Detection(
                    filing=filing,
                    event_type=_event_type(phrase),
                    event_date=event_date.isoformat() if event_date else None,
                    matched_phrase=phrase,
                    matched_location=section.location,
                    snippet=snippet,
                    status="dismissed",
                    dismissal_reason=reason,
                )
            )

    if not detections:
        detections.append(
            Detection(
                filing=filing,
                event_type=None,
                event_date=None,
                matched_phrase=None,
                matched_location=None,
                snippet="",
                status="dismissed",
                dismissal_reason="no_qualifying_phrase_found",
            )
        )
    return detections
