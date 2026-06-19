import unittest
from dataclasses import dataclass
from datetime import date

from popday.emailer import _sec_readable_url, build_alert_body


@dataclass(frozen=True)
class Alert:
    company_name: str = "HARMONIC INC."
    event_label: str = "Investor Day"
    event_date: date = date(2026, 9, 15)
    filing_url: str = "https://www.sec.gov/Archives/example.txt"
    source_label: str = "SEC filing"
    form_type: str = "8-K"
    event_url: str = ""
    evidence_url: str = ""
    evidence_label: str = ""
    snippet: str = (
        "Harmonic also announced it will host an Investor Day event in New York City "
        "on September 15, 2026, offering a detailed look at the company's core "
        "technologies, innovation and growth outlook. The hybrid event will include "
        "limited in-person attendance by invitation only and a live webcast available "
        "on Harmonic's Investor Relations website."
    )
    hype_status: str = "quiet"
    hype_count: int = 0
    hype_provisional: bool = True


class EmailerTests(unittest.TestCase):
    def test_alert_body_contains_qualitative_nugget_and_excerpt(self):
        body = build_alert_body([Alert()])

        self.assertIn("MAIN NUGGET", body)
        self.assertIn("KEY EXCERPT", body)
        self.assertIn("core technologies, innovation and growth outlook", body)
        self.assertNotIn("Disclosure activity", body)
        self.assertNotIn("voluntary filings since announcement", body)

    def test_alert_body_drops_leading_fragment(self):
        alert = Alert(
            snippet=(
                "ized software-defined broadband networks powered by intelligence capabilities. "
                "We are proud of the exceptional work.” Harmonic also announced it will host an "
                "Investor Day event in New York City on September 15, 2026, offering a detailed "
                "look at the company's core technologies, innovation and growth outlook."
            )
        )

        body = build_alert_body([alert])

        self.assertIn("Harmonic also announced it will host", body)
        self.assertNotIn("MAIN NUGGET\nized", body)

    def test_alert_body_includes_company_event_link_when_found(self):
        alert = Alert(event_url="https://investor.example.com/events/investor-day")

        body = build_alert_body([alert])

        self.assertIn("COMPANY EVENT / IR LINK", body)
        self.assertIn("https://investor.example.com/events/investor-day", body)

    def test_sec_complete_submission_url_becomes_readable_index_page(self):
        url = "https://www.sec.gov/Archives/edgar/data/851310/0001193125-26-273457.txt"

        self.assertEqual(
            _sec_readable_url(url),
            "https://www.sec.gov/Archives/edgar/data/851310/000119312526273457/0001193125-26-273457-index.htm",
        )

    def test_alert_body_uses_readable_sec_page_link(self):
        alert = Alert(
            filing_url="https://www.sec.gov/Archives/edgar/data/851310/0001193125-26-273457.txt"
        )

        body = build_alert_body([alert])

        self.assertIn("SEC FILING: 8-K", body)
        self.assertIn("0001193125-26-273457-index.htm", body)
        self.assertNotIn("0001193125-26-273457.txt", body)

    def test_alert_body_puts_evidence_link_before_parent_filing(self):
        alert = Alert(
            filing_url="https://www.sec.gov/Archives/edgar/data/851310/0001193125-26-273457.txt",
            evidence_url="https://www.sec.gov/Archives/edgar/data/851310/000119312526273457/d842935dex991.htm",
            evidence_label="Exhibit 99.1",
        )

        body = build_alert_body([alert])

        self.assertIn("EVIDENCE: Exhibit 99.1", body)
        self.assertIn("d842935dex991.htm", body)
        self.assertLess(body.find("EVIDENCE: Exhibit 99.1"), body.find("SEC FILING: 8-K"))


if __name__ == "__main__":
    unittest.main()
