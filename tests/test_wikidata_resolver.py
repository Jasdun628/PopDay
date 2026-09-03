"""Tests for popday/wikidata_resolver.py - Wikidata-backed company website
auto-resolve (replaces the retired heuristic domain-guesser)."""

from __future__ import annotations

import unittest
from unittest import mock

from popday.wikidata_resolver import (
    _clean_search_name,
    resolve_website_wikidata,
    select_official_website,
)

BUSINESS_CLAIM = {"mainsnak": {"datavalue": {"value": {"id": "Q4830453"}}}}  # instance of: business
HUMAN_CLAIM = {"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}  # instance of: human


def _website_claims(url: str, *, business: bool = True) -> dict:
    claims = {"P856": [{"mainsnak": {"datavalue": {"value": url}}}]}
    if business:
        claims["P31"] = [BUSINESS_CLAIM]
    return claims


class CleanSearchNameTests(unittest.TestCase):
    def test_strips_trailing_legal_suffix(self):
        self.assertEqual(_clean_search_name("Cytokinetics Inc"), "Cytokinetics")

    def test_strips_comma_and_period_suffix(self):
        self.assertEqual(_clean_search_name("YETI Holdings, Inc."), "YETI Holdings")

    def test_keeps_brand_carrying_holdings_word(self):
        # "Holdings" is deliberately not in the suffix set - stripping it
        # here would turn a specific company into an ambiguous generic term.
        self.assertEqual(_clean_search_name("YETI Holdings"), "YETI Holdings")

    def test_keeps_and_co_phrase_intact(self):
        # Regression test: over-stripping "Merck & Co., Inc." all the way
        # down to bare "Merck" resolved to the wrong company on Wikidata
        # (the unrelated German Merck KGaA/Merck Group) during testing.
        self.assertEqual(_clean_search_name("Merck & Co., Inc."), "Merck & Co")

    def test_strips_state_of_incorporation_tag(self):
        self.assertEqual(_clean_search_name("Pitney Bowes Inc /DE/"), "Pitney Bowes")

    def test_all_suffix_words_falls_back_to_original(self):
        self.assertEqual(_clean_search_name("Inc Corp"), "Inc Corp")

    def test_empty_name(self):
        self.assertEqual(_clean_search_name(""), "")


class SelectOfficialWebsiteTests(unittest.TestCase):
    def test_returns_p856_value_for_a_business_entity(self):
        self.assertEqual(
            select_official_website(_website_claims("https://example.com/")),
            "https://example.com/",
        )

    def test_no_p31_claim_at_all_rejected(self):
        # Regression test for a real production incident: without this
        # gate, a short/ambiguous company name search can return an
        # unrelated but "notable" entity's website - e.g. "Brunswick"
        # (the company) resolving to a Georgia city's .gov site, "Harrow"
        # to a UK school, "Deluxe" to an unrelated person's page. Every
        # confirmed-correct company match in testing carried a business P31
        # claim; none of the false positives did.
        claims = {"P856": [{"mainsnak": {"datavalue": {"value": "https://example.com/"}}}]}
        self.assertEqual(select_official_website(claims), "")

    def test_human_instance_of_rejected(self):
        claims = {
            "P856": [{"mainsnak": {"datavalue": {"value": "https://example.com/"}}}],
            "P31": [HUMAN_CLAIM],
        }
        self.assertEqual(select_official_website(claims), "")

    def test_no_p856_claim_returns_empty(self):
        self.assertEqual(select_official_website({"P31": [BUSINESS_CLAIM]}), "")

    def test_junk_taxonomy_url_rejected_even_for_a_business(self):
        claims = {
            "P856": [{"mainsnak": {"datavalue": {"value": "http://www.xbrl.org/2003/role/foo"}}}],
            "P31": [BUSINESS_CLAIM],
        }
        self.assertEqual(select_official_website(claims), "")

    def test_malformed_claim_shape_returns_empty(self):
        self.assertEqual(
            select_official_website({"P856": [{"mainsnak": {}}], "P31": [BUSINESS_CLAIM]}), ""
        )


class ResolveWebsiteWikidataTests(unittest.TestCase):
    def test_returns_website_of_top_candidate(self):
        with mock.patch(
            "popday.wikidata_resolver.search_candidate_qids", return_value=["Q1"]
        ), mock.patch(
            "popday.wikidata_resolver.fetch_entity_claims",
            return_value=_website_claims("https://example.com/"),
        ):
            result = resolve_website_wikidata("Example Co", "PopDay/0.1 test")
        self.assertEqual(result, "https://example.com/")

    def test_no_search_results_returns_empty(self):
        with mock.patch("popday.wikidata_resolver.search_candidate_qids", return_value=[]):
            result = resolve_website_wikidata("Nonexistent Co", "PopDay/0.1 test")
        self.assertEqual(result, "")

    def test_search_failure_returns_empty_not_raise(self):
        with mock.patch(
            "popday.wikidata_resolver.search_candidate_qids", side_effect=RuntimeError("boom")
        ):
            result = resolve_website_wikidata("Example Co", "PopDay/0.1 test")
        self.assertEqual(result, "")

    def test_skips_website_less_top_candidate_for_next_ranked_one(self):
        # Regression test: "Ferrari N.V." originally only checked the top
        # search hit, which had no P856, even though the second-ranked
        # candidate (the actual company) did.
        claims_by_qid = {
            "Q1": {},  # top hit, no website
            "Q2": _website_claims("https://ferrari.com/"),
        }
        with mock.patch(
            "popday.wikidata_resolver.search_candidate_qids", return_value=["Q1", "Q2"]
        ), mock.patch(
            "popday.wikidata_resolver.fetch_entity_claims",
            side_effect=lambda qid, ua: claims_by_qid[qid],
        ):
            result = resolve_website_wikidata("Ferrari N.V.", "PopDay/0.1 test")
        self.assertEqual(result, "https://ferrari.com/")

    def test_skips_top_candidate_that_is_not_a_business(self):
        # Regression test for the real "Brunswick Corp" -> Georgia city
        # incident: the top search hit is a same-named non-business entity
        # with its own website; it must be rejected in favor of the next
        # candidate that's an actual business, and if none is, "" - never
        # the non-business entity's site.
        claims_by_qid = {
            "Q1": _website_claims("http://www.brunswickga.org/", business=False),
            "Q2": _website_claims("https://www.brunswick.com/"),
        }
        with mock.patch(
            "popday.wikidata_resolver.search_candidate_qids", return_value=["Q1", "Q2"]
        ), mock.patch(
            "popday.wikidata_resolver.fetch_entity_claims",
            side_effect=lambda qid, ua: claims_by_qid[qid],
        ):
            result = resolve_website_wikidata("Brunswick", "PopDay/0.1 test")
        self.assertEqual(result, "https://www.brunswick.com/")

    def test_no_business_candidate_anywhere_returns_empty(self):
        claims_by_qid = {
            "Q1": _website_claims("http://www.brunswickga.org/", business=False),
            "Q2": _website_claims("http://www.brunswickme.org/", business=False),
        }
        with mock.patch(
            "popday.wikidata_resolver.search_candidate_qids", return_value=["Q1", "Q2"]
        ), mock.patch(
            "popday.wikidata_resolver.fetch_entity_claims",
            side_effect=lambda qid, ua: claims_by_qid[qid],
        ):
            result = resolve_website_wikidata("Brunswick", "PopDay/0.1 test")
        self.assertEqual(result, "")

    def test_ticker_match_is_preferred_over_top_ranked_candidate(self):
        claims_by_qid = {
            "Q1": _website_claims("https://wrong-merck.example/"),
            "Q2": {
                "P249": [{"mainsnak": {"datavalue": {"value": "MRK"}}}],
                **_website_claims("https://www.merck.com/"),
            },
        }
        with mock.patch(
            "popday.wikidata_resolver.search_candidate_qids", return_value=["Q1", "Q2"]
        ), mock.patch(
            "popday.wikidata_resolver.fetch_entity_claims",
            side_effect=lambda qid, ua: claims_by_qid[qid],
        ):
            result = resolve_website_wikidata("Merck", "PopDay/0.1 test", ticker="MRK")
        self.assertEqual(result, "https://www.merck.com/")

    def test_falls_back_to_raw_name_when_cleaned_name_finds_nothing(self):
        # "Weird Co" cleans to "Weird" (a plausible-but-wrong search term
        # here); the raw, uncleaned name is tried next and succeeds.
        calls = []

        def fake_search(query, ua, **kwargs):
            calls.append(query)
            return ["Q1"] if query == "Weird Co" else []

        with mock.patch(
            "popday.wikidata_resolver.search_candidate_qids", side_effect=fake_search
        ), mock.patch(
            "popday.wikidata_resolver.fetch_entity_claims",
            return_value=_website_claims("https://example.com/"),
        ):
            result = resolve_website_wikidata("Weird Co", "PopDay/0.1 test")
        self.assertEqual(result, "https://example.com/")
        self.assertEqual(calls, ["Weird", "Weird Co"])


if __name__ == "__main__":
    unittest.main()
