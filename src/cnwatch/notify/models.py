"""Models for the notification layer.

A notification is derived, never stored: it is composed from rows the fetch and
classify steps already wrote, sent through one channel, and then forgotten
except for one line in ``out/logs/``. That is what makes FR-43 checkable — a
failed send has nowhere to damage the report or the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

# Why the round ended the way it did. Kept as explicit values so a test can
# assert "nothing was sent *because the channel is off*", not merely "not sent".
DISABLED = "disabled"
NOTHING_TO_SEND = "nothing_to_send"
DRY_RUN = "dry_run"
SENT = "sent"
FAILED = "failed"


@dataclass(frozen=True)
class NotifyMessage:
    channel: str
    title: str
    body: str
    at: str

    @property
    def text(self) -> str:
        return "%s\n%s" % (self.title, self.body)


@dataclass
class NotifyOutcome:
    channel: str
    enabled: bool
    day: str
    items: int
    reason: str
    delivered: bool = False
    detail: str = ""
    message: NotifyMessage = None  # type: ignore[assignment]

    @property
    def failed(self) -> bool:
        return self.reason == FAILED

    def render(self, show_message: bool = False) -> str:
        lines = [
            "notify  channel=%s  day=%s"
            % (self.channel, self.day if self.day else "-")
        ]
        if not self.enabled:
            lines.append("disabled: the channel is none; no external call was made")
            return "\n".join(lines)
        lines.append("items %d" % self.items)
        if self.reason == NOTHING_TO_SEND:
            lines.append("nothing to send: no stored announcement for that day")
        elif self.reason == DRY_RUN:
            lines.append("dry run: composed but not sent")
        elif self.reason == SENT:
            lines.append("sent via %s" % self.channel)
        elif self.reason == FAILED:
            lines.append("failed: %s" % self.detail)
            lines.append("the failure was written to out/logs/; the report and the "
                         "database are untouched")
        if show_message and self.message is not None:
            lines.append("")
            lines.append(self.message.text)
        return "\n".join(lines)


__all__ = [
    "DISABLED",
    "DRY_RUN",
    "FAILED",
    "NOTHING_TO_SEND",
    "SENT",
    "NotifyMessage",
    "NotifyOutcome",
]
