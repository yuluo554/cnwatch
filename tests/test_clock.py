"""Clock and date discipline.

Two facts drive these tests, both measured: the upstream timestamp is epoch
milliseconds, and its date is the *exchange* date (UTC+8). The frozen
2026-10-15 snapshot contains 922 rows whose UTC date is 2026-10-14, so a
UTC-derived date would silently mis-file a large share of one day.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cnwatch.clock import (
    ClockError,
    FixedClock,
    SystemClock,
    exchange_date_from_epoch_ms,
    fixed_clock_for,
    format_date,
    parse_date,
    resolve_clock,
    shift_days,
)


def test_parse_date_is_strict():
    assert parse_date("2026-10-16") == dt.date(2026, 10, 16)
    for bad in ("2026-1-6", "20261016", "2026-13-01", "", None):
        with pytest.raises(ClockError):
            parse_date(bad)  # type: ignore[arg-type]


def test_shift_days_crosses_month_and_year_boundaries():
    assert shift_days("2026-11-01", -1) == "2026-10-31"
    assert shift_days("2026-12-31", 1) == "2027-01-01"
    assert shift_days("2028-03-01", -1) == "2028-02-29"


def test_fixed_clock_is_stable_and_iso_utc():
    clock = fixed_clock_for("2026-10-16")
    assert clock.today() == dt.date(2026, 10, 16)
    assert clock.timestamp() == "2026-10-16T12:00:00Z"
    assert clock.timestamp() == clock.timestamp()


def test_fixed_clock_accepts_a_naive_instant_as_utc():
    clock = FixedClock(dt.datetime(2026, 10, 16, 8, 30))
    assert clock.timestamp() == "2026-10-16T08:30:00Z"


def test_exchange_today_differs_from_utc_today_after_midnight_in_shanghai():
    """At 02:00 Beijing (18:00 UTC) the exchange is already on the next day."""

    clock = FixedClock(dt.datetime(2026, 10, 15, 18, 0, tzinfo=dt.timezone.utc))
    assert clock.today() == dt.date(2026, 10, 15)
    assert clock.exchange_today() == dt.date(2026, 10, 16)


def test_exchange_today_matches_utc_during_the_overlap():
    clock = FixedClock(dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.timezone.utc))
    assert clock.exchange_today() == clock.today() == dt.date(2026, 10, 16)


def test_system_clock_is_timezone_aware():
    moment = SystemClock().now()
    assert moment.tzinfo is not None


def test_resolve_clock_prefers_the_explicit_date():
    pinned = fixed_clock_for("2020-01-02")
    assert resolve_clock("2026-10-16", pinned).today() == dt.date(2026, 10, 16)
    assert resolve_clock(None, pinned) is pinned


def test_exchange_date_uses_utc_plus_eight():
    # 2026-10-15 20:44:29 in Shanghai is 2026-10-15 12:44:29 UTC.
    assert exchange_date_from_epoch_ms(1792068269000) == "2026-10-15"
    # 2026-10-16 07:00 Shanghai is 2026-10-15 23:00 UTC: the UTC date is wrong.
    midnight_ish = int(dt.datetime(2026, 10, 15, 23, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert exchange_date_from_epoch_ms(midnight_ish) == "2026-10-16"


def test_exchange_date_rejects_non_numeric_values():
    for bad in (None, "soon", ""):
        with pytest.raises(ClockError):
            exchange_date_from_epoch_ms(bad)


def test_format_date_round_trips():
    assert format_date(parse_date("2026-02-28")) == "2026-02-28"
