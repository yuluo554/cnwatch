"""Storage for ``classifications``.

Two reads and one write, and the write is the interesting one.

``pending`` selects rows that have no classification yet **or** whose stored
``rules_version`` is stale, which is what makes "upgrade the rule package, then
re-run ``classify``" work without a manual reset. ``replace`` upserts a whole
batch in one transaction: a run either lands entirely or leaves the previous
labels untouched, so an interrupted classify cannot leave half a day labelled
with two different rule versions.

``classified_at`` is passed in by the caller, which owns the clock.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

_WRITE_SQL = """
INSERT INTO classifications (
    announcement_id, category, tags_json, risk_level, rules_version, classified_at
) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(announcement_id) DO UPDATE SET
    category      = excluded.category,
    tags_json     = excluded.tags_json,
    risk_level    = excluded.risk_level,
    rules_version = excluded.rules_version,
    classified_at = excluded.classified_at
"""


@dataclass(frozen=True)
class PendingRow:
    announcement_id: str
    code: str
    title: str
    announce_date: str
    stored_version: Optional[str]

    @property
    def is_stale(self) -> bool:
        return self.stored_version is not None


def _parse_tags(raw: str) -> Tuple[str, ...]:
    try:
        decoded = json.loads(raw or "[]")
    except ValueError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(item) for item in decoded)


@dataclass(frozen=True)
class StoredClassification:
    announcement_id: str
    category: str
    tags: Tuple[str, ...]
    risk_level: str
    rules_version: str
    classified_at: str


class ClassifyRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # -- reads -------------------------------------------------------------- #

    def pending(
        self,
        rules_version: str,
        since: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[PendingRow]:
        """Rows needing a label, oldest announcement first.

        Ordering is by ``(announce_date, announcement_id)`` so a run over the
        same database always writes in the same order; it is also the order the
        log reads best in.
        """

        sql = [
            "SELECT a.announcement_id, a.code, a.title, a.announce_date,",
            "       c.rules_version AS stored_version",
            "FROM announcements a",
            "LEFT JOIN classifications c",
            "       ON c.announcement_id = a.announcement_id",
            "WHERE (c.announcement_id IS NULL OR c.rules_version IS NOT ?)",
        ]
        params: List[object] = [rules_version]
        if since:
            sql.append("AND a.announce_date >= ?")
            params.append(since)
        sql.append("ORDER BY a.announce_date, a.announcement_id")
        if limit is not None:
            sql.append("LIMIT ?")
            params.append(int(limit))

        rows = self._connection.execute(" ".join(sql), params).fetchall()
        return [
            PendingRow(
                announcement_id=str(row["announcement_id"]),
                code=str(row["code"]),
                title=str(row["title"]),
                announce_date=str(row["announce_date"]),
                stored_version=None
                if row["stored_version"] is None
                else str(row["stored_version"]),
            )
            for row in rows
        ]

    def counts(self) -> dict:
        total = int(
            self._connection.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        )
        classified = int(
            self._connection.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
        )
        return {"announcements": total, "classifications": classified}

    def get(self, announcement_id: str) -> Optional[StoredClassification]:
        row = self._connection.execute(
            "SELECT * FROM classifications WHERE announcement_id = ?",
            (announcement_id,),
        ).fetchone()
        if row is None:
            return None
        return StoredClassification(
            announcement_id=str(row["announcement_id"]),
            category=str(row["category"]),
            tags=_parse_tags(str(row["tags_json"])),
            risk_level=str(row["risk_level"]),
            rules_version=str(row["rules_version"]),
            classified_at=str(row["classified_at"]),
        )

    def all(self) -> List[StoredClassification]:
        rows = self._connection.execute(
            "SELECT * FROM classifications ORDER BY announcement_id"
        ).fetchall()
        return [
            StoredClassification(
                announcement_id=str(row["announcement_id"]),
                category=str(row["category"]),
                tags=_parse_tags(str(row["tags_json"])),
                risk_level=str(row["risk_level"]),
                rules_version=str(row["rules_version"]),
                classified_at=str(row["classified_at"]),
            )
            for row in rows
        ]

    def rows_for_categories(self, categories: Iterable[str]) -> List[str]:
        wanted = list(categories)
        if not wanted:
            return []
        marks = ",".join("?" for _ in wanted)
        rows = self._connection.execute(
            "SELECT announcement_id FROM classifications "
            "WHERE category IN (%s) ORDER BY announcement_id" % marks,
            wanted,
        ).fetchall()
        return [str(row["announcement_id"]) for row in rows]

    # -- write -------------------------------------------------------------- #

    def replace(
        self, records: Sequence[Tuple[str, str, Tuple[str, ...], str, str, str]]
    ) -> int:
        """Upsert ``(announcement_id, category, tags, risk, version, at)`` rows.

        One transaction for the batch: a failure mid-run must not leave the
        database with a mix of old and new labels for the same rule version.
        """

        try:
            for announcement_id, category, tags, risk_level, version, at in records:
                payload = json.dumps(list(tags), ensure_ascii=False, separators=(",", ":"))
                self._connection.execute(
                    _WRITE_SQL,
                    (announcement_id, category, payload, risk_level, version, at),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return len(records)
