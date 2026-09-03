"""Tests for popday/website_resolver.py - heuristic company-website auto-resolve."""

from __future__ import annotations

import unittest
from unittest import mock

from popday.website_resolver import (
    WebsiteCandidateChecker,
    domain_candidates,
    resolve_website_heuristic,
)


class DomainCandidatesTests(unittest.TestCase):
    def test_strips_legal_suffix_and_concatenates(self):
        self.assertEqual(
            domain_candidates("Commercial Metals Co"),
            ["commercialmetals.com", "commercial-metals.com"],
        )

    def test_multi_word_name_also_offers_hyphenated_candidate(self):
        self.assertEqual(
            domain_candidates("Barnes & Noble Education, Inc."),
            ["barnesnobleeducation.com", "barnes-noble-education.com"],
        )

    def test_single_word_name_has_no_hyphenated_variant(self):
        self.assertEqual(domain_candidates("Cytokinetics Inc"), ["cytokinetics.com"])

    def test_name_that_is_entirely_suffix_words_yields_nothing(self):
        self.assertEqual(domain_candidates("Inc Corp"), [])

    def test_empty_name_yields_nothing(self):
        self.assertEqual(domain_candidates(""), [])
        self.assertEqual(domain_candidates(None), [])


class WebsiteCandidateCheckerTests(unittest.TestCase):
    def _mock_response(self, *, status=200, content_type="text/html", body=b"", final_url=""):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.status = status
        response.headers = {"Content-Type": content_type}
        response.read.return_value = body
        response.geturl.return_value = final_url
        return response

    def test_confirms_live_html_page_mentioning_company(self):
        checker = WebsiteCandidateChecker("PopDay/0.1 test")
        response = self._mock_response(
            body=(
                b"<html><head><title>Commercial Metals</title></head><body>"
                b"<nav>Home About Products Investors Careers Contact</nav>"
                b"<h1>Welcome to Commercial Metals</h1>"
                b"<p>Commercial Metals Company is a global leader in "
                b"steel and metal recycling, manufacturing, and fabrication.</p>"
                b"<footer>Copyright Commercial Metals Company. All rights reserved.</footer>"
                b"</body></html>"
            ),
            final_url="https://www.commercialmetals.com/",
        )
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = checker.verify("commercialmetals.com", "Commercial Metals Co")
        self.assertEqual(result, "https://www.commercialmetals.com/")

    def test_rejects_thin_client_side_redirect_gate(self):
        # Regression test for a real production incident: "Kyivstar Group
        # Ltd." resolved to kyivstar.com, a domain-squatted page whose only
        # "content" is a <script>window.location.replace(...)</script>
        # redirect gate that ultimately leads to an unrelated scam survey
        # site. The redirect URL happened to echo the domain's own hostname
        # ("kyivstar.com"), which passed the old raw-HTML content-match
        # check even though a human visiting the page sees nothing real.
        # Fixed by requiring a minimum amount of *visible* (script/style
        # stripped) text before any content match is even attempted.
        checker = WebsiteCandidateChecker("PopDay/0.1 test")
        response = self._mock_response(
            body=(
                b"<html><head><title>Loading...</title></head><body>"
                b"<script>window.location.replace("
                b"'https://kyivstar.com/?ch=1&js=eyJhbGciOiJIUzI1NiJ9');</script>"
                b"</body></html>"
            ),
            final_url="https://kyivstar.com/",
        )
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = checker.verify("kyivstar.com", "Kyivstar Group Ltd.")
        self.assertEqual(result, "")

    def test_rejects_parked_domain_page(self):
        checker = WebsiteCandidateChecker("PopDay/0.1 test")
        response = self._mock_response(
            body=b"This domain is for sale. Buy it now!",
            final_url="https://parkedsite.com/",
        )
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = checker.verify("parkedsite.com", "Some Company")
        self.assertEqual(result, "")

    def test_rejects_match_when_anchor_word_is_too_short_to_trust(self):
        # Regression test for a real false positive found during testing:
        # "SES S.a." generated the candidate "sessa.com" (an accidental
        # concatenation of "ses" + "sa"), which was live, not parked, and
        # would have been accepted under liveness-only verification even
        # though it isn't SES S.A.'s actual site. Short/abbreviated names
        # must never skip content verification.
        checker = WebsiteCandidateChecker("PopDay/0.1 test")
        response = self._mock_response(
            body=b"<html>Welcome to Sessa Marine, builders of fine boats</html>",
            final_url="https://sessa.com/",
        )
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = checker.verify("sessa.com", "SES S.a.")
        self.assertEqual(result, "")

    def test_rejects_page_that_never_mentions_the_company(self):
        checker = WebsiteCandidateChecker("PopDay/0.1 test")
        response = self._mock_response(
            body=b"<html>Completely unrelated content about weather</html>",
            final_url="https://unrelated.com/",
        )
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = checker.verify("unrelated.com", "Commercial Metals Co")
        self.assertEqual(result, "")

    def test_rejects_non_html_response(self):
        checker = WebsiteCandidateChecker("PopDay/0.1 test")
        response = self._mock_response(content_type="application/json", body=b"{}")
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = checker.verify("example.com", "Example Co")
        self.assertEqual(result, "")

    def test_network_failure_returns_empty_not_raise(self):
        import urllib.error

        checker = WebsiteCandidateChecker("PopDay/0.1 test")
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no dns")):
            result = checker.verify("doesnotexist.example", "Example Co")
        self.assertEqual(result, "")


class ResolveWebsiteHeuristicTests(unittest.TestCase):
    def test_returns_first_confident_candidate(self):
        with mock.patch(
            "popday.website_resolver.WebsiteCandidateChecker.verify",
            return_value="https://www.commercialmetals.com/",
        ) as verify:
            result = resolve_website_heuristic("Commercial Metals Co", "PopDay/0.1 test")
        self.assertEqual(result, "https://www.commercialmetals.com/")
        verify.assert_called_once_with("commercialmetals.com", "Commercial Metals Co")

    def test_no_candidates_verify_returns_empty(self):
        with mock.patch(
            "popday.website_resolver.WebsiteCandidateChecker.verify", return_value=""
        ):
            result = resolve_website_heuristic("Barnes & Noble Education, Inc.", "PopDay/0.1 test")
        self.assertEqual(result, "")

    def test_name_with_no_candidates_never_calls_checker(self):
        with mock.patch(
            "popday.website_resolver.WebsiteCandidateChecker.verify"
        ) as verify:
            result = resolve_website_heuristic("Inc Corp", "PopDay/0.1 test")
        self.assertEqual(result, "")
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
