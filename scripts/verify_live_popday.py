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
        all(tab in front for tab in ["Summary", "Investor Days", "System Health", "Candidates", "Filings", "Help"]),
        failures,
    )
    check("rules tab hidden", "Rules" not in front, failures)
    check("email alerts management link", "Email Alerts" in front and "/admin/recipients" in front, failures)
    check("health strip", "Scanner health" in front, failures)
    front_text = plain_text(front)
    check("front door current", "PopDay is not current" not in front_text, failures)

    candidates = fetch("?tab=candidates&v=verify")
    candidates_text = plain_text(candidates)
    check("hype column", "Hype" in candidates_text, failures)
    check(
        "hype labels visible",
        "provisional" in candidates_text or "voluntary filing" in candidates_text,
        failures,
    )

    announcements = fetch("?tab=announcements&v=verify")
    text = plain_text(announcements)
    check("investor-days column removed", "Company Event Event Date" not in text and "Company Event Date" in text, failures)
    check("triangle sorters", announcements.count("▲") == 2 and announcements.count("▼") == 2, failures)
    check("hi-lo labels removed", "Hi</a>" not in announcements and "Lo</a>" not in announcements, failures)
    check(
        "newest filed first",
        "SLB LIMITED/NV" in text
        and "Climb Global Solutions" in text
        and text.find("SLB LIMITED/NV") < text.find("Climb Global Solutions"),
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
