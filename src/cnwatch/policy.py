"""The single policy table: ``data/README.md`` section 4.

K7 says every number a run depends on — request interval, retry delays and
count, ``pageSize``, the incremental window, the first-run lookback, the
request timeout and the PDF extraction range — is defined in
``data/README.md`` section 4 and nowhere else. Modules parse that table instead
of restating it, so a code change that quietly alters the policy makes the
documentation disagree with reality, and the test comparing the two fails.

The table is Markdown, so parsing is textual and strict: a renamed or missing
row is a loud error, never a silent default. M2 parsed this section from
``fetch/config.py``; M5 added a second consumer (``archive/``), so the reading
of the table now lives here and both modules build their own typed view on top
of it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from .datadir import resolve_data_dir
from .errors import UsageError

README_RELPATH = Path("README.md")
SECTION_HEADING = "## 四、数值口径"


def section(text: str) -> str:
    """The text of section 4, up to the next level-2 heading."""

    start = text.find(SECTION_HEADING)
    if start < 0:
        raise UsageError(
            "data/README.md no longer has a %r section" % SECTION_HEADING,
            hint="the run-time policy is parsed from that section; restore it "
            "instead of hard-coding the numbers",
        )
    following = text.find("\n## ", start + len(SECTION_HEADING))
    return text[start:] if following < 0 else text[start:following]


def rows(section_text: str) -> Dict[str, str]:
    """``label -> value`` for every table row in a section.

    Labels are wrapped in backticks in the policy table; the backticks are
    stripped so parser keys stay readable.
    """

    found: Dict[str, str] = {}
    for line in section_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        found[cells[0].replace("`", "")] = cells[1]
    return found


def table(text: str) -> Dict[str, str]:
    return rows(section(text))


def read_table(data_dir: Optional[Path] = None) -> Tuple[Dict[str, str], str]:
    """Parse the table from a checkout's ``data/README.md``. Returns (rows, source)."""

    directory = Path(data_dir) if data_dir is not None else resolve_data_dir()
    readme = directory / README_RELPATH
    if not readme.is_file():
        raise UsageError("data policy table not found: %s" % readme)
    return table(readme.read_text(encoding="utf-8")), "%s#section-4" % readme.as_posix()


def require(found: Dict[str, str], labels: Sequence[str]) -> None:
    """A missing row is an error, not a fallback."""

    missing = [label for label in labels if label not in found]
    if missing:
        raise UsageError(
            "data/README.md section 4 is missing rows: %s" % ", ".join(missing)
        )


def first_int(value: str, label: str, pattern: str = r"(\d+)") -> int:
    match = re.search(pattern, value)
    if not match:
        raise UsageError(
            "cannot parse %s from data/README.md section 4 (value %r)" % (label, value)
        )
    return int(match.group(1))


__all__ = [
    "README_RELPATH",
    "SECTION_HEADING",
    "first_int",
    "read_table",
    "require",
    "rows",
    "section",
    "table",
]
