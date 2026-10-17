"""The report round: read, assemble, render in memory, then write.

Order of operations is the contract (plan/04 4.7, plan/HANDOFF-M4 §七):

1. validate ``--date`` / ``--generated-at`` — a bad argument writes nothing;
2. refuse to create a runtime root around a missing database;
3. read the stored rows, the prior baseline and the covering fetch run;
4. build the document and render **both** formats into memory;
5. only then create ``out/reports/<date>/`` and write the two files;
6. record the run row and today's baseline ids in one transaction.

Step 4 before step 5 is what makes "rendering failure leaves no half artifact"
true: the HTML guard runs during rendering, so a page that would reference a
remote asset fails while the output directory is still empty.

Exit codes are decided here, not in the CLI:

* ``2`` — no database, or the report date has neither rows nor a baseline;
* ``1`` — a report was produced, but the fetch run covering the date failed
  some stocks, so the page is marked incomplete;
* ``0`` — normal.

The renderer never recomputes a label. Every ``category``/``tags``/``risk``
value comes from ``classifications``; this module does not import the matcher
(``tests/test_report.py`` enforces that in the AST).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from ..clock import Clock, ClockError, parse_date
from ..errors import UsageError
from ..paths import Paths
from .assembly import build_document
from .html import render_html
from .labels import LabelTable, load_label_table
from .markdown import render_markdown
from .models import ReportDocument
from .repository import ReportRepository

MARKDOWN_NAME = "daily.md"
HTML_NAME = "daily.html"


@dataclass(frozen=True)
class ReportOutcome:
    report_date: str
    generated_at: str
    markdown_path: Path
    html_path: Path
    item_count: int
    baseline_created: bool
    baseline_date: str
    added: int
    removed: int
    high: int
    mid: int
    low: int
    unclassified: int
    failed_stocks: int
    dropped: int
    content_sha256: str

    @property
    def complete(self) -> bool:
        return self.failed_stocks == 0

    def render(self) -> str:
        lines = [
            "report  %s  generated_at=%s" % (self.report_date, self.generated_at),
            "items %d  high %d  mid %d  low %d  unclassified %d"
            % (self.item_count, self.high, self.mid, self.low, self.unclassified),
        ]
        if self.baseline_created:
            lines.append("baseline: first run, established for %s; added 0" % self.report_date)
        else:
            lines.append(
                "baseline %s: added %d, removed %d"
                % (self.baseline_date, self.added, self.removed)
            )
        if self.failed_stocks:
            lines.append(
                "fetch incomplete: %d stocks failed; the report is marked incomplete"
                % self.failed_stocks
            )
        lines.append("dropped by fetch %d" % self.dropped)
        lines.append("markdown: %s" % self.markdown_path)
        lines.append("html:     %s" % self.html_path)
        return "\n".join(lines)


def parse_generated_at(value: str) -> str:
    """Validate ``--generated-at`` and keep it verbatim.

    Kept verbatim (not normalised) so the injected timestamp appears in the
    report exactly as the caller wrote it; normalising would make two spellings
    of the same instant produce different bytes for no benefit.
    """

    text = str(value).strip()
    if not text:
        raise UsageError("--generated-at needs an ISO-8601 timestamp")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        _dt.datetime.fromisoformat(candidate)
    except ValueError:
        raise UsageError(
            "--generated-at is not an ISO-8601 timestamp: %r" % (value,),
            hint="for example 2026-10-16T12:00:00Z",
        )
    return text


def report_dir(paths: Paths, report_date: str) -> Path:
    return paths.reports_dir / report_date


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_report(
    repository: ReportRepository,
    report_date: str,
    generated_at: str,
    labels: Optional[LabelTable] = None,
) -> Tuple[ReportDocument, str, str]:
    """Assemble and render. Returns ``(document, markdown, html)``.

    Pure with respect to the filesystem: nothing is written here, which is what
    lets the caller decide the output directory only after rendering succeeded.
    """

    rows = repository.rows_for_date(report_date)
    baseline_date = repository.latest_baseline_before(report_date)
    baseline_ids = repository.baseline_items(baseline_date) if baseline_date else []

    if not rows and baseline_date is None:
        unlabelled = repository.unlabelled_ids(report_date)
        if unlabelled:
            raise UsageError(
                "%d announcements for %s carry no classification"
                % (len(unlabelled), report_date),
                hint="run `cnwatch classify` first; the report renders stored "
                "labels and will not invent one",
            )
        elsewhere = repository.other_source_count(report_date)
        if elsewhere:
            raise UsageError(
                "no watchlist announcements for %s, but %d market-sweep rows exist"
                % (report_date, elsewhere),
                hint="`fetch --all` rows never enter the daily report (FR-18); "
                "run `cnwatch fetch` for the watchlist first",
            )
        raise UsageError(
            "no announcements for %s and no earlier report to compare against"
            % report_date,
            hint="run `cnwatch fetch` then `cnwatch classify` for that date first",
        )

    described = repository.describe_ids(baseline_ids)
    fetch = repository.latest_fetch_covering(report_date)
    dropped = repository.dropped_count(None if fetch is None else fetch.run_id)
    # Rows stored without a label cannot appear in either view (they have no
    # category or risk to render), so the report counts them explicitly rather
    # than showing a list that is quietly shorter than the database.
    unlabelled = len(repository.unlabelled_ids(report_date))

    document = build_document(
        report_date=report_date,
        generated_at=generated_at,
        rows=rows,
        labels=labels or load_label_table(),
        baseline_date=baseline_date,
        baseline_ids=baseline_ids,
        described=described,
        fetch=fetch,
        dropped=dropped,
        failed_codes=() if fetch is None else fetch.failed_codes,
        unlabelled=unlabelled,
    )
    return document, render_markdown(document), render_html(document)


def generate_report(
    paths: Paths,
    repository: ReportRepository,
    clock: Clock,
    report_date: Optional[str] = None,
    generated_at: Optional[str] = None,
    labels: Optional[LabelTable] = None,
) -> ReportOutcome:
    """Produce the two files and the database rows. Returns the outcome."""

    if report_date is None:
        day = clock.exchange_today().strftime("%Y-%m-%d")
    else:
        try:
            parse_date(report_date)
        except ClockError as exc:
            raise UsageError(str(exc))
        day = report_date

    stamp = clock.timestamp() if generated_at is None else parse_generated_at(generated_at)

    document, markdown_text, html_text = build_report(
        repository, day, stamp, labels=labels
    )

    # Rendering succeeded: from here on the output directory may be created.
    target = report_dir(paths, day)
    target.mkdir(parents=True, exist_ok=True)
    markdown_path = target / MARKDOWN_NAME
    html_path = target / HTML_NAME
    with open(str(markdown_path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown_text)
    with open(str(html_path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(html_text)

    summary = document.summary
    digest = _sha256(markdown_text)
    repository.record(
        report_date=day,
        generated_at=stamp,
        markdown_path=str(markdown_path),
        html_path=str(html_path),
        item_count=summary.item_count,
        baseline_created=summary.baseline_created,
        failed_stocks=summary.failed_stocks,
        content_sha256=digest,
        baseline_ids=[entry.announcement_id for entry in document.entries],
    )

    return ReportOutcome(
        report_date=day,
        generated_at=stamp,
        markdown_path=markdown_path,
        html_path=html_path,
        item_count=summary.item_count,
        baseline_created=summary.baseline_created,
        baseline_date=summary.baseline_date,
        added=summary.added,
        removed=summary.removed,
        high=summary.high,
        mid=summary.mid,
        low=summary.low,
        unclassified=summary.unclassified,
        failed_stocks=summary.failed_stocks,
        dropped=summary.dropped,
        content_sha256=digest,
    )


def report_dates(repository: ReportRepository) -> List[str]:
    """Report dates already recorded, oldest first. Used by docs and tests."""

    return repository.report_dates()


__all__ = [
    "HTML_NAME",
    "MARKDOWN_NAME",
    "ReportOutcome",
    "build_report",
    "generate_report",
    "parse_generated_at",
    "report_dir",
]
