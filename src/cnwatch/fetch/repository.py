"""Announcements, runs and failures: storage and the cursor query.

The insert is deliberately an upsert that only touches ``last_seen_at``: if the
upstream later rewrites a title, the stored history keeps what was first seen.
The run row is written before any request so an interrupted run is visible as
``running`` rather than invisible.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional, Tuple

from .models import Announcement

_INSERT_SQL = """
INSERT INTO announcements (
    announcement_id, code, name, title, announce_date,
    adjunct_url, adjunct_type, announcement_type, source_kind,
    first_seen_at, last_seen_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(announcement_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
"""


class FetchRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # -- cursor ------------------------------------------------------------ #

    def last_successful_window_end(self, source_kind: str = "watchlist") -> Optional[str]:
        """Most recent ``window_end`` of a complete or degraded watchlist run.

        ``failed`` and ``running`` runs are excluded: advancing the cursor past
        a run that did not finish would create a permanent blind spot.
        """

        row = self._connection.execute(
            """
            SELECT window_end FROM fetch_runs
            WHERE source_kind = ? AND status IN ('complete', 'degraded')
            ORDER BY window_end DESC, run_id DESC
            LIMIT 1
            """,
            (source_kind,),
        ).fetchone()
        return None if row is None else str(row["window_end"])

    def has_successful_run(self, source_kind: str = "watchlist") -> bool:
        return self.last_successful_window_end(source_kind) is not None

    # -- runs -------------------------------------------------------------- #

    def start_run(
        self,
        started_at: str,
        window_start: str,
        window_end: str,
        source_kind: str,
        stocks_total: int,
        baseline_created: bool,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO fetch_runs
                (started_at, window_start, window_end, source_kind,
                 stocks_total, baseline_created, status)
            VALUES (?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                started_at,
                window_start,
                window_end,
                source_kind,
                stocks_total,
                1 if baseline_created else 0,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        finished_at: str,
        status: str,
        stocks_ok: int,
        stocks_failed: int,
        requests: int,
        pages: int,
        inserted: int,
        dropped: int,
    ) -> None:
        self._connection.execute(
            """
            UPDATE fetch_runs SET
                finished_at = ?, status = ?, stocks_ok = ?, stocks_failed = ?,
                requests = ?, pages = ?, inserted = ?, dropped = ?
            WHERE run_id = ?
            """,
            (
                finished_at,
                status,
                stocks_ok,
                stocks_failed,
                requests,
                pages,
                inserted,
                dropped,
                run_id,
            ),
        )
        self._connection.commit()

    def record_failure(
        self, run_id: int, code: str, attempts: int, last_error: str
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO fetch_failures (run_id, code, attempts, last_error)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, code) DO UPDATE SET
                attempts = excluded.attempts,
                last_error = excluded.last_error
            """,
            (run_id, code, attempts, last_error),
        )
        self._connection.commit()

    def run(self, run_id: int) -> Optional[sqlite3.Row]:
        return self._connection.execute(
            "SELECT * FROM fetch_runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def failures(self, run_id: int) -> List[sqlite3.Row]:
        return self._connection.execute(
            "SELECT code, attempts, last_error FROM fetch_failures "
            "WHERE run_id = ? ORDER BY code",
            (run_id,),
        ).fetchall()

    # -- announcements ----------------------------------------------------- #

    def store(self, announcements: List[Announcement], seen_at: str) -> Tuple[int, int]:
        """Insert-or-touch. Returns ``(inserted, already_present)``.

        Existence is checked inside the same transaction as the write, so the
        counts describe this database state rather than a racing one. The
        transaction is per stock: one failed stock must not roll back the
        stocks that already succeeded.
        """

        inserted = 0
        existing = 0
        try:
            for item in announcements:
                known = self._connection.execute(
                    "SELECT 1 FROM announcements WHERE announcement_id = ?",
                    (item.announcement_id,),
                ).fetchone()
                self._connection.execute(
                    _INSERT_SQL,
                    (
                        item.announcement_id,
                        item.code,
                        item.name,
                        item.title,
                        item.announce_date,
                        item.adjunct_url,
                        item.adjunct_type,
                        item.announcement_type,
                        item.source_kind,
                        seen_at,
                        seen_at,
                    ),
                )
                if known is None:
                    inserted += 1
                else:
                    existing += 1
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return inserted, existing

    def count(self) -> int:
        return int(
            self._connection.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        )

    def ids_for_code(self, code: str, start: str, end: str) -> List[str]:
        rows = self._connection.execute(
            "SELECT announcement_id FROM announcements "
            "WHERE code = ? AND announce_date BETWEEN ? AND ? "
            "ORDER BY announcement_id",
            (code, start, end),
        ).fetchall()
        return [str(row["announcement_id"]) for row in rows]
