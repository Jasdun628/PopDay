# PopDay

PopDay tracks listed-company Investor Day, Analyst Day, and Capital Markets Day announcements and sends a plain email alert when a qualifying future event is found.

V1 is intentionally small:

- US-only
- EDGAR automated scanning, plus stored known company-news announcements
- Python
- SQLite persistence
- Email-only output
- No public website, accounts, dashboard, market commentary, or scoring

## Setup

Use Python 3.11+ on the Mac mini.

```bash
cp config.example.json config.json
```

Edit `config.json`, or use environment variables:

- `POPDAY_DB_PATH`
- `POPDAY_SEC_USER_AGENT`
- `POPDAY_REQUEST_DELAY_SECONDS`
- `POPDAY_SMTP_HOST`
- `POPDAY_SMTP_PORT`
- `POPDAY_SMTP_USERNAME`
- `POPDAY_SMTP_PASSWORD`
- `POPDAY_EMAIL_FROM`
- `POPDAY_EMAIL_TO`
- `POPDAY_UNSUBSCRIBE_BASE_URL`
- `POPDAY_UNSUBSCRIBE_SECRET`

Set the SEC User-Agent contact clearly, for example:

```text
PopDay/0.1 you@example.com
```

SEC may reject requests that use the placeholder `contact@example.com`.

## Commands

```bash
python popday.py --date today
python popday.py --date previous-business-day
python popday.py --date 2026-05-21 --dry-run
python popday.py --show-rules
python popday.py --recent-candidates
python popday.py --send-test-email
python popday.py --debug-ui
```

`--dry-run` prints detected alerts and does not send email.

If no qualifying future event is found, PopDay sends no email and prints nothing during a normal run.

`--debug-ui` starts a read-only local page at `http://127.0.0.1:8765`.
It shows rules, alert requirements, recent processed filings, and recent candidate decisions.

## Automatic Runs

The LaunchAgent template at `launchd/com.popday.alerts.plist` runs PopDay at
04:30 and 08:00 Tuesday-Saturday, using the Mac's local time. It scans the
previous SEC business day because SEC daily indexes often arrive late in the
US evening, after same-day UK polling windows have already passed. It also runs
once when loaded.

The installed background runtime lives at `/Users/jasondunne/PopDayRuntime` so
macOS privacy controls do not block a background process from reading files under
`Documents`.

Logs are written to:

- `/Users/jasondunne/PopDayRuntime/logs/popday.launchd.out.log`
- `/Users/jasondunne/PopDayRuntime/logs/popday.launchd.err.log`

## Detection Rules

PopDay scans 8-K and 6-K filings. It checks filing/document structure before falling back to full-body text:

1. Filing or exhibit headline
2. Exhibit description
3. Press release title
4. First few paragraphs
5. Full body as low-confidence backup

An alert requires:

1. New filing not previously processed
2. Form type is 8-K or 6-K
3. Qualifying investor-event phrase found
4. Appears to announce a future event
5. Future event date extracted nearby
6. Event has not already been alerted

Routine phrases like earnings calls, quarterly results calls, annual meetings, and shareholder meetings are context-sensitive and do not automatically suppress a high-signal Investor Day announcement.

Known company-news announcements can also be stored in the Investor Days list and emailed with the same plain alert format. These are labelled by source type and do not pretend to be EDGAR detections.

## Email Output

For one alert, PopDay now sends a richer plain-text and HTML email:

```text
Subject: PopDay alert: Investor Day announced

POPDAY ALERT
============

PopDay found 1 new investor-event announcement.

Company: Company X
Event:   Investor Day
Date:    1st April 2028

MAIN NUGGET
Company X will host an Investor Day on 1 April 2028...

KEY EXCERPT
The clean announcement excerpt from the press-release exhibit.

Source:
https://www.sec.gov/...
```

For several alerts, PopDay sends one email listing all of them.

Alert emails include a clickable unsubscribe link. Without a public PopDay URL,
the link is a pre-filled `mailto:` unsubscribe request. If
`unsubscribe_base_url` and `unsubscribe_secret` are configured, PopDay also
supports a direct `/unsubscribe` link that marks the recipient inactive.

## EDGAR Access

PopDay uses the SEC public data endpoints with a clear User-Agent and conservative request pacing. The default delay is `0.65` seconds, targeting about 1-2 requests per second and staying well under 10 requests per second. Fetches retry with backoff on 403, 429, and 5xx responses.

## Optional Stock Reaction Module

`popday/stock_reaction.py` is a separate optional enrichment module using `yfinance` if installed. It is not imported by the V1 scan path and stock reaction is never included in alert emails.
