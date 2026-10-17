"""Terminal rendering.

``list`` and ``search`` print a table. Width is computed from the actual cell
content with East-Asian wide characters counted as two columns, because a
column misaligned by a Chinese name is the kind of bug that makes a CLI look
unfinished.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable, List, Sequence

# Columns of a "CJK wide" character occupy two terminal cells.
_WIDE = ("W", "F")


def display_width(text: str) -> int:
    total = 0
    for char in text:
        total += 2 if unicodedata.east_asian_width(char) in _WIDE else 1
    return total


def _pad(text: str, width: int) -> str:
    return text + " " * (width - display_width(text))


def render_table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """Fixed-width table; no borders, one header underline."""

    materialised: List[List[str]] = [[str(cell) for cell in row] for row in rows]
    widths = [display_width(header) for header in headers]
    for row in materialised:
        if len(row) != len(headers):
            raise ValueError("row has %d cells, header has %d" % (len(row), len(headers)))
        widths = [max(width, display_width(cell)) for width, cell in zip(widths, row)]

    header_line = "  ".join(_pad(header, width) for header, width in zip(headers, widths))
    separator = "  ".join("-" * width for width in widths)
    lines = [header_line.rstrip(), separator]
    for row in materialised:
        lines.append("  ".join(_pad(cell, width) for cell, width in zip(row, widths)).rstrip())
    return "\n".join(lines)
