"""Local daily report: one assembled document, two renderers.

The package is intentionally narrow. It reads stored rows, partitions them two
ways, renders Markdown and a single-file HTML page, and records the run plus
the day's baseline. It does not classify, does not re-grade risk, and does not
import the matcher — ``tests/test_report.py`` checks that in the AST.
"""

from __future__ import annotations

from .assembly import BASELINE_NOTE, build_document, tag_index
from .html import ExternalResourceError, assert_no_external_resources, render_html
from .labels import LabelTable, load_label_table
from .markdown import render_markdown
from .models import (
    UNCLASSIFIED,
    Entry,
    RemovedItem,
    ReportDocument,
    ReportGroup,
    ReportSummary,
    source_url,
)
from .repository import ReportRepository
from .service import (
    HTML_NAME,
    MARKDOWN_NAME,
    ReportOutcome,
    build_report,
    generate_report,
    parse_generated_at,
    report_dir,
)

__all__ = [
    "BASELINE_NOTE",
    "Entry",
    "ExternalResourceError",
    "HTML_NAME",
    "LabelTable",
    "MARKDOWN_NAME",
    "RemovedItem",
    "ReportDocument",
    "ReportGroup",
    "ReportOutcome",
    "ReportRepository",
    "ReportSummary",
    "UNCLASSIFIED",
    "assert_no_external_resources",
    "build_document",
    "build_report",
    "generate_report",
    "load_label_table",
    "parse_generated_at",
    "render_html",
    "render_markdown",
    "report_dir",
    "source_url",
    "tag_index",
]
