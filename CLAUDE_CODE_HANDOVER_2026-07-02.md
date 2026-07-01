# Claude Code Handover - PopDay - morning of 2nd Jul 2026

Supersedes `CLAUDE_CODE_HANDOVER_2026-07-01.md`. Read `OPERATING_MODEL.md` first.

## Names And Locations

- Mac Mini PopDay repo: `/Users/jasondunne/Documents/PopDay` (source of truth).
- GitHub PopDay repo: `git@github.com:Jasdun628/PopDay.git` (remote is named `github`).
- PythonAnywhere live site: `https://jasdun.pythonanywhere.com/` (deploy target only; SSH `Jasdun@ssh.pythonanywhere.com`, app dir `/home/Jasdun/popday`).
- Mac Mini runtime DB: `/Users/jasondunne/PopDayRuntime/popday.sqlite3`.

## Current State (end of 1st Jul 2026)

Mac Mini repo = GitHub = PythonAnywhere, all in step. Latest commits (newest first):

```text
Add item 2.02 to hype qualifying set and a dry-run reclassify preview
Group Price Reaction columns into thematic sections (labelled band + dotted separators)
Price Reaction: ticker under company (Yahoo-linked), colour-code % moves and average
Add Investor Day Close column (green pop / red tank) to Price Reaction
Add date-TBD investor days; average interval return
```

Working tree is clean apart from the usual untracked daily-summary / `next-day-handovers/` files — **leave those alone**.

## What Shipped 1st Jul 2026

- **Repo drift fixed.** PythonAnywhere had a forked git history (a nightly `git add -A && push` job). Reset PA to GitHub, disabled the nightly push script, ignored `backups/` + `*.bak`. Recovery branch on PA: `pa-prereset-backup-20260701T105416Z`.
- **Dates abbreviated** — months (Sept) and weekdays (Weds/Thurs) across the UI + emails.
- **Price Reaction tab overhaul:** split Upcoming/Legacy; per-column help on hover of the heading (dotted underline); merged Filed/Accepted with the time on a second line; new **Investor Day Close** column (green pop / red tank, blank for upcoming); **Average interval return** footer (green/red); ticker moved under the company name, linked to Yahoo Finance; **thematic column grouping** (labelled band + dotted separators); "Yahoo (daily)" source label; removed the source/refreshed status line.
- **Research / Hype cleanup:** removed sort from non-key columns, deleted the Presentation/Deck Count column, smaller sort icons, sort arrows stacked under labels.
- **Tab order:** Price Reaction is now the leftmost tab and the default landing view.
- **Date-TBD investor days:** dateless-but-announced investor days (e.g. Dynatrace) are now flagged `alert_candidate_tbd` and shown as "Date TBD" under Upcoming instead of being dropped. Browser-only (never emailed).
- **Hype:** 8-K item 2.02 added to the qualifying set (paper definition); `--reclassify --dry-run` preview added. Still **inert until `--watch-hype` runs**; labels stay provisional.
- **Permissions allowlist** broadened in `.claude/settings.local.json` to cut prompt spam.

## Current Objective (unchanged)

Jason clicks the PopDay Safari Favourite and immediately sees the current reality of the system.

## Open / Parked (Jason's call)

1. **Dynatrace date-TBD** should surface on its own on the next scheduled scan (1 Jul filings are scanned on the 2 Jul run). No action needed; just confirm it appears.
2. **Hype 2.02 effect not yet measured.** A `--reclassify` dry-run shows 0 changes because stored `detected_json` is empty/old-rule. Real measurement needs a fresh **`--watch-hype`** EDGAR scan (live fetch + DB write) — awaiting Jason's go.
3. **Deploy hardening (recommended).** `python3 scripts/popday_ops.py deploy` keeps exiting 1 because its verify runs *before* PythonAnywhere reloads (lag). It is almost always a **false alarm** — the files do reach PA. Workaround in use: after deploy, `ssh ... touch /var/www/jasdun_pythonanywhere_com_wsgi.py` then re-check. Worth fixing so the deploy force-reloads and verifies *after* the reload.

## Jason's Working Preferences

- Treat yourself as **delegated CTO**; Jason is a non-programmer product owner. Do the heavy lifting, hide complexity, explain in plain English.
- **Do not ask permission for routine/reversible steps** (status checks, dry-runs, reads). Just do them and report. Stop only for irreversible writes, publishing data/secrets, or a genuine "this doesn't match what you asked" moment.
- **Abbreviate** months (Sept) and weekdays (Weds/Thurs).
- **Colour gains green, losses red** (value *and* label) for any move/return.
- When a column's definition changes, update **both** the Help manual and the "?" tooltips in the same deploy.

## Do Not Do Without Asking

- No live emails. No destructive DB changes. No editing PythonAnywhere directly except emergency recovery. Don't touch the untracked daily-summary / next-day-handover files. No paid market-data services.

## Useful Live URLs

```text
https://jasdun.pythonanywhere.com/?tab=price_reaction
https://jasdun.pythonanywhere.com/?tab=announcements
https://jasdun.pythonanywhere.com/?tab=research
https://jasdun.pythonanywhere.com/?tab=health
https://jasdun.pythonanywhere.com/?tab=help
```
