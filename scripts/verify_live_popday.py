#!/usr/bin/env python3
"""Verify the public PopDay front door after deploys."""

from __future__ import annotations

import re
import sys
import urllib.request


BASE_URL = "https://jasdun.pythonanywhere.com/"


def fetch(path: str = "") -> str:
    url = BASE_URL + path
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def plain_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", html))


def nav_text(html: str) -> str:
    match = re.search(r"<nav\b.*?</nav>", html, re.S)
    return plain_text(match.group(0)) if match else ""


def check(name: str, ok: bool, failures: list[str]) -> None:
    print(f"{name}: {'OK' if ok else 'FAIL'}")
    if not ok:
        failures.append(name)


def main() -> int:
    failures: list[str] = []

    front = fetch()
    check("front title", "<title>PopDay</title>" in front, failures)
    check(
        "public tabs",
        all(tab in front for tab in ["System Health", "Investor Days", "Research / Hype", "Price Reaction", "Schedule", "Scan Log", "Help"])
        and "Filings" not in nav_text(front),
        failures,
    )
    check(
        "schedule before system health",
        nav_text(front).find("Price Reaction")
        < nav_text(front).find("Investor Days")
        < nav_text(front).find("Research / Hype")
        < nav_text(front).find("Scan Log")
        < nav_text(front).find("Schedule")
        < nav_text(front).find("System Health")
        < nav_text(front).find("Help"),
        failures,
    )
    check(
        "price reaction before scan log",
        nav_text(front).find("Price Reaction")
        < nav_text(front).find("Scan Log"),
        failures,
    )
    check("rules tab hidden", "Rules" not in front, failures)
    check("email alerts management link", "Email Alerts" in front and "/admin/recipients" in front, failures)
    check(
        "opens price reaction tab",
        "Cached daily market data" in front,
        failures,
    )
    front_text = plain_text(front)
    check("front door current", "PopDay is not current" not in front_text, failures)

    candidates = fetch("?tab=candidates&v=verify")
    candidates_text = plain_text(candidates)
    check("hype column", "Hype" in candidates_text, failures)
    check(
        "hype labels visible",
        "provisional" in candidates_text or "voluntary filing" in candidates_text or "not checked" in candidates_text,
        failures,
    )

    research = fetch("?tab=research&v=verify")
    research_text = plain_text(research)
    check("research tab", "Research / Hype" in research_text, failures)
    check("research upcoming legacy sections", research_text.find("Upcoming") < research_text.find("Legacy"), failures)
    check(
        "research hype columns",
        all(
            label in research_text
            for label in [
                "Raw Hype Count",
                "Investor Comms Count",
                "8-K 7.01 Count",
                "8-K 8.01 Count",
                "Latest filing AD-ID",
            ]
        ),
        failures,
    )

    price_reaction = fetch("?tab=price_reaction&v=verify")
    price_text = plain_text(price_reaction)
    check("price reaction tab", "Price Reaction" in price_text, failures)
    check(
        "price reaction cache labels",
        all(
            label in price_text
            for label in [
                "Cached daily market data",
                "Previous Close",
                "Reaction Close",
                "Daily Volatility",
                "Interval Return",
                "Total interval return",
            ]
        ),
        failures,
    )
    check(
        "price reaction upcoming legacy sections",
        price_text.find("Upcoming") < price_text.find("Legacy"),
        failures,
    )

    announcements = fetch("?tab=announcements&v=verify")
    text = plain_text(announcements)
    check("evidence links visible", "Exhibit 99.1" in text or "Business Wire release" in text, failures)
    check("upcoming legacy sections", text.find("Upcoming") < text.find("Legacy"), failures)
    check(
        "company website links",
        "https://www.harmonicinc.com/" in announcements
        or "https://www.slb.com/" in announcements
        or "https://www.climbglobalsolutions.com/" in announcements
        or "https://www.samsara.com/" in announcements
        or "https://www.radian.com/" in announcements,
        failures,
    )
    check(
        "investor-days column order",
        "<th>Email</th>" not in announcements
        and announcements.find("<th>Company</th>")
        < announcements.find("<th>Evidence</th>")
        < announcements.find("Event Date"),
        failures,
    )
    check("triangle sorters", announcements.count("▲") == 6 and announcements.count("▼") == 6, failures)
    check("hi-lo labels removed", "Hi</a>" not in announcements and "Lo</a>" not in announcements, failures)
    check(
        "upcoming before legacy rows",
        "HARMONIC INC." in text
        and "Climb Global Solutions" in text
        and "SLB LIMITED/NV" in text
        and "Samsara Inc." in text
        and text.find("Upcoming") < text.find("HARMONIC INC.")
        and text.find("Climb Global Solutions") < text.find("Legacy")
        and text.find("Legacy") < text.find("SLB LIMITED/NV")
        and text.find("SLB LIMITED/NV") < text.find("Samsara Inc."),
        failures,
    )

    if failures:
        print("PopDay live verification failed: " + ", ".join(failures), file=sys.stderr)
        return 1

    print("PopDay live verification passed.")
    print(f"Open: {BASE_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
