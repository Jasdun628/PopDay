"""PopDay command line interface."""

from __future__ import annotations

import argparse
import urllib.error
from dataclasses import dataclass, replace as dc_replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .company_websites import fetch_edgar_website, normalize_cik, resolve_company_website
from .config import load_config
from .coverage_gap import missed_business_days
from .date_extract import format_human_date
from .db import Database
from .detector import detect_in_parsed_filing, detect_in_sections, detect_in_uk_announcement
from .debug_server import serve_debug_ui
from .edgar_fetch import (
    EdgarBlockedError,
    EdgarClient,
    EdgarUnavailableError,
    Filing,
    TARGET_FORMS,
)
from .sources.investegate import (
    InvestegateBlockedError,
    InvestegateClient,
    InvestegateRobotsDisallowedError,
    _headline_matches,
)
from .emailer import (
    build_alert_body,
    send_alert_email,
    send_ops_alert_email,
    send_privileged_format_test_email,
)
from .filing_parser import parse_sec_filing
from .hype import reclassify_hype_tracking, update_uk_hype_from_index, watch_hype_candidates
from .stock_reaction import refresh_price_reactions
from .parser import parse_filing_sections
from .rules import ALERT_REQUIREMENTS


@dataclass(frozen=True)
class Alert:
    detection_id: int
    company_name: str
    event_label: str
    event_date: date
    filing_url: str
    source_label: str = "Source"
    form_type: str = ""
    snippet: str = ""
    event_url: str = ""
    evidence_url: str = ""
    evidence_label: str = ""
    hype_status: str = ""
    hype_count: int | None = None
    hype_provisional: bool = False
    recovered_from: str = ""  # missed scan date this alert was recovered from
    market: str = "US"
    context_text: str = ""
    # True when this company's resolved link (curated + EDGAR) is still
    # empty at alert time - drives one informational note line in the alert
    # email (see popday/emailer.py). Never blocks anything; see
    # popday/company_websites.py for the resolution order.
    company_url_missing: bool = False


PRIVILEGED_TEST_RECIPIENT = "jd@jasondunne.co.uk"


def _enriched_filing(filing: Filing, parsed: object) -> Filing:
    accession = str(getattr(parsed, "accession", "") or filing.accession_number)
    cik = str(getattr(parsed, "cik", "") or filing.cik).zfill(10)
    company_name = str(getattr(parsed, "company_name", "") or filing.company_name)
    form_type = str(getattr(parsed, "form_type", "") or filing.form_type)
    filing_date = str(getattr(parsed, "filed_date", "") or filing.filing_date)
    acceptance_datetime = str(
        getattr(parsed, "acceptance_datetime", "") or getattr(filing, "acceptance_datetime", "")
    )
    return Filing(
        accession_number=accession,
        cik=cik,
        company_name=company_name,
        form_type=form_type,
        filing_date=filing_date,
        filing_url=filing.filing_url,
        primary_document=filing.primary_document,
        acceptance_datetime=acceptance_datetime,
    )


def previous_business_day(value: date | None = None) -> date:
    candidate = (value or date.today()) - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def parse_run_date(value: str) -> date:
    normalized = value.strip().lower()
    if normalized == "today":
        return date.today()
    if normalized in {"previous-business-day", "previous_business_day", "prev-business-day", "yesterday-business"}:
        return previous_business_day()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _alert_from_detection(
    detection_id: int, detection: object, recovered_from: str = ""
) -> Alert:
    return Alert(
        recovered_from=recovered_from,
        detection_id=detection_id,
        company_name=detection.filing.company_name,
        event_label=detection.event_type or "Investor Day",
        event_date=datetime.strptime(detection.event_date, "%Y-%m-%d").date(),
        filing_url=detection.filing.filing_url,
        source_label="SEC filing",
        form_type=str(detection.filing.form_type or ""),
        snippet=str(detection.snippet or ""),
        event_url=str(getattr(detection, "event_url", "") or ""),
        evidence_url=str(getattr(detection, "evidence_url", "") or ""),
        evidence_label=str(getattr(detection, "evidence_label", "") or ""),
        context_text=str(getattr(detection, "context_text", "") or ""),
    )


def _alert_from_uk_detection(
    detection_id: int, detection: object, recovered_from: str = ""
) -> Alert:
    return Alert(
        recovered_from=recovered_from,
        detection_id=detection_id,
        company_name=detection.filing.company_name,
        event_label=detection.event_type or "Capital Markets Day",
        event_date=datetime.strptime(detection.event_date, "%Y-%m-%d").date(),
        filing_url=detection.filing.filing_url,
        source_label="Investegate",
        form_type=str(detection.filing.form_type or ""),
        snippet=str(detection.snippet or ""),
        event_url="",
        evidence_url=str(getattr(detection, "evidence_url", "") or ""),
        evidence_label=str(getattr(detection, "evidence_label", "") or ""),
        market="UK",
        context_text=str(getattr(detection, "context_text", "") or ""),
    )


