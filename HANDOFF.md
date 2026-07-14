# UK Market Extension — Handoff

Status as of 2026-07-14: **Phase 1 discovery adapter built and tested, in isolation. Not yet wired into the live scanner, database, or web UI.** Everything below reflects what actually exists in the repo right now, not the eventual target state.

## Why this stopped here

The brief's own architecture goal — "everything downstream market-agnostic" — means wiring this adapter in means touching `popday/cli.py`, `popday/detector.py`, and `popday/db.py`, all of which are currently EDGAR-specific (accession numbers, CIKs) and currently drive the live, working US scanner. The brief also names three explicit checkpoints (DB migration, backfill insert, first push that publishes data) before any irreversible write. Rather than build all of that in one uninterrupted pass, this stops at a clean boundary: a fully self-contained, fully tested adapter that touches nothing live. Everything past this point is real, scoped follow-on work, not a decision that needs revisiting.

## What's built

### `popday/sources/` package
- `popday/sources/__init__.py` — the common `Announcement` dataclass every adapter will eventually produce. Fields: `source`, `market`, `dedup_key`, `company_name`, `company_identifier` (CIK for US, EPIC ticker for UK), `headline`, `wire_or_form`, `announced_at` (ISO UTC), `detail_url`, `raw_text`.
- `popday/sources/investegate.py` — the UK discovery adapter (`InvestegateClient`). **Not yet built:** `popday/sources/edgar.py` — the brief calls for wrapping the existing `popday/edgar_fetch.py` behind the same `Announcement` interface. Deferred until it's clear exactly what shape the wiring into `cli.py` needs, so the wrap isn't guessed at twice.

