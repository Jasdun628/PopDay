# Claude Code Handover - PopDay - 2026-07-01

This handover is for Claude Code taking over PopDay work from Codex.

## Names And Locations

- Device: Mac Mini.
- Mac Mini PopDay repo: `/Users/jasondunne/Documents/PopDay`.
- GitHub PopDay repo: `git@github.com:Jasdun628/PopDay.git`.
- PythonAnywhere live site: `https://jasdun.pythonanywhere.com/`.
- PythonAnywhere app directory: `/home/Jasdun/popday`.
- Mac Mini runtime DB: `/Users/jasondunne/PopDayRuntime/popday.sqlite3`.

Use these names exactly. Do not call the Mac Mini "the Mac" or "desktop". Do not say only "the repo"; say Mac Mini PopDay repo, GitHub PopDay repo, or PythonAnywhere.

## Current State

Latest Mac Mini PopDay repo commit:

```bash
0ff3e7b Split Research Hype by event timing
```

Recent commits:

```bash
0ff3e7b Split Research Hype by event timing
c25ae74 Add cached Price Reaction tab
8977201 Update Help manual for current PopDay workflow
```

Current `git status -sb` in the Mac Mini PopDay repo shows `main...github/main` plus old unrelated untracked files:

```text
?? mac-mini-daily-active-project-summary-2026-06-15.md
?? mac-mini-daily-active-project-summary-2026-06-16.md
?? mac-mini-daily-active-project-summary-2026-06-17.md
?? mac-mini-daily-active-project-summary-2026-06-18.md
?? mac-mini-daily-active-project-summary-2026-06-19.md
?? mac-mini-daily-active-project-summary-2026-06-22.md
?? next-day-handovers/
```

Leave those untracked files alone unless Jason specifically asks.

## Workflow Jason Expects

Mac Mini PopDay repo is the source of truth.

For user-facing PopDay changes, "done" means:

1. Change the Mac Mini PopDay repo.
2. Run local checks.
3. Deploy to PythonAnywhere.
4. Verify the live public site and buttons.
5. Commit and push to the GitHub PopDay repo.

Do not silently stop at local changes when the request affects the public UI.

Update the public Help tab once per day during active PopDay work. Delete stale
or unused Help copy, add any new user-visible features, and keep tab titles
exactly current. Treat stale Help text as a product bug.

Use the stable operator command where possible:

```bash
cd /Users/jasondunne/Documents/PopDay
python3 scripts/popday_ops.py runtime-summary
python3 scripts/popday_ops.py verify-live
python3 scripts/popday_ops.py deploy
python3 scripts/popday_ops.py backfill-acceptance
python3 scripts/popday_ops.py refresh-price-reaction
python3 scripts/popday_ops.py check-pythonanywhere-db
```

Avoid ad hoc `python3 -c` probes unless there is a real debugging reason.

## Standard Checks

Run before deploy:

```bash
cd /Users/jasondunne/Documents/PopDay
python3 -m py_compile popday.py flask_app.py popday/*.py scripts/*.py
python3 -m unittest discover -s tests
```

Deploy and live verify:

```bash
python3 scripts/popday_ops.py deploy
```

The deploy script backs up the Mac Mini runtime DB, syncs runtime code/status, copies to PythonAnywhere, reloads PythonAnywhere, then runs:

```bash
python3 scripts/verify_live_popday.py
python3 scripts/verify_live_popday_buttons.py
```

PythonAnywhere can lag during reload. The deploy verifier may fail once, then pass on retry. If final retry passes, that is acceptable.

## Recent Work Completed

### Price Reaction Tab

Live tab:

```text
https://jasdun.pythonanywhere.com/?tab=price_reaction
```

Implemented cached daily market data for qualifying Investor Day announcements.

Shows:

- Company
- Ticker
- Event Date
- Announced / Filed
- Accepted
- Reaction Day
- Previous Close
- Reaction Close
- Move
- Intraday Range
- Latest Close
- Interval Return
- Daily Volatility
- Status/source

Important: this is cached daily market data, not live tick data.

Current live cache has 6 rows, all `Ok`.

Barnes & Noble Education is especially important:

- Accepted: `Wednesday 24th June 2026 16:24 ET`
- Reaction day: `25th June 2026`
- Reason: acceptance was after 4pm ET, so use next trading day.

Data path:

- SEC CIK/ticker mapping: `https://www.sec.gov/files/company_tickers.json`
- Daily price data: tries Stooq CSV first, then Yahoo chart daily JSON fallback.
- In practice current rows use `yahoo_chart_daily_json`.

Key files:

- `popday/stock_reaction.py`
- `scripts/refresh_price_reaction.py`
- `scripts/popday_ops.py`
- `popday/db.py`
- `templates/admin.html`
- `flask_app.py`
- `tests/test_stock_reaction.py`

Backup manifests now include:

```text
price_reactions: 6
```

### Research / Hype Split

Live tab:

```text
https://jasdun.pythonanywhere.com/?tab=research
```

Research / Hype is now split into:

- Upcoming
- Legacy

Terminology matters to Jason. Use exactly "Upcoming" and "Legacy".

Rule:

- Upcoming means the Investor Day has not passed.
- Legacy means the Investor Day has passed.

Live check after deployment showed:

- 2 Upcoming rows
- 4 Legacy rows

Key files:

- `flask_app.py`
- `templates/admin.html`
- `scripts/verify_live_popday.py`
- `scripts/verify_live_popday_buttons.py`
- `tests/test_public_candidates_route.py`

### Investor Days Split

Investor Days was already split into Upcoming and Legacy using the same basic event-date rule.

## UI Principles Jason Has Asked For

- The public PythonAnywhere site is the product surface.
- Button clicks matter. Verify actual tabs/buttons on the live page.
- Company names in Investor Days should be linked where company website URLs are known.
- Use "Upcoming" and "Legacy" where the distinction is relevant.
- Explain in simple plain English.
- Be explicit about Mac Mini PopDay repo vs PythonAnywhere vs GitHub PopDay repo.

## Price Reaction Caveats

Do not call the current data "live prices".

Use wording like:

- cached daily data
- latest available daily close
- daily market data

The current volatility is a transparent calculation from daily close-to-close returns over the interval, not a third-party mystery score. This is intentional because Jason wants PopDay to move toward red/green target-firm classification using auditable measures.

Likely next Price Reaction improvements:

1. Add clearer column explanations or hover/help copy if Jason asks.
2. Add automatic scheduled refresh for price reactions.
3. Add export/download later if needed.
4. Decide the red/green classification rules only after the data looks sane.

## Good Next Step

If Jason asks what to do next, recommend:

1. Inspect the live Price Reaction rows for sanity.
2. Decide whether daily volatility and interval return should feed a first draft red/green classification.
3. Add a simple non-final classification column only after Jason agrees the metric definitions are right.

## Do Not Do Without Asking

- Do not send live emails.
- Do not make destructive DB changes.
- Do not edit PythonAnywhere directly unless it is an emergency recovery.
- Do not change unrelated untracked daily-summary/next-day-handover files.
- Do not introduce paid market-data services.

## Useful Live URLs

```text
https://jasdun.pythonanywhere.com/?tab=announcements
https://jasdun.pythonanywhere.com/?tab=research
https://jasdun.pythonanywhere.com/?tab=price_reaction
https://jasdun.pythonanywhere.com/?tab=candidates
https://jasdun.pythonanywhere.com/?tab=health
https://jasdun.pythonanywhere.com/?tab=summary
https://jasdun.pythonanywhere.com/?tab=help
```
