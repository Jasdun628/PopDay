"""PopDay command line interface."""

from __future__ import annotations

import argparse
import urllib.error
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .config import load_config
from .date_extract import format_human_date
from .db import Database
from .detector import detect_in_parsed_filing, detect_in_sections
from .debug_server import serve_debug_ui
from .edgar_fetch import (
    EdgarBlockedError,
    EdgarClient,
    EdgarUnavailableError,
    Filing,
    TARGET_FORMS,
)
from .emailer import (
    build_alert_body,
    send_alert_email,
    send_privileged_format_test_email,
)
from .filing_parser import parse_sec_filing
from .hype import reclassify_hype_tracking, watch_hype_candidates
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


def _alert_from_detection(detection_id: int, detection: object) -> Alert:
    return Alert(
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
    )


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


def run_scan(args: argparse.Namespace) -> int:
    print(f"PopDay launchd run started: {_health_timestamp()}", flush=True)
    config = load_config(args.config)
    if config.sec_user_agent_has_placeholder_contact:
        run_date = parse_run_date(args.date)
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

    db = Database(config.db_path)
    db.seed_recipients(config.email_recipients)
    run_date = parse_run_date(args.date)
    client = EdgarClient(config.sec_user_agent, config.request_delay_seconds)
    alerts: list[Alert] = []
    filings_parsed = 0
    eight_k_sanity_count = 0
    include_phrases = db.active_phrases("include")
    routine_phrases = db.active_phrases("routine_context")

    scan_run_id = db.start_scan_run(run_date)

    def _fail_scan(reason: str, detail: str) -> int:
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
        return 1

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
                if detection.status == "alert_candidate" and detection_id:
                    alerts.append(_alert_from_detection(detection_id, detection))
                elif detection.status == "alert_candidate" and args.dry_run:
                    alerts.append(_alert_from_detection(0, detection))

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

        def _record_ok(alerts_sent: int) -> None:
            db.finish_scan_run(
                scan_run_id,
                status="ok",
                filings_seen=len(filings),
                filings_parsed=filings_parsed,
                alerts_sent=alerts_sent,
                source=discovery_source,
                error=downstream_note,
            )

        if not alerts:
            _record_ok(0)
            _print_health_summary(
                filing_date=run_date,
                index_status="available",
                filings_parsed=filings_parsed,
                eight_k_sanity_count=eight_k_sanity_count,
                alerts_sent=0,
            )
            return 0

        if args.dry_run or args.reprocess:
            # Reprocess writes the re-detected candidates to the database (so the
            # Investor Days tab updates) but, like dry-run, never emails.
            _record_ok(0)
            note = "DRY-RUN" if args.dry_run else "REPROCESS (detections written, no email)"
            _print_health_summary(
                filing_date=run_date,
                index_status="available",
                filings_parsed=filings_parsed,
                eight_k_sanity_count=eight_k_sanity_count,
                alerts_sent=0,
            )
            print(f"[{note}] {len(alerts)} candidate alert(s):")
            print(build_alert_body(alerts))
            return 0

        send_alert_email(config, alerts, recipients=db.active_alert_recipients())
        db.mark_alert_sent([alert.detection_id for alert in alerts])
        _record_ok(len(alerts))
        _print_health_summary(
            filing_date=run_date,
            index_status="available",
            filings_parsed=filings_parsed,
            eight_k_sanity_count=eight_k_sanity_count,
            alerts_sent=len(alerts),
        )
        return 0
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
    finally:
        db.close()


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
        updated = reclassify_hype_tracking(config, db, dry_run=dry_run)
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
