"""PopDay command line interface."""

from __future__ import annotations

import argparse
import urllib.error
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .config import load_config
from .date_extract import format_human_date
from .db import Database
from .detector import detect_in_sections
from .debug_server import serve_debug_ui
from .edgar_fetch import EdgarClient, TARGET_FORMS
from .emailer import (
    build_alert_body,
    send_alert_email,
    send_privileged_format_test_email,
)
from .hype import watch_hype_candidates
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
    snippet: str = ""
    hype_status: str = ""
    hype_count: int | None = None


PRIVILEGED_TEST_RECIPIENT = "jd@jasondunne.co.uk"


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
        snippet=str(detection.snippet or ""),
    )


def _alert_from_known(row: object) -> Alert:
    return Alert(
        detection_id=int(row["id"]),
        company_name=str(row["company_name"]),
        event_label=str(row["event_type"] or "Investor Day"),
        event_date=datetime.strptime(str(row["event_date"]), "%Y-%m-%d").date(),
        filing_url=str(row["source_url"]),
        source_label=str(row["source_label"] or "Source"),
        snippet="",
        hype_status="",
        hype_count=None,
    )


def _alert_from_sent_row(row: object) -> Alert:
    return Alert(
        detection_id=int(row["id"]),
        company_name=str(row["company_name"]),
        event_label=str(row["event_type"] or "Investor Day"),
        event_date=datetime.strptime(str(row["event_date"]), "%Y-%m-%d").date(),
        filing_url=str(row["source_url"]),
        source_label=str(row["source_label"] or "Source"),
        snippet=str(row["snippet"] or ""),
        hype_status=str(row["hype_status"] or ""),
        hype_count=int(row["qualifying_count"]) if row["qualifying_count"] is not None else None,
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

    try:
        try:
            filings = client.filings_for_date(run_date, max_companies=args.max_companies)
        except urllib.error.HTTPError as exc:
            index_status = "not yet available" if exc.code == 403 else "error"
            _print_health_summary(
                filing_date=run_date,
                index_status=index_status,
                filings_parsed=0,
                eight_k_sanity_count=0,
                alerts_sent=0,
            )
            print(
                f"SEC EDGAR returned HTTP {exc.code} while fetching the daily filing index.\n"
                "This can happen if the daily index for the requested date is not published yet, "
                "or if SEC rejects the User-Agent.\n"
                "Check the date and User-Agent, then try again later."
            )
            return 0 if exc.code == 403 else 1
        except urllib.error.URLError as exc:
            _print_health_summary(
                filing_date=run_date,
                index_status="error",
                filings_parsed=0,
                eight_k_sanity_count=0,
                alerts_sent=0,
            )
            print(
                "PopDay could not reach SEC EDGAR while fetching the daily filing index.\n"
                f"Network error: {exc.reason}\n"
                "Check the internet connection and try again."
            )
            return 1

        eight_k_sanity_count = sum(1 for filing in filings if filing.form_type == "8-K")

        for filing in filings:
            if filing.form_type not in TARGET_FORMS:
                continue
            if not args.dry_run and db.already_processed(filing.accession_number):
                continue

            raw = client.get_text(filing.filing_url)
            sections = parse_filing_sections(raw)
            filings_parsed += 1
            detections = detect_in_sections(
                filing,
                sections,
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

        if not alerts:
            _print_health_summary(
                filing_date=run_date,
                index_status="available",
                filings_parsed=filings_parsed,
                eight_k_sanity_count=eight_k_sanity_count,
                alerts_sent=0,
            )
            return 0

        if args.dry_run:
            _print_health_summary(
                filing_date=run_date,
                index_status="available",
                filings_parsed=filings_parsed,
                eight_k_sanity_count=eight_k_sanity_count,
                alerts_sent=0,
            )
            print(build_alert_body(alerts))
            return 0

        send_alert_email(config, alerts, recipients=db.active_alert_recipients())
        db.mark_alert_sent([alert.detection_id for alert in alerts])
        _print_health_summary(
            filing_date=run_date,
            index_status="available",
            filings_parsed=filings_parsed,
            eight_k_sanity_count=eight_k_sanity_count,
            alerts_sent=len(alerts),
        )
        return 0
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
            f"{row['hype_status']} ({row['qualifying_count']})"
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
    parser.add_argument("--show-rules", action="store_true", help="Show read-only rules/debug view.")
    parser.add_argument(
        "--recent-candidates",
        action="store_true",
        help="Show recent candidate matches and dismissal reasons.",
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
    parser.add_argument("--debug-ui", action="store_true", help="Start the local read-only debug UI.")
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
    if args.debug_ui:
        return debug_ui(args)
    if not args.date:
        parser.error(
            "--date is required unless using --show-rules, --recent-candidates, "
            "--send-test-email, --send-known-alerts, --watch-hype, or --debug-ui"
        )
    return run_scan(args)
