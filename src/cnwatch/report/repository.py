"""Reads and writes for the report layer.

Three reads (today's labelled rows, one prior baseline, the last fetch run) and
one transactional write (run row + today's baseline ids).

The read joins ``announcements`` to ``classifications`` with an **inner** join.
A row that was never classified is not silently reported as ``low``: it would
be a bug in the classify step, and a report that quietly invents a risk level
for it is worse than a report that omits it. ``unclassified_ids`` exists so the
caller can notice such rows and say so.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..report.models import UNCLASSIFIED


@dataclass(frozen=True)
class ReportRow:
    """A joined announcement + classification row, as stored."""

    announcement_id: str
    code: str
    name: str
    title: str
    announce_date: str
    adjunct_url: str
    category: str
    tags: Tuple[str, ...]
    risk_level: str
    rules_version: str


@dataclass(frozen=True)
class BaselineRow:
    announcement_id: str
    title: str
    announce_date: str


@dataclass(frozen=True)
class FetchSnapshot:
    """The most recent watchlist fetch run, as the report header needs it."""

    run_id: int
    window_start: str
    window_end: str
    status: str
    stocks_total: int
    stocks_failed: int
    failed_codes: Tuple[str, ...]


def _parse_tags(raw: str) -> Tuple[str, ...]:
    import json

    try:
        decoded = json.loads(raw or "[]")
    except ValueError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(item) for item in decoded)


class ReportRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # -- reads -------------------------------------------------------------- #

    def rows_for_date(self, report_date: str, source_kind: str = "watchlist") -> List[ReportRow]:
        """Every classified announcement dated exactly ``report_date``.

        Scoped to ``source_kind`` (FR-18): a ``fetch --all`` sweep stores the
        whole market under ``market``, and mixing 1300 market rows into a
        20-stock watchlist report would bury the very items the report exists
        to surface. The market sweep is a fallback, not a second report feed.

        Ordered by ``(announce_date desc, announcement_id)`` so the database
        returns a stable sequence; the renderers apply the risk-first key.
        """

        rows = self._connection.execute(
            """
            SELECT a.announcement_id, a.code, a.name, a.title, a.announce_date,
                   a.adjunct_url, c.category, c.tags_json, c.risk_level,
                   c.rules_version
            FROM announcements a
            JOIN classifications c ON c.announcement_id = a.announcement_id
            WHERE a.announce_date = ? AND a.source_kind = ?
            ORDER BY a.announce_date DESC, a.announcement_id ASC
            """,
            (report_date, source_kind),
        ).fetchall()
        return [
            ReportRow(
                announcement_id=str(row["announcement_id"]),
                code=str(row["code"]),
                name=str(row["name"]),
                title=str(row["title"]),
                announce_date=str(row["announce_date"]),
                adjunct_url=str(row["adjunct_url"]),
                category=str(row["category"]),
                tags=_parse_tags(str(row["tags_json"])),
                risk_level=str(row["risk_level"]),
                rules_version=str(row["rules_version"]),
            )
            for row in rows
        ]

    def other_source_count(self, report_date: str, source_kind: str = "watchlist") -> int:
        """Rows on the date from the other source, to explain an empty report."""

        row = self._connection.execute(
            "SELECT COUNT(*) FROM announcements "
            "WHERE announce_date = ? AND source_kind != ?",
            (report_date, source_kind),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def unlabelled_ids(self, report_date: str) -> List[str]:
        """Watchlist announcements on the date with no classification row."""

        rows = self._connection.execute(
            """
            SELECT a.announcement_id
            FROM announcements a
            LEFT JOIN classifications c
                   ON c.announcement_id = a.announcement_id
            WHERE a.announce_date = ? AND a.source_kind = 'watchlist'
              AND c.announcement_id IS NULL
            ORDER BY a.announcement_id
            """,
            (report_date,),
        ).fetchall()
        return [str(row["announcement_id"]) for row in rows]

    def latest_baseline_before(self, report_date: str) -> Optional[str]:
        """The most recent report date strictly before ``report_date``."""

        row = self._connection.execute(
            "SELECT MAX(report_date) FROM report_baseline_items WHERE report_date < ?",
            (report_date,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    def baseline_items(self, report_date: str) -> List[str]:
        rows = self._connection.execute(
            "SELECT announcement_id FROM report_baseline_items "
            "WHERE report_date = ? ORDER BY announcement_id",
            (report_date,),
        ).fetchall()
        return [str(row["announcement_id"]) for row in rows]

    def describe_ids(self, announcement_ids: Sequence[str]) -> Dict[str, BaselineRow]:
        """Title and date for baseline ids, including ones since removed."""

        wanted = [str(item) for item in announcement_ids]
        if not wanted:
            return {}
        marks = ",".join("?" for _ in wanted)
        rows = self._connection.execute(
            "SELECT announcement_id, title, announce_date FROM announcements "
            "WHERE announcement_id IN (%s)" % marks,
            wanted,
        ).fetchall()
        return {
            str(row["announcement_id"]): BaselineRow(
                announcement_id=str(row["announcement_id"]),
                title=str(row["title"]),
                announce_date=str(row["announce_date"]),
            )
            for row in rows
        }

    def latest_fetch_covering(self, report_date: str) -> Optional[FetchSnapshot]:
        """The watchlist run whose window covers ``report_date``, else the latest.

        A report for a past date must describe the run that actually covered
        that date, not whatever ran most recently — otherwise regenerating an
        old report would borrow today's failure list. When no run's window
        covers the date (the common case for a replayed snapshot day), the most
        recent run is the best available context and is used.

        Whether a stock failed is a fetch outcome; the report only reports it.
        """

        row = self._connection.execute(
            """
            SELECT run_id, window_start, window_end, status,
                   stocks_total, stocks_failed
            FROM fetch_runs
            WHERE source_kind = 'watchlist'
              AND window_start <= ? AND window_end >= ?
            ORDER BY run_id DESC
            LIMIT 1
            """,
            (report_date, report_date),
        ).fetchone()
        if row is None:
            row = self._connection.execute(
                """
                SELECT run_id, window_start, window_end, status,
                       stocks_total, stocks_failed
                FROM fetch_runs
                WHERE source_kind = 'watchlist'
                ORDER BY run_id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        failures = self._connection.execute(
            "SELECT code FROM fetch_failures WHERE run_id = ? ORDER BY code",
            (int(row["run_id"]),),
        ).fetchall()
        return FetchSnapshot(
            run_id=int(row["run_id"]),
            window_start=str(row["window_start"]),
            window_end=str(row["window_end"]),
            status=str(row["status"]),
            stocks_total=int(row["stocks_total"]),
            stocks_failed=int(row["stocks_failed"]),
            failed_codes=tuple(str(item["code"]) for item in failures),
        )

    def dropped_count(self, run_id: Optional[int]) -> int:
        if run_id is None:
            return 0
        row = self._connection.execute(
            "SELECT dropped FROM fetch_runs WHERE run_id = ?", (int(run_id),)
        ).fetchone()
        return 0 if row is None else int(row["dropped"])

    def existing_run(self, report_date: str) -> Optional[sqlite3.Row]:
        return self._connection.execute(
            "SELECT * FROM report_runs WHERE report_date = ?", (report_date,)
        ).fetchone()

    def report_dates(self) -> List[str]:
        rows = self._connection.execute(
            "SELECT report_date FROM report_runs ORDER BY report_date"
        ).fetchall()
        return [str(row["report_date"]) for row in rows]

    def database_exists(self) -> bool:
        row = self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'announcements'"
        ).fetchone()
        return row is not None

    # -- write -------------------------------------------------------------- #

    def record(
        self,
        report_date: str,
        generated_at: str,
        markdown_path: str,
        html_path: str,
        item_count: int,
        baseline_created: bool,
        failed_stocks: int,
        content_sha256: str,
        baseline_ids: Sequence[str],
    ) -> None:
        """Persist the run row and today's baseline ids in one transaction.

        Both halves describe the same report. Writing them separately would let
        a crash leave a run row whose baseline was never stored, which the next
        day would then read as "first run" and silently report zero new items.
        """

        try:
            self._connection.execute(
                """
                INSERT INTO report_runs (
                    report_date, generated_at, markdown_path, html_path,
                    item_count, baseline_created, failed_stocks, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_date) DO UPDATE SET
                    generated_at     = excluded.generated_at,
                    markdown_path    = excluded.markdown_path,
                    html_path        = excluded.html_path,
                    item_count       = excluded.item_count,
                    baseline_created = excluded.baseline_created,
                    failed_stocks    = excluded.failed_stocks,
                    content_sha256   = excluded.content_sha256
                """,
                (
                    report_date,
                    generated_at,
                    markdown_path,
                    html_path,
                    int(item_count),
                    1 if baseline_created else 0,
                    int(failed_stocks),
                    content_sha256,
                ),
            )
            self._connection.execute(
                "DELETE FROM report_baseline_items WHERE report_date = ?",
                (report_date,),
            )
            self._connection.executemany(
                "INSERT INTO report_baseline_items (report_date, announcement_id) "
                "VALUES (?, ?)",
                [(report_date, str(item)) for item in baseline_ids],
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise


__all__ = [
    "BaselineRow",
    "FetchSnapshot",
    "ReportRepository",
    "ReportRow",
    "UNCLASSIFIED",
]