### Investegate adapter — verified mechanics (2026-07-14)
- Index/archive pages: `https://www.investegate.co.uk/today-announcements/YYYY-MM-DD?perPage=300&page=N`. `perPage` valid values: 50/100/200/300 (confirmed from the page's own `<select>`). Each day page is scoped to exactly that calendar day (confirmed: 300/300 rows in a test fetch were all dated the same day). ~700-900 rows/day fits in `page=1..3`; `index_for_date()` walks pages until one returns fewer than `perPage` rows.
- Row structure (table `table.table-investegate`, one `<tr>` per announcement): timestamp (`%d %b %Y %I:%M %p`, Europe/London — converted to UTC), wire source (`RNS`/`PRN`/`MFN`/`BZW`/...), company name + EPIC ticker (from the `/company/{TICKER}` link), headline + detail URL (`a.announcement-link`).
- Detail URL / dedup key: `https://www.investegate.co.uk/announcement/{wire}/{company-slug}--{ticker-slug}/{headline-slug}/{numeric-id}` — the trailing numeric ID is the dedup key, stored as `Announcement.dedup_key`.
- Detail page body: the RNS wire body is a nested `<body>` tag embedded verbatim inside the outer page's own `<body>` by the distributor — `fetch_detail_text()` takes the **last** `<body>` found, not the first.
- `robots.txt` disallows `/tp001`, `/ui/login`, `/ui/register`, `/callback`, `/search-company`, and some ad/email-click-away paths — none of which this adapter touches. Checked once at `InvestegateClient` construction (`_check_robots`); raises `InvestegateRobotsDisallowedError` if that ever changes to cover a path this adapter needs.
- Rate limit: 2s minimum between requests (`delay_seconds`, floored at 2.0 regardless of the constructor argument).
- Error semantics: `InvestegateBlockedError` on 403/429-after-backoff, and separately on any 200 response whose body matches a bot-challenge signature (`checking your browser`, `cf-browser-verification`, Cloudflare's "Attention Required!" / "Just a moment..." pages) — a 200 challenge page is still a block, never treated as a silent success.

### Canary target (pinned, verified)
**Glencore plc — "Notice of Capital Markets Day"** (genuine FTSE-100 constituent):
`https://www.investegate.co.uk/announcement/rns/glencore--glen/notice-of-capital-markets-day/9184165`
Filed 21 Oct 2025 09:00 UK time; event 3 Dec 2025 1pm UK time. `probe_canary()` fetches this fixed URL and confirms the `<h1 id="main-title">` still reads "Notice of Capital Markets Day", the numeric ID and "Glencore" still appear in the page. Wired to run only when a live day comes back with zero headline matches — distinguishes "genuinely quiet day" from "the site changed / this adapter silently broke".

### Matching
`_headline_matches(headline, include_phrases, exclude_phrases)` — case-insensitive, word-boundary phrase match against `include_phrases` (so `CMD` matches "2026 CMD Update" but not "Recmd Trading Update"), substring-match veto against `exclude_phrases` checked first. `InvestegateClient.scan()` fetches the day's full index, filters to headline matches, then fetches full detail text **only** for matches (not the whole day) — respects the rate limit and Investegate's personal-use terms (metadata + our own extracted nuggets only, no bulk body storage).

### Advanced search endpoint (found, not used at runtime)
`GET /advanced-search/draw` — requires a session cookie plus the `X-XSRF-TOKEN` header (URL-decoded from the `XSRF-TOKEN` cookie) and `X-Requested-With: XMLHttpRequest`, or it silently redirects instead of erroring. Params: `key_word` (free text), `date_from`, `date_to`, `exclude_navs` (required, `true`/`false`), plus `search_for`/`search_word`/`categories`/`sources`/`sectors`/`page`. This is how the canary target above was found — useful for future one-off historical lookups, but the brief is explicit the pipeline should never depend on it, and it doesn't: `index_for_date()`/`scan()` use only the plain day-page GETs.

### Tests
`tests/test_investegate_adapter.py` (15 tests, all passing) against two real fixtures saved in `tests/fixtures/`:
- `investegate_index_2026-07-14.html` — a real day's index page (300 rows).
- `investegate_canary_glencore_9184165.html` — the real canary detail page.

Covers: row parsing correctness (spot-checked one real row's exact fields), dedup on numeric ID, headline matching (including the `CMD` word-boundary case and all three named false-positive exclusions), the canary passing against the real fixture and correctly failing when the headline or block-detection changes, and `scan()`'s contract of only fetching detail pages for actual matches.

## Requirements
Added `beautifulsoup4>=4.12` to `requirements.txt` — a genuine hard dependency for this adapter's table parsing (unlike `filing_parser.py`'s optional/graceful-fallback use of it for the US side). Installed locally on the Mac Mini for dev/test parity (PA already had `bs4` 4.12.3 installed — installing it locally too closes exactly the kind of environment gap that caused the `html_to_text` bug found and fixed in this morning's PA migration work).

## Decisions made so far (delegated, per the brief)
- **Canary target:** Glencore "Notice of Capital Markets Day" (see above) — chosen for being an unambiguous FTSE-100 name with a clean, simple announcement structure.
- **Archive day-URL pattern:** `/today-announcements/YYYY-MM-DD` (found by reading the archive calendar's link hrefs, as instructed).
- **Page-size param:** `perPage`, values 50/100/200/300 (found in the page's own `<select class="table-length-selector">`).
- **Advanced-search XHR:** got it working within the 30-minute timebox (needs the XSRF header, not just cookies) — noted above as available for future one-off lookups, but per the brief the pipeline does not depend on it.

## Not yet started (in brief order)
1. `popday/sources/edgar.py` — thin wrap of the existing `EdgarClient` behind the `Announcement` interface.
2. Shared nugget/date-extraction helper factored out of `popday/filing_parser.py` + `popday/date_extract.py` for both markets to use (currently EDGAR-only).
3. DB migration: `market` column on `detections`/`hype_tracking`/`known_announcements` (default `'US'`), new `prices` table, ticker-mapping table. **Checkpoint before running against the live DB**, per the brief.
4. Wiring `popday/cli.py`'s scan orchestration to call both adapters and write market-tagged rows.
5. Per-source self-healing backfill (gap detection keyed by `source` in `scan_runs`, not just date) and per-source coverage canary wiring into `scripts/generate_status_json.py`.
6. Phase 1b hype classifier UK mapping (non-routine-announcement counting, routine exclusion list, `--market` flag on `--reclassify`).
7. Phase 2 price capture (`yfinance`, pinned to `0.2.66` — confirmed working on PA during Phase 0's gating check; the system-installed `0.2.4` does not work, silently returns empty data). EPIC→Yahoo ticker mapping, GBp/100 conversion (confirmed real: Yahoo quotes LSE stocks in pence, e.g. Vodafone's `currency: "GBp"`, price ~97-117 meaning ~£0.97-1.17). PA scheduled task at 20:30 UTC.
8. Web UI market filter pill, currency symbols, Investegate outbound links (never render Investegate body text verbatim — link out only, per their terms).
9. Email digest grouped by market.
10. The rest of the verification checklist (migration-idempotency test, canary monkeypatch test in the `test_coverage_canary.py` style, ops-alarm regression run, one live single-day UK scan from a PA console before adding the scheduled task).

## Phase 0 gating check result (for the record)
Both Investegate and Yahoo Finance are reachable from PA — no blocker, no plan-tier decision needed. PA is confirmed on a paid tier (5000s/day CPU quota). One real finding: raw curl to `query1.finance.yahoo.com` returns 429 (a well-known public Yahoo rate-limit quirk on that specific host, not a PA network restriction) — `query2.finance.yahoo.com` works cleanly, and the `yfinance` library works once pinned to a current version (see Phase 2 note above).
