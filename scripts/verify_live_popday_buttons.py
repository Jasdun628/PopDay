#!/usr/bin/env python3
"""Smoke-test PopDay's live public navigation as user-facing button clicks."""

from __future__ import annotations

import argparse
import html as html_lib
import re
import socket
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser


BASE_URL = "https://jasdun.pythonanywhere.com/"


@dataclass(frozen=True)
class PublicTab:
    label: str
    expected_tab: str
    required_text: tuple[str, ...]


PUBLIC_TABS = (
    PublicTab(
        "Price Reaction",
        "price_reaction",
        (
            "Price Reaction",
            "Cached daily market data",
            "Previous Close",
            "Daily Volatility",
            "Interval Return",
            "Average interval return",
        ),
    ),
    PublicTab(
        "Investor Days",
        "announcements",
        (
            "Investor Days",
            "Running list of qualifying Investor Day announcements",
            "Company",
            "Evidence",
            "Upcoming",
            "Legacy",
        ),
    ),
    PublicTab(
        "Research / Hype",
        "research",
        (
            "Research / Hype",
            "Research counts are shown only where PopDay has enough data",
            "Upcoming",
            "Legacy",
            "Raw Hype Count",
            "Investor Comms Count",
        ),
    ),
    PublicTab(
        "Scan Log",
        "candidates",
        (
            "Scan Log",
            "Filings PopDay inspected",
            "Status",
            "Source",
            "Next scan",
            "Recent scan runs",
        ),
    ),
    PublicTab(
        "System Health",
        "summary",
        (
            "Summary",
            "Scanner health",
            "Browser freshness",
            "Latest scan facts",
        ),
    ),
    PublicTab(
        "Help",
        "help",
        (
            "Help",
            "What each public PopDay tab is for",
            "Price Reaction",
            "Investor Days",
            "Research / Hype",
            "Scan Log",
            "System Health",
        ),
    ),
)


class NavLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._nav_depth = 0
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "nav":
            self._nav_depth = 1
            return
        if self._nav_depth:
            self._nav_depth += 1
            if tag == "a":
                self._current_href = dict(attrs).get("href")
                self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if not self._nav_depth:
            return
        if tag == "a" and self._current_href is not None:
            text = normalize_text("".join(self._current_text))
            self.links.append((text, self._current_href))
            self._current_href = None
            self._current_text = []
        self._nav_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._nav_depth and self._current_href is not None:
            self._current_text.append(data)


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._current_href = dict(attrs).get("href")
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href is not None:
            text = html_lib.unescape(normalize_text("".join(self._current_text)))
            self.links.append((text, self._current_href))
            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)


def fetch(url: str) -> tuple[str, str, int]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "PopDay-live-button-smoke-test/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                html = response.read().decode("utf-8", "replace")
                return html, response.geturl(), response.status
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def plain_text(markup: str) -> str:
    return html_lib.unescape(normalize_text(re.sub("<[^>]+>", " ", markup)))


def nav_links(html: str) -> dict[str, str]:
    parser = NavLinkParser()
    parser.feed(html)
    return {label: href for label, href in parser.links if label and href}


def all_links(markup: str) -> dict[str, list[str]]:
    parser = AnchorParser()
    parser.feed(markup)
    links: dict[str, list[str]] = {}
    for label, href in parser.links:
        if label and href:
            links.setdefault(label, []).append(href)
    return links


