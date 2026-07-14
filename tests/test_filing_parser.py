import sys
import types
import unittest
from unittest import mock

from popday.filing_parser import html_to_text


class HtmlToTextBs4FallbackTests(unittest.TestCase):
    def test_falls_back_to_stdlib_parser_when_bs4_raises(self):
        fake_bs4 = types.ModuleType("bs4")
        fake_bs4.BeautifulSoup = mock.Mock(side_effect=Exception("ParserRejectedMarkup"))
        with mock.patch.dict(sys.modules, {"bs4": fake_bs4}):
            text = html_to_text("<p>Investor Day announced <![<<AZ\\(YCT?HP^K_ \" garbage</p>")

        self.assertIn("Investor Day announced", text)

    def test_falls_back_to_stdlib_parser_when_bs4_missing(self):
        with mock.patch.dict(sys.modules, {"bs4": None}):
            text = html_to_text("<p>Investor Day webcast</p>")

        self.assertEqual(text, "Investor Day webcast")


if __name__ == "__main__":
    unittest.main()
