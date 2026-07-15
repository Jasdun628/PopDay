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


    def test_xbrl_taxonomy_urls_are_never_picked_as_event_url(self):
        """Regression test for the Ligand alert (14 Jul 2026): a filing whose
        only URLs are XBRL taxonomy/linkbase references must come up with no
        event_url at all, not a wrong link. "presentation" is a substring of
        "presentationLinkbaseRef", which used to score these as if they were
        a real investor-presentation link."""
        raw = """
<SEC-HEADER>
ACCESSION NUMBER: 0000000000-26-000003
CONFORMED SUBMISSION TYPE: 8-K
COMPANY CONFORMED NAME: EXAMPLE INC
CENTRAL INDEX KEY: 0000000001
FILED AS OF DATE: 20260714
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
<p>See http://www.xbrl.org/2003/role/presentationLinkbaseRef and
http://www.xbrl.org/2003/role/label for taxonomy details.
Also https://xbrl.sec.gov/dei/2026/dei-2026.xsd and
https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany.</p>
</body></html>
</TEXT>
</DOCUMENT>
"""
        parsed = parse_sec_filing(raw)
        filing = Filing(
            accession_number="0000000000-26-000003",
            cik="0000000001",
            company_name="Example Inc.",
            form_type="8-K",
            filing_date="2026-07-14",
            filing_url="https://www.sec.gov/Archives/edgar/data/1/0000000000-26-000003.txt",
            primary_document="example.htm",
        )

        detections = detect_in_parsed_filing(filing, parsed, date(2026, 7, 14))

        self.assertEqual(detections[0].status, "alert_candidate")
        self.assertEqual(detections[0].event_url, "")

    def test_ifrs_taxonomy_urls_are_never_picked_as_event_url(self):
        """Regression test found during a historical event_url cleanup
        (2026-07-15): foreign private issuers (6-K/20-F) use the IFRS
        taxonomy namespace (xbrl.ifrs.org) instead of xbrl.org, and its
        "NonadjustingEventsAfterReportingPeriod" URI slug contains "events" -
        one of the EVENT_LINK_HINTS keywords - so it used to score as a real
        link once it dodged the xbrl.org-only host filter."""
        raw = """
<SEC-HEADER>
ACCESSION NUMBER: 0000000000-26-000004
CONFORMED SUBMISSION TYPE: 6-K
COMPANY CONFORMED NAME: EXAMPLE PLC
CENTRAL INDEX KEY: 0000000001
FILED AS OF DATE: 20260714
ITEM INFORMATION: 7.01
</SEC-HEADER>
<DOCUMENT>
<TYPE>EX-99.1
<FILENAME>d123dex991.htm
<DESCRIPTION>Press release
<TEXT>
<html><body>
<p>Example plc will host an Investor Day on September 15, 2026, offering
a detailed look at strategy and growth.</p>
<p>See https://xbrl.ifrs.org/taxonomy/2024-03-27/full_ifrs/full_ifrs-cor_2024-03-27.xsd#ifrs-full_NonadjustingEventsAfterReportingPeriodAxis
for taxonomy details.</p>
</body></html>
</TEXT>
</DOCUMENT>
"""
        parsed = parse_sec_filing(raw)
        filing = Filing(
            accession_number="0000000000-26-000004",
            cik="0000000001",
            company_name="Example plc",
            form_type="6-K",
            filing_date="2026-07-14",
            filing_url="https://www.sec.gov/Archives/edgar/data/1/0000000000-26-000004.txt",
            primary_document="example.htm",
        )

        detections = detect_in_parsed_filing(filing, parsed, date(2026, 7, 14))

        self.assertEqual(detections[0].status, "alert_candidate")
        self.assertEqual(detections[0].event_url, "")

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
