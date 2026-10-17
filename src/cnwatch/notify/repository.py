"""The read the notification layer needs: one day's stored items, risk first.

Read-only by construction — there is no write method here, which is what makes
"通知失败不影响日报与 DB" (FR-43) structural rather than a promise. The join is
the same inner join the report uses, and it is scoped to
``source_kind='watchlist'`` for the same reason (FR-18): an alert about the
whole market is not an alert.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List

_RISK_ORDER = "CASE c.risk_level WHEN 'high' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END"


@dataclass(frozen=True)
class AlertRow:
    announcement_id: str
    code: str
    name: str
    title: str
    announce_date: str
    category: str
    risk_level: str


class NotifyRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def rows_for_day(self, day: str, source_kind: str = "watchlist") -> List[AlertRow]:
        rows = self._connection.execute(
            """
            SELECT a.announcement_id, a.code, a.name, a.title, a.announce_date,
                   c.category, c.risk_level
            FROM announcements a
            JOIN classifications c ON c.announcement_id = a.announcement_id
            WHERE a.announce_date = ? AND a.source_kind = ?
            ORDER BY %s ASC, a.announce_date DESC, a.announcement_id ASC
            """
            % _RISK_ORDER,
            (day, source_kind),
        ).fetchall()
        return [
            AlertRow(
                announcement_id=str(row["announcement_id"]),
                code=str(row["code"]),
                name=str(row["name"]),
                title=str(row["title"]),
                announce_date=str(row["announce_date"]),
                category=str(row["category"]),
                risk_level=str(row["risk_level"]),
            )
            for row in rows
        ]

    def checkpoint(self) -> Dict[str, int]:
        """Row counts, so a caller (or a test) can show a failed send changed nothing."""

        row = self._connection.execute(
            "SELECT (SELECT COUNT(*) FROM announcements) AS announcements, "
            "(SELECT COUNT(*) FROM classifications) AS classifications, "
            "(SELECT COUNT(*) FROM archive_records) AS archive_records, "
            "(SELECT COUNT(*) FROM report_runs) AS report_runs"
        ).fetchone()
        if row is None:
            return {"announcements": 0, "classifications": 0, "archive_records": 0, "report_runs": 0}
        return {
            "announcements": int(row["announcements"]),
            "classifications": int(row["classifications"]),
            "archive_records": int(row["archive_records"]),
            "report_runs": int(row["report_runs"]),
        }


__all__ = ["AlertRow", "NotifyRepository"]
