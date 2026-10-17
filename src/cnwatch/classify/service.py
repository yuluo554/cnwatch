"""The classify round: load the package, label what is pending, report.

Order of operations is the contract, and it is chosen so that a broken package
cannot damage a good database:

1. load and fully validate the rule package (raises ``UsageError``, exit 2);
2. select the pending rows — never classified, or labelled with an older
   ``rules_version``;
3. classify every row in memory;
4. write the batch in one transaction.

Step 1 happening before step 2 is what makes "26 类不全 → 退出码 2 且不写任何
分类结果" true: there is no partial state to roll back because nothing has
been written yet.

Unclassified rows are stored like any other row. Skipping them would make the
daily report's "未分类" count permanently zero, which is the failure mode
plan/06 M4 is designed to catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..clock import Clock
from .engine import (
    DROP_EMPTY_TITLE,
    DROP_TITLE_TOO_LONG,
    UNCLASSIFIED,
    Classification,
    MatchTrace,
    classify_title,
)
from .repository import ClassifyRepository, PendingRow
from .rules import RulePackage

_VALID_DROPS = (DROP_EMPTY_TITLE, DROP_TITLE_TOO_LONG)


@dataclass(frozen=True)
class ClassifiedRow:
    announcement_id: str
    code: str
    title: str
    category: str
    tags: Tuple[str, ...]
    risk_level: str
    also_matched: Tuple[str, ...]
    dropped_title: str


@dataclass
class ClassifySummary:
    rules_version: str
    classified_at: str
    pending: int
    classified: int
    unclassified: int
    stale: int
    by_risk: dict
    by_category: dict
    by_tag: dict
    also_matched: dict
    dropped_titles: int
    announcements_total: int
    classifications_total: int
    rows: List[ClassifiedRow] = field(default_factory=list)

    def render(self, limit: int = 10) -> str:
        lines = [
            "classify  rules_version=%s" % self.rules_version,
            "pending %d  classified %d  (stale relabelled %d)"
            % (self.pending, self.classified, self.stale),
            "risk  high %d  mid %d  low %d"
            % (
                self.by_risk.get("high", 0),
                self.by_risk.get("mid", 0),
                self.by_risk.get("low", 0),
            ),
        ]
        if self.unclassified:
            lines.append("unclassified %d" % self.unclassified)
        for tag in sorted(self.by_tag):
            lines.append("tag %-9s %d" % (tag, self.by_tag[tag]))
        if self.dropped_titles:
            lines.append("dropped titles %d (stored as unclassified)" % self.dropped_titles)
        if self.pending == 0:
            lines.append("nothing pending: every stored announcement already carries %s"
                         % self.rules_version)
        top = sorted(self.by_category.items(), key=lambda item: (-item[1], item[0]))
        if top:
            lines.append("categories (top %d):" % min(limit, len(top)))
            for category_id, count in top[:limit]:
                lines.append("  %-26s %d" % (category_id, count))
            if len(top) > limit:
                lines.append("  ... %d more" % (len(top) - limit))
        if self.also_matched:
            detail = ", ".join(
                "%s %d" % (category_id, count)
                for category_id, count in sorted(self.also_matched.items())
            )
            lines.append("also matched (log only, does not affect the label): %s" % detail)
        lines.append(
            "database: announcements %d, classifications %d"
            % (self.announcements_total, self.classifications_total)
        )
        return "\n".join(lines)

    def log_lines(self) -> List[str]:
        """One line per row that the summary cannot show; written to out/logs."""

        lines = []
        for row in self.rows:
            if row.also_matched or row.dropped_title:
                lines.append(
                    "%s %s category=%s also_matched=%s dropped=%s"
                    % (
                        row.announcement_id,
                        row.code,
                        row.category,
                        ",".join(row.also_matched) or "-",
                        row.dropped_title or "-",
                    )
                )
        return lines


def classify_pending(
    repository: ClassifyRepository,
    package: RulePackage,
    clock: Clock,
    since: Optional[str] = None,
) -> ClassifySummary:
    pending = repository.pending(package.rules_version, since=since)
    at = clock.timestamp()
    trace = MatchTrace()

    rows: List[ClassifiedRow] = []
    batch = []
    by_risk: dict = {}
    by_category: dict = {}
    by_tag: dict = {}
    unclassified = 0
    stale = 0
    dropped = 0

    for item in pending:
        result = classify_title(item.title, package, trace)
        if item.is_stale:
            stale += 1
        if result.dropped_title in _VALID_DROPS:
            dropped += 1
        if result.is_unclassified:
            unclassified += 1
        by_risk[result.risk_level] = by_risk.get(result.risk_level, 0) + 1
        by_category[result.category] = by_category.get(result.category, 0) + 1
        for tag in result.tags:
            by_tag[tag] = by_tag.get(tag, 0) + 1
        rows.append(
            ClassifiedRow(
                announcement_id=item.announcement_id,
                code=item.code,
                title=item.title,
                category=result.category,
                tags=result.tags,
                risk_level=result.risk_level,
                also_matched=result.also_matched,
                dropped_title=result.dropped_title,
            )
        )
        batch.append(
            (
                item.announcement_id,
                result.category,
                result.tags,
                result.risk_level,
                package.rules_version,
                at,
            )
        )

    written = repository.replace(batch)
    counts = repository.counts()
    return ClassifySummary(
        rules_version=package.rules_version,
        classified_at=at,
        pending=len(pending),
        classified=written,
        unclassified=unclassified,
        stale=stale,
        by_risk=by_risk,
        by_category=by_category,
        by_tag=by_tag,
        also_matched=dict(trace.also_matched_counts),
        dropped_titles=dropped,
        announcements_total=counts["announcements"],
        classifications_total=counts["classifications"],
        rows=rows,
    )


def classify_one(title: str, package: RulePackage) -> Classification:
    """Convenience for callers that hold a single title (tests, one-off checks)."""

    return classify_title(title, package)


__all__ = [
    "UNCLASSIFIED",
    "ClassifiedRow",
    "ClassifySummary",
    "classify_one",
    "classify_pending",
    "ClassifyRepository",
]
