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
# Cabrera, Kolokolova & Zhang count three voluntary 8-K item types as pre-event
# "hype" disclosures:
#   2.02 = Results of Operations and Financial Condition
#   7.01 = Regulation FD Disclosure
#   8.01 = Other Events
QUALIFYING_ITEM_RE = re.compile(r"\b(?:2\.02|7\.01|8\.01)\b")
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
    normalized = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
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
    item_codes = [item.strip() for item in re.split(r"[,;]\s*", items) if item.strip()]

    primary_document = str(filing.get("primaryDocument") or "").strip()
    accession_no_dashes = accession_number.replace("-", "")
    cik_no_zeros = str(int(candidate.cik)) if candidate.cik.isdigit() else candidate.cik
    return {
        "accession_number": accession_number,
        "filing_date": filing_date_raw,
        "form": form_type,
        "item_codes": item_codes,
        "primary_document": primary_document,
        "source_url": (
            f"{SEC_BASE}/Archives/edgar/data/{cik_no_zeros}/{accession_no_dashes}/{primary_document}"
            if primary_document and accession_no_dashes
            else ""
        ),
        # Kept lightweight for PythonAnywhere; full exhibit text can be added later if the paper's
        # definition turns out to weight disclosure complexity.
        "text_proxy": {
            "clean_text_chars": None,
            "clean_text_words": None,
            "todo": "fetch_primary_document_text_if_complexity_weighting_is_needed",
        },
    }


def _status_for(candidate: HypeCandidate, *, as_of: date, threshold: int, qualifying_count: int) -> str:
    if candidate.event_date >= as_of:
        return "building" if qualifying_count >= threshold else "quiet"
    return "hyped" if qualifying_count >= threshold else "non_hyped"


def _qualifying_count_from_detected_json(detected_json: str) -> int:
    try:
        payload = json.loads(detected_json or "[]")
    except Exception:
        return 0
    if not isinstance(payload, list):
        return 0
    return len(payload)


def _item_codes_from_detected_json(detected_json: str) -> list[str]:
    """Distinct 8-K item codes across the stored qualifying matches."""
    try:
        payload = json.loads(detected_json or "[]")
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    codes: set[str] = set()
    for match in payload:
        if isinstance(match, dict):
            for code in match.get("item_codes", []) or []:
                codes.add(str(code).strip())
    return sorted(c for c in codes if c)


def _reclassified_status(*, event_date: str, as_of: date, threshold: int, detected_json: str) -> tuple[int, str]:
    event_day = _parse_iso_date(event_date) or as_of
    qualifying_count = _qualifying_count_from_detected_json(detected_json)
    candidate = HypeCandidate(
        candidate_id=0,
        accession_number="",
        company_name="",
        cik="",
        announcement_date=as_of,
        event_date=event_day,
        event_type="",
        filing_url="",
    )
    return qualifying_count, _status_for(
        candidate,
        as_of=as_of,
        threshold=max(threshold, 1),
        qualifying_count=qualifying_count,
    )


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
            hype_definition_version=config.hype_definition_version,
            provisional=config.hype_provisional,
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
                "provisional": config.hype_provisional,
            }
        )

    return watched


def reclassify_hype_tracking(
    config: Config,
    db: Database,
    *,
    as_of: date | None = None,
    dry_run: bool = False,
    market: str | None = None,
) -> list[dict[str, Any]]:
    """Recompute hype labels from stored detected_json only.

    With ``dry_run=True`` nothing is written to the database; each returned row
    also carries the previous label so callers can preview what would change.
    ``market`` limits the pass to one market's rows (None = all markets).
    """
    today = as_of or date.today()
    updated: list[dict[str, Any]] = []
    for row in db.all_hype_tracking_rows(market=market):
        detected_json = str(row["detected_json"] or "[]")
        old_status = str(row["hype_status"] or "")
        row_market = str(row["market"] or "US") if "market" in row.keys() else "US"
        qualifying_count, status = _reclassified_status(
            event_date=str(row["event_date"] or ""),
            as_of=today,
            threshold=config.hype_threshold,
            detected_json=detected_json,
        )
        item_codes = _item_codes_from_detected_json(detected_json)
        if not dry_run:
            db.upsert_hype_tracking(
                candidate_id=int(row["candidate_id"]),
                cik=str(row["cik"]),
                announcement_date=str(row["announcement_date"]),
                event_date=str(row["event_date"]),
                qualifying_count=qualifying_count,
                hype_status=status,
                hype_definition_version=config.hype_definition_version,
                provisional=config.hype_provisional,
                last_checked=today.isoformat(),
                detected_json=detected_json,
                market=row_market,
            )
        updated.append(
            {
                "candidate_id": int(row["candidate_id"]),
                "event_date": str(row["event_date"]),
                "qualifying_count": qualifying_count,
                "old_status": old_status,
                "hype_status": status,
                "provisional": config.hype_provisional,
                "item_codes": item_codes,
                "changed": old_status != status,
            }
        )
    return updated


