"""Immutable models for the daily report.

The report layer is a *renderer*: every value here was already decided by
``fetch`` (which rows exist) and ``classify`` (category, tags, risk). Nothing in
this module inspects a title, and nothing recomputes a risk level.

Two readings are worth stating because they are easy to get wrong:

``sort_key``
    ``(risk_rank, announce_date desc, announcement_id asc)`` (plan/04 4.5),
    defined once here and reused by both views. The date is turned into a
    negated ordinal so a single ascending tuple sort yields descending dates —
    a two-key sort would need the reader to reason about stability.

``UNCLASSIFIED``
    Spelled locally rather than imported from ``classify``. The AST gate in
    ``tests/test_report.py`` forbids the report package from importing the
    matcher at all, so the constant is duplicated and a test pins the two
    spellings together. Importing it would drag the rule engine into a layer
    that must not be able to make a decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from ..fetch.models import STATIC_BASE

UNCLASSIFIED = "unclassified"

RISK_LEVELS = ("high", "mid", "low")
RISK_RANK: Dict[str, int] = {"high": 0, "mid": 1, "low": 2}
_UNKNOWN_RANK = len(RISK_RANK)


def date_ordinal(value: str) -> int:
    """``YYYY-MM-DD`` -> ``20261016``, for an orderable, timezone-free key."""

    digits = str(value).replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError("expected a YYYY-MM-DD date, got %r" % (value,))
    return int(digits)


def safe_date_ordinal(value: str) -> int:
    """``date_ordinal`` that never raises.

    Sorting must not fail because one stored row carries a malformed date: the
    row still has to appear in the report, and the reader can see the bad date
    printed next to it. Such rows sort to the end of their risk band.
    """

    try:
        return date_ordinal(value)
    except ValueError:
        return 0


def source_url(adjunct_url: str) -> str:
    """The same link rule ``fetch`` applies, kept identical on purpose."""

    text = (adjunct_url or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return STATIC_BASE + text.lstrip("/")


@dataclass(frozen=True)
class Entry:
    """One announcement as it will be rendered."""

    announcement_id: str
    code: str
    name: str
    title: str
    announce_date: str
    category: str
    category_name: str
    tags: Tuple[str, ...]
    tag_names: Tuple[str, ...]
    risk_level: str
    rules_version: str
    url: str

    @property
    def is_unclassified(self) -> bool:
        return self.category == UNCLASSIFIED

    @property
    def sort_key(self) -> Tuple[int, int, str]:
        return (
            RISK_RANK.get(self.risk_level, _UNKNOWN_RANK),
            -safe_date_ordinal(self.announce_date),
            self.announcement_id,
        )


@dataclass(frozen=True)
class RemovedItem:
    """A baseline id that is absent today. The history row is never deleted."""

    announcement_id: str
    title: str
    announce_date: str


@dataclass(frozen=True)
class ReportGroup:
    """One heading in one view: a stock (view A) or a label (view B)."""

    key: str
    label: str
    entries: Tuple[Entry, ...]


@dataclass(frozen=True)
class ReportSummary:
    report_date: str
    generated_at: str
    item_count: int
    high: int
    mid: int
    low: int
    unclassified: int
    added: int
    removed: int
    baseline_created: bool
    baseline_date: str
    failed_stocks: int
    dropped: int
    rules_versions: Tuple[str, ...]
    complete: bool
    unlabelled: int = 0


@dataclass(frozen=True)
class ReportDocument:
    """Everything both renderers need. One assembly, two formats."""

    summary: ReportSummary
    entries: Tuple[Entry, ...]
    by_stock: Tuple[ReportGroup, ...]
    by_label: Tuple[ReportGroup, ...]
    added: Tuple[Entry, ...]
    removed: Tuple[RemovedItem, ...]
    unclassified: Tuple[Entry, ...]
    failed_codes: Tuple[str, ...]

    def id_set(self) -> set:
        return {entry.announcement_id for entry in self.entries}

    @property
    def unlabelled_total(self) -> int:
        """Announcements on the day that have no stored label.

        They are absent from both views (an unlabelled row has no category or
        risk to show), so the report states the number and points at
        ``cnwatch classify`` instead of quietly rendering a shorter list.
        """

        return self.summary.unlabelled

    def stock_view_ids(self) -> set:
        return {
            entry.announcement_id
            for group in self.by_stock
            for entry in group.entries
        }

    def label_view_ids(self) -> set:
        return {
            entry.announcement_id
            for group in self.by_label
            for entry in group.entries
        }
