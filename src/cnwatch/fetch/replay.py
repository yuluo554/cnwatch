"""Offline replay of the frozen snapshots.

The replay transport answers the exact request bodies the client builds, using
only fields the snapshot kept. That is why the M1 snapshot carries the whole
day rather than a pre-paged extract: a replay that reads the trimmed snapshot
and re-slices it per page exercises the client's real pagination, ordering and
``totalAnnouncement`` reconciliation instead of feeding it the answer.

A request for a stock that has no rows on the frozen day is answered with the
same empty page the live endpoint returns, so "this stock had no filings that
day" stays distinguishable from "this stock failed".
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..clock import exchange_date_from_epoch_ms
from .transport import Response, Transport

# The snapshot keeps the eight fields the project uses; ``accounting`` and
# ``source`` describe the capture, not the API.
SNAPSHOT_SUFFIX = "announce-day-"


@dataclass
class SnapshotDay:
    date: str
    rows: List[dict]
    total_announcement: int

    @classmethod
    def load(cls, path: Path) -> "SnapshotDay":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = payload["announcements"]
        return cls(
            date=str(payload["source"]["se_date"]).split("~")[0],
            rows=list(rows),
            total_announcement=int(payload["accounting"]["total_announcement"]),
        )


def load_day(snapshots_dir: Path, day: str) -> SnapshotDay:
    path = Path(snapshots_dir) / ("%s%s.json" % (SNAPSHOT_SUFFIX, day))
    if not path.is_file():
        raise FileNotFoundError(
            "no frozen snapshot for %s under %s" % (day, Path(snapshots_dir))
        )
    return SnapshotDay.load(path)


class ReplayTransport(Transport):
    """Answers ``hisAnnouncement/query`` from frozen snapshots.

    ``fail_codes`` makes a stock's every request raise a retryable error, which
    is how the retry/failure-isolation tests inject a 5xx without a network.
    ``mismatch_codes`` makes the server declare more rows than it returns,
    exercising the "declared != collected" failure path.
    """

    def __init__(
        self,
        snapshots: Dict[str, SnapshotDay],
        fail_codes: Optional[set] = None,
        mismatch_codes: Optional[set] = None,
        status: int = 503,
    ) -> None:
        self._snapshots = snapshots
        self._fail_codes = set(fail_codes or ())
        self._mismatch_codes = set(mismatch_codes or ())
        self._status = status
        self.requests: List[dict] = []

    def post(self, url: str, body: bytes, headers: dict, timeout: float) -> Response:
        fields = dict(urllib.parse.parse_qsl(body.decode("utf-8")))
        stock = fields.get("stock", "")
        code = stock.split(",")[0] if stock else ""
        page = int(fields.get("pageNum") or 1)
        page_size = int(fields.get("pageSize") or 30)
        se_start, _, se_end = (fields.get("seDate") or "").partition("~")
        self.requests.append(
            {
                "code": code,
                "page": page,
                "page_size": page_size,
                "se_date": fields.get("seDate"),
                "column": fields.get("column"),
                "fields": fields,
            }
        )

        from .transport import TransportError

        if code and code in self._fail_codes:
            raise TransportError("HTTP %d (injected)" % self._status, retryable=True, status=self._status)

        rows = self._rows_for(code, se_start, se_end)
        total = len(rows)
        if code and code in self._mismatch_codes:
            total = len(rows) + 5

        start = (page - 1) * page_size
        window = rows[start:start + page_size]
        payload = {"announcements": window, "totalAnnouncement": total}
        return Response(status=200, body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _rows_for(self, code: str, start: str, end: str) -> List[dict]:
        rows: List[dict] = []
        for day, snapshot in sorted(self._snapshots.items()):
            if day < start or day > end:
                continue
            for row in snapshot.rows:
                if not code or str(row.get("secCode")) == code:
                    rows.append(row)
        # The live endpoint orders by disclosure time descending within a page;
        # the frozen file already carries that order, so replay keeps it.
        return rows


def snapshot_transport(
    snapshots_dir: Path,
    days: List[str],
    fail_codes=None,
    mismatch_codes=None,
) -> ReplayTransport:
    return ReplayTransport(
        {day: load_day(snapshots_dir, day) for day in days},
        fail_codes=fail_codes,
        mismatch_codes=mismatch_codes,
    )


def dates_in(snapshot: SnapshotDay) -> set:
    return {exchange_date_from_epoch_ms(row["announcementTime"]) for row in snapshot.rows}
