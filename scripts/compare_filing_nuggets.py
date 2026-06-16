#!/usr/bin/env python3
"""One-off comparison of legacy vs new PopDay filing nuggets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from popday.config import load_config
from popday.detector import detect_in_sections
from popday.edgar_fetch import EdgarClient, Filing
from popday.emailer import _main_nugget
from popday.filing_parser import best_nugget, parse_sec_filing
from popday.parser import parse_filing_sections


@dataclass(frozen=True)
class Target:
    label: str
    filing_url: str


TARGETS = [
    Target(
        label="Samsara",
        filing_url="https://www.sec.gov/Archives/edgar/data/1642896/000162828026040788/0001628280-26-040788.txt",
    ),
    Target(
        label="Climb Global Solutions",
        filing_url="https://www.sec.gov/Archives/edgar/data/945983/000143774926019562/0001437749-26-019562.txt",
    ),
]


def _parse_iso_date(value: str) -> datetime.date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _legacy_nuggets(raw: str, filing: Filing) -> list[str]:
    sections = parse_filing_sections(raw)
    detections = detect_in_sections(filing, sections, _parse_iso_date(filing.filing_date))
    nuggets: list[str] = []
    for detection in detections:
        if detection.status != "alert_candidate":
            continue
        nugget = _main_nugget(
            SimpleNamespace(
                snippet=detection.snippet,
                event_label=detection.event_type or "Investor Day",
            )
        )
        nuggets.append(nugget or detection.snippet)
    return nuggets


def _base_filing(url: str, accession: str, cik: str, company_name: str, form_type: str, filing_date: str) -> Filing:
    return Filing(
        accession_number=accession,
        cik=cik,
        company_name=company_name,
        form_type=form_type,
        filing_date=filing_date,
        filing_url=url,
        primary_document=url.rsplit("/", 1)[-1],
    )


def main() -> int:
    config = load_config()
    if config.sec_user_agent_has_placeholder_contact:
        raise SystemExit("Set a real SEC User-Agent in config.json before running this script.")

    client = EdgarClient(config.sec_user_agent, config.request_delay_seconds)

    for target in TARGETS:
        raw = client.get_text(target.filing_url)
        parsed = parse_sec_filing(raw)
        filing = _base_filing(
            target.filing_url,
            parsed.accession,
            parsed.cik,
            parsed.company_name,
            parsed.form_type,
            parsed.filed_date,
        )
        old_nuggets = _legacy_nuggets(raw, filing)
        new_nugget = best_nugget(parsed)

        print("=" * 88)
        print(f"{target.label} | accession {parsed.accession} | CIK {parsed.cik}")
        print("=" * 88)
        if old_nuggets:
            for index, old_nugget in enumerate(old_nuggets, start=1):
                print(f"Old nugget {index}: {old_nugget}")
                print(f"New nugget     : {new_nugget}")
                print("-" * 88)
        else:
            print("Old nugget     : <no legacy alert_candidate nugget found>")
            print(f"New nugget     : {new_nugget}")
            print("-" * 88)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
