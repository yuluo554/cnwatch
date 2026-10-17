"""Injectable time.

Nothing in the business layer calls ``datetime.now()`` or ``time.time()``: the
fetch window, the audit timestamps and the run records all take a ``Clock`` so a
test can pin "today" and replay a frozen day without the answer changing
overnight.

Two clocks ship:

``SystemClock``  the production implementation, reading the system clock.
``FixedClock``   a frozen instant, used by tests and by ``--as-of``.

Exchange timestamps arrive as epoch milliseconds. ``announce_date`` derives the
date in the exchange's own timezone (UTC+8): deriving it in UTC shifts 922 of
the 1097 rows of the frozen 2026-10-15 snapshot into the previous day, so the
timezone is not cosmetic.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Optional

EXCHANGE_TZ = _dt.timezone(_dt.timedelta(hours=8), "Asia/Shanghai")

DATE_FORMAT = "%Y-%m-%d"


class ClockError(ValueError):
    """A timestamp or date string the project cannot interpret."""


def parse_date(value: str) -> _dt.date:
    """Parse ``YYYY-MM-DD`` strictly; ``2026-1-1`` is rejected, not repaired."""

    if not isinstance(value, str) or len(value) != 10:
        raise ClockError("expected a YYYY-MM-DD date, got %r" % (value,))
    try:
        return _dt.datetime.strptime(value, DATE_FORMAT).date()
    except ValueError as exc:
        raise ClockError("invalid date %r: %s" % (value, exc))


def format_date(value: _dt.date) -> str:
    return value.strftime(DATE_FORMAT)


def parse_instant(value: str) -> str:
    """Validate an ISO-8601 instant and return it verbatim.

    Kept verbatim (not normalised) so an injected timestamp appears exactly as
    the caller wrote it: normalising would make two spellings of the same
    instant produce different bytes for no benefit.
    """

    text = str(value).strip()
    if not text:
        raise ClockError("expected an ISO-8601 timestamp, got %r" % (value,))
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        _dt.datetime.fromisoformat(candidate)
    except ValueError:
        raise ClockError("invalid ISO-8601 timestamp %r" % (value,))
    return text


def shift_days(value: str, days: int) -> str:
    return format_date(parse_date(value) + _dt.timedelta(days=days))


def exchange_date_from_epoch_ms(value) -> str:
    """Epoch milliseconds -> ``YYYY-MM-DD`` in the exchange timezone."""

    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        raise ClockError("announcementTime is not epoch milliseconds: %r" % (value,))
    moment = _dt.datetime.fromtimestamp(milliseconds / 1000.0, EXCHANGE_TZ)
    return moment.strftime(DATE_FORMAT)


class Clock:
    """Read-only interface the business layer depends on."""

    def now(self) -> _dt.datetime:  # pragma: no cover - interface
        raise RuntimeError("Clock.now() must be provided by a concrete clock")

    def today(self) -> _dt.date:
        return self.now().date()

    def exchange_today(self) -> _dt.date:
        """The current date in the exchange's timezone.

        ``today()`` is the UTC date, which at 02:00 in Shanghai is still the
        previous day. The fetch window must end on the exchange's day, or an
        early-morning run closes the window before the day's filings exist.
        """

        return self.now().astimezone(EXCHANGE_TZ).date()

    def timestamp(self) -> str:
        """ISO-8601 UTC timestamp with a ``Z`` suffix, second precision."""

        moment = self.now().astimezone(_dt.timezone.utc)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SystemClock(Clock):
    def now(self) -> _dt.datetime:
        return _dt.datetime.now(_dt.timezone.utc)


@dataclass(frozen=True)
class FixedClock(Clock):
    """A pinned instant. ``instant`` must carry a timezone or be read as UTC."""

    instant: _dt.datetime

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None:
            object.__setattr__(
                self, "instant", self.instant.replace(tzinfo=_dt.timezone.utc)
            )

    def now(self) -> _dt.datetime:
        return self.instant


def fixed_clock_for(day: str, hour: int = 12) -> FixedClock:
    """Deterministic noon-UTC clock for a ``YYYY-MM-DD`` date."""

    parsed = parse_date(day)
    return FixedClock(
        _dt.datetime(parsed.year, parsed.month, parsed.day, hour, 0, 0, tzinfo=_dt.timezone.utc)
    )


@dataclass(frozen=True)
class PinnedTimestamp(Clock):
    """Wraps a clock so ``timestamp()`` returns a fixed string.

    Used by the commands that record when they ran (``report``,
    ``archive``): pinning the instant is what makes a repeated run reproduce
    the same stored value, and therefore the same artifact.
    """

    base: Clock
    pinned: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "pinned", parse_instant(self.pinned))

    def now(self) -> _dt.datetime:
        return self.base.now()

    def timestamp(self) -> str:
        return self.pinned


def resolve_clock(today: Optional[str] = None, clock: Optional[Clock] = None) -> Clock:
    """``--today`` wins over an injected clock; both are explicit."""

    if today is not None:
        return fixed_clock_for(today)
    return clock if clock is not None else SystemClock()
