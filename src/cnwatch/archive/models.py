"""Immutable models for the archive layer.

One archived filing is one row in ``archive_records`` (M0 schema, unchanged):

``local_path``/``byte_size``/``downloaded_at``
    what was written, how large it was, and when — the three fields FR-40 asks
    for. ``byte_size`` is the number of bytes actually written, so the record
    can be checked against the file on disk.
``text_status``/``text_excerpt``
    the two-valued outcome of the optional text step. ``unavailable`` means
    "there is no usable text for this filing", whether because extraction was
    not requested, the extractor produced nothing, or the text layer is the
    garbled kind ``data/README.md`` S5 warns about. It never means "the excerpt
    is empty but the extraction succeeded" — an empty extraction is reported as
    unavailable instead of being dressed up as success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

# Per-item outcome, mirroring the fetch layer's ok/failed vocabulary.
ARCHIVED = "archived"
SKIPPED = "skipped"
FAILED = "failed"

TEXT_EXTRACTED = "extracted"
TEXT_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ArchiveTarget:
    """One stored announcement selected for archiving."""

    announcement_id: str
    code: str
    name: str
    title: str
    announce_date: str
    source_url: str
    risk_level: str = ""
    category: str = ""


@dataclass(frozen=True)
class ArchiveRecord:
    """A row of ``archive_records``, as stored."""

    announcement_id: str
    local_path: str
    byte_size: int
    downloaded_at: str
    text_status: str
    text_excerpt: str


def archive_path(
    root: Path, code: str, announce_date: str, announcement_id: str, suffix: str
) -> Path:
    """``data/pdf/<code>/<date>/<announcement_id>.PDF`` (plan/04 5.2).

    The suffix is passed in because it is policy (``data/README.md`` section 4),
    not a literal this module is allowed to decide.
    """

    return Path(root) / str(code) / str(announce_date) / ("%s%s" % (announcement_id, suffix))


@dataclass
class ArchiveItem:
    """What happened to one requested filing."""

    announcement_id: str
    code: str
    status: str
    local_path: str = ""
    byte_size: int = 0
    text_status: str = ""
    reason: str = ""
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAILED


@dataclass
class ArchiveSummary:
    targets: int
    archived: int
    skipped: int
    failed: int
    text_requested: bool
    text_extracted: int
    text_unavailable: int
    bytes_total: int
    requests: int = 0
    retries: int = 0
    items: List[ArchiveItem] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.failed == 0

    def failed_items(self) -> List[ArchiveItem]:
        return [item for item in self.items if item.failed]

    def render(self) -> str:
        lines = [
            "archive  %d requested  archived %d  already present %d  failed %d"
            % (self.targets, self.archived, self.skipped, self.failed)
        ]
        lines.append("bytes written %d" % self.bytes_total)
        lines.append("requests %d (retries %d)" % (self.requests, self.retries))
        if self.text_requested:
            lines.append(
                "text  extracted %d  unavailable %d"
                % (self.text_extracted, self.text_unavailable)
            )
        else:
            lines.append(
                "text  not requested (--text enables extraction; %d unavailable)"
                % self.text_unavailable
            )
        for item in self.items:
            if item.status == ARCHIVED:
                lines.append(
                    "  %s  %s  %d B  text=%s"
                    % (item.announcement_id, item.local_path, item.byte_size, item.text_status)
                )
            elif item.status == SKIPPED:
                lines.append(
                    "  %s  already archived  text=%s%s"
                    % (
                        item.announcement_id,
                        item.text_status,
                        "" if not item.detail else "  (%s)" % item.detail,
                    )
                )
        failures = self.failed_items()
        if failures:
            lines.append("failures:")
            for item in failures:
                lines.append(
                    "  %s  %s  %s" % (item.announcement_id, item.reason, item.detail)
                )
        lines.append(
            "result: %s" % ("complete" if self.complete else "incomplete")
        )
        return "\n".join(lines)


__all__ = [
    "ARCHIVED",
    "ArchiveItem",
    "ArchiveRecord",
    "ArchiveSummary",
    "ArchiveTarget",
    "FAILED",
    "SKIPPED",
    "TEXT_EXTRACTED",
    "TEXT_UNAVAILABLE",
    "archive_path",
]
