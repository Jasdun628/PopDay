import unittest
from dataclasses import dataclass
from datetime import date

from popday.emailer import _sec_readable_url, build_alert_body, build_alert_html


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
    context_text: str = ""
    company_url_missing: bool = False


class EmailerTests(unittest.TestCase):
    def test_alert_body_contains_qualitative_nugget_and_excerpt(self):
        # Detection.snippet is always a single sentence in real usage (it
        # comes from best_nugget()); the class default above has two
        # concatenated sentences purely to exercise Key Excerpt's own
        # multi-sentence trimming in other tests, so it's overridden here.
        alert = Alert(
            snippet=(
                "Harmonic also announced it will host an Investor Day event in New York "
                "City on September 15, 2026, offering a detailed look at the company's "
                "core technologies, innovation and growth outlook."
            ),
            context_text=(
                "Harmonic also announced it will host an Investor Day event in New York "
                "City on September 15, 2026, offering a detailed look at the company's "
                "core technologies, innovation and growth outlook. The event will mark "
                "Harmonic's first Investor Day since its 2019 spin-off and will include a "
                "5-year outlook for the broadband business. The hybrid event will include "
                "limited in-person attendance by invitation only and a live webcast "
                "available on Harmonic's Investor Relations website."
            )
        )
        body = build_alert_body([alert])

        self.assertIn("MAIN NUGGET", body)
        self.assertIn("KEY EXCERPT", body)
        self.assertIn("core technologies, innovation and growth outlook", body)
        self.assertIn("5-year outlook", body)
        self.assertNotIn("Disclosure activity", body)
        self.assertNotIn("voluntary filings since announcement", body)

    def test_main_nugget_omitted_without_context_text(self):
        # No context_text (e.g. legacy detect_in_sections path, or a known
        # announcement with no filing text): Main Nugget must not fall back
        # to re-deriving the same sentence as Key Excerpt.
        body = build_alert_body([Alert()])

        self.assertNotIn("MAIN NUGGET", body)
        self.assertIn("KEY EXCERPT", body)

    def test_main_nugget_omitted_when_no_distinct_sentence_nearby(self):
        # A short press release where every neighboring sentence either
        # lacks a number/date/context marker or IS the Key Excerpt: nothing
        # distinct to show, so Main Nugget is correctly dropped rather than
        # duplicating Key Excerpt.
        alert = Alert(
            snippet="Example Corp will host an Investor Day on September 15, 2026.",
            context_text=(
                "Example Corp will host an Investor Day on September 15, 2026. "
                "The event will be well attended. Management looks forward to it."
            ),
        )
        body = build_alert_body([alert])

        self.assertNotIn("MAIN NUGGET", body)

    def test_main_nugget_suppressed_when_too_similar_to_key_excerpt(self):
        # Similarity-guard backstop: even a "neighboring" sentence that just
        # restates Key Excerpt in slightly different words must not surface
        # as a second, near-duplicate block.
        alert = Alert(
            snippet="Example Corp will host an Investor Day on September 15, 2026.",
            context_text=(
                "Example Corp will host an Investor Day on September 15, 2026. "
                "Example Corp will host an Investor Day event on 15 September 2026 in full."
            ),
        )
        body = build_alert_body([alert])

        self.assertNotIn("MAIN NUGGET", body)

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

    def test_alert_body_notes_missing_company_website(self):
        alert = Alert(company_url_missing=True)

        body = build_alert_body([alert])

        self.assertIn("Note:    No website on file for this company yet", body)

    def test_alert_body_omits_missing_website_note_when_resolved(self):
        alert = Alert(company_url_missing=False)

        body = build_alert_body([alert])

        self.assertNotIn("No website on file", body)

    def test_alert_html_notes_missing_company_website(self):
        alert = Alert(company_url_missing=True)

        html = build_alert_html([alert])

        self.assertIn("No website on file for this company yet", html)

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
