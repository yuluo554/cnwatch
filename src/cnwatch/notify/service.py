"""The notify round: compose one message from stored rows, send it, log a failure.

Order of operations is the contract:

1. a channel that is not enabled returns immediately — the database is not even
   read, so "default off = zero external calls" holds by construction;
2. the day's stored (and classified) watchlist items are read;
3. nothing to say → nothing is sent, and the round still succeeds;
4. otherwise compose one message, optionally print it (``--dry-run``), send it.

A failed send raises ``NotifyError``, the caller logs one line and exits 1. The
report and the database are untouched because this service never writes to
them: the message is derived, not stored.

The body carries the same disclaimer as every other user-facing surface, taken
from the single definition in ``cnwatch.disclaimer`` rather than re-typed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..clock import Clock
from ..disclaimer import DISCLAIMER
from .channels import Notifier, NotifyError
from .models import (
    DISABLED,
    DRY_RUN,
    FAILED,
    NOTHING_TO_SEND,
    SENT,
    NotifyMessage,
    NotifyOutcome,
)
from .repository import AlertRow, NotifyRepository

# How many items a message lists before it summarises the rest. A display cap,
# not a policy number: the counters above the list are always complete.
MAX_BODY_ITEMS = 10

WATCHLIST = "watchlist"


@dataclass
class NotifyRequest:
    repository: NotifyRepository
    notifier: Notifier
    clock: Clock
    day: str
    channel: str
    dry_run: bool = False


def compose_message(
    day: str, rows: List[AlertRow], channel: str, at: str
) -> Optional[NotifyMessage]:
    """``None`` when there is nothing worth sending."""

    if not rows:
        return None

    counts = {"high": 0, "mid": 0, "low": 0}
    for row in rows:
        counts[row.risk_level] = counts.get(row.risk_level, 0) + 1

    title = "cnwatch %s 公告提醒：high %d / mid %d / low %d" % (
        day,
        counts.get("high", 0),
        counts.get("mid", 0),
        counts.get("low", 0),
    )

    lines: List[str] = []
    for row in rows[:MAX_BODY_ITEMS]:
        lines.append(
            "%s %s  [%s]  %s" % (row.code, row.name, row.risk_level, row.title)
        )
    hidden = len(rows) - len(lines)
    if hidden > 0:
        lines.append("… 其余 %d 条见日报" % hidden)
    lines.append(DISCLAIMER)

    return NotifyMessage(channel=channel, title=title, body="\n".join(lines), at=at)


def notify_day(request: NotifyRequest) -> NotifyOutcome:
    if not request.notifier.enabled:
        return NotifyOutcome(
            channel=request.channel,
            enabled=False,
            day=request.day,
            items=0,
            reason=DISABLED,
        )

    rows = request.repository.rows_for_day(request.day, WATCHLIST)
    at = request.clock.timestamp()
    message = compose_message(request.day, rows, request.channel, at)
    if message is None:
        return NotifyOutcome(
            channel=request.channel,
            enabled=True,
            day=request.day,
            items=0,
            reason=NOTHING_TO_SEND,
        )

    if request.dry_run:
        return NotifyOutcome(
            channel=request.channel,
            enabled=True,
            day=request.day,
            items=len(rows),
            reason=DRY_RUN,
            message=message,
        )

    try:
        request.notifier.send(message)
    except NotifyError as exc:
        return NotifyOutcome(
            channel=request.channel,
            enabled=True,
            day=request.day,
            items=len(rows),
            reason=FAILED,
            detail=str(exc),
            message=message,
        )

    return NotifyOutcome(
        channel=request.channel,
        enabled=True,
        day=request.day,
        items=len(rows),
        reason=SENT,
        delivered=True,
        message=message,
    )


def log_line(outcome: NotifyOutcome) -> str:
    """One line for ``out/logs/notify.log`` — failures never touch the console alone."""

    detail = outcome.detail or outcome.reason
    return "channel=%s day=%s items=%d result=%s %s" % (
        outcome.channel,
        outcome.day or "-",
        outcome.items,
        outcome.reason,
        detail,
    )


__all__ = [
    "MAX_BODY_ITEMS",
    "NotifyRequest",
    "compose_message",
    "log_line",
    "notify_day",
]
