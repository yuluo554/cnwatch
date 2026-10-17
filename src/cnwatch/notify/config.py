"""Notification configuration, read from the single documented source.

``data/README.md`` section 4 owns the default channel (``通知默认值``). The
default is parsed rather than hard-coded so the documented policy and the
behaviour cannot drift: if the row said ``webhook``, this module would report
``webhook`` and the test pinning ``none`` would fail, instead of the code
quietly making outbound calls the documentation never sanctioned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ..errors import UsageError
from ..policy import read_table, require, table as _table

CHANNELS = ("none", "desktop", "email", "webhook")
DEFAULT_ROW = "通知默认值"


@dataclass(frozen=True)
class NotifyConfig:
    default_channel: str
    source: str

    def as_dict(self) -> Dict[str, object]:
        return {"default_channel": self.default_channel}


def _config_from_rows(rows: Dict[str, str], source: str) -> NotifyConfig:
    require(rows, [DEFAULT_ROW])
    # The policy table wraps literal values in backticks ("`none`（关闭）"); strip
    # them here so the parsed value is the channel name itself.
    raw = rows[DEFAULT_ROW].replace("`", "")
    match = re.match(r"\s*([A-Za-z]+)", raw)
    if not match:
        raise UsageError(
            "cannot read the default notification channel from %r" % raw
        )
    channel = match.group(1).lower()
    if channel not in CHANNELS:
        raise UsageError(
            "the documented default channel %r is not one of %s"
            % (channel, ", ".join(CHANNELS))
        )
    return NotifyConfig(default_channel=channel, source=source)


def parse_config(text: str, source: str = "data/README.md") -> NotifyConfig:
    return _config_from_rows(_table(text), source)


def load_config(data_dir: Optional[Path] = None) -> NotifyConfig:
    rows, source = read_table(data_dir)
    return _config_from_rows(rows, source)


def table_rows(config: NotifyConfig) -> list:
    return [
        "source: %s" % config.source,
        "default_channel: %s" % config.default_channel,
        "channels: %s" % ", ".join(CHANNELS),
    ]


__all__ = ["CHANNELS", "DEFAULT_ROW", "NotifyConfig", "load_config", "parse_config", "table_rows"]
