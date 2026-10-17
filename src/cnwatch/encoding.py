"""Console encoding guard.

Chinese Windows consoles often report ``cp936``/``gbk``, which can encode
Chinese text; those streams must not be touched. Streams that report a
strictly ASCII/latin encoding (for example a redirected pipe on a legacy
locale) are reconfigured to UTF-8 so that CJK output does not crash.

Only known ASCII-like encodings are reconfigured. Unknown encodings are left
alone: the guard never guesses.
"""

from __future__ import annotations

import sys
from typing import Iterable, List, Optional

_UTF8_NAMES = frozenset({"utf-8", "utf8", "utf-8-sig", "cp65001"})

_ASCII_LIKE_NAMES = frozenset(
    {
        "ascii",
        "us-ascii",
        "ansi_x3.4-1968",
        "cp1252",
        "windows-1252",
        "latin-1",
        "iso-8859-1",
        "iso8859-1",
    }
)

# Encodings that can already carry Chinese text. Touching them would either
# be a no-op or break the console the user actually has.
_CJK_NAMES = frozenset(
    {
        "cp936",
        "gbk",
        "gb2312",
        "gb18030",
        "big5",
        "cp950",
        "shift_jis",
        "cp932",
        "euc-jp",
        "euc-kr",
        "cp949",
    }
)


def _normalize(encoding: Optional[str]) -> str:
    if not encoding:
        return ""
    return encoding.strip().lower().replace("_", "-")


def needs_reconfigure(encoding: Optional[str]) -> bool:
    """Return True only for known ASCII-like encodings."""

    name = _normalize(encoding)
    if not name or name in _UTF8_NAMES or name in _CJK_NAMES:
        return False
    return name in _ASCII_LIKE_NAMES


def ensure_utf8_stdio(streams: Optional[Iterable[object]] = None) -> List[str]:
    """Reconfigure ASCII-like streams to UTF-8 and return what changed.

    Returns the original encoding names that were replaced. Streams without a
    ``encoding`` attribute or without ``reconfigure`` (StringIO, pytest capture
    objects) are skipped silently.
    """

    targets = list(streams) if streams is not None else [sys.stdout, sys.stderr]
    changed: List[str] = []
    for stream in targets:
        original = getattr(stream, "encoding", None)
        if not needs_reconfigure(original):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue
        changed.append(str(original))
    return changed
