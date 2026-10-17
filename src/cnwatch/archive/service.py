"""The archive round: resolve, download, optionally extract, record.

Order of operations is the contract, and it mirrors ``classify`` (K17):

1. refuse to run when text was requested and no backend exists — *before* any
   download, directory or database row exists, so "依赖缺失 → 退出码 2 且不写
   空摘要" is structural rather than a cleanup path;
2. select the stored rows the filters name;
3. per filing, in one pass: skip if already archived intact, otherwise download
   once, optionally extract, then write ``archive_records``;
4. isolate failures: one filing that cannot be downloaded does not stop the
   others and does not produce a half record.

Idempotency is defined on ``announcement_id`` (the primary key). Re-archiving a
filing whose file is on disk with the recorded byte size performs **no download
and writes no new row**. That includes a re-run with ``--text`` when a previous
run stored ``unavailable``: the text is re-attempted **from the local file** and
the same row is updated — the bytes are not fetched twice, and the count of rows
for that id stays one.

The excerpt never feeds classification (plan/04 §5.3). This module does not
import the matcher, and the archive tables are not read by the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from ..clock import Clock
from ..errors import UsageError
from ..paths import Paths
from .config import ArchiveConfig
from .extract import (
    INSTALL_HINT,
    ExtractionFailed,
    ExtractionUnavailable,
    TextExtractor,
    make_excerpt,
)
from .models import (
    ARCHIVED,
    FAILED,
    SKIPPED,
    TEXT_EXTRACTED,
    TEXT_UNAVAILABLE,
    ArchiveItem,
    ArchiveRecord,
    ArchiveSummary,
    ArchiveTarget,
    archive_path,
)
from .repository import ArchiveRepository
from .transport import PdfDownloadError, PdfDownloader


def require_extractor(extractor: Optional[TextExtractor], text_requested: bool) -> None:
    """Exit-2 gate: the user asked for text and nothing can produce it.

    Called before the first download *and* before any runtime directory is
    created, so the failure leaves the home directory exactly as it was.
    """

    if not text_requested:
        return
    if extractor is not None and extractor.available():
        return
    raise UsageError(
        "PDF text extraction was requested but no extractor is installed",
        hint=INSTALL_HINT,
    )


@dataclass
class ArchiveRequest:
    """Everything one archive round needs, so the CLI stays a wiring layer."""

    paths: Paths
    repository: ArchiveRepository
    downloader: PdfDownloader
    clock: Clock
    config: ArchiveConfig
    targets: Sequence[ArchiveTarget]
    extractor: Optional[TextExtractor] = None
    text_requested: bool = False


def archive_targets(request: ArchiveRequest) -> ArchiveSummary:
    require_extractor(request.extractor, request.text_requested)

    items: List[ArchiveItem] = []
    archived = 0
    skipped = 0
    failed = 0
    text_extracted = 0
    text_unavailable = 0
    bytes_total = 0

    for target in request.targets:
        destination = archive_path(
            request.paths.pdf_dir,
            target.code,
            target.announce_date,
            target.announcement_id,
            request.config.suffix,
        )
        existing = request.repository.existing(target.announcement_id)

        if _is_intact(existing):
            item, extracted = _reuse(request, target, existing)
            skipped += 1
            if extracted:
                text_extracted += 1
            elif item.text_status == TEXT_UNAVAILABLE:
                text_unavailable += 1
            items.append(item)
            continue

        item = _download(request, target, destination)
        if item.status == FAILED:
            failed += 1
            items.append(item)
            continue
        archived += 1
        bytes_total += item.byte_size
        if item.text_status == TEXT_EXTRACTED:
            text_extracted += 1
        else:
            text_unavailable += 1
        items.append(item)

    return ArchiveSummary(
        targets=len(request.targets),
        archived=archived,
        skipped=skipped,
        failed=failed,
        text_requested=request.text_requested,
        text_extracted=text_extracted,
        text_unavailable=text_unavailable,
        bytes_total=bytes_total,
        requests=request.downloader.attempts,
        retries=request.downloader.retries,
        items=items,
    )


# -- internals -------------------------------------------------------------- #


def _is_intact(record: Optional[ArchiveRecord]) -> bool:
    """A record whose file is present and the recorded size matches."""

    if record is None:
        return False
    path = Path(record.local_path)
    try:
        return path.is_file() and path.stat().st_size == int(record.byte_size)
    except OSError:  # noqa: BLE001 - an unreadable file is a reason to re-download
        return False


def _reuse(
    request: ArchiveRequest, target: ArchiveTarget, record: ArchiveRecord
) -> tuple:
    """No download. Possibly recover text from the file already on disk."""

    item = ArchiveItem(
        announcement_id=target.announcement_id,
        code=target.code,
        status=SKIPPED,
        local_path=record.local_path,
        byte_size=int(record.byte_size),
        text_status=record.text_status,
    )
    if not request.text_requested or record.text_status == TEXT_EXTRACTED:
        return item, False

    try:
        data = Path(record.local_path).read_bytes()
    except OSError as exc:  # noqa: BLE001 - the size check above makes this rare
        item.detail = "could not re-read the local file: %s" % exc
        return item, False

    excerpt, status = _extract(request, data)
    if status == TEXT_EXTRACTED:
        request.repository.store(
            ArchiveRecord(
                announcement_id=record.announcement_id,
                local_path=record.local_path,
                byte_size=int(record.byte_size),
                downloaded_at=record.downloaded_at,
                text_status=status,
                text_excerpt=excerpt,
            )
        )
        item.text_status = status
        item.detail = "text recovered from the local file (no download)"
        return item, True

    item.detail = "text still unavailable (no download)"
    return item, False


def _download(
    request: ArchiveRequest, target: ArchiveTarget, destination: Path
) -> ArchiveItem:
    try:
        data = request.downloader.fetch(target.source_url)
    except PdfDownloadError as exc:
        return ArchiveItem(
            announcement_id=target.announcement_id,
            code=target.code,
            status=FAILED,
            reason=exc.reason,
            detail=exc.detail,
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(str(destination), "wb") as handle:
        handle.write(data)

    excerpt = ""
    status = TEXT_UNAVAILABLE
    if request.text_requested:
        excerpt, status = _extract(request, data)

    record = ArchiveRecord(
        announcement_id=target.announcement_id,
        local_path=str(destination),
        byte_size=len(data),
        downloaded_at=request.clock.timestamp(),
        text_status=status,
        text_excerpt=excerpt,
    )
    request.repository.store(record)
    return ArchiveItem(
        announcement_id=target.announcement_id,
        code=target.code,
        status=ARCHIVED,
        local_path=str(destination),
        byte_size=int(record.byte_size),
        text_status=status,
    )


def _extract(request: ArchiveRequest, data: bytes) -> tuple:
    """``(excerpt, text_status)``. Never raises for a filing that has no text."""

    extractor = request.extractor
    if extractor is None:
        return "", TEXT_UNAVAILABLE
    try:
        raw = extractor.extract(data, request.config.pages)
    except ExtractionUnavailable as exc:
        # require_extractor() ran before the first download; reaching this means
        # the backend vanished mid-run, which is a configuration problem, not a
        # property of this filing.
        raise UsageError(
            "the PDF text extractor disappeared during the run: %s" % exc,
            hint=INSTALL_HINT,
        )
    except ExtractionFailed:
        return "", TEXT_UNAVAILABLE
    except Exception:  # noqa: BLE001 - a garbled PDF must not fail the round
        return "", TEXT_UNAVAILABLE

    excerpt = make_excerpt(raw, request.config.excerpt_chars)
    if not excerpt:
        # A backend that produced nothing is the "text layer unusable" case, not
        # a success with an empty excerpt.
        return "", TEXT_UNAVAILABLE
    return excerpt, TEXT_EXTRACTED


__all__ = [
    "ARCHIVED",
    "ArchiveRequest",
    "ArchiveSummary",
    "FAILED",
    "SKIPPED",
    "archive_targets",
    "require_extractor",
]
