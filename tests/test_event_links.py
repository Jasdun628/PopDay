import unittest
from datetime import date

from popday.detector import detect_in_parsed_filing
from popday.edgar_fetch import Filing
from popday.filing_parser import extract_links, parse_sec_filing


class EventLinkTests(unittest.TestCase):
    def test_extract_links_preserves_anchor_text(self):
        links = extract_links(
            '<a href="https://investor.example.com/events">Investor Day webcast</a>'
        )

        self.assertEqual(
            links,
            [{"url": "https://investor.example.com/events", "text": "Investor Day webcast"}],
        )

    def test_parser_captures_acceptance_datetime_in_new_york_time(self):
        raw = """
<SEC-HEADER>
ACCESSION NUMBER: 0000000000-26-000001
CONFORMED SUBMISSION TYPE: 8-K
COMPANY CONFORMED NAME: EXAMPLE INC
CENTRAL INDEX KEY: 0000000001
FILED AS OF DATE: 20260619
<ACCEPTANCE-DATETIME>20260619163205
</SEC-HEADER>
"""
        parsed = parse_sec_filing(raw)

        self.assertEqual(parsed.acceptance_datetime, "2026-06-19T16:32:05-04:00")

    def test_detector_attaches_best_company_event_link(self):
        raw = """
<SEC-HEADER>
ACCESSION NUMBER: 0000000000-26-000001
CONFORMED SUBMISSION TYPE: 8-K
COMPANY CONFORMED NAME: EXAMPLE INC
CENTRAL INDEX KEY: 0000000001
FILED AS OF DATE: 20260619
ITEM INFORMATION: 7.01
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-99.1
<FILENAME>d123dex991.htm
<DESCRIPTION>Press release
<TEXT>
<html><body>
<p>Example Inc. will host an Investor Day on September 15, 2026, offering
a detailed look at strategy and growth.</p>
<p>The live webcast will be available at
<a href="https://investors.example.com/events/investor-day">Investor Day webcast</a>.</p>
</body></html>
</TEXT>
</DOCUMENT>
"""
        parsed = parse_sec_filing(raw)
        filing = Filing(
            accession_number="0000000000-26-000001",
            cik="0000000001",
            company_name="Example Inc.",
            form_type="8-K",
            filing_date="2026-06-19",
            filing_url="https://www.sec.gov/Archives/edgar/data/1/0000000000-26-000001.txt",
            primary_document="example.htm",
        )

        detections = detect_in_parsed_filing(filing, parsed, date(2026, 6, 19))

        self.assertEqual(detections[0].status, "alert_candidate")
        self.assertEqual(
            detections[0].event_url,
            "https://investors.example.com/events/investor-day",
        )
        self.assertEqual(detections[0].evidence_label, "Exhibit 99.1")
        self.assertEqual(
            detections[0].evidence_url,
            "https://www.sec.gov/Archives/edgar/data/1/000000000026000001/d123dex991.htm",
        )


    def test_dateless_investor_day_is_flagged_as_date_tbd(self):
        raw = """
<SEC-HEADER>
ACCESSION NUMBER: 0000000000-26-000002
CONFORMED SUBMISSION TYPE: 8-K
COMPANY CONFORMED NAME: EXAMPLE INC
CENTRAL INDEX KEY: 0000000001
FILED AS OF DATE: 20260701
ITEM INFORMATION: 7.01
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-99.1
<FILENAME>ex991.htm
<DESCRIPTION>Press release
<TEXT>
<html><body>
<p>Example Inc. plans to hold an Investor Day following its second quarter fiscal 2027 results.</p>
</body></html>
</TEXT>
</DOCUMENT>
"""
        parsed = parse_sec_filing(raw)
        filing = Filing(
            accession_number="0000000000-26-000002",
            cik="0000000001",
            company_name="Example Inc.",
            form_type="8-K",
            filing_date="2026-07-01",
            filing_url="https://www.sec.gov/Archives/edgar/data/1/0000000000-26-000002.txt",
            primary_document="example.htm",
        )

        detection = detect_in_parsed_filing(filing, parsed, date(2026, 7, 1))[0]

        self.assertEqual(detection.status, "alert_candidate_tbd")
        self.assertIsNone(detection.event_date)
        self.assertEqual(detection.event_type, "Investor Day")
        self.assertEqual(detection.matched_phrase, "investor day")


if __name__ == "__main__":
    unittest.main()
