"""Assemble one ``ReportDocument`` from stored rows.

This is the only place that decides *shape*: which entries exist, how they are
grouped, what counts as added or removed. Both renderers consume the result and
add nothing but formatting, which is what makes "Markdown and HTML agree"
checkable instead of hopeful.

What this module deliberately does **not** do:

* it never looks at a title to infer anything (category, tags and risk are read
  from ``classifications``);
* it never recomputes a risk level or a category;
* it never re-derives "added" from timestamps. Added/removed are set
  differences against ``report_baseline_items``, per plan/04 4.6.

The dual view is a *partition*: ``_partition`` assigns every entry to exactly
one group under a caller-supplied key. Because both views are built from the
same list through the same function, ``set(view A ids) == set(view B ids)``
holds by construction rather than by assertion (the test still asserts it, but
a failure would mean the partition function was bypassed).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from ..disclaimer import DATA_SOURCE
from .labels import LabelTable
from .models import (
    UNCLASSIFIED,
    Entry,
    RemovedItem,
    ReportDocument,
    ReportGroup,
    ReportSummary,
    safe_date_ordinal,
    source_url,
)
from .repository import BaselineRow, FetchSnapshot, ReportRow

BASELINE_NOTE = "首次运行，已建立基线"


def _partition(entries: Sequence[Entry], key_of) -> List[ReportGroup]:
    """Assign each entry to exactly one group; order groups by their best entry.

    ``key_of`` returns ``(key, label)``. Group order is ``(best sort_key, key)``:
    a stock whose most severe item is ``high`` sorts above one whose best is
    ``mid``, and equal ranks fall back to the key so the order never depends on
    dict insertion order.
    """

    buckets: Dict[str, List[Entry]] = {}
    labels: Dict[str, str] = {}
    for entry in entries:
        key, label = key_of(entry)
        buckets.setdefault(key, []).append(entry)
        labels[key] = label

    groups = []
    for key, members in buckets.items():
        ordered = tuple(sorted(members, key=lambda item: item.sort_key))
        groups.append(ReportGroup(key=key, label=labels[key], entries=ordered))
    groups.sort(key=lambda group: (group.entries[0].sort_key, group.key))
    return groups


def _entry_from(row: ReportRow, labels: LabelTable) -> Entry:
    return Entry(
        announcement_id=row.announcement_id,
        code=row.code,
        name=row.name,
        title=row.title,
        announce_date=row.announce_date,
        category=row.category,
        category_name=labels.name_of(row.category),
        tags=tuple(row.tags),
        tag_names=tuple(labels.name_of(tag) for tag in row.tags),
        risk_level=row.risk_level,
        rules_version=row.rules_version,
        url=source_url(row.adjunct_url),
    )


def _removed_items(
    ids: Sequence[str],
    described: Dict[str, BaselineRow],
) -> Tuple[RemovedItem, ...]:
    items = []
    for announcement_id in ids:
        row = described.get(announcement_id)
        items.append(
            RemovedItem(
                announcement_id=announcement_id,
                title=row.title if row is not None else "",
                announce_date=row.announce_date if row is not None else "",
            )
        )
    items.sort(key=lambda item: (safe_date_ordinal(item.announce_date), item.announcement_id))
    return tuple(items)


def build_document(
    report_date: str,
    generated_at: str,
    rows: Sequence[ReportRow],
    labels: LabelTable,
    baseline_date: Optional[str],
    baseline_ids: Sequence[str],
    described: Dict[str, BaselineRow],
    fetch: Optional[FetchSnapshot],
    dropped: int,
    failed_codes: Sequence[str] = (),
    unlabelled: int = 0,
) -> ReportDocument:
    """Turn stored rows into the document both formats render."""

    entries = tuple(sorted((_entry_from(row, labels) for row in rows), key=lambda item: item.sort_key))
    today_ids = {entry.announcement_id for entry in entries}
    previous_ids = set(baseline_ids)

    baseline_created = baseline_date is None
    if baseline_created:
        # A baseline run reports nothing as new. Every row is today's only
        # because there is nothing to compare it against.
        added: Tuple[Entry, ...] = ()
    else:
        added = tuple(entry for entry in entries if entry.announcement_id not in previous_ids)

    removed = () if baseline_created else _removed_items(
        [item for item in baseline_ids if item not in today_ids], described
    )

    by_stock = _partition(entries, lambda entry: (entry.code, "%s %s" % (entry.code, entry.name)))
    by_label = _partition(entries, lambda entry: (entry.category, entry.category_name))

    high = sum(1 for entry in entries if entry.risk_level == "high")
    mid = sum(1 for entry in entries if entry.risk_level == "mid")
    low = sum(1 for entry in entries if entry.risk_level == "low")
    unclassified = tuple(entry for entry in entries if entry.category == UNCLASSIFIED)

    failed_stocks = 0 if fetch is None else fetch.stocks_failed
    summary = ReportSummary(
        report_date=report_date,
        generated_at=generated_at,
        item_count=len(entries),
        high=high,
        mid=mid,
        low=low,
        unclassified=len(unclassified),
        added=len(added),
        removed=len(removed),
        baseline_created=baseline_created,
        baseline_date=baseline_date or "",
        failed_stocks=failed_stocks,
        dropped=int(dropped),
        rules_versions=tuple(sorted({entry.rules_version for entry in entries})),
        complete=failed_stocks == 0,
        unlabelled=int(unlabelled),
    )

    return ReportDocument(
        summary=summary,
        entries=entries,
        by_stock=tuple(by_stock),
        by_label=tuple(by_label),
        added=added,
        removed=removed,
        unclassified=unclassified,
        failed_codes=tuple(sorted(str(code) for code in failed_codes)),
    )


def tag_index(document: ReportDocument, order: Sequence[str]) -> List[Tuple[str, Tuple[Entry, ...]]]:
    """Tag -> entries carrying it, in the package's fixed patch order.

    This is an index over the category view, not a second partition: an entry
    appears under its own category group exactly once and may also be listed
    here. Set equality between the two views is therefore untouched.
    """

    buckets: Dict[str, List[Entry]] = {}
    for entry in document.entries:
        for tag in entry.tags:
            buckets.setdefault(tag, []).append(entry)

    ordered: List[Tuple[str, Tuple[Entry, ...]]] = []
    for tag in order:
        if tag in buckets:
            ordered.append((tag, tuple(sorted(buckets.pop(tag), key=lambda item: item.sort_key))))
    for tag in sorted(buckets):
        ordered.append((tag, tuple(sorted(buckets.pop(tag), key=lambda item: item.sort_key))))
    return ordered


__all__ = ["BASELINE_NOTE", "DATA_SOURCE", "build_document", "tag_index"]
