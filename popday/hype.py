"""Hype-vs-quiet watcher for upcoming Analyst and Investor Days."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .config import Config
from .db import Database
from .edgar_fetch import EdgarClient, SEC_BASE


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
QUALIFYING_ITEM_RE = re.compile(r"\b(?:7\.01|8\.01)\b")
ELIGIBLE_EVENT_TYPES = {"analyst day", "investor day"}


@dataclass(frozen=True)
class HypeCandidate:
    candidate_id: int
    accession_number: str
    company_name: str
    cik: str
    announcement_date: date
    event_date: date
    event_type: str
    filing_url: str


def _parse_iso_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _eligible_event_type(event_type: str) -> bool:
    return (event_type or "").strip().lower() in ELIGIBLE_EVENT_TYPES


def _watch_window_includes(event_date: date, as_of: date) -> bool:
    return event_date >= (as_of - timedelta(days=5))


def _candidate_from_row(row: Any) -> HypeCandidate | None:
    announcement_date = _parse_iso_date(str(row["filing_date"] or ""))
    event_date = _parse_iso_date(str(row["event_date"] or ""))
    event_type = str(row["event_type"] or "")
    if not announcement_date or not event_date or not _eligible_event_type(event_type):
        return None
    return HypeCandidate(
        candidate_id=int(row["id"]),
        accession_number=str(row["accession_number"]),
        company_name=str(row["company_name"]),
        cik=str(row["cik"]).zfill(10),
        announcement_date=announcement_date,
        event_date=event_date,
        event_type=event_type,
        filing_url=str(row["filing_url"]),
    )


def _recent_filings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    recent = dict(payload.get("filings", {}).get("recent", {}))
    keys = list(recent.keys())
    lengths = [len(recent[key]) for key in keys if isinstance(recent.get(key), list)]
    if not lengths:
        return []
    count = min(lengths)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        row = {
            key: recent[key][index]
            for key in keys
            if isinstance(recent.get(key), list) and len(recent[key]) > index
        }
        rows.append(row)
    return rows


def _qualifying_match(candidate: HypeCandidate, filing: dict[str, Any]) -> dict[str, Any] | None:
    form_type = str(filing.get("form") or "").strip()
    if form_type != "8-K":
        return None

    filing_date_raw = str(filing.get("filingDate") or "").strip()
    filing_date = _parse_iso_date(filing_date_raw)
    if not filing_date:
        return None
    if not (candidate.announcement_date < filing_date <= candidate.event_date):
        return None

    accession_number = str(filing.get("accessionNumber") or "").strip()
    if accession_number and accession_number == candidate.accession_number:
        return None

    items = str(filing.get("items") or "").strip()
    if not QUALIFYING_ITEM_RE.search(items):
        return None

    primary_document = str(filing.get("primaryDocument") or "").strip()
    accession_no_dashes = accession_number.replace("-", "")
    cik_no_zeros = str(int(candidate.cik)) if candidate.cik.isdigit() else candidate.cik
    return {
        "accession_number": accession_number,
        "filing_date": filing_date_raw,
        "form": form_type,
        "items": items,
        "primary_document": primary_document,
        "source_url": (
            f"{SEC_BASE}/Archives/edgar/data/{cik_no_zeros}/{accession_no_dashes}/{primary_document}"
            if primary_document and accession_no_dashes
            else ""
        ),
    }


def _status_for(candidate: HypeCandidate, *, as_of: date, threshold: int, qualifying_count: int) -> str:
    if candidate.event_date >= as_of:
        return "building" if qualifying_count >= threshold else "quiet"
    return "hyped" if qualifying_count >= threshold else "non_hyped"


def watch_hype_candidates(
    config: Config,
    db: Database,
    *,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    today = as_of or date.today()
    client = EdgarClient(config.sec_user_agent, delay_seconds=max(config.request_delay_seconds, 0.2))
    watched: list[dict[str, Any]] = []
    submissions_cache: dict[str, list[dict[str, Any]]] = {}

    for row in db.hype_watch_candidates():
        candidate = _candidate_from_row(row)
        if not candidate or not _watch_window_includes(candidate.event_date, today):
            continue

        recent_filings = submissions_cache.get(candidate.cik)
        if recent_filings is None:
            payload = client.get_json(SUBMISSIONS_URL.format(cik=candidate.cik))
            recent_filings = _recent_filings(payload)
            submissions_cache[candidate.cik] = recent_filings

        matches = [
            match
            for filing in recent_filings
            if (match := _qualifying_match(candidate, filing)) is not None
        ]
        qualifying_count = len(matches)
        status = _status_for(
            candidate,
            as_of=today,
            threshold=max(config.hype_threshold, 1),
            qualifying_count=qualifying_count,
        )
        db.upsert_hype_tracking(
            candidate_id=candidate.candidate_id,
            cik=candidate.cik,
            announcement_date=candidate.announcement_date.isoformat(),
            event_date=candidate.event_date.isoformat(),
            qualifying_count=qualifying_count,
            hype_status=status,
            last_checked=today.isoformat(),
            detected_json=json.dumps(matches, sort_keys=True),
        )
        watched.append(
            {
                "candidate_id": candidate.candidate_id,
                "company_name": candidate.company_name,
                "event_type": candidate.event_type,
                "event_date": candidate.event_date.isoformat(),
                "qualifying_count": qualifying_count,
                "hype_status": status,
            }
        )

    return watched
