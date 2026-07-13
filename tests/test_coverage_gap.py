from datetime import date

from popday.coverage_gap import missed_business_days


def _d(day):  # July 2026 helper
    return date(2026, 7, day)


def test_gap_in_middle_returned_oldest_first():
    # Ok on Mon 6 and Thu 9; Tue 7 + Wed 8 missed; run scans Fri 10.
    covered = {_d(6), _d(9)}
    out = missed_business_days(
        covered, main_run_date=_d(10), earliest_ok=_d(6)
    )
    assert out == [_d(7), _d(8)]


def test_weekends_never_flagged():
    # Ok Thu 2; Fri 3 missed; 4-5 Jul is a weekend; run scans Mon 6.
    covered = {_d(2)}
    out = missed_business_days(covered, main_run_date=_d(6), earliest_ok=_d(2))
    assert out == [_d(3)]


def test_fresh_install_recovers_nothing():
    out = missed_business_days(set(), main_run_date=_d(10), earliest_ok=None)
    assert out == []


def test_explicit_floor_reaches_before_first_ok_run():
    # The June historical gap: table only has ok rows from 6 Jul, but
    # --backfill-from lets the sweep reach back to 18 Jun.
    covered = {_d(6), _d(7), _d(8), _d(9)}
    out = missed_business_days(
        covered,
        main_run_date=_d(10),
        earliest_ok=_d(6),
        explicit_floor=date(2026, 6, 18),
    )
    assert out[0] == date(2026, 6, 18)
    assert out[-1] == date(2026, 7, 3)  # last business day before the ok run on 6 Jul
    assert date(2026, 6, 20) not in out  # Saturday
    assert all(d.weekday() < 5 for d in out)


def test_cap_limits_sweep_size():
    out = missed_business_days(
        set(),
        main_run_date=_d(10),
        earliest_ok=date(2026, 1, 2),
        max_days=5,
    )
    assert len(out) == 5
    assert out == sorted(out)


def test_fully_covered_yields_empty():
    covered = {_d(6), _d(7), _d(8), _d(9)}
    out = missed_business_days(covered, main_run_date=_d(10), earliest_ok=_d(6))
    assert out == []


def test_day_before_main_run_included_when_missed():
    # Scheduler dead Thu+Fri; run comes back Mon 13 scanning Fri 10 as its
    # main date; Thu 9 must be swept, Fri 10 must NOT (main run handles it).
    covered = {_d(6), _d(7), _d(8)}
    out = missed_business_days(covered, main_run_date=_d(10), earliest_ok=_d(6))
    assert out == [_d(9)]
    assert _d(10) not in out