def unlinked_investor_day_companies(markup: str) -> list[str]:
    unlinked: list[str] = []
    cells = re.findall(
        r'<td class="primary-cell">\s*(.*?)\s*</td>',
        markup,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for cell in cells:
        text = plain_text(cell)
        if text and "<a " not in cell.lower():
            unlinked.append(text)
    return unlinked


def check(name: str, ok: bool, failures: list[str], detail: str = "") -> None:
    suffix = f" - {detail}" if detail else ""
    print(f"{name}: {'OK' if ok else 'FAIL'}{suffix}")
    if not ok:
        failures.append(f"{name}{suffix}")


def query_tab(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    values = urllib.parse.parse_qs(parsed.query)
    return values.get("tab", [""])[0]


def check_public_tabs(base_url: str, failures: list[str]) -> None:
    front_html, front_url, front_status = fetch(base_url)
    front_text = plain_text(front_html)
    links = nav_links(front_html)

    check("front door loads", front_status == 200 and "PopDay" in front_text, failures, front_url)
    check("public nav has no Rules tab", "Rules" not in links, failures)

    for tab in PUBLIC_TABS:
        href = links.get(tab.label)
        check(f"{tab.label} button exists", href is not None, failures)
        if href is None:
            continue

        target = urllib.parse.urljoin(base_url, href)
        html, final_url, status = fetch(target)
        text = plain_text(html)
        parsed = urllib.parse.urlparse(final_url)

        check(f"{tab.label} click loads", status == 200, failures, final_url)
        check(
            f"{tab.label} stays public",
            not parsed.path.startswith("/admin"),
            failures,
            final_url,
        )
        check(
            f"{tab.label} opens expected tab",
            query_tab(final_url) == tab.expected_tab,
            failures,
            final_url,
        )
        for required in tab.required_text:
            check(f"{tab.label} shows {required}", required in text, failures)
        if tab.expected_tab == "announcements":
            unlinked = unlinked_investor_day_companies(html)
            check(
                "Investor Days company names are linked",
                not unlinked,
                failures,
                f"unlinked: {', '.join(unlinked)}" if unlinked else "",
            )
            check("Investor Days Email column removed", "<th>Email</th>" not in html, failures)
            check(
                "Investor Days Evidence between Company and Event Date",
                html.find("<th>Company</th>") != -1
                and html.find("<th>Evidence</th>") != -1
                and html.find("Event Date") != -1
                and html.find("<th>Company</th>") < html.find("<th>Evidence</th>")
                and html.find("<th>Evidence</th>") < html.find("Event Date"),
                failures,
            )


def check_email_alert_boundary(base_url: str, failures: list[str]) -> None:
    target = urllib.parse.urljoin(base_url, "/admin/recipients")
    html, final_url, status = fetch(target)
    text = plain_text(html)
    parsed = urllib.parse.urlparse(final_url)
    check("Email Alerts unauthenticated request loads", status == 200, failures, final_url)
    check(
        "Email Alerts redirects to login when signed out",
        parsed.path == "/admin/login",
        failures,
        final_url,
    )
    check("Email Alerts login page visible", "Sign in" in text or "Password" in text, failures)
    check("Email Alerts does not expose recipients signed out", "Unsubscribe" not in text, failures)


def check_expected_companies(base_url: str, companies: list[str], failures: list[str]) -> None:
    if not companies:
        return
    html, final_url, status = fetch(urllib.parse.urljoin(base_url, "/?tab=announcements"))
    text = plain_text(html)
    check("expected-company page loads", status == 200, failures, final_url)
    for company in companies:
        check(f"Investor Days includes {company}", company in text, failures)


def check_expected_company_links(
    base_url: str,
    company_links: list[str],
    failures: list[str],
) -> None:
    if not company_links:
        return
    html, final_url, status = fetch(urllib.parse.urljoin(base_url, "/?tab=announcements"))
    text = plain_text(html)
    links = all_links(html)
    check("expected-company-link page loads", status == 200, failures, final_url)
    for value in company_links:
        if "=" not in value:
            check(f"expected company link argument {value}", False, failures, "use Company=URL")
            continue
        company, expected_url = [part.strip() for part in value.split("=", 1)]
        company_hrefs = links.get(company, [])
        check(f"Investor Days includes {company}", company in text, failures)
        check(
            f"Investor Days links {company}",
            expected_url in company_hrefs,
            failures,
            f"expected {expected_url}; found {company_hrefs or 'no link'}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify PopDay's live public buttons and admin boundary."
    )
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument(
        "--expect-company",
        action="append",
        default=[],
        help="Company name that must appear on the live Investor Days tab.",
    )
    parser.add_argument(
        "--expect-company-link",
        action="append",
        default=[],
        help="Company=URL pair that must appear as a live Investor Days hyperlink.",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/") + "/"
    failures: list[str] = []

    check_public_tabs(base_url, failures)
    check_email_alert_boundary(base_url, failures)
    check_expected_companies(base_url, args.expect_company, failures)
    check_expected_company_links(base_url, args.expect_company_link, failures)

    if failures:
        print("PopDay live button verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("PopDay live button verification passed.")
    print(f"Open: {base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
