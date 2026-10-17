"""Archive configuration, read from the single documented source.

Same discipline as ``fetch/config.py`` (K7): the extraction page range, the
summary length and the on-disk file name come from ``data/README.md`` section
4. Nothing here restates them, so a change to the documented policy without a
matching change in code makes the two disagree and fails the test that compares
them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from ..errors import UsageError
from ..policy import (
    first_int as _first_int,
    read_table,
    require,
    table as _table,
)

# Row label -> internal key. Every one of these is policy, not taste.
_ROW_KEYS = {
    "PDF 文本抽取范围": "pages",
    "PDF 摘要长度": "excerpt_chars",
    "PDF 归档路径": "path_template",
    "归档文本抽取": "default_extract",
    "单轮全局请求间隔": "min_interval_seconds",
    "重试退避": "retry_delays",
    "请求超时": "request_timeout_seconds",
}

# ``data/pdf/<code>/<date>/<announcement_id>.PDF`` — the shape is checked, not
# just the file extension, because a template that silently moved would put the
# user's files somewhere else.
_TEMPLATE_RE = re.compile(r"data/pdf/<code>/<date>/<announcement_id>(\.[A-Za-z]+)")


@dataclass(frozen=True)
class ArchiveConfig:
    pages: int
    excerpt_chars: int
    suffix: str
    default_extract: bool
    min_interval_seconds: float
    retry_delays: tuple
    request_timeout_seconds: int
    source: str

    @property
    def max_attempts(self) -> int:
        return len(self.retry_delays) + 1

    def as_dict(self) -> Dict[str, object]:
        return {
            "pages": self.pages,
            "excerpt_chars": self.excerpt_chars,
            "suffix": self.suffix,
            "default_extract": self.default_extract,
            "min_interval_seconds": self.min_interval_seconds,
            "retry_delays": list(self.retry_delays),
            "request_timeout_seconds": self.request_timeout_seconds,
        }


def _config_from_rows(rows: Dict[str, str], source: str) -> ArchiveConfig:
    require(rows, list(_ROW_KEYS))

    template = rows["PDF 归档路径"]
    match = _TEMPLATE_RE.search(template)
    if not match:
        raise UsageError(
            "data/README.md section 4 no longer documents the archive path as "
            "data/pdf/<code>/<date>/<announcement_id>.PDF + suffix (value %r)"
            % template
        )

    backoff_raw = rows["重试退避"]
    delays = tuple(int(value) for value in re.findall(r"([0-9]+)\s*s", backoff_raw))
    if not delays:
        raise UsageError("cannot parse the retry delays from %r" % backoff_raw)

    return ArchiveConfig(
        pages=_first_int(rows["PDF 文本抽取范围"], "PDF extraction page range"),
        excerpt_chars=_first_int(rows["PDF 摘要长度"], "PDF excerpt length"),
        suffix=match.group(1),
        default_extract=_default_flag(rows["归档文本抽取"]),
        min_interval_seconds=_interval(rows["单轮全局请求间隔"]),
        retry_delays=delays,
        request_timeout_seconds=_first_int(
            rows["请求超时"], "request timeout"
        ),
        source=source,
    )


def _default_flag(value: str) -> bool:
    """Read the ``归档文本抽取`` default instead of assuming it is off."""

    if value.startswith("默认关闭"):
        return False
    if value.startswith("默认开启"):
        return True
    raise UsageError(
        "the 归档文本抽取 row must start with 默认关闭 or 默认开启, got %r" % value
    )


def _interval(value: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*s", value)
    if not match:
        raise UsageError("cannot parse the request interval from %r" % value)
    return float(match.group(1))


def parse_config(text: str, source: str = "data/README.md") -> ArchiveConfig:
    return _config_from_rows(_table(text), source)


def load_config(data_dir: Optional[Path] = None) -> ArchiveConfig:
    rows, source = read_table(data_dir)
    return _config_from_rows(rows, source)


def table_rows(config: ArchiveConfig) -> list:
    """Human-readable echo of what was parsed; used by ``archive --show-config``."""

    return [
        "source: %s" % config.source,
        "pages: %d" % config.pages,
        "excerpt_chars: %d" % config.excerpt_chars,
        "suffix: %s" % config.suffix,
        "default_extract: %s" % ("on" if config.default_extract else "off"),
        "interval: %ss" % config.min_interval_seconds,
        "retry_delays: %s" % ", ".join("%ss" % delay for delay in config.retry_delays),
        "max_attempts: %d" % config.max_attempts,
        "request_timeout_seconds: %d" % config.request_timeout_seconds,
    ]


__all__ = ["ArchiveConfig", "load_config", "parse_config", "table_rows"]
