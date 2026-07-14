"""Email digest market grouping: US-only output unchanged, mixed grouped."""

from __future__ import annotations

import unittest
from datetime import date

from popday.cli import Alert
from popday.emailer import build_alert_body, build_alert_html


def _alert(company: str, market: str = "US") -> Alert:
    return Alert(
        detection_id=1,
        company_name=company,
        event_label="Investor Day",
        event_date=date(2026, 9, 1),
        filing_url="https://example.test/filing",
        market=market,
    )


class EmailGroupingTests(unittest.TestCase):
    def test_us_only_email_has_no_section_headers(self):
        body = build_alert_body([_alert("Alpha Corp"), _alert("Beta Inc")])
        self.assertNotIn("US - SEC EDGAR", body)
        self.assertNotIn("UK - RNS", body)

    def test_us_only_body_identical_to_legacy_shape(self):
        body = build_alert_body([_alert("Alpha Corp")])
        self.assertIn("POPDAY ALERT", body)
        self.assertIn("PopDay found 1 new investor-event announcement.", body)
        self.assertIn("Company: Alpha Corp", body)

    def test_mixed_markets_grouped_with_us_first(self):
        body = build_alert_body(
            [_alert("UK Plc", market="UK"), _alert("US Corp", market="US")]
        )
        self.assertIn("US - SEC EDGAR", body)
        self.assertIn("UK - RNS (via Investegate)", body)
        self.assertLess(body.index("US - SEC EDGAR"), body.index("UK - RNS (via Investegate)"))
        self.assertLess(body.index("US Corp"), body.index("UK Plc"))

    def test_uk_only_email_has_no_section_headers(self):
        body = build_alert_body([_alert("UK Plc", market="UK")])
        self.assertNotIn("UK - RNS", body)

    def test_html_mixed_markets_grouped(self):
        html_body = build_alert_html(
            [_alert("UK Plc", market="UK"), _alert("US Corp", market="US")]
        )
        self.assertIn("US - SEC EDGAR", html_body)
        self.assertIn("UK - RNS (via Investegate)", html_body)


if __name__ == "__main__":
    unittest.main()