def _regenerate_status_json(config) -> None:
    """Refresh the front door's status JSON after a real scan (PA production).

    The System Health tab's freshness clock reads this file's mtime. It was
    historically refreshed only by deploys, so a quiet stretch with no
    commits aged the file toward a false STALE/BROKEN banner (partially
    masked, pre-14 Jul 2026, by the old weekend staleness allowance). With
    config.status_json_path set (PA production; unset in local dev), every
    real scan - successful or failed - now refreshes it, so the freshness
    clock tracks scans, not deploys. Failures print a warning and never
    propagate: status JSON is derived data, alerting must not die for it.
    """
    if not config.status_json_path:
        return
    import subprocess
    import sys

    script = Path(__file__).resolve().parent.parent / "scripts" / "generate_status_json.py"
    app_dir = script.parent.parent
    try:
        subprocess.run(
            [
                sys.executable,
                str(script),
                "--output", config.status_json_path,
                "--db-path", config.db_path,
                "--source-repo", str(app_dir),
                "--runtime-dir", str(app_dir),
                "--backup-root", str(app_dir / "backups"),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        print(f"Status JSON refreshed: {config.status_json_path}")
    except Exception as exc:  # noqa: BLE001 - derived data must never kill a scan
        print(f"WARNING - could not refresh status JSON: {exc}")


def _alert_from_known(row: object) -> Alert:
    return Alert(
        detection_id=int(row["id"]),
        company_name=str(row["company_name"]),
        event_label=str(row["event_type"] or "Investor Day"),
        event_date=datetime.strptime(str(row["event_date"]), "%Y-%m-%d").date(),
        filing_url=str(row["source_url"]),
        source_label=str(row["source_label"] or "Source"),
        form_type="",
        snippet="",
        event_url="",
        evidence_url=str(row["source_url"] or ""),
        evidence_label=str(row["source_label"] or "Source"),
        hype_status="",
        hype_count=None,
        hype_provisional=False,
    )


def _alert_from_sent_row(row: object) -> Alert:
    return Alert(
        detection_id=int(row["id"]),
        company_name=str(row["company_name"]),
        event_label=str(row["event_type"] or "Investor Day"),
        event_date=datetime.strptime(str(row["event_date"]), "%Y-%m-%d").date(),
        filing_url=str(row["source_url"]),
        source_label=str(row["source_label"] or "Source"),
        form_type=str(row["form_type"] or ""),
        snippet=str(row["snippet"] or ""),
        event_url=str(row["event_url"] or ""),
        evidence_url=str(row["evidence_url"] or ""),
        evidence_label=str(row["evidence_label"] or ""),
        hype_status=str(row["hype_status"] or ""),
        hype_count=int(row["qualifying_count"]) if row["qualifying_count"] is not None else None,
        hype_provisional=bool(row["provisional"]) if row["provisional"] is not None else False,
    )


def _health_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _print_health_summary(
    *,
    filing_date: date,
    index_status: str,
    filings_parsed: int,
    eight_k_sanity_count: int,
    alerts_sent: int,
) -> None:
    print(f"Filing date scanned: {filing_date.isoformat()}", flush=True)
    print(f"EDGAR index status: {index_status}", flush=True)
    print(f"Filings parsed: {filings_parsed}", flush=True)
    print(f"8-K sanity count: {eight_k_sanity_count}", flush=True)
    print(f"Qualifying PopDay alerts sent: {alerts_sent}", flush=True)


def _refresh_downstream_caches(config, db) -> str:
    """Refresh every store derived from detections, returning warning notes.

    Chained to any scan that writes detections so the Price Reaction cache and
    hype tracking can never silently drift out of step with the tabs that read
    detections live. Neither refresh sends email. A failure here must not stop
    alerting, so problems are printed and returned for the scan_runs record
    rather than raised.
    """
    notes: list[str] = []
    try:
        rows = refresh_price_reactions(db, user_agent=config.sec_user_agent)
        print(f"Price Reaction cache refreshed: {len(rows)} announcement(s).")
    except Exception as exc:  # noqa: BLE001 - keep the scan alive
        notes.append(f"price reaction refresh failed: {exc}")
        print(f"WARNING - Price Reaction cache refresh failed: {exc}")
    try:
        watched = watch_hype_candidates(config, db)
        print(f"Hype watcher checked {len(watched)} candidate(s).")
    except Exception as exc:  # noqa: BLE001 - keep the scan alive
        notes.append(f"hype watcher failed: {exc}")
        print(f"WARNING - hype watcher failed: {exc}")
    return "; ".join(notes)


def _ensure_company_website(db: Database, client: EdgarClient, filing: Filing) -> str:
    """Capture EDGAR's self-reported website once, the first time a company
    is discovered - not at page-render time (see popday/company_websites.py).
    Cosmetic data: a fetch failure (403, network) is logged, never swallowed,
    but must never block the scan or touch the ops-alarm state machine - the
    company simply stays uncaptured until a later scan or the backfill
    script retries it. Returns the freshly-captured website ("" if none, or
    if already captured/skipped/failed).
    """
    cik = normalize_cik(filing.cik)
    if not cik or db.has_company_website_row(cik):
        return ""
    try:
        website = fetch_edgar_website(client, cik)
    except Exception as exc:  # noqa: BLE001 - cosmetic data must never block alerting
        print(f"WARNING - could not capture EDGAR website for {filing.company_name} ({cik}): {exc}")
        return ""
    db.upsert_company_website(cik=cik, company_name=filing.company_name, edgar_website=website)
    return website


@dataclass
class _DayScanOutcome:
    """Result of scanning one filing date, email deliberately deferred.

    exit_code non-zero means the day's scan_runs row is already recorded as
    failed and the caller must stop (never half-heal silently). exit_code 0
    means the row is recorded ok with alerts_sent=0; the caller sends one
    combined email at the end and fills the true count in via
    set_scan_run_alerts_sent.
    """

    exit_code: int
    alerts: list  # list[Alert]
    scan_run_id: int = 0
    filings_parsed: int = 0
    eight_k_sanity_count: int = 0


def _scan_single_date(
    config,
    db: Database,
    args: argparse.Namespace,
    run_date: date,
    *,
    run_kind: str,
) -> _DayScanOutcome:
    """Scan one filing date through the normal machinery, without emailing.

    Used identically for the main scheduled date and for downtime-recovery
    backfill days; already_processed dedup makes re-sweeps harmless.
    """
    client = EdgarClient(config.sec_user_agent, config.request_delay_seconds)
    alerts: list[Alert] = []
    filings_parsed = 0
    eight_k_sanity_count = 0
    include_phrases = db.active_phrases("include")
    routine_phrases = db.active_phrases("routine_context")
    recovered_from = run_date.isoformat() if run_kind == "backfill" else ""
    edgar_websites = db.company_websites_by_cik()

    scan_run_id = db.start_scan_run(run_date, run_kind=run_kind)

    def _fail_scan(reason: str, detail: str) -> _DayScanOutcome:
        """Record and report a hard scan failure. NEVER exits 0.

        Historical bug: a 403 from SEC was treated as 'index not yet
        available' and the scan exited 0 (green), so blocked runs were
        invisible for weeks. 403 now always means a failed run.
        """
        db.finish_scan_run(
            scan_run_id,
            status="failed",
            filings_seen=0,
            filings_parsed=0,
            alerts_sent=0,
            source="",
            error=f"{reason}: {detail}"[:2000],
        )
        _print_health_summary(
            filing_date=run_date,
            index_status="error",
            filings_parsed=0,
            eight_k_sanity_count=0,
            alerts_sent=0,
        )
        print(f"SCAN FAILED - {reason}\n{detail}")
        # Banner-only health was invisible unless someone opened the site;
        # a failed run now emails Jason directly (cooldown-deduped so a
        # multi-day outage alarms without spamming). Dry-runs are manual
        # tests and stay silent.
        if not args.dry_run:
            full_detail = f"{reason}\n\n{detail}"[:4000]
            verdict = db.check_and_update_ops_alert(
                "scan_failure", is_broken=True, detail=full_detail
            )
            if verdict in {"new_failure", "still_failing"}:
                try:
                    send_ops_alert_email(
                        config,
                        "PopDay ALARM - scan failed"
                        + (" (still failing)" if verdict == "still_failing" else ""),
                        full_detail,
                    )
                except Exception as email_exc:  # noqa: BLE001 - never mask the real failure
                    print(f"WARNING - could not send ops alert email: {email_exc}")
        return _DayScanOutcome(1, [], scan_run_id=scan_run_id)

    try:
        # Primary discovery: EDGAR full-text search API, queried with the
        # include phrases (a handful of small JSON calls). Fallback: the
        # legacy daily master index, which SEC's bot filtering now blocks
        # for some automated clients.
        filings: list[Filing] = []
        discovery_source = ""
        primary_error: Exception | None = None
        try:
            filings = client.search_filings_for_phrases(run_date, include_phrases)
            discovery_source = "efts"
        except (EdgarBlockedError, EdgarUnavailableError, urllib.error.HTTPError, ValueError) as exc:
            primary_error = exc
        if primary_error is not None:
            try:
                filings = client.filings_for_date(
                    run_date, max_companies=args.max_companies
                )
                discovery_source = "daily-index"
                print(
                    "Note: full-text search discovery failed "
                    f"({primary_error}); fell back to the daily index."
                )
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and run_date >= date.today():
                    # The only legitimate "not published yet" case.
                    return _fail_scan(
                        "daily index not yet published",
                        f"HTTP 404 for {run_date}. Full-text search also failed: {primary_error}",
                    )
                return _fail_scan(
                    f"SEC returned HTTP {exc.code} on the daily index",
                    f"Full-text search also failed: {primary_error}",
                )
            except EdgarBlockedError as exc:
                return _fail_scan(
                    "SEC EDGAR is blocking this machine (HTTP 403)",
                    f"{exc}. Full-text search also failed: {primary_error}. "
                    "This is usually a temporary IP block for automated "
                    "clients; the run will be retried on the next schedule.",
                )
            except EdgarUnavailableError as exc:
                return _fail_scan("could not reach SEC EDGAR", str(exc))

        # A zero-hit EFTS day is only trustworthy if the instrument still
        # works. One control query against a fixed historical week that is
        # guaranteed to match; if even that returns nothing, the query or
        # response parsing is broken (API drift) and this run must not be
        # reported as a quiet green day.
        if discovery_source == "efts" and not filings:
            try:
                control_ok = client.discovery_control_probe()
            except (EdgarBlockedError, EdgarUnavailableError, urllib.error.HTTPError) as exc:
                return _fail_scan(
                    "zero filings found and the discovery control probe errored",
                    f"Cannot verify the full-text search instrument: {exc}",
                )
            if not control_ok:
                return _fail_scan(
                    "discovery instrument broken",
                    "Live queries and the known-good control query both "
                    "returned zero hits from EDGAR full-text search. The "
                    "query format or response parsing no longer matches the "
                    "API - do not trust green runs until this is fixed.",
                )
            print(
                "Zero filings matched today; control probe confirms the "
                "search instrument works (verified quiet day)."
            )

        eight_k_sanity_count = sum(1 for filing in filings if filing.form_type == "8-K")

        for filing in filings:
            if filing.form_type not in TARGET_FORMS:
                continue
            if args.reprocess:
                # Re-run detection over the window, but never touch a filing whose
                # detection was already emailed as an alert (protects alert history).
                if not args.dry_run and db.filing_has_sent_alert(filing.accession_number):
                    continue
                if not args.dry_run:
                    db.delete_detections_for_accession(filing.accession_number)
            elif not args.dry_run and db.already_processed(filing.accession_number):
                continue

            raw = client.get_text(filing.filing_url)
            filings_parsed += 1
            if not args.dry_run:
                captured = _ensure_company_website(db, client, filing)
                if captured:
                    edgar_websites[normalize_cik(filing.cik)] = captured
            if args.legacy_parser:
                sections = parse_filing_sections(raw)
                detections = detect_in_sections(
                    filing,
                    sections,
                    run_date,
                    include_phrases=include_phrases,
                    routine_phrases=routine_phrases,
                )
            else:
                parsed = parse_sec_filing(raw)
                filing = _enriched_filing(filing, parsed)
                detections = detect_in_parsed_filing(
                    filing,
                    parsed,
                    run_date,
                    include_phrases=include_phrases,
                    routine_phrases=routine_phrases,
                )

            for detection in detections:
                if detection.status == "alert_candidate" and detection.event_type and detection.event_date:
                    if db.detection_already_alerted(
                        filing.company_name, detection.event_type, detection.event_date
                    ):
                        detection = type(detection)(
                            filing=detection.filing,
                            event_type=detection.event_type,
                            event_date=detection.event_date,
                            matched_phrase=detection.matched_phrase,
                            matched_location=detection.matched_location,
                            snippet=detection.snippet,
                            items=detection.items,
                            status="dismissed",
                            dismissal_reason="event_already_alerted",
                        )
                detection_id = 0 if args.dry_run else db.insert_detection(detection)
                if detection.status == "alert_candidate" and (detection_id or args.dry_run):
                    alert = _alert_from_detection(detection_id, detection, recovered_from)
                    missing = not resolve_company_website(
                        alert.company_name, filing.cik, config.company_websites, edgar_websites
                    )
                    alerts.append(dc_replace(alert, company_url_missing=missing))

            if not args.dry_run:
                db.mark_processed(filing)

        # Keep derived stores in step with detections. July 2026 lesson: the
        # Price Reaction tab reads a cache that was only refreshed by hand, so
        # reprocessed detections never reached it. Any run that writes
        # detections now refreshes downstream automatically; failures are
        # reported loudly (and recorded on the scan run) but never block
        # alerting.
        downstream_note = ""
        if not args.dry_run:
            downstream_note = _refresh_downstream_caches(config, db)

        control = ""
        if client.stats.control_ok is True:
            control = "ok"
        elif client.stats.control_ok is False:
            control = "broken"
        elif discovery_source == "efts" and filings:
            control = "ok"  # live hits are themselves proof the instrument works
        # Coverage truth is status='ok'. A dry-run writes no detections, so it
        # must never claim a day as covered - otherwise one manual dry-run
        # after downtime would silently consume the gap without repopulating.
        db.finish_scan_run(
            scan_run_id,
            status="dry-run" if args.dry_run else "ok",
            filings_seen=len(filings),
            filings_parsed=filings_parsed,
            alerts_sent=0,  # combined email happens later; count filled in then
            source=discovery_source,
            error=downstream_note,
            efts_total_hits=client.stats.efts_total_hits,
            discovery_control=control,
        )
        # A real (non-dry-run) success clears any open scan-failure alarm
        # with exactly one all-clear email. Backfill days count too - a
        # successful sweep IS the recovery.
        if not args.dry_run:
            verdict = db.check_and_update_ops_alert(
                "scan_failure", is_broken=False, detail=""
            )
            if verdict == "recovered":
                try:
                    send_ops_alert_email(
                        config,
                        "PopDay - scan recovered",
                        "PopDay completed a successful scan again. "
                        "The earlier failure alarm is cleared.",
                    )
                except Exception as email_exc:  # noqa: BLE001
                    print(f"WARNING - could not send recovery email: {email_exc}")
        _print_health_summary(
            filing_date=run_date,
            index_status="available",
            filings_parsed=filings_parsed,
            eight_k_sanity_count=eight_k_sanity_count,
            alerts_sent=0,
        )
        return _DayScanOutcome(
            0,
            alerts,
            scan_run_id=scan_run_id,
            filings_parsed=filings_parsed,
            eight_k_sanity_count=eight_k_sanity_count,
        )
    except EdgarBlockedError as exc:
        return _fail_scan("SEC EDGAR blocked a request mid-run (HTTP 403)", str(exc))
    except EdgarUnavailableError as exc:
        return _fail_scan("lost connection to SEC EDGAR mid-run", str(exc))
    except Exception as exc:  # noqa: BLE001 - a scheduled run must never die silently
        import traceback

        return _fail_scan(
            f"unexpected error ({type(exc).__name__})",
            traceback.format_exc(limit=8),
        )


def _scan_single_date_uk(
    config,
    db: Database,
    args: argparse.Namespace,
    run_date: date,
    *,
    run_kind: str,
) -> _DayScanOutcome:
    """Scan one UK calendar day via Investegate, mirroring _scan_single_date.

    Same contract: never a silent green - a block, a robots.txt change, or a
    broken canary is a failed run with its own per-source ops alarm
    ('scan_failure:investegate'), so a UK outage can never be masked by a
    healthy US run or vice versa. already_processed dedup on the Investegate
    numeric ID makes backfill re-sweeps harmless, exactly like the US path.
    """
    alerts: list[Alert] = []
    filings_parsed = 0
    recovered_from = run_date.isoformat() if run_kind == "backfill" else ""
    scan_run_id = db.start_scan_run(run_date, run_kind=run_kind, source="investegate")

    def _fail_scan(reason: str, detail: str) -> _DayScanOutcome:
        db.finish_scan_run(
            scan_run_id,
            status="failed",
            filings_seen=0,
            filings_parsed=0,
            alerts_sent=0,
            source="investegate-index",
            error=f"{reason}: {detail}"[:2000],
        )
        _print_health_summary(
            filing_date=run_date,
            index_status="error",
            filings_parsed=0,
            eight_k_sanity_count=0,
            alerts_sent=0,
        )
        print(f"SCAN FAILED (UK) - {reason}\n{detail}")
        if not args.dry_run:
            full_detail = f"{reason}\n\n{detail}"[:4000]
            verdict = db.check_and_update_ops_alert(
                "scan_failure:investegate", is_broken=True, detail=full_detail
            )
            if verdict in {"new_failure", "still_failing"}:
                try:
                    send_ops_alert_email(
                        config,
                        "PopDay ALARM - UK (Investegate) scan failed"
                        + (" (still failing)" if verdict == "still_failing" else ""),
                        full_detail,
                    )
                except Exception as email_exc:  # noqa: BLE001 - never mask the real failure
                    print(f"WARNING - could not send ops alert email: {email_exc}")
        return _DayScanOutcome(1, [], scan_run_id=scan_run_id)

    try:
        try:
            client = InvestegateClient(
                config.uk_user_agent, config.uk_request_delay_seconds
            )
        except InvestegateRobotsDisallowedError as exc:
            return _fail_scan(
                "Investegate robots.txt now disallows a path PopDay needs", str(exc)
            )

        index = client.index_for_date(run_date)
        matches = [
            row
            for row in index
            if _headline_matches(
                row.headline, config.uk_include_phrases, config.uk_exclude_phrases
            )
        ]

        control = ""
        if matches:
            control = "ok"  # live matches are themselves proof the instrument works
        else:
            # Zero matches is only trustworthy if the pinned historical canary
            # announcement still parses - otherwise the site may have changed
            # under us and this "quiet day" is actually a silently broken scan.
            try:
                control_ok = client.probe_canary()
            except (InvestegateBlockedError, urllib.error.URLError) as exc:
                return _fail_scan(
                    "zero UK matches and the canary probe errored",
                    f"Cannot verify the Investegate instrument: {exc}",
                )
            if not control_ok:
                return _fail_scan(
                    "UK discovery instrument broken",
                    "Zero live matches and the pinned Glencore canary "
                    "announcement no longer parses as expected. The site has "
                    "likely changed - do not trust green UK runs until the "
                    "adapter is fixed.",
                )
            control = "ok"
            print(
                "Zero UK announcements matched today; canary probe confirms "
                "the instrument works (verified quiet day)."
            )

        routine_phrases = db.active_phrases("routine_context")
        for row in matches:
            if args.reprocess:
                if not args.dry_run and db.filing_has_sent_alert(row.dedup_key):
                    continue
                if not args.dry_run:
                    db.delete_detections_for_accession(row.dedup_key)
            elif not args.dry_run and db.already_processed(row.dedup_key):
                continue

            detail_text = client.fetch_detail_text(row.detail_url)
            filings_parsed += 1
            enriched = dc_replace(row, raw_text=detail_text)
            detections = detect_in_uk_announcement(
                enriched, run_date, config.uk_include_phrases, routine_phrases
            )
            for detection in detections:
                if (
                    detection.status == "alert_candidate"
                    and detection.event_type
                    and detection.event_date
                    and db.detection_already_alerted(
                        detection.filing.company_name,
                        detection.event_type,
                        detection.event_date,
                    )
                ):
                    detection = dc_replace(
                        detection,
                        status="dismissed",
                        dismissal_reason="event_already_alerted",
                    )
                detection_id = 0 if args.dry_run else db.insert_detection(detection)
                if detection.status == "alert_candidate" and (detection_id or args.dry_run):
                    alert = _alert_from_uk_detection(detection_id, detection, recovered_from)
                    missing = not resolve_company_website(
                        alert.company_name, None, config.company_websites, None
                    )
                    alerts.append(dc_replace(alert, company_url_missing=missing))
            if not args.dry_run:
                db.mark_processed(
                    Filing(
                        accession_number=row.dedup_key,
                        cik="",
                        company_name=row.company_name,
                        form_type=row.wire_or_form or "RNS",
                        filing_date=run_date.isoformat(),
                        filing_url=row.detail_url,
                        primary_document="",
                        acceptance_datetime=row.announced_at,
                    ),
                    market="UK",
                )

        # Hype pass over the full day index (already in memory): count
        # non-routine announcements for companies with open UK events.
        downstream_note = ""
        if not args.dry_run:
            try:
                watched = update_uk_hype_from_index(config, db, index, run_date)
                if watched:
                    print(f"UK hype pass updated {len(watched)} candidate(s).")
            except Exception as exc:  # noqa: BLE001 - hype must never block alerting
                downstream_note = f"UK hype pass failed: {exc}"
                print(f"WARNING - {downstream_note}")

        db.finish_scan_run(
            scan_run_id,
            status="dry-run" if args.dry_run else "ok",
            filings_seen=len(matches),
            filings_parsed=filings_parsed,
            alerts_sent=0,
            source="investegate-index",
            error=downstream_note,
            efts_total_hits=len(index),
            discovery_control=control,
        )
        if not args.dry_run:
            verdict = db.check_and_update_ops_alert(
                "scan_failure:investegate", is_broken=False, detail=""
            )
            if verdict == "recovered":
                try:
                    send_ops_alert_email(
                        config,
                        "PopDay - UK (Investegate) scan recovered",
                        "PopDay completed a successful UK scan again. "
                        "The earlier failure alarm is cleared.",
                    )
                except Exception as email_exc:  # noqa: BLE001
                    print(f"WARNING - could not send recovery email: {email_exc}")
        _print_health_summary(
            filing_date=run_date,
            index_status="available",
            filings_parsed=filings_parsed,
            eight_k_sanity_count=0,
            alerts_sent=0,
        )
        return _DayScanOutcome(
            0,
            alerts,
            scan_run_id=scan_run_id,
            filings_parsed=filings_parsed,
        )
    except InvestegateBlockedError as exc:
        return _fail_scan("Investegate blocked a request mid-run", str(exc))
    except urllib.error.URLError as exc:
        return _fail_scan("lost connection to Investegate mid-run", str(exc))
    except Exception as exc:  # noqa: BLE001 - a scheduled run must never die silently
        import traceback

        return _fail_scan(
            f"unexpected error ({type(exc).__name__})",
            traceback.format_exc(limit=8),
        )


def run_scan(args: argparse.Namespace) -> int:
    print(f"PopDay launchd run started: {_health_timestamp()}", flush=True)
    config = load_config(args.config)
    run_date = parse_run_date(args.date)
    if config.sec_user_agent_has_placeholder_contact:
        _print_health_summary(
            filing_date=run_date,
            index_status="error",
            filings_parsed=0,
            eight_k_sanity_count=0,
            alerts_sent=0,
        )
        print(
            "PopDay needs a real SEC User-Agent contact before scanning EDGAR.\n\n"
            "Set it in config.json, for example:\n"
            '  "sec_user_agent": "PopDay/0.1 your-email@example.com"\n\n'
            "Or run once with:\n"
            '  POPDAY_SEC_USER_AGENT="PopDay/0.1 your-email@example.com" '
            "python3 popday.py --date today --dry-run"
        )
        return 2

    source = str(getattr(args, "source", "") or "edgar")
    scan_one_date = _scan_single_date_uk if source == "investegate" else _scan_single_date

    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    try:
        # Self-healing coverage: before scanning the main date, sweep any
        # business days missed during downtime (failed runs, dead scheduler,
        # a source-side block). Coverage truth is the scan_runs table, PER
        # SOURCE - a healthy US run never marks a UK day covered or vice
        # versa; already_processed dedup makes re-sweeps harmless.
        explicit_floor = None
        if getattr(args, "backfill_from", None):
            explicit_floor = datetime.strptime(args.backfill_from, "%Y-%m-%d").date()
        earliest_ok = db.earliest_ok_run_date(source=source)
        floor = explicit_floor or earliest_ok
        covered = db.covered_ok_dates(floor, source=source) if floor else set()
        missed = missed_business_days(
            covered,
            main_run_date=run_date,
            earliest_ok=earliest_ok,
            explicit_floor=explicit_floor,
        )
        if missed:
            print(
                f"Recovering {len(missed)} missed day(s): "
                + ", ".join(day.isoformat() for day in missed)
            )

        all_alerts: list[Alert] = []
        alerts_by_run: dict[int, int] = {}
        for day in missed:
            outcome = scan_one_date(config, db, args, day, run_kind="backfill")
            if outcome.exit_code != 0:
                # Never half-heal silently: the failed day is recorded, the
                # remaining sweep (and the main date) is aborted so the red
                # banner fires; days already swept keep their ok rows and the
                # next healthy run resumes exactly here.
                print(
                    "Downtime recovery aborted at "
                    f"{day.isoformat()}; remaining days will be swept by the "
                    "next healthy run."
                )
                return outcome.exit_code
            all_alerts.extend(outcome.alerts)
            alerts_by_run[outcome.scan_run_id] = len(outcome.alerts)

        main = scan_one_date(config, db, args, run_date, run_kind="scheduled")
        if main.exit_code != 0:
            return main.exit_code
        all_alerts.extend(main.alerts)
        alerts_by_run[main.scan_run_id] = len(main.alerts)

        if not all_alerts:
            return 0

        if args.dry_run or args.reprocess:
            # Reprocess writes the re-detected candidates to the database (so the
            # Investor Days tab updates) but, like dry-run, never emails.
            note = "DRY-RUN" if args.dry_run else "REPROCESS (detections written, no email)"
            print(f"[{note}] {len(all_alerts)} candidate alert(s):")
            print(build_alert_body(all_alerts))
            return 0

        # ONE combined email covering the recovered days and the main date.
        send_alert_email(config, all_alerts, recipients=db.active_alert_recipients())
        db.mark_alert_sent([alert.detection_id for alert in all_alerts])
        for run_id, count in alerts_by_run.items():
            if count:
                db.set_scan_run_alerts_sent(run_id, count)
        print(f"Combined alert email sent: {len(all_alerts)} alert(s).")
        return 0
    finally:
        db.close()
        # Every real run - ok or failed - refreshes the front door's status
        # snapshot so its freshness clock tracks scans, not deploys. After
        # db.close() so the subprocess sees fully committed scan_runs rows.
        if not args.dry_run:
            _regenerate_status_json(config)


def show_rules(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    try:
        print("Include and routine-context phrases")
        for row in db.rules():
            active = "active" if row["active"] else "inactive"
            print(f"- {row['rule_type']}: {row['phrase']} ({active}) - {row['description']}")

        print("\nAlert requirements")
        for requirement in ALERT_REQUIREMENTS:
            print(f"- {requirement}")

        print("\nRecent processed filings")
        for row in db.recent_processed():
            print(
                f"- {row['processed_timestamp']} {row['form_type']} "
                f"{row['company_name']} {row['accession_number']} filed {row['filing_date']}"
            )
        return 0
    finally:
        db.close()


def recent_candidates(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    try:
        rows = db.recent_candidates(limit=args.limit)
        if not rows:
            print("No recent candidates.")
            return 0
        for row in rows:
            reason = row["dismissal_reason"] or "alert-ready"
            event_date = row["event_date"] or "no date"
            phrase = row["matched_phrase"] or "no phrase"
            location = row["matched_location"] or "no location"
            print(
                f"- {row['created_timestamp']} [{row['status']}] {row['company_name']} "
                f"{row['form_type']} filed {row['filing_date']}: {phrase} / {event_date} "
                f"at {location} - {reason}"
            )
            print(f"  Source: {row['filing_url']}")
        return 0
    finally:
        db.close()


def send_test(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    try:
        latest_batch = db.latest_sent_alert_batch()
        if not latest_batch:
            print("No recent real PopDay alert batch is available to replay as a format test.")
            return 1
        alerts = [_alert_from_sent_row(row) for row in latest_batch]
        send_privileged_format_test_email(
            config,
            alerts,
            recipient=PRIVILEGED_TEST_RECIPIENT,
        )
    except Exception as exc:
        print(f"Test email failed: {exc}")
        return 1
    finally:
        db.close()
    print(f"Test email sent to {PRIVILEGED_TEST_RECIPIENT}.")
    return 0


def send_known_alerts(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    try:
        rows = db.unsent_known_announcements()
        alerts = [_alert_from_known(row) for row in rows]
        if not alerts:
            print("No unsent known Investor Day announcements.")
            return 0
        if args.dry_run:
            print(build_alert_body(alerts))
            return 0
        send_alert_email(config, alerts, recipients=db.active_alert_recipients())
        db.mark_known_alert_sent([alert.detection_id for alert in alerts])
    except Exception as exc:
        print(f"Known announcement alert failed: {exc}")
        return 1
    finally:
        db.close()
    print(f"Known announcement alerts sent: {len(alerts)}")
    return 0


def watch_hype(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if config.sec_user_agent_has_placeholder_contact:
        print(
            "PopDay needs a real SEC User-Agent contact before running the hype watcher.\n"
            "Set it in config.json or POPDAY_SEC_USER_AGENT and try again."
        )
        return 2

    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    try:
        watched = watch_hype_candidates(config, db)
    except Exception as exc:
        print(f"Hype watcher failed: {exc}")
        return 1
    finally:
        db.close()

    print(f"Hype watcher checked {len(watched)} candidate(s).")
    for row in watched[:20]:
        print(
            f"- {row['company_name']} {row['event_type']} {row['event_date']}: "
            f"{row['hype_status']}{' provisional' if row['provisional'] else ''} "
            f"({row['qualifying_count']})"
        )
    return 0


def _is_hyped_label(status: str) -> bool:
    # "building" (future event) and "hyped" (past event) both mean count >= threshold.
    return status in {"hyped", "building"}


def reclassify_hype(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        updated = reclassify_hype_tracking(
            config, db, dry_run=dry_run, market=getattr(args, "market", None)
        )
    except Exception as exc:
        print(f"Hype reclassification failed: {exc}")
        return 1
    finally:
        db.close()

    changed = [row for row in updated if row.get("changed")]
    to_hyped = [
        row for row in changed
        if _is_hyped_label(row["hype_status"]) and not _is_hyped_label(row["old_status"])
    ]
    to_quiet = [
        row for row in changed
        if not _is_hyped_label(row["hype_status"]) and _is_hyped_label(row["old_status"])
    ]

    mode = "DRY-RUN (no database write)" if dry_run else "wrote"
    print(
        f"Reclassify {mode}: {len(updated)} hype row(s) evaluated with "
        f"threshold={config.hype_threshold}, version={config.hype_definition_version}."
    )
    print(
        f"Would relabel {len(changed)} event(s): "
        f"{len(to_hyped)} quiet->hyped, {len(to_quiet)} hyped->quiet."
    )
    sample = changed or updated
    for row in sample[:20]:
        items = ",".join(row.get("item_codes") or []) or "-"
        print(
            f"- candidate {row['candidate_id']} {row['event_date']}: "
            f"{row['old_status'] or '(none)'} -> {row['hype_status']}"
            f"{' provisional' if row['provisional'] else ''} "
            f"(count={row['qualifying_count']}, items={items})"
        )
    return 0


def debug_ui(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    db.close()
    try:
        serve_debug_ui(config.db_path, args.host, args.port)
    except KeyboardInterrupt:
        print("\nPopDay debug UI stopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan EDGAR for future Investor Day announcements.")
    parser.add_argument("--config", help="Path to config.json. Defaults to ./config.json if present.")
    parser.add_argument(
        "--date",
        help="Run date as YYYY-MM-DD, today, or previous-business-day.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print alerts without sending email.")
    parser.add_argument(
        "--source",
        choices=["edgar", "investegate"],
        default="edgar",
        help=(
            "Discovery source to scan: 'edgar' (US, default - unchanged existing "
            "behaviour) or 'investegate' (UK RNS wire). Each source has its own "
            "scan_runs coverage, self-healing sweep, canary, and ops alarms."
        ),
    )
    parser.add_argument(
        "--market",
        choices=["US", "UK"],
        help="With --reclassify: limit the relabel pass to one market (default: all).",
    )
    parser.add_argument(
        "--backfill-from",
        help=(
            "YYYY-MM-DD floor for the automatic downtime-recovery sweep, "
            "overriding the earliest-recorded-ok-run floor. Use for deliberate "
            "historical recovery (e.g. a gap from before scan_runs existed)."
        ),
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help=(
            "Re-run detection over already-processed filings (replacing their "
            "stored detections). Writes to the database but never sends email; "
            "use to back-fill after a detector improvement."
        ),
    )
    parser.add_argument("--show-rules", action="store_true", help="Show read-only rules/debug view.")
    parser.add_argument(
        "--recent-candidates",
        action="store_true",
        help="Show recent scan log entries and dismissal reasons.",
    )
    parser.add_argument("--send-test-email", action="store_true", help="Send one SMTP test email.")
    parser.add_argument(
        "--send-known-alerts",
        action="store_true",
        help="Send alert emails for unsent known non-EDGAR announcements.",
    )
    parser.add_argument(
        "--watch-hype",
        action="store_true",
        help="Classify upcoming Analyst and Investor Days as hyped or quiet using SEC submissions JSON.",
    )
    parser.add_argument(
        "--reclassify",
        action="store_true",
        help=(
            "Recompute hype labels from stored detected_json only, with no EDGAR calls. "
            "Add --dry-run to preview label changes without writing to the database."
        ),
    )
    parser.add_argument("--debug-ui", action="store_true", help="Start the local read-only debug UI.")
    parser.add_argument(
        "--legacy-parser",
        action="store_true",
        help="Use the older section-based filing parser for one-cycle comparison.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Debug UI host.")
    parser.add_argument("--port", type=int, default=8765, help="Debug UI port.")
    parser.add_argument("--limit", type=int, default=30, help="Recent candidate row limit.")
    parser.add_argument(
        "--max-companies",
        type=int,
        help="Debug/testing limit for number of company submissions to scan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.show_rules:
        return show_rules(args)
    if args.recent_candidates:
        return recent_candidates(args)
    if args.send_test_email:
        return send_test(args)
    if args.send_known_alerts:
        return send_known_alerts(args)
    if args.watch_hype:
        return watch_hype(args)
    if args.reclassify:
        return reclassify_hype(args)
    if args.debug_ui:
        return debug_ui(args)
    if not args.date:
        parser.error(
            "--date is required unless using --show-rules, --recent-candidates, "
            "--send-test-email, --send-known-alerts, --watch-hype, --reclassify, or --debug-ui"
        )
    return run_scan(args)
