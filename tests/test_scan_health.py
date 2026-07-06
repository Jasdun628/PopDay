"""Regression tests for the July 2026 silent-scan-failure fix.

The historical bug: SEC 403 on the daily index was reported as "index not
yet available" and the scan exited 0. These tests pin the new behaviour:
403 is a hard, recorded failure; EFTS discovery works and falls back.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from popday.db import Database
from popday.edgar_fetch import EdgarBlockedError, EdgarClient, Filing


def _client() -> EdgarClient:
    return EdgarClient("PopDay tests test@example.com", delay_seconds=0.1)


def test_efts_hit_parsing():
    client = _client()
    payload = {
        "hits": {
            "hits": [
                {
                    "_id": "0001234567-26-000123:pressrelease.htm",
                    "_source": {
                        "ciks": ["0000320193"],
                        "display_names": ["Apple Inc.  (CIK 0000320193)"],
                        "root_forms": ["8-K"],
                        "file_date": "2026-07-02",
                    },
                }
            ]
        }
    }
    client.get_json = lambda url: payload  # type: ignore[method-assign]
    filings = client.search_filings_for_phrases(date(2026, 7, 2), ["investor day"])
    assert len(filings) == 1
    filing = filings[0]
    assert filing.form_type == "8-K"
    assert filing.cik == "0000320193"
    assert filing.company_name == "Apple Inc."
    assert filing.matched_phrase == "investor day"
    assert filing.accession_number == "0001234567-26-000123"
    assert filing.filing_url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")


def test_efts_dedupes_across_phrases():
    client = _client()
    hit = {
        "_id": "0009999999-26-000001:doc.htm",
        "_source": {
            "ciks": ["1318605"],
            "display_names": ["Tesla, Inc.  (CIK 0001318605)"],
            "root_forms": ["8-K"],
            "file_date": "2026-07-02",
        },
    }
    client.get_json = lambda url: {"hits": {"hits": [hit]}}  # type: ignore[method-assign]
    filings = client.search_filings_for_phrases(
        date(2026, 7, 2), ["investor day", "analyst day"]
    )
    assert len(filings) == 1


def test_blocked_error_raised_after_retries(monkeypatch):
    import urllib.error

    client = _client()
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def _always_403(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _always_403)
    with pytest.raises(EdgarBlockedError):
        client.get_text("https://www.sec.gov/Archives/edgar/daily-index/x.idx")
    assert client.stats.http_403 >= 1


def test_scan_run_recording(tmp_path):
    db = Database(str(tmp_path / "popday.sqlite3"))
    run_id = db.start_scan_run(date(2026, 7, 3))
    db.finish_scan_run(
        run_id,
        status="failed",
        filings_seen=0,
        filings_parsed=0,
        alerts_sent=0,
        source="",
        error="SEC EDGAR is blocking this machine (HTTP 403)",
    )
    health = db.latest_scan_health()
    assert health["last_run_status"] == "failed"
    assert health["last_success_utc"] is None
    assert "403" in health["last_run_error"]

    run_id = db.start_scan_run(date(2026, 7, 4))
    db.finish_scan_run(
        run_id,
        status="ok",
        filings_seen=12,
        filings_parsed=12,
        alerts_sent=1,
        source="efts",
        error="",
    )
    health = db.latest_scan_health()
    assert health["last_run_status"] == "ok"
    assert health["last_success_utc"] is not None
    assert health["last_run_source"] == "efts"
    db.close()
