"""Reads and writes for the archive layer.

Target selection is a *read* over rows the fetch and classify steps already
stored. The archive layer never creates an announcement, never labels one and
never touches ``classifications``: if a row is unlabelled, a ``--risk`` or
``--category`` filter simply cannot see it (the join is an inner join), and
naming it with ``--announcement-id`` still works, because the user pointed at
it explicitly.

Filters are scoped to ``source_kind='watchlist'`` for the same reason the daily
report is (FR-18): ``fetch --all`` stores the whole market, and a ``--risk high``
filter that silently reached it would start downloading thousands of filings.
An explicit ``--announcement-id`` is not scope-limited — the user named the row.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Sequence

from ..fetch.models import source_url_for
from .models import ArchiveRecord, ArchiveTarget


class ArchiveRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # -- target selection --------------------------------------------------- #

    def targets(
        self,
        announcement_ids: Sequence[str] = (),
        codes: Sequence[str] = (),
        category: Optional[str] = None,
        risk: Optional[str] = None,
        watchlist_only: bool = True,
    ) -> List[ArchiveTarget]:
        """Rows matching every supplied filter, in a stable order."""

        clauses = []
        params: List[object] = []
        named = [str(item) for item in announcement_ids]

        if named:
            clauses.append("a.announcement_id IN (%s)" % ",".join("?" for _ in named))
            params.extend(named)
        if codes:
            wanted = [str(item) for item in codes]
            clauses.append("a.code IN (%s)" % ",".join("?" for _ in wanted))
            params.extend(wanted)
        if category is not None:
            clauses.append("c.category = ?")
            params.append(str(category))
        if risk is not None:
            clauses.append("c.risk_level = ?")
            params.append(str(risk))
        if watchlist_only and not named:
            clauses.append("a.source_kind = 'watchlist'")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._connection.execute(
            """
            SELECT a.announcement_id, a.code, a.name, a.title, a.announce_date,
                   a.adjunct_url, c.category, c.risk_level
            FROM announcements a
            LEFT JOIN classifications c ON c.announcement_id = a.announcement_id
            %s
            ORDER BY a.code ASC, a.announce_date ASC, a.announcement_id ASC
            """
            % where,
            params,
        ).fetchall()
        return [
            ArchiveTarget(
                announcement_id=str(row["announcement_id"]),
                code=str(row["code"]),
                name=str(row["name"]),
                title=str(row["title"]),
                announce_date=str(row["announce_date"]),
                source_url=source_url_for(str(row["adjunct_url"])),
                category=str(row["category"] or ""),
                risk_level=str(row["risk_level"] or ""),
            )
            for row in rows
        ]

    # -- archive_records ---------------------------------------------------- #

    def existing(self, announcement_id: str) -> Optional[ArchiveRecord]:
        row = self._connection.execute(
            """
            SELECT announcement_id, local_path, byte_size, downloaded_at,
                   text_status, text_excerpt
            FROM archive_records WHERE announcement_id = ?
            """,
            (str(announcement_id),),
        ).fetchone()
        if row is None:
            return None
        return ArchiveRecord(
            announcement_id=str(row["announcement_id"]),
            local_path=str(row["local_path"]),
            byte_size=int(row["byte_size"]),
            downloaded_at=str(row["downloaded_at"]),
            text_status=str(row["text_status"]),
            text_excerpt=str(row["text_excerpt"]),
        )

    def records(self) -> List[ArchiveRecord]:
        rows = self._connection.execute(
            "SELECT announcement_id, local_path, byte_size, downloaded_at, "
            "text_status, text_excerpt FROM archive_records ORDER BY announcement_id"
        ).fetchall()
        return [
            ArchiveRecord(
                announcement_id=str(row["announcement_id"]),
                local_path=str(row["local_path"]),
                byte_size=int(row["byte_size"]),
                downloaded_at=str(row["downloaded_at"]),
                text_status=str(row["text_status"]),
                text_excerpt=str(row["text_excerpt"]),
            )
            for row in rows
        ]

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM archive_records").fetchone()
        return 0 if row is None else int(row[0])

    def store(self, record: ArchiveRecord) -> None:
        """Insert or update one row. ``announcement_id`` stays the primary key."""

        self._connection.execute(
            """
            INSERT INTO archive_records (
                announcement_id, local_path, byte_size, downloaded_at,
                text_status, text_excerpt
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(announcement_id) DO UPDATE SET
                local_path    = excluded.local_path,
                byte_size     = excluded.byte_size,
                downloaded_at = excluded.downloaded_at,
                text_status   = excluded.text_status,
                text_excerpt  = excluded.text_excerpt
            """,
            (
                record.announcement_id,
                record.local_path,
                int(record.byte_size),
                record.downloaded_at,
                record.text_status,
                record.text_excerpt,
            ),
        )
        self._connection.commit()

    def counts(self) -> Dict[str, int]:
        row = self._connection.execute(
            "SELECT COUNT(*) AS records, "
            "SUM(CASE WHEN text_status = 'extracted' THEN 1 ELSE 0 END) AS extracted "
            "FROM archive_records"
        ).fetchone()
        if row is None:
            return {"records": 0, "extracted": 0}
        return {
            "records": int(row["records"] or 0),
            "extracted": int(row["extracted"] or 0),
        }


__all__ = ["ArchiveRepository"]
