# UK Market Extension — Handoff

Status as of 2026-07-14 (evening): **all phases built and tested on branch `feature/uk-market`. NOT merged to main, NOT deployed, live DB NOT migrated.** Three checkpoints remain, in order: (1) run the DB migration against the live PA database, (2) any historical backfill insert, (3) merge/push/deploy + enable the PA scheduled tasks (the "first push that publishes UK data"). The branch must not reach `main` before checkpoint 1 — the twice-daily auto-deploy gate ships anything on main, and `popday/db.py` auto-migrates any database it opens.

## Architecture (as built)

One source adapter per market; everything downstream market-agnostic:

- `popday/sources/__init__.py` — the common `Announcement` dataclass (source, market, dedup_key, company_name, company_identifier, headline, wire_or_form, announced_at UTC, detail_url, raw_text).
- `popday/sources/investegate.py` — UK adapter: `InvestegateClient` with `index_for_date()` (full day index, `perPage=300`, pages until short page), `scan()` (headline matches only, detail text fetched only for matches), `probe_canary()` (pinned Glencore CMD announcement), `InvestegateBlockedError` (403/429-after-backoff/Cloudflare-challenge-by-body-signature), robots.txt checked at construction (`InvestegateRobotsDisallowedError`), 2s minimum request spacing (floored).
- `popday/sources/edgar.py` — thin wrap over `popday/edgar_fetch.py` (`EdgarSource.scan()/probe_canary()`, `announcement_from_filing()`). **Honest limitation:** `cli.py`'s US path still consumes `Filing` natively (it needs the SGML envelope richness); the wrap exists for uniform orchestration and future convergence, documented in its docstring.
- `market` column (`'US'`/`'UK'`, default `'US'`) on detections, processed_filings, known_announcements, hype_tracking, price_reactions; `source` column (default `'edgar'`) on scan_runs. New tables `prices` (UNIQUE(ticker,date)) and `ticker_mappings` (manual_override column wins over derived symbol).
- Detection: `popday/detector.py detect_in_uk_announcement()` — reuses `split_sentences` + `extract_event_date` verbatim (no forked logic; they were already generic, so no refactor of the US path was needed). UK-specific: word-boundary phrase matching (so `cmd` never matches "recommend"), headline-match counts as announcement context, TBD path when the headline announces but no date parses. `Detection` gained `market` and `ticker` fields (defaults preserve US behaviour byte-for-byte).
- CLI: `popday.py --source investegate --date previous-business-day` runs the UK scan; `--source edgar` (default) is the unchanged US path. `--market US|UK` filters `--reclassify`. Per-source: coverage sweep (`covered_ok_dates`/`earliest_ok_run_date` take a source), ops-alarm keys (`scan_failure:investegate`, heartbeat `heartbeat:investegate`), canary control, self-healing backfill (UK backfill = the same scan pointed at archive day pages, which are the same URL shape as today's page).
- Hype (Phase 1b): `popday/hype.py update_uk_hype_from_index()` — runs inside each UK scan against the already-in-memory day index; counts non-routine announcements (config `uk_routine_headlines` exclusion list) from companies with open UK events, window (announcement, event]; appends to `detected_json` in the US shape, deduplicated on the numeric ID, so `qualifying_count` and `--reclassify` work identically across markets. `reclassify_hype_tracking` preserves each row's market (regression-tested).
- Prices (Phase 2, capture only): `popday/prices.py` + `scripts/capture_uk_prices.py`. EPIC→Yahoo mapping (`VOD→VOD.L`, `NG.→NG.L`, `BT.A→BT-A.L`) recorded in ticker_mappings; GBp÷100→'GBP'; never FX-converts; window [announcement−14cd, event+14cd] (≈10 trading days each side, erring generous — capture-only); failures logged non-fatal, upserts heal gaps. Suggested PA task: 20:30 UTC daily (checkpoint 3 gates creating it).
- Web UI: market pill row `All | US | UK` (`?market=`) on Investor Days, Research/Hype, Price Reaction, Scan Log; carried through all sort links. UK rows link out to Investegate detail pages (source_type CASE in the SQL; `_prepare_row`'s non-EDGAR passthrough). £/$ per row from market. **Never renders Investegate body text** — snippets are our own extracted nugget sentences only, per their personal-use terms.
- Email: one combined daily digest, grouped by market with section headers (`US - SEC EDGAR` / `UK - RNS (via Investegate)`) ONLY when a batch spans both markets; single-market emails (all existing traffic) are unchanged, regression-tested.
- Health: per-source `sources` block in status JSON (`scan_health` + `coverage_health` per source); headline health = worst across sources; flask banner evaluates per-source (UK banner prefixed "UK scan:"); heartbeat loops sources with ≥1 ok run (a never-launched source can't false-alarm). `_assess_coverage` treats `investegate-index` like `efts` (zero matches is normal; only `daily-index` treats all-zero as breakage).

## Standing requirements — how they were preserved
- **Dead-man's switch:** every UK run writes a scan_runs row (source='investegate'); blocks/robots-changes/canary failures are `failed` rows, never silent greens.
- **Ops-alarm state machine:** untouched in db.py; UK uses its own alert keys through the same `check_and_update_ops_alert` (cooldown, no spam, exactly one recovery email per source).
- **Self-healing backfill:** per-source gap detection; first-ever UK run has `earliest_ok=None` → no surprise sweep; UK re-sweeps dedup on the numeric ID.

## Also fixed in this branch (found during the build)
`status_json_path` config key: every real scan now regenerates the status JSON (subprocess, non-fatal). Without this, the front door's freshness clock only reset on deploys — after today's removal of the weekend staleness allowance, a quiet no-commit weekend would have shown a false STALE/BROKEN banner by Monday (the allowance had been accidentally masking this deploy-coupling). Set `"status_json_path": "/home/Jasdun/popday/status/popday_status.json"` in PA's config.json at rollout (part of checkpoint 3).

## Migration (checkpoint 1)
`scripts/migrate_uk_market.py --db-path /home/Jasdun/popday/popday.sqlite3` — prints the plan and row counts, takes a timestamped file backup, opens the DB (which applies the idempotent SCHEMA+_migrate), verifies columns exist and row counts unchanged. `--dry-run` prints the plan only. Safe to run twice ("nothing to do"). All changes additive.

## Rollout order (after checkpoints)
1. Checkpoint 1: run migration on live PA DB (via the script, not a side-effect deploy).
2. Merge `feature/uk-market` → main, push, deploy (checkpoint 3 approval covers this — the deploy is what publishes UK-capable code); add `status_json_path` to PA config.json.
3. Create PA scheduled task: `cd /home/Jasdun/popday && /home/Jasdun/.local/bin/python3 popday.py --config config.json --date previous-business-day --source investegate` — suggested 05:30 UTC daily (after the day page for the previous London day is complete and clear of the 04:00 US task). First runs can be `--dry-run` for a day if desired.
4. Create the price-capture task at 20:30 UTC: `cd /home/Jasdun/popday && /home/Jasdun/.local/bin/python3 scripts/capture_uk_prices.py --config config.json`. Requires `pip install --user yfinance==0.2.66` on PA (already installed 2026-07-14 during Phase 0 testing).
5. Update the Help tab per the Daily Help Manual Rule (market pills, UK rows, Investegate links) — deliberately left until the feature is actually visible.
6. Checkpoint 2 (only if a historical UK backfill is wanted): `--source investegate --backfill-from YYYY-MM-DD`, after showing counts via a dry-run.

## Config keys added (all optional, defaults in popday/config.py)
`uk_user_agent` (defaults to sec_user_agent), `uk_request_delay_seconds` (2.0 floor), `uk_include_phrases`, `uk_exclude_phrases`, `uk_routine_headlines`, `status_json_path` ("" = disabled).

## Delegated decisions (made, per the brief)
- Archive day-URL pattern: `/today-announcements/YYYY-MM-DD` (+`?perPage=300&page=N`).
- Page-size param: `perPage` (50/100/200/300).
- Canary: Glencore "Notice of Capital Markets Day", ID 9184165, filed 21 Oct 2025, event 3 Dec 2025 — `probe_canary()` checks headline in `<h1 id="main-title">`, the numeric ID, and "Glencore" in the page. The real fixture is saved and the UK detector extracts `2025-12-03` from its actual RNS text in tests.
- Advanced-search XHR: works (`GET /advanced-search/draw` with session cookie + `X-XSRF-TOKEN` header + `exclude_navs` param) — used to find the canary; runtime pipeline deliberately does not depend on it.
- Shared extraction helper shape: none needed — `split_sentences`/`extract_event_date` were already generic importable functions; the UK detector imports them directly rather than forking or rewiring the US path.
- Hype threshold: shared `hype_threshold` config key per the brief. Note: live config value is currently 2 (v1-abstract-guess era); the paper's confirmed definition is ≥1. Changing the value is a one-line config edit + `--reclassify`, left for Jason since it relabels existing US events too.
- UK scan_runs semantics: `filings_seen` = headline matches (sparse, like EFTS); `efts_total_hits` column reused for the raw day-index row count (volume signal); `discovery_source` = 'investegate-index'.

## Test coverage (36 new tests; 131 total, all green)
Adapter parsing/dedup/canary/matching against real fixtures; UK detection incl. the real Glencore text, year-prefix guard, TBD path, cmd word-boundary; migration idempotency + old-schema upgrade + market defaults; per-source coverage/heartbeat/health worst-of; UK hype counting/dedup/window/reclassify-market-preservation; ticker mapping (NG., BT.A, VOD), GBp÷100, capture windows; email grouping (US-only unchanged).
