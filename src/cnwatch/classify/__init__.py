"""Rule-based classification (M3).

Public surface: a validated rule package, a single deterministic matcher, and a
service that labels pending announcements. Nothing here reads the clock, the
network or ``announcementType``.
"""

from __future__ import annotations

from .engine import (
    DROP_EMPTY_TITLE,
    DROP_TITLE_TOO_LONG,
    UNCLASSIFIED,
    Classification,
    MatchTrace,
    classify_title,
    highest_risk,
)
from .repository import ClassifyRepository, PendingRow, StoredClassification
from .rules import (
    CategoryRule,
    PatchRule,
    RulePackage,
    RulePackageError,
    load_rule_package,
    rules_path,
)
from .service import ClassifiedRow, ClassifySummary, classify_pending
from .text import normalize_title

__all__ = [
    "CategoryRule",
    "Classification",
    "ClassifiedRow",
    "ClassifyRepository",
    "ClassifySummary",
    "DROP_EMPTY_TITLE",
    "DROP_TITLE_TOO_LONG",
    "MatchTrace",
    "PatchRule",
    "PendingRow",
    "RulePackage",
    "RulePackageError",
    "StoredClassification",
    "UNCLASSIFIED",
    "classify_pending",
    "classify_title",
    "highest_risk",
    "load_rule_package",
    "normalize_title",
    "rules_path",
]
