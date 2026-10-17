"""Display names for categories and patch tags.

The report has to print ``减持`` rather than ``reduction``, but it must not be
able to *decide* anything. This module therefore reads the two frozen resource
files as plain JSON and keeps only the ``id -> name`` pairs; it never imports
``cnwatch.classify`` and never looks at a title.

A missing or unreadable resource degrades the *label*, not the report: an
unknown id renders as the id itself. That is deliberate — a display name is not
worth failing a report over, and rendering the raw id is honest about the gap.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

ENUM_FILENAME = "official-categories.json"
RULES_RELPATH = Path("rules") / "rules-v1.json"

# ``unclassified`` is a stored label but not an official category, so it has no
# entry in the enum file. Mapping it here keeps the display name next to the
# other names instead of special-casing it in two renderers.
FALLBACK_NAMES = {"unclassified": "未分类"}


def default_resources_dir() -> Path:
    """Same location the other layers use; spelled here so no import is needed.

    Reaching into ``cnwatch.classify`` for this helper would make the report
    package depend on the matcher, which is exactly what the AST gate forbids.
    """

    return Path(__file__).resolve().parents[1] / "resources"


class LabelTable:
    def __init__(self, names: Dict[str, str]) -> None:
        self._names = dict(names)

    def name_of(self, rule_id: str) -> str:
        return self._names.get(rule_id, rule_id)

    def as_dict(self) -> Dict[str, str]:
        return dict(self._names)


def _read(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_label_table(resources_dir: Optional[Path] = None) -> LabelTable:
    root = Path(resources_dir) if resources_dir is not None else default_resources_dir()
    names: Dict[str, str] = dict(FALLBACK_NAMES)

    for row in _read(root / ENUM_FILENAME).get("categories", []) or []:
        if isinstance(row, dict) and row.get("id") and row.get("name"):
            names[str(row["id"])] = str(row["name"])

    for row in _read(root / RULES_RELPATH).get("keyword_patches", []) or []:
        if isinstance(row, dict) and row.get("id") and row.get("name"):
            names[str(row["id"])] = str(row["name"])

    return LabelTable(names)


__all__ = ["LabelTable", "load_label_table"]