def update_uk_hype_from_index(
    config: Config,
    db: Database,
    index_rows: list[Any],
    run_date: date,
) -> list[dict[str, Any]]:
    """UK hype pass: count non-routine announcements for open UK events.

    The UK mapping of the paper's US definition (voluntary 8-K items 2.02/
    7.01/8.01 between announcement and event): any announcement from the same
    company on any wire whose headline is not on the routine exclusion list,
    dated inside (announcement_date, event_date]. Runs against the day's full
    Investegate index, already in memory from the scan - no extra fetches.

    Observations append into hype_tracking.detected_json in the same shape as
    the US watcher (deduplicated on the numeric announcement ID), so
    qualifying_count/--reclassify work identically across markets.
    """
    routine = [phrase.lower() for phrase in config.uk_routine_headlines]
    updated: list[dict[str, Any]] = []
    for event in db.uk_open_hype_events(run_date.isoformat()):
        ticker = str(event["ticker"] or "").strip()
        if not ticker:
            continue
        announcement_date = _parse_iso_date(str(event["filing_date"] or ""))
        event_date = _parse_iso_date(str(event["event_date"] or ""))
        if not announcement_date or not event_date:
            continue
        # New observations only count inside the paper's window
        # (announcement, event]; after the event we still recompute so the
        # label finalises building->hyped / quiet->non_hyped automatically
        # (the US watcher does the same for ~5 days post-event) - the SQL
        # window (event >= run_date - 7d) bounds how long that continues.
        counting = announcement_date < run_date <= event_date
        if run_date <= announcement_date:
            continue

        observations = []
        for row in index_rows if counting else []:
            if str(getattr(row, "company_identifier", "") or "").strip() != ticker:
                continue
            if str(getattr(row, "dedup_key", "")) == str(event["accession_number"]):
                continue
            headline = str(getattr(row, "headline", "") or "").lower()
            if any(excl in headline for excl in routine):
                continue
            observations.append(
                {
                    "accession_number": str(row.dedup_key),
                    "filing_date": run_date.isoformat(),
                    "form": str(getattr(row, "wire_or_form", "") or "RNS"),
                    "item_codes": [],
                    "primary_document": "",
                    "source_url": str(getattr(row, "detail_url", "") or ""),
                    "headline": str(getattr(row, "headline", "") or ""),
                }
            )

        existing_row = db.hype_tracking_for_candidate(int(event["id"]))
        try:
            existing = json.loads(str(existing_row["detected_json"] or "[]")) if existing_row else []
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        seen_ids = {
            str(match.get("accession_number"))
            for match in existing
            if isinstance(match, dict)
        }
        merged = existing + [
            obs for obs in observations if obs["accession_number"] not in seen_ids
        ]
        if not merged and existing_row is None and not observations:
            # Nothing observed yet and no row to update - still record the
            # tracking row so the Research tab shows a real zero, not unknown.
            pass
        qualifying_count = len(merged)
        candidate = HypeCandidate(
            candidate_id=int(event["id"]),
            accession_number=str(event["accession_number"]),
            company_name=str(event["company_name"]),
            cik=str(event["cik"] or ticker),
            announcement_date=announcement_date,
            event_date=event_date,
            event_type=str(event["event_type"] or ""),
            filing_url=str(event["filing_url"]),
        )
        status = _status_for(
            candidate,
            as_of=run_date,
            threshold=max(config.hype_threshold, 1),
            qualifying_count=qualifying_count,
        )
        db.upsert_hype_tracking(
            candidate_id=candidate.candidate_id,
            cik=ticker,
            announcement_date=announcement_date.isoformat(),
            event_date=event_date.isoformat(),
            qualifying_count=qualifying_count,
            hype_status=status,
            hype_definition_version=config.hype_definition_version,
            provisional=True,
            last_checked=run_date.isoformat(),
            detected_json=json.dumps(merged, sort_keys=True),
            market="UK",
        )
        updated.append(
            {
                "candidate_id": candidate.candidate_id,
                "company_name": candidate.company_name,
                "ticker": ticker,
                "event_date": event_date.isoformat(),
                "qualifying_count": qualifying_count,
                "hype_status": status,
                "new_today": len(merged) - len(existing),
            }
        )
    return updated
