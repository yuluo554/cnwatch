"""Watchlist behaviour: master lookup, mutation, audit, and master refresh.

Every ``orgId`` here comes from :func:`load_stock_master`; there is no branch
that computes one from a code. A code missing from the master is rejected with
a named reason rather than being stored with a guessed identifier, because a
wrong ``orgId`` makes the fetch silently return another company's filings.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from ..clock import Clock
from ..errors import PartialFailure, UsageError
from .models import (
    AuditRecord,
    ImportOutcome,
    StockMaster,
    WatchlistEntry,
    validate_code,
    validate_group,
)
from .repository import WatchlistRepository

MASTER_HEADER = ("code", "pinyin", "category", "orgId", "zwjc")
IMPORT_REQUIRED = ("code", "group")


def load_stock_master(path: Path) -> Dict[str, StockMaster]:
    """``code -> StockMaster`` from the frozen CSV ledger.

    The ledger is committed evidence, so a malformed row is a defect in the
    repository rather than user error: it aborts with a message that names the
    line instead of quietly shrinking the lookup table.
    """

    master_path = Path(path)
    if not master_path.is_file():
        raise UsageError(
            "stock master not found: %s" % master_path,
            hint="run `cnwatch watchlist sync-master` against a checkout, "
            "or pass --data-dir",
        )
    mapping: Dict[str, StockMaster] = {}
    with master_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in MASTER_HEADER if name not in (reader.fieldnames or [])]
        if missing:
            raise UsageError(
                "stock master %s is missing columns: %s"
                % (master_path, ", ".join(missing))
            )
        for line_no, row in enumerate(reader, start=2):
            code = (row.get("code") or "").strip()
            org_id = (row.get("orgId") or "").strip()
            if not code or not org_id:
                raise UsageError(
                    "stock master line %d has an empty code or orgId" % line_no
                )
            mapping[code.upper()] = StockMaster(
                code=code.upper(),
                pinyin=(row.get("pinyin") or "").strip(),
                category=(row.get("category") or "").strip(),
                org_id=org_id,
                name=(row.get("zwjc") or "").strip(),
            )
    return mapping


def import_rows_from_csv(path: Path) -> List[dict]:
    """Read a user CSV: required ``code,group``; optional ``name,note,org_id``.

    File-level problems (missing column, non-UTF-8, empty file) raise, and no
    row is imported. Row-level problems are returned to the service so the rest
    of the file can still land (FR-01).
    """

    csv_path = Path(path)
    if not csv_path.is_file():
        raise UsageError("CSV not found: %s" % csv_path)
    try:
        raw = csv_path.read_bytes()
    except OSError as exc:
        raise UsageError("cannot read %s: %s" % (csv_path, exc))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UsageError(
            "%s is not valid UTF-8: %s" % (csv_path, exc),
            hint="re-save the file as UTF-8 (Excel: 另存为 → CSV UTF-8)",
        )
    # Comment lines are documented in data/fixtures/watchlist-sample.csv; strip
    # only leading ones so a '#' inside a note stays intact.
    lines = []
    for line in text.splitlines():
        if line.startswith("#") and not lines:
            continue
        lines.append(line)
    body = "\n".join(lines).strip()
    if not body:
        raise UsageError("%s contains no data rows" % csv_path)

    reader = csv.DictReader(body.splitlines())
    fields = [name.strip() for name in (reader.fieldnames or [])]
    missing = [name for name in IMPORT_REQUIRED if name not in fields]
    if missing:
        raise UsageError(
            "%s is missing required columns: %s" % (csv_path, ", ".join(missing)),
            hint="required: code, group; optional: name, note, org_id",
        )
    rows = []
    for row in reader:
        rows.append({(key or "").strip(): (value or "").strip() for key, value in row.items()})
    if not rows:
        raise UsageError("%s contains no data rows" % csv_path)
    return rows


class WatchlistService:
    """Transactional watchlist operations. Prints nothing; returns outcomes."""

    def __init__(
        self,
        repository: WatchlistRepository,
        master: Dict[str, StockMaster],
        clock: Clock,
        source: str = "manual",
    ) -> None:
        self._repository = repository
        self._master = master
        self._clock = clock
        self._source = source

    # -- queries ----------------------------------------------------------- #

    def list_entries(self, group: Optional[str] = None) -> List[WatchlistEntry]:
        entries = self._repository.all()
        if group is None:
            return entries
        wanted = validate_group(group)
        return [entry for entry in entries if entry.group == wanted]

    def fetchable_entries(self) -> List[WatchlistEntry]:
        """Entries the fetch layer may request (``in_stock_master = 1``)."""

        return self._repository.fetchables()

    def search(self, query: str) -> List[WatchlistEntry]:
        return self._repository.search(query)

    def audit(self, code: str) -> List[AuditRecord]:
        return self._repository.audit_for(validate_code(code))

    def count(self) -> int:
        return len(self._repository.codes())

    # -- mutations --------------------------------------------------------- #

    def add(
        self,
        code: str,
        group: str,
        note: str = "",
        name: Optional[str] = None,
        update: bool = False,
    ) -> WatchlistEntry:
        normalised = validate_code(code)
        group_name = validate_group(group)
        existing = self._repository.get(normalised)
        if existing is not None and not update:
            raise UsageError(
                "stock %s is already on the watchlist" % normalised,
                hint="pass --update to overwrite the group or note",
            )
        entry = self._entry_from_master(normalised, group_name, note, name)
        old = ""
        if existing is not None:
            old = json.dumps(
                {"group": existing.group, "note": existing.note}, ensure_ascii=False
            )
        new = json.dumps({"group": entry.group, "note": entry.note}, ensure_ascii=False)
        try:
            self._repository.upsert(
                entry, self._clock.timestamp(), "edit" if existing else "add", old, new
            )
            self._repository.commit()
        except Exception:
            self._repository.rollback()
            raise
        return entry

    def remove(self, code: str) -> WatchlistEntry:
        normalised = validate_code(code)
        existing = self._repository.get(normalised)
        if existing is None:
            raise UsageError("stock %s is not on the watchlist" % normalised)
        snapshot = json.dumps(
            {
                "name": existing.name,
                "group": existing.group,
                "org_id": existing.org_id,
                "note": existing.note,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            self._repository.delete(
                normalised, self._clock.timestamp(), self._source, snapshot
            )
            self._repository.commit()
        except Exception:
            self._repository.rollback()
            raise
        return existing

    def edit(
        self,
        code: str,
        group: Optional[str] = None,
        note: Optional[str] = None,
    ) -> WatchlistEntry:
        normalised = validate_code(code)
        existing = self._repository.get(normalised)
        if existing is None:
            raise UsageError("stock %s is not on the watchlist" % normalised)
        if group is None and note is None:
            raise UsageError(
                "nothing to edit",
                hint="pass --group, --note, or both",
            )
        new_group = existing.group if group is None else validate_group(group)
        new_note = existing.note if note is None else note
        if new_group == existing.group and new_note == existing.note:
            return existing
        updated = WatchlistEntry(
            code=existing.code,
            name=existing.name,
            pinyin=existing.pinyin,
            group=new_group,
            org_id=existing.org_id,
            added_at=existing.added_at,
            source=existing.source,
            note=new_note,
            in_stock_master=existing.in_stock_master,
        )
        old = json.dumps(
            {"group": existing.group, "note": existing.note}, ensure_ascii=False
        )
        new = json.dumps({"group": new_group, "note": new_note}, ensure_ascii=False)
        try:
            self._repository.upsert(updated, self._clock.timestamp(), "edit", old, new)
            self._repository.commit()
        except Exception:
            self._repository.rollback()
            raise
        return updated

    def import_csv(self, path: Path) -> ImportOutcome:
        rows = import_rows_from_csv(path)
        return self.import_rows(rows, source="csv:%s" % Path(path).name)

    def import_rows(
        self, rows: Sequence[dict], source: Optional[str] = None
    ) -> ImportOutcome:
        """Import valid rows, reject the rest by line, and audit the batch.

        A rejected row never blocks the file: the caller turns a non-empty
        rejection list into exit code 1, so "78 imported, 2 refused" is visible
        instead of being reported as a clean run.
        """

        origin = source or "csv:import"
        added = 0
        updated = 0
        skipped = 0
        rejected = []
        at = self._clock.timestamp()
        try:
            for index, row in enumerate(rows, start=2):
                code_raw = str(row.get("code", "")).strip()
                try:
                    code = validate_code(code_raw)
                    group = validate_group(str(row.get("group", "")))
                except UsageError as exc:
                    rejected.append((index, code_raw, exc.message))
                    continue
                stock = self._master.get(code)
                if stock is None:
                    rejected.append(
                        (index, code, "code %s is not in the current stock master" % code)
                    )
                    continue
                claimed_org = str(row.get("org_id", "")).strip()
                if claimed_org and claimed_org != stock.org_id:
                    rejected.append(
                        (
                            index,
                            code,
                            "orgId %s does not match the master value %s"
                            % (claimed_org, stock.org_id),
                        )
                    )
                    continue
                existing = self._repository.get(code)
                name = str(row.get("name", "")).strip() or stock.name
                note = str(row.get("note", "")).strip()
                entry = WatchlistEntry(
                    code=code,
                    name=name,
                    pinyin=stock.pinyin,
                    group=group,
                    org_id=stock.org_id,
                    added_at=existing.added_at if existing else at,
                    source=origin if existing is None else existing.source,
                    note=note,
                    in_stock_master=True,
                )
                old = ""
                if existing is not None:
                    if existing.group == group and existing.note == note and existing.name == name:
                        skipped += 1
                        continue
                    old = json.dumps(
                        {"group": existing.group, "note": existing.note},
                        ensure_ascii=False,
                    )
                    updated += 1
                else:
                    added += 1
                new = json.dumps({"group": group, "note": note}, ensure_ascii=False)
                self._repository.upsert(entry, at, "import", old, new)
            self._repository.commit()
        except Exception:
            self._repository.rollback()
            raise
        return ImportOutcome(
            added=added, updated=updated, skipped=skipped, rejected=tuple(rejected)
        )

    def sync_master(self, master: Optional[Dict[str, StockMaster]] = None) -> dict:
        """Refresh ``name/pinyin/org_id/in_stock_master``; keep the user's fields.

        A code that vanished from the master is marked ``in_stock_master = 0``
        and keeps its row: deleting it would silently drop a user's holding
        (FR-06).
        """

        source = self._master if master is None else master
        at = self._clock.timestamp()
        refreshed = 0
        marked_missing = 0
        try:
            for entry in self._repository.all():
                stock = source.get(entry.code)
                if stock is None:
                    if entry.in_stock_master:
                        missing = WatchlistEntry(
                            code=entry.code,
                            name=entry.name,
                            pinyin=entry.pinyin,
                            group=entry.group,
                            org_id=entry.org_id,
                            added_at=entry.added_at,
                            source=entry.source,
                            note=entry.note,
                            in_stock_master=False,
                        )
                        self._repository.upsert(
                            missing,
                            at,
                            "edit",
                            json.dumps({"in_stock_master": 1}, ensure_ascii=False),
                            json.dumps({"in_stock_master": 0}, ensure_ascii=False),
                        )
                        marked_missing += 1
                    continue
                if (
                    stock.name == entry.name
                    and stock.pinyin == entry.pinyin
                    and stock.org_id == entry.org_id
                    and entry.in_stock_master
                ):
                    continue
                refreshed_entry = WatchlistEntry(
                    code=entry.code,
                    name=stock.name,
                    pinyin=stock.pinyin,
                    group=entry.group,
                    org_id=stock.org_id,
                    added_at=entry.added_at,
                    source=entry.source,
                    note=entry.note,
                    in_stock_master=True,
                )
                self._repository.upsert(
                    refreshed_entry,
                    at,
                    "edit",
                    json.dumps(
                        {"name": entry.name, "org_id": entry.org_id}, ensure_ascii=False
                    ),
                    json.dumps(
                        {"name": stock.name, "org_id": stock.org_id}, ensure_ascii=False
                    ),
                )
                refreshed += 1
            self._repository.commit()
        except Exception:
            self._repository.rollback()
            raise
        return {
            "checked": self.count(),
            "refreshed": refreshed,
            "marked_missing": marked_missing,
            "master_size": len(source),
        }

    # -- helpers ----------------------------------------------------------- #

    def _entry_from_master(
        self, code: str, group: str, note: str, name: Optional[str]
    ) -> WatchlistEntry:
        stock = self._master.get(code)
        if stock is None:
            raise UsageError(
                "code %s is not in the current stock master" % code,
                hint="the orgId must come from the frozen master; run "
                "`cnwatch watchlist sync-master` after re-capturing it",
            )
        existing = self._repository.get(code)
        return WatchlistEntry(
            code=code,
            name=(name or stock.name).strip() or stock.name,
            pinyin=stock.pinyin,
            group=group,
            org_id=stock.org_id,
            added_at=existing.added_at if existing else self._clock.timestamp(),
            source=existing.source if existing else self._source,
            note=note,
            in_stock_master=True,
        )


def reject_summary(rejected: Iterable) -> PartialFailure:
    items = list(rejected)
    details = ["line %d (%s): %s" % item for item in items]
    return PartialFailure("%d row(s) rejected" % len(items), details)
