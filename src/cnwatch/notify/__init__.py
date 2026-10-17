"""Optional notifications (M5).

Three channels — desktop, email, webhook — chosen explicitly, with ``none`` the
documented default (``data/README.md`` section 4). The package is read-only
against the database: it composes a message from rows other milestones stored,
sends it through one injected channel, and reports what happened. A failure is
logged and exits 1; it cannot touch the daily report or the stored data.
"""

from __future__ import annotations

from .channels import (
    DesktopNotifier,
    EmailNotifier,
    NotifyError,
    Notifier,
    NullNotifier,
    SmtpSettings,
    WebhookNotifier,
    build_notifier,
    desktop_command,
)
from .config import CHANNELS, NotifyConfig, load_config, parse_config, table_rows
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
from .service import MAX_BODY_ITEMS, NotifyRequest, compose_message, log_line, notify_day

__all__ = [
    "AlertRow",
    "CHANNELS",
    "DISABLED",
    "DRY_RUN",
    "DesktopNotifier",
    "EmailNotifier",
    "FAILED",
    "MAX_BODY_ITEMS",
    "NOTHING_TO_SEND",
    "NotifyConfig",
    "NotifyError",
    "NotifyMessage",
    "NotifyOutcome",
    "NotifyRepository",
    "NotifyRequest",
    "Notifier",
    "NullNotifier",
    "SENT",
    "SmtpSettings",
    "WebhookNotifier",
    "build_notifier",
    "compose_message",
    "desktop_command",
    "load_config",
    "log_line",
    "notify_day",
    "parse_config",
    "table_rows",
]
