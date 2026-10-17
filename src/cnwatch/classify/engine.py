"""The matcher: title in, ``{category, tags, risk_level}`` out.

Determinism is the whole product here, so the algorithm is short and stated
once (plan/04 3.4):

1. normalize the title;
2. walk the official categories in ``(priority, id)`` order and take the
   **first** hit — never the best-scoring hit, because there is no score;
3. scan the four keyword patches independently and append every hit to
   ``tags`` in the package's fixed order;
4. risk is the highest level among the chosen category and the patch tags.

Rule 4 is the one place where a reasonable reader could expect the opposite
("highest over *all* official matches"). The frozen truth ledger settles it:
``南京晶升…2025年第一季度报告（更正版）`` is a 一季报 that also matches 补充更正
(``mid``), and the ledger expects ``low``. Only the winning category raises the
risk; the other matches are recorded as ``also_matched`` for the log and never
influence the stored label. ``tests/test_classify_engine.py`` pins that reading
against all 86 ledger rows.

``announcementType`` is not read here and must not be: the package declares
``announcement_type_used=false`` and the AST gate checks the source (K12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .rules import RulePackage
from .text import is_usable_title, normalize_title

UNCLASSIFIED = "unclassified"
RISK_ORDER = {"low": 0, "mid": 1, "high": 2}

# Why a row was stored without a class. ``""`` means it has one.
DROP_EMPTY_TITLE = "empty_title"
DROP_TITLE_TOO_LONG = "title_too_long"


@dataclass(frozen=True)
class Classification:
    category: str
    tags: Tuple[str, ...]
    risk_level: str
    also_matched: Tuple[str, ...] = ()
    dropped_title: str = ""

    @property
    def is_unclassified(self) -> bool:
        return self.category == UNCLASSIFIED

    def tags_json(self) -> str:
        """Canonical JSON for storage: no spaces, package order preserved."""

        return "[" + ", ".join('"%s"' % tag for tag in self.tags) + "]"


@dataclass
class MatchTrace:
    """Per-row diagnostics. Counters feed the run summary, details the log."""

    also_matched_counts: dict = field(default_factory=dict)

    def observe(self, classification: Classification) -> None:
        for category_id in classification.also_matched:
            self.also_matched_counts[category_id] = (
                self.also_matched_counts.get(category_id, 0) + 1
            )


def _strip_neutralizers(text: str, neutralizers: Tuple[str, ...]) -> str:
    stripped = text
    for neutralizer in neutralizers:
        stripped = stripped.replace(neutralizer, "")
    return stripped


def _official_matches(normalized: str, package: RulePackage) -> List[str]:
    hits = []
    for rule in package.match_order:
        candidate = _strip_neutralizers(normalized, rule.neutralizers)
        if any(pattern in candidate for pattern in rule.patterns):
            hits.append(rule.id)
    return hits


def _patch_hits(normalized: str, package: RulePackage) -> List[str]:
    hits = []
    for rule in package.patches:
        if any(negation in normalized for negation in rule.negations):
            continue
        candidate = _strip_neutralizers(normalized, rule.neutralizers)
        if any(pattern in candidate for pattern in rule.patterns):
            hits.append(rule.id)
    return hits


def highest_risk(levels) -> str:
    chosen: Optional[str] = None
    for level in levels:
        if chosen is None or RISK_ORDER[level] > RISK_ORDER[chosen]:
            chosen = level
    return chosen if chosen is not None else "low"


def classify_title(title: str, package: RulePackage, trace: Optional[MatchTrace] = None) -> Classification:
    """Classify one title. Never raises on odd input; it reports instead."""

    if not is_usable_title(title, package.title_limit):
        reason = DROP_TITLE_TOO_LONG if isinstance(title, str) and title.strip() else DROP_EMPTY_TITLE
        return Classification(
            category=UNCLASSIFIED,
            tags=(),
            risk_level=package.default_risk,
            dropped_title=reason,
        )

    normalized = normalize_title(title)
    if not normalized:
        return Classification(
            category=UNCLASSIFIED,
            tags=(),
            risk_level=package.default_risk,
            dropped_title=DROP_EMPTY_TITLE,
        )

    matches = _official_matches(normalized, package)
    tags = tuple(_patch_hits(normalized, package))
    category = matches[0] if matches else UNCLASSIFIED
    also = tuple(matches[1:])

    levels = [package.risk_of(category)] + [package.risk_of(tag) for tag in tags]
    result = Classification(
        category=category,
        tags=tags,
        risk_level=highest_risk(levels),
        also_matched=also,
    )
    if trace is not None:
        trace.observe(result)
    return result
