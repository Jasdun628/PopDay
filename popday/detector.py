"""Investor-event detection logic."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urljoin

from .date_extract import extract_event_date
from .db import utc_now
from .edgar_fetch import Filing
from .filing_parser import ParsedFiling, best_nugget, split_sentences
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

EVENT_LINK_HINTS = (
    "investor",
    "analyst",
    "event",
    "webcast",
    "presentation",
    "ir",
)


@dataclass(frozen=True)
class Detection:
    filing: Filing
    event_type: str | None
    event_date: str | None
    matched_phrase: str | None
    matched_location: str | None
    snippet: str
    items: list[str]
    status: str
    dismissal_reason: str | None
    event_url: str = ""
    evidence_url: str = ""
    evidence_label: str = ""
    alert_sent: bool = False
    market: str = "US"
    ticker: str = ""

    def to_record(self) -> dict[str, object]:
        return {
            "accession_number": self.filing.accession_number,
            "company_name": self.filing.company_name,
            "cik": self.filing.cik,
            "form_type": self.filing.form_type,
            "filing_date": self.filing.filing_date,
            "acceptance_datetime": getattr(self.filing, "acceptance_datetime", "") or None,
            "filing_url": self.filing.filing_url,
            "event_type": self.event_type,
            "event_date": self.event_date,
            "matched_phrase": self.matched_phrase,
            "matched_location": self.matched_location,
            "snippet": self.snippet,
            "items_json": json_items(self.items),
            "event_url": self.event_url,
            "evidence_url": self.evidence_url,
            "evidence_label": self.evidence_label,
            "status": self.status,
            "dismissal_reason": self.dismissal_reason,
            "alert_sent": int(self.alert_sent),
            "alert_sent_timestamp": None,
            "created_timestamp": utc_now(),
            "market": self.market,
            "ticker": self.ticker or None,
        }


def json_items(items: list[str]) -> str:
    import json

    return json.dumps([item.strip() for item in items if item and item.strip()])


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


def _sec_readable_url(filing_url: str) -> str:
    marker = "/Archives/edgar/data/"
    if marker not in filing_url or not filing_url.endswith(".txt"):
        return filing_url
    tail = filing_url.split(marker, 1)[1]
    parts = tail.split("/")
    if len(parts) < 2:
        return filing_url
    cik = parts[0]
    accession = parts[-1].replace(".txt", "")
    accession_no_dashes = parts[1] if len(parts) >= 3 else accession.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik}/{accession_no_dashes}/{accession}-index.htm"
    )


def _sec_document_url(filing_url: str, filename: str) -> str:
    filename = filename.strip()
    if not filename:
        return _sec_readable_url(filing_url)
    marker = "/Archives/edgar/data/"
    if marker not in filing_url:
        return urljoin(filing_url, filename)
    tail = filing_url.split(marker, 1)[1]
    parts = tail.split("/")
    if len(parts) < 2:
        return filing_url
    cik = parts[0]
    accession = parts[-1].replace(".txt", "")
    accession_no_dashes = parts[1] if len(parts) >= 3 else accession.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik}/{accession_no_dashes}/{filename}"
    )


def _evidence_label(document: dict, fallback: str) -> str:
    doc_type = str(document.get("type") or "").strip().upper()
    description = str(document.get("description") or "").strip()
    if doc_type.startswith("EX-"):
        return doc_type.replace("EX-", "Exhibit ")
    if "press release" in description.lower():
        return "Press release"
    return fallback


def _candidate_documents(parsed: ParsedFiling, filing_url: str) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    seen: set[str] = set()
    for document in parsed.documents:
        text = str(document.get("text") or "").strip()
        if not text or text in seen:
            continue
        doc_type = str(document.get("type") or "").upper()
        description = str(document.get("description") or "").lower()
        is_press_release = (
            doc_type.startswith("EX-99")
            or "press release" in description
            or any(trigger in text.lower() for trigger in ("investor day", "analyst day"))
        )
        if not is_press_release:
            continue
        documents.append(
            {
                "location": "press_release",
                "text": text,
                "evidence_url": _sec_document_url(filing_url, str(document.get("filename") or "")),
                "evidence_label": _evidence_label(document, "Press release"),
            }
        )
        seen.add(text)
    for document in parsed.documents:
        text = str(document.get("text") or "").strip()
        doc_type = str(document.get("type") or "").upper()
        if doc_type == parsed.form_type.upper() and text and text not in seen:
            documents.append(
                {
                    "location": "cover_page",
                    "text": text,
                    "evidence_url": _sec_readable_url(filing_url),
                    "evidence_label": f"{parsed.form_type or 'SEC'} filing",
                }
            )
            seen.add(text)
    if parsed.cover_text and parsed.cover_text not in seen:
        documents.append(
            {
                "location": "cover_page",
                "text": parsed.cover_text,
                "evidence_url": _sec_readable_url(filing_url),
                "evidence_label": f"{parsed.form_type or 'SEC'} filing",
            }
        )
    return documents


def _best_event_url(parsed: ParsedFiling) -> str:
    best_url = ""
    best_score = -1
    for document in parsed.documents:
        doc_type = str(document.get("type") or "").upper()
        description = str(document.get("description") or "").lower()
        doc_score = 2 if doc_type.startswith("EX-99") or "press release" in description else 0
        for link in document.get("links") or []:
            url = str(link.get("url") or "").strip()
            text = str(link.get("text") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            combined = f"{text} {url}".lower()
            score = doc_score
            if "sec.gov" in combined:
                score -= 3
            if any(hint in combined for hint in EVENT_LINK_HINTS):
                score += 4
            if "webcast" in combined or "events" in combined:
                score += 2
            if score > best_score:
                best_score = score
                best_url = url
    return best_url if best_score >= 3 else ""


def _best_event_signal(
    parsed: ParsedFiling,
    filing_url: str,
    run_date: date,
    include_phrases: list[str],
    routine_phrases: list[str],
) -> tuple[str | None, str | None, str | None, str | None, str, str]:
    best_phrase: str | None = None
    best_location: str | None = None
    best_snippet: str | None = None
    best_event_date: str | None = None
    best_evidence_url = ""
    best_evidence_label = ""
    best_score = -1

    nugget = best_nugget(parsed, triggers=tuple(include_phrases))
    for document in _candidate_documents(parsed, filing_url):
        location = document["location"]
        text = document["text"]
        lowered = text.lower()
        for phrase in include_phrases:
            if phrase not in lowered:
                continue
            for sentence in split_sentences(text):
                normalized = re.sub(r"\s+", " ", sentence).strip()
                if not normalized or phrase not in normalized.lower():
                    continue
                phrase_context = normalized.lower()
                phrase_idx = phrase_context.find(phrase)
                # "2025 Capital Markets Day" — a phrase named after an earlier
                # year is a reference to a past event, not a new announcement.
                year_prefix = re.search(r"(19|20)\d{2}\s*$", phrase_context[:phrase_idx])
                if year_prefix and int(year_prefix.group(0)) < run_date.year:
                    continue
                event_date = extract_event_date(
                    normalized, run_date, anchor=phrase_idx
                )
                if not event_date:
                    continue
                announcement_context = _has_any(phrase_context, ANNOUNCEMENT_CUES)
                # A clear forward-looking announcement ("will host ... on <future
                # date>") must not be discarded just because the sentence also
                # says it was "previously announced". Only treat past-cue text as
                # a genuine replay/backward reference when there is no
                # announcement cue at all.
                if _has_any(phrase_context, PAST_CUES) and not announcement_context:
                    continue
                if _has_any(phrase_context, routine_phrases) and not announcement_context:
                    continue
                score = 0
                if normalized == nugget:
                    score += 4
                if location == "press_release":
                    score += 3
                if announcement_context:
                    score += 2
                if len(normalized) <= 300:
                    score += 1
                if score > best_score:
                    best_score = score
                    best_phrase = phrase
                    best_location = location
                    best_snippet = nugget or normalized
                    best_event_date = event_date.isoformat()
                    best_evidence_url = document["evidence_url"]
                    best_evidence_label = document["evidence_label"]
    return best_phrase, best_location, best_snippet, best_event_date, best_evidence_url, best_evidence_label


def _best_tbd_signal(
    parsed: ParsedFiling,
    filing_url: str,
    include_phrases: list[str],
    run_date: date,
) -> tuple[str, str, str, str, str] | None:
    """A strong future investor-event announcement that names no concrete date yet.

    Only fires on press-release text with a clear announcement cue (e.g. "to hold",
    "will host") and no past/replay language, so a dateless "we plan to hold an
    Investor Day" is surfaced as date-TBD rather than silently dropped.
    """
    nugget = best_nugget(parsed, triggers=tuple(include_phrases))
    best: tuple[str, str, str, str, str] | None = None
    best_score = -1
    for document in _candidate_documents(parsed, filing_url):
        if document["location"] != "press_release":
            continue
        text = document["text"]
        lowered = text.lower()
        for phrase in include_phrases:
            if phrase not in lowered:
                continue
            for sentence in split_sentences(text):
                normalized = re.sub(r"\s+", " ", sentence).strip()
                if not normalized or phrase not in normalized.lower():
                    continue
                context = normalized.lower()
                # A phrase named after an earlier year ("2025 Capital Markets
                # Day") is a past-event reference, not a fresh announcement.
                year_prefix = re.search(r"(19|20)\d{2}\s*$", context[: context.find(phrase)])
                if year_prefix and int(year_prefix.group(0)) < run_date.year:
                    continue
                if not _has_any(context, ANNOUNCEMENT_CUES):
                    continue
                if _has_any(context, PAST_CUES):
                    continue
                score = 0
                if normalized == nugget:
                    score += 4
                if len(normalized) <= 300:
                    score += 1
                if score > best_score:
                    best_score = score
                    best = (
                        phrase,
                        "press_release",
                        nugget or normalized,
                        document["evidence_url"],
                        document["evidence_label"],
                    )
    return best


def detect_in_parsed_filing(
    filing: Filing,
    parsed: ParsedFiling,
    run_date: date,
    include_phrases: list[str] | None = None,
    routine_phrases: list[str] | None = None,
) -> list[Detection]:
    include_phrases = include_phrases if include_phrases is not None else INCLUDE_PHRASES
    routine_phrases = routine_phrases if routine_phrases is not None else ROUTINE_PHRASES
    items = list(parsed.items)

    matched_phrase, matched_location, snippet, event_date, evidence_url, evidence_label = _best_event_signal(
        parsed, filing.filing_url, run_date, include_phrases, routine_phrases
    )
    if matched_phrase and matched_location and snippet and event_date:
        return [
            Detection(
                filing=filing,
                event_type=_event_type(matched_phrase),
                event_date=event_date,
                matched_phrase=matched_phrase,
                matched_location=matched_location,
                snippet=snippet,
                items=items,
                event_url=_best_event_url(parsed),
                evidence_url=evidence_url,
                evidence_label=evidence_label,
                status="alert_candidate",
                dismissal_reason=None,
            )
        ]

    body_text = " ".join(document["text"] for document in _candidate_documents(parsed, filing.filing_url)).lower()
    has_phrase = any(phrase in body_text for phrase in include_phrases)

    if has_phrase:
        tbd = _best_tbd_signal(parsed, filing.filing_url, include_phrases, run_date)
        if tbd:
            phrase, location, snippet, evidence_url, evidence_label = tbd
            return [
                Detection(
                    filing=filing,
                    event_type=_event_type(phrase),
                    event_date=None,
                    matched_phrase=phrase,
                    matched_location=location,
                    snippet=snippet,
                    items=items,
                    event_url=_best_event_url(parsed),
                    evidence_url=evidence_url,
                    evidence_label=evidence_label,
                    status="alert_candidate_tbd",
                    dismissal_reason=None,
                )
            ]

    dismissal_reason = "missing_future_event_date" if has_phrase else "no_qualifying_phrase_found"
    return [
        Detection(
            filing=filing,
            event_type=None,
            event_date=None,
            matched_phrase=None,
            matched_location=None,
            snippet=best_nugget(parsed, triggers=tuple(include_phrases)),
            items=items,
            event_url=_best_event_url(parsed),
            evidence_url=_sec_readable_url(filing.filing_url),
            evidence_label=f"{filing.form_type or 'SEC'} filing",
            status="dismissed",
            dismissal_reason=dismissal_reason,
        )
    ]


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
            event_date = extract_event_date(
                snippet, run_date, anchor=phrase_context.find(phrase)
            )
            high_signal = not section.location.startswith("full_body:")
            announcement_context = _has_any(phrase_context, ANNOUNCEMENT_CUES)
            past_context = _has_any(phrase_context, PAST_CUES)

            # Past-cue text should only veto when there's no forward-looking
            # announcement cue (see _best_event_signal for the rationale).
            if event_date and (high_signal or announcement_context) and not (past_context and not announcement_context):
                detections.append(
                    Detection(
                        filing=filing,
                        event_type=_event_type(phrase),
                        event_date=event_date.isoformat(),
                        matched_phrase=phrase,
                        matched_location=section.location,
                        snippet=snippet,
                        items=[],
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
                    items=[],
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
                items=[],
                status="dismissed",
                dismissal_reason="no_qualifying_phrase_found",
            )
        )
    return detections


# --------------------------------------------------------------- UK (text)

def _phrase_in_text(phrase: str, text: str) -> int:
    """Word-boundary phrase position in text (lowered), or -1.

    Word boundaries matter for the UK trigger list: 'cmd' as a bare substring
    would match 'recommend'; as a bounded word it only matches real 'CMD'
    usage. Multi-word phrases behave identically to substring matching.
    """
    match = re.search(rf"\b{re.escape(phrase.lower())}\b", text.lower())
    return match.start() if match else -1


def detect_in_uk_announcement(
    announcement: Any,
    run_date: date,
    include_phrases: list[str],
    routine_phrases: list[str],
) -> list[Detection]:
    """Detect an investor event in one Investegate announcement's text.

    The UK equivalent of detect_in_parsed_filing, operating on the plain
    detail-page text (popday.sources.Announcement.raw_text) instead of SGML
    documents. Reuses the exact same sentence splitting (split_sentences) and
    event-date extraction (extract_event_date) as the US path - no forked
    logic - with one deliberate difference: a trigger phrase in the HEADLINE
    counts as announcement context, because an RNS headlined e.g. "Notice of
    Capital Markets Day" is itself the announcement, cue words or not.
    """
    headline = str(getattr(announcement, "headline", "") or "")
    text = str(getattr(announcement, "raw_text", "") or "")
    wire = str(getattr(announcement, "wire_or_form", "") or "RNS")
    filing = Filing(
        accession_number=str(announcement.dedup_key),
        cik="",
        company_name=str(announcement.company_name),
        form_type=wire,
        filing_date=run_date.isoformat(),
        filing_url=str(announcement.detail_url),
        primary_document="",
        acceptance_datetime=str(getattr(announcement, "announced_at", "") or ""),
    )
    ticker = str(getattr(announcement, "company_identifier", "") or "")
    evidence_label = f"{wire} announcement"

    headline_phrase = next(
        (p for p in include_phrases if _phrase_in_text(p, headline) != -1), None
    )

    best_phrase: str | None = None
    best_snippet: str | None = None
    best_event_date: str | None = None
    best_score = -1
    saw_phrase = headline_phrase is not None
    for sentence in split_sentences(text):
        normalized = re.sub(r"\s+", " ", sentence).strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        for phrase in include_phrases:
            phrase_idx = _phrase_in_text(phrase, normalized)
            if phrase_idx == -1:
                continue
            saw_phrase = True
            # "2025 Capital Markets Day" named after an earlier year is a
            # reference to a past event, not a new announcement.
            year_prefix = re.search(r"(19|20)\d{2}\s*$", lowered[:phrase_idx])
            if year_prefix and int(year_prefix.group(0)) < run_date.year:
                continue
            event_date = extract_event_date(normalized, run_date, anchor=phrase_idx)
            if not event_date:
                continue
            announcement_context = (
                headline_phrase is not None or _has_any(lowered, ANNOUNCEMENT_CUES)
            )
            if _has_any(lowered, PAST_CUES) and not announcement_context:
                continue
            if _has_any(lowered, routine_phrases) and not announcement_context:
                continue
            score = 0
            if announcement_context:
                score += 2
            if _has_any(lowered, ANNOUNCEMENT_CUES):
                score += 2
            if len(normalized) <= 300:
                score += 1
            if score > best_score:
                best_score = score
                best_phrase = phrase
                best_snippet = normalized
                best_event_date = event_date.isoformat()

    if best_phrase and best_snippet and best_event_date:
        return [
            Detection(
                filing=filing,
                event_type=_event_type(best_phrase),
                event_date=best_event_date,
                matched_phrase=best_phrase,
                matched_location="announcement_body",
                snippet=best_snippet,
                items=[],
                evidence_url=filing.filing_url,
                evidence_label=evidence_label,
                status="alert_candidate",
                dismissal_reason=None,
                market="UK",
                ticker=ticker,
            )
        ]

    if headline_phrase is not None:
        # Headline announces the event but no concrete date parsed from the
        # body: surface as date-TBD rather than silently dropping it (the
        # same contract as the US _best_tbd_signal path).
        return [
            Detection(
                filing=filing,
                event_type=_event_type(headline_phrase),
                event_date=None,
                matched_phrase=headline_phrase,
                matched_location="headline",
                snippet=re.sub(r"\s+", " ", headline).strip(),
                items=[],
                evidence_url=filing.filing_url,
                evidence_label=evidence_label,
                status="alert_candidate_tbd",
                dismissal_reason=None,
                market="UK",
                ticker=ticker,
            )
        ]

    reason = "missing_future_event_date" if saw_phrase else "no_qualifying_phrase_found"
    return [
        Detection(
            filing=filing,
            event_type=None,
            event_date=None,
            matched_phrase=None,
            matched_location=None,
            snippet=re.sub(r"\s+", " ", headline).strip(),
            items=[],
            evidence_url=filing.filing_url,
            evidence_label=evidence_label,
            status="dismissed",
            dismissal_reason=reason,
            market="UK",
            ticker=ticker,
        )
    ]
