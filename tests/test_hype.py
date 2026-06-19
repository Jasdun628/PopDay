import unittest
from datetime import date

from popday.hype import _candidate_from_row, _parse_iso_date


class HypeDateParsingTests(unittest.TestCase):
    def test_parse_iso_and_sec_compact_dates(self):
        self.assertEqual(_parse_iso_date("2026-06-17"), date(2026, 6, 17))
        self.assertEqual(_parse_iso_date("20260617"), date(2026, 6, 17))

    def test_candidate_accepts_runtime_filing_date_format(self):
        row = {
            "id": 4272,
            "accession_number": "0000851310-26-000001",
            "company_name": "HARMONIC INC.",
            "cik": "0000851310",
            "filing_date": "20260617",
            "event_date": "2026-09-15",
            "event_type": "Investor Day",
            "filing_url": "https://www.sec.gov/example.txt",
        }

        candidate = _candidate_from_row(row)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.announcement_date, date(2026, 6, 17))


if __name__ == "__main__":
    unittest.main()
