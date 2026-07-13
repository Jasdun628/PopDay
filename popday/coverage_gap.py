"""Self-healing coverage: find business days PopDay missed during downtime.

A day is "covered" when a scan_runs row with status='ok' exists for it. After
any downtime (failed runs, dead scheduler, SEC block), the first healthy run
calls missed_business_days() and sweeps the gap automatically - no human step.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def no_scan_day_allowance_hours(last: datetime, now: datetime) -> int:
    """Hours of extra staleness allowance for scheduled no-scan days.

    PopDay's launchd schedule runs Tue-Sat only, so a Saturday-morning scan is
    legitimately the newest one until Tuesday. Each Sunday or Monday inside
    the gap stretches staleness thresholds by 24h; without this every health
    check went red (or alarmed) each weekend on a perfectly healthy system.
    """
    allowance = 0
    cursor = (last + timedelta(days=1)).date()
    while cursor <= now.date():
        if cursor.weekday() in (6, 0):  # Sunday, Monday
            allowance += 24
        cursor += timedelta(days=1)
    return allowance


def _previous_business_day(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def missed_business_days(
    covered_ok_dates: set[date],
    *,
    main_run_date: date,
    earliest_ok: date | None,
    explicit_floor: date | None = None,
    max_days: int = 30,
) -> list[date]:
    """Business days needing backfill, oldest first.

    Candidates are business days strictly before main_run_date (that date is
    about to be scanned by the current run anyway), walking back at most
    max_days business days, never earlier than the floor.

    Floor rules:
    - explicit_floor (the --backfill-from override) wins when given;
    - otherwise earliest_ok: never flag days from before PopDay started
      recording successful runs, so a fresh install cannot trigger a
      surprise 30-day sweep;
    - no ok runs ever and no explicit floor -> nothing to recover from.
    """
    floor = explicit_floor or earliest_ok
    if floor is None:
        return []

    missed: list[date] = []
    cursor = _previous_business_day(main_run_date)
    examined = 0
    while cursor >= floor and examined < max_days:
        if cursor not in covered_ok_dates:
            missed.append(cursor)
        examined += 1
        cursor = _previous_business_day(cursor)
    missed.reverse()
    return missed
