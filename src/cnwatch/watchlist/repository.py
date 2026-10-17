"""SQLite access for the watchlist tables.

All writes go through this repository so that the data row and its audit row are
written in the same transaction — a change that cannot be audited must not
happen. The timestamp is always passed in by the caller, which owns the clock.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from .models import AuditRecord, WatchlistEntry

_ENTRY_COLUMNS = (
    "code, name, pinyin, group_name, org_id, added_at, source, note, in_stock_master"
)


def entry_from_row(row: sqlite3.Row) -> WatchlistEntry:
    return WatchlistEntry(
        code=str(row["code"]),
        name=str(row["name"]),
        pinyin=str(row["pinyin"]),
        group=str(row["group_name"]),
        org_id=str(row["org_id"]),
        added_at=str(row["added_at"]),
        source=str(row["source"]),
        note=str(row["note"]),
        in_stock_master=bool(row["in_stock_master"]),
    )


class WatchlistRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # -- reads ------------------------------------------------------------- #

    def all(self) -> List[WatchlistEntry]:
        rows = self._connection.execute(
            "SELECT %s FROM watchlist ORDER BY group_name, code" % _ENTRY_COLUMNS
        ).fetchall()
        return [entry_from_row(row) for row in rows]

    def get(self, code: str) -> Optional[WatchlistEntry]:
        row = self._connection.execute(
            "SELECT %s FROM watchlist WHERE code = ?" % _ENTRY_COLUMNS, (code,)
        ).fetchone()
        return None if row is None else entry_from_row(row)

    def codes(self) -> List[str]:
        rows = self._connection.execute(
            "SELECT code FROM watchlist ORDER BY code"
        ).fetchall()
        return [str(row["code"]) for row in rows]

    def fetchables(self) -> List[WatchlistEntry]:
        """Rows the fetch layer may request: present in the current stock master."""

        rows = self._connection.execute(
            "SELECT %s FROM watchlist WHERE in_stock_master = 1 ORDER BY code"
            % _ENTRY_COLUMNS
        ).fetchall()
        return [entry_from_row(row) for row in rows]

    def search(self, query: str) -> List[WatchlistEntry]:
        """Three-tier search; the ordering rule lives in ``match_score``."""

        needle = (query or "").strip()
        if not needle:
            return []
        upper = needle.upper()
        lowered = needle.lower()
        scored = []
        for entry in self.all():
            score = match_score(entry, upper, lowered)
            if score is not None:
                scored.append((score, entry.code, entry))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in scored]

    def audit_for(self, code: str) -> List[AuditRecord]:
        rows = self._connection.execute(
            "SELECT audit_id, at, action, code, old_value, new_value, source "
            "FROM watchlist_audit WHERE code = ? ORDER BY audit_id",
            (code,),
        ).fetchall()
        return [
            AuditRecord(
                audit_id=int(row["audit_id"]),
                at=str(row["at"]),
                action=str(row["action"]),
                code=str(row["code"]),
                old_value=str(row["old_value"] or ""),
                new_value=str(row["new_value"] or ""),
                source=str(row["source"]),
            )
            for row in rows
        ]

    # -- writes ------------------------------------------------------------ #

    def upsert(
        self,
        entry: WatchlistEntry,
        at: str,
        action: str,
        old_value: str = "",
        new_value: str = "",
        audit: bool = True,
    ) -> None:
        if action not in ("add", "remove", "edit", "import"):
            raise ValueError("unsupported audit action %r" % (action,))
        self._connection.execute(
            """
            INSERT INTO watchlist
                (code, name, pinyin, group_name, org_id, added_at, source, note, in_stock_master)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                pinyin = excluded.pinyin,
                group_name = excluded.group_name,
                org_id = excluded.org_id,
                note = excluded.note,
                in_stock_master = excluded.in_stock_master
            """,
            (
                entry.code,
                entry.name,
                entry.pinyin,
                entry.group,
                entry.org_id,
                entry.added_at,
                entry.source,
                entry.note,
                1 if entry.in_stock_master else 0,
            ),
        )
        if audit:
            self._write_audit(at, action, entry.code, old_value, new_value, entry.source)

    def delete(self, code: str, at: str, source: str, snapshot: str) -> None:
        self._connection.execute("DELETE FROM watchlist WHERE code = ?", (code,))
        self._write_audit(at, "remove", code, snapshot, "", source)

    def _write_audit(
        self,
        at: str,
        action: str,
        code: str,
        old_value: str,
        new_value: str,
        source: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO watchlist_audit
                (at, action, code, old_value, new_value, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (at, action, code, old_value, new_value, source),
        )

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()


def match_score(entry: WatchlistEntry, upper: str, lowered: str) -> Optional[int]:
    """Search tiers: 0 = code prefix, 1 = name contains, 2 = pinyin prefix.

    Lower score sorts first; ties break by ascending code. The rule is fixed so
    that the same query always returns the same order (FR-02).
    """

    if entry.code.startswith(upper):
        return 0
    if lowered in entry.name.lower():
        return 1
    if entry.pinyin and entry.pinyin.lower().startswith(lowered):
        return 2
    return None
