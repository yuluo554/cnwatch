"""Fetch configuration, read from the single documented source.

K7 says every number used by the fetch path (request interval, retry delays,
retry count, page size, overlap, first-run lookback) is defined in
``data/README.md`` section 4 and nowhere else. This module parses that table at
runtime instead of restating it, so a code change that quietly alters the
policy makes ``data/README.md`` disagree with reality and fails the test that
compares the two.

The table is Markdown, so parsing is textual and strict: a renamed row is a
loud error, not a silent default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..datadir import resolve_data_dir
from ..errors import UsageError

README_RELPATH = Path("README.md")
SECTION_HEADING = "## 四、数值口径"

# Row label in data/README.md section 4 -> internal key.
_ROW_KEYS = {
    "单轮全局请求间隔": "min_interval_seconds",
    "重试退避": "retry_delays",
    "pageSize": "page_size",
    "增量窗口": "window_rule",
    "首次回看天数": "first_run_lookback_days",
    "请求超时": "request_timeout_seconds",
}


@dataclass(frozen=True)
class FetchConfig:
    min_interval_seconds: float
    retry_delays: tuple
    page_size: int
    window_rule: str
    first_run_lookback_days: int
    request_timeout_seconds: int
    source: str

    @property
    def max_attempts(self) -> int:
        """One initial request plus one per configured retry."""

        return len(self.retry_delays) + 1

    def as_dict(self) -> Dict[str, object]:
        return {
            "min_interval_seconds": self.min_interval_seconds,
            "retry_delays": list(self.retry_delays),
            "page_size": self.page_size,
            "window_rule": self.window_rule,
            "first_run_lookback_days": self.first_run_lookback_days,
            "request_timeout_seconds": self.request_timeout_seconds,
        }


def _section(text: str) -> str:
    start = text.find(SECTION_HEADING)
    if start < 0:
        raise UsageError(
            "data/README.md no longer has a %r section" % SECTION_HEADING,
            hint="the fetch policy is parsed from that section; restore it "
            "instead of hard-coding the numbers",
        )
    following = text.find("\n## ", start + len(SECTION_HEADING))
    return text[start:] if following < 0 else text[start:following]


def _rows(section: str) -> Dict[str, str]:
    rows: Dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        # Labels are wrapped in backticks in the policy table; normalise them so
        # the parser keys stay readable.
        rows[cells[0].replace("`", "")] = cells[1]
    return rows


def _first_int(pattern: str, value: str, label: str) -> int:
    match = re.search(pattern, value)
    if not match:
        raise UsageError(
            "cannot parse %s from data/README.md section 4 (value %r)" % (label, value)
        )
    return int(match.group(1))


def parse_config(text: str, source: str = "data/README.md") -> FetchConfig:
    """Parse section 4. Missing rows are errors, not defaults."""

    rows = _rows(_section(text))
    missing = [label for label in _ROW_KEYS if label not in rows]
    if missing:
        raise UsageError(
            "data/README.md section 4 is missing rows: %s" % ", ".join(missing)
        )

    interval_raw = rows["单轮全局请求间隔"]
    interval_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*s", interval_raw)
    if not interval_match:
        raise UsageError("cannot parse the request interval from %r" % interval_raw)

    backoff_raw = rows["重试退避"]
    delays = tuple(int(value) for value in re.findall(r"([0-9]+)\s*s", backoff_raw))
    if not delays:
        raise UsageError("cannot parse the retry delays from %r" % backoff_raw)
    count_match = re.search(r"最多重试\s*([0-9]+)\s*次", backoff_raw)
    if not count_match:
        raise UsageError(
            "the retry row must state the retry count (最多重试 N 次), got %r"
            % backoff_raw
        )
    if int(count_match.group(1)) != len(delays):
        raise UsageError(
            "retry row is inconsistent: %d delays but it says 最多重试 %s 次"
            % (len(delays), count_match.group(1))
        )

    return FetchConfig(
        min_interval_seconds=float(interval_match.group(1)),
        retry_delays=delays,
        page_size=_first_int(r"(\d+)", rows["pageSize"], "pageSize"),
        window_rule=rows["增量窗口"],
        first_run_lookback_days=_first_int(
            r"(\d+)", rows["首次回看天数"], "first-run lookback"
        ),
        request_timeout_seconds=_first_int(
            r"(\d+)", rows["请求超时"], "request timeout"
        ),
        source=source,
    )


def load_config(data_dir: Optional[Path] = None) -> FetchConfig:
    directory = Path(data_dir) if data_dir is not None else resolve_data_dir()
    readme = directory / README_RELPATH
    if not readme.is_file():
        raise UsageError("data policy table not found: %s" % readme)
    return parse_config(
        readme.read_text(encoding="utf-8"),
        source="%s#section-4" % readme.as_posix(),
    )


def window_start(last_success: Optional[str], today: str, config: FetchConfig) -> str:
    """Incremental window start: last success minus the documented overlap.

    Without a cursor the first run looks back ``first_run_lookback_days`` so a
    new user sees recent history; the result is still baseline (no "new items")
    because there is nothing to compare against.
    """

    from ..clock import shift_days

    if last_success is None:
        return shift_days(today, -config.first_run_lookback_days)
    overlap = _overlap_days(config.window_rule)
    return shift_days(last_success, -overlap)


def _overlap_days(rule: str) -> int:
    """The rule reads "上次成功日期 − 1 天": take the number, do not assume it.

    The minus is matched as ASCII hyphen, U+2212 or the word 减, because the
    documentation is prose and a typographic minus must not silently disable
    the overlap.
    """

    match = re.search(r"(?:[-\u2212]|减)\s*([0-9]+)\s*天", rule)
    if not match:
        raise UsageError(
            "cannot read the overlap day count from the window rule %r" % rule
        )
    return int(match.group(1))


def describe(config: FetchConfig) -> str:
    return (
        "interval=%ss page_size=%d retries=%s lookback=%dd"
        % (
            config.min_interval_seconds,
            config.page_size,
            "/".join(str(delay) for delay in config.retry_delays),
            config.first_run_lookback_days,
        )
    )


def table_rows(config: FetchConfig) -> List[str]:
    """Human-readable echo of what was parsed; used by ``fetch --show-config``."""

    return [
        "source: %s" % config.source,
        "interval: %ss" % config.min_interval_seconds,
        "page_size: %d" % config.page_size,
        "retry_delays: %s" % ", ".join("%ss" % delay for delay in config.retry_delays),
        "max_attempts: %d" % config.max_attempts,
        "window_rule: %s" % config.window_rule,
        "first_run_lookback_days: %d" % config.first_run_lookback_days,
        "request_timeout_seconds: %d" % config.request_timeout_seconds,
    ]
