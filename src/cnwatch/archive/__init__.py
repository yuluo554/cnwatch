"""On-demand PDF archiving (M5).

Public surface: a parsed policy view, the storage layout, a downloader that
obeys the round's rate/retry policy, an optional text extractor with an explicit
"no backend" state, and one service call that ties them together.

The package writes only ``data/pdf/`` and the ``archive_records`` table. It does
not classify, does not touch the daily report, and does not read the clock
except through the injected ``Clock``.
"""

from __future__ import annotations

from .config import ArchiveConfig, load_config, parse_config, table_rows
from .extract import (
    INSTALL_HINT,
    ExtractionFailed,
    ExtractionUnavailable,
    PdftotextExtractor,
    PyMuPdfExtractor,
    TextExtractor,
    UnavailableExtractor,
    make_excerpt,
    resolve_extractor,
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
from .service import ArchiveRequest, archive_targets, require_extractor
from .transport import (
    PdfDownloadError,
    PdfDownloader,
    PdfTransport,
    RecordingPdfTransport,
    UrllibPdfTransport,
)

__all__ = [
    "ARCHIVED",
    "ArchiveConfig",
    "ArchiveItem",
    "ArchiveRecord",
    "ArchiveRepository",
    "ArchiveRequest",
    "ArchiveSummary",
    "ArchiveTarget",
    "FAILED",
    "INSTALL_HINT",
    "ExtractionFailed",
    "ExtractionUnavailable",
    "PdftotextExtractor",
    "PdfDownloadError",
    "PdfDownloader",
    "PdfTransport",
    "PyMuPdfExtractor",
    "RecordingPdfTransport",
    "SKIPPED",
    "TEXT_EXTRACTED",
    "TEXT_UNAVAILABLE",
    "TextExtractor",
    "UnavailableExtractor",
    "UrllibPdfTransport",
    "archive_path",
    "archive_targets",
    "load_config",
    "make_excerpt",
    "parse_config",
    "require_extractor",
    "resolve_extractor",
    "table_rows",
]
