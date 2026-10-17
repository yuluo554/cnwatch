"""The announcement query client: one stock per request, paged, retried.

Three behaviours here are requirements rather than choices, each with a
measured reason in ``data/README.md``:

* **one stock per request** — a comma-joined ``stock`` parameter returns only
  the first entry whatever the order (约束 1);
* **``pageSize`` from the policy table** — the server caps it (约束 4);
* **declared total must equal what was collected** — otherwise the stock is
  failed, because a short read reported as success is an invisible miss
  (约束 4 + 模块 2 §2.6).

The client returns either a complete ``StockResult`` or raises ``StockFailed``.
There is no partial success: a half-collected stock would poison deduplication.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Optional

from ..clock import exchange_date_from_epoch_ms
from .config import FetchConfig
from .models import Announcement, DroppedItem, parse_row
from .transport import RateLimiter, Response, Transport, TransportError

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
REFERER = "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"
COLUMN = "szse"
TAB_NAME = "fulltext"
MAX_PAGES = 400

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cnwatch/0.1",
    "Referer": REFERER,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


class StockFailed(Exception):
    """One stock could not be fetched completely. Never a silent empty result.

    The attempt split travels with the exception so the run record can still say
    "one first request plus N retries" for a stock that failed.
    """

    def __init__(
        self,
        code: str,
        reason: str,
        attempts: int,
        detail: str = "",
        retries: int = 0,
        page_requests: int = 0,
    ) -> None:
        super().__init__("%s: %s" % (code, reason))
        self.code = code
        self.reason = reason
        self.attempts = attempts
        self.detail = detail
        self.retries = retries
        self.page_requests = page_requests

    @property
    def first_requests(self) -> int:
        """Whatever is not a retry or a page request was the stock's first ask."""

        return max(1, self.attempts - self.retries - self.page_requests)


@dataclass
class PageStats:
    """Request accounting, split so each DoD claim stays checkable.

    ``first_requests`` counts one per stock (page 1, first attempt) and is what
    "requests == watchlist length" refers to; ``page_requests`` and ``retries``
    are counted separately because folding them together would make a batch or
    a retry storm look like a healthy per-stock round.
    """

    first_requests: int = 0
    page_requests: int = 0
    retries: int = 0
    duplicates: int = 0

    @property
    def total(self) -> int:
        return self.first_requests + self.page_requests + self.retries


@dataclass
class StockResult:
    code: str
    announcements: List[Announcement] = field(default_factory=list)
    dropped: List[DroppedItem] = field(default_factory=list)
    declared_total: int = 0
    pages: int = 0
    first_requests: int = 0
    page_requests: int = 0
    retries: int = 0

    @property
    def collected(self) -> int:
        return len(self.announcements)

    @property
    def requests(self) -> int:
        return self.first_requests + self.page_requests + self.retries


def build_body(code: str, org_id: str, se_date: str, page: int, page_size: int) -> bytes:
    """The exact form body, in a fixed field order for reproducible requests.

    ``stock`` is empty for the market sweep (no filter); for a single stock it is
    ``code,orgId`` — the only shape the endpoint honours.
    """

    stock = "%s,%s" % (code, org_id) if code else ""
    fields = (
        ("pageNum", str(page)),
        ("pageSize", str(page_size)),
        ("column", COLUMN),
        ("tabName", TAB_NAME),
        ("plate", ""),
        ("stock", stock),
        ("searchkey", ""),
        ("secid", ""),
        ("category", ""),
        ("trade", ""),
        ("seDate", se_date),
        ("sortName", ""),
        ("sortType", ""),
        ("isHLtitle", "true"),
    )
    return urllib.parse.urlencode(fields).encode("utf-8")


class AnnouncementClient:
    def __init__(
        self,
        transport: Transport,
        limiter: RateLimiter,
        config: FetchConfig,
        sleeper=None,
    ) -> None:
        self._transport = transport
        self._limiter = limiter
        self._config = config
        self._sleep = sleeper

    # -- one stock --------------------------------------------------------- #

    def fetch_stock(
        self, code: str, org_id: str, se_date: str, source_kind: str = "watchlist"
    ) -> StockResult:
        stats = PageStats()
        first = self._request(code, org_id, se_date, page=1, stats=stats)
        declared = _declared_total(first)
        rows = first.get("announcements") or []

        collected: Dict[str, Announcement] = {}
        order: List[str] = []
        dropped: List[DroppedItem] = []
        _absorb(rows, collected, order, dropped, code, source_kind, stats)
        pages = 1
        page = 1

        while True:
            if declared == 0:
                break
            if len(collected) + len(dropped) >= declared:
                break
            if not rows:
                break
            if pages >= MAX_PAGES:
                raise StockFailed(
                    code,
                    "pagination_runaway",
                    stats.total,
                    "stopped after %d pages" % pages,
                    retries=stats.retries,
                    page_requests=stats.page_requests,
                )
            page += 1
            payload = self._request(code, org_id, se_date, page=page, stats=stats)
            rows = payload.get("announcements") or []
            pages += 1
            _absorb(rows, collected, order, dropped, code, source_kind, stats)

        # Reconcile on unique ids: a page overlap (the endpoint can repeat a row
        # while new filings shift the pages) must not be counted as two rows and
        # hide a shortfall. Dropped rows account for their share of the declared
        # total — they are reported by id, never silently subtracted — but they
        # are not "collected", so the count that must match is the sum.
        if len(collected) + len(dropped) != declared:
            raise StockFailed(
                code,
                "total_mismatch",
                stats.total,
                "declared %d, collected %d unique (+%d dropped, %d duplicate rows)"
                % (declared, len(collected), len(dropped), stats.duplicates),
                retries=stats.retries,
                page_requests=stats.page_requests,
            )

        return StockResult(
            code=code,
            announcements=[collected[announcement_id] for announcement_id in order],
            dropped=dropped,
            declared_total=declared,
            pages=pages,
            first_requests=stats.first_requests,
            page_requests=stats.page_requests,
            retries=stats.retries,
        )

    # -- pagination of the market fallback -------------------------------- #

    def fetch_market_day(self, se_date: str, source_kind: str = "market") -> StockResult:
        """``--all`` mode: no stock filter, paged the same way."""

        stats = PageStats()
        first = self._request(None, None, se_date, page=1, stats=stats)
        declared = _declared_total(first)
        rows = first.get("announcements") or []
        collected: Dict[str, Announcement] = {}
        order: List[str] = []
        dropped: List[DroppedItem] = []
        _absorb(rows, collected, order, dropped, "", source_kind, stats)
        pages = 1
        page = 1
        while declared and len(collected) + len(dropped) < declared and rows:
            if pages >= MAX_PAGES:
                raise StockFailed(
                    "market",
                    "pagination_runaway",
                    stats.total,
                    retries=stats.retries,
                    page_requests=stats.page_requests,
                )
            page += 1
            payload = self._request(None, None, se_date, page=page, stats=stats)
            rows = payload.get("announcements") or []
            pages += 1
            _absorb(rows, collected, order, dropped, "", source_kind, stats)
        if len(collected) + len(dropped) != declared:
            raise StockFailed(
                "market",
                "total_mismatch",
                stats.total,
                "declared %d, collected %d unique" % (declared, len(collected)),
                retries=stats.retries,
                page_requests=stats.page_requests,
            )
        return StockResult(
            code="market",
            announcements=[collected[announcement_id] for announcement_id in order],
            dropped=dropped,
            declared_total=declared,
            pages=pages,
            first_requests=stats.first_requests,
            page_requests=stats.page_requests,
            retries=stats.retries,
        )

    # -- request + retry --------------------------------------------------- #

    def _request(
        self,
        code: Optional[str],
        org_id: Optional[str],
        se_date: str,
        page: int,
        stats: PageStats,
    ) -> dict:
        if code is None:
            body = build_body("", "", se_date, page, self._config.page_size)
        else:
            body = build_body(code, org_id or "", se_date, page, self._config.page_size)

        attempt = 0
        while True:
            self._limiter.wait()
            if attempt:
                stats.retries += 1
            elif page == 1:
                stats.first_requests += 1
            else:
                stats.page_requests += 1
            try:
                response = self._transport.post(
                    QUERY_URL, body, HEADERS, self._config.request_timeout_seconds
                )
                return _decode(response, code or "market", page)
            except TransportError as exc:
                if not exc.retryable:
                    raise StockFailed(
                        code or "market",
                        "http_error",
                        stats.total,
                        exc.message,
                        retries=stats.retries,
                        page_requests=stats.page_requests,
                    )
                if attempt >= len(self._config.retry_delays):
                    raise StockFailed(
                        code or "market",
                        "retries_exhausted",
                        stats.total,
                        exc.message,
                        retries=stats.retries,
                        page_requests=stats.page_requests,
                    )
                delay = self._config.retry_delays[attempt]
                if self._sleep is not None:
                    self._sleep(delay)
                attempt += 1
            except StockFailed:
                raise
            except ValueError as exc:
                raise StockFailed(
                    code or "market",
                    "parse_error",
                    stats.total,
                    str(exc),
                    retries=stats.retries,
                    page_requests=stats.page_requests,
                )


def _decode(response: Response, label: str, page: int) -> dict:
    if response.status != 200:
        raise TransportError(
            "unexpected status %d for %s page %d" % (response.status, label, page),
            retryable=response.status >= 500,
            status=response.status,
        )
    if not response.body:
        # The endpoint answers an empty page with an empty body, not "{}".
        return {"announcements": [], "totalAnnouncement": 0}
    try:
        payload = json.loads(response.text("utf-8"))
    except ValueError as exc:
        raise ValueError("response for %s page %d is not JSON: %s" % (label, page, exc))
    if payload is None:
        return {"announcements": [], "totalAnnouncement": 0}
    if not isinstance(payload, dict):
        raise ValueError("response for %s page %d is not a JSON object" % (label, page))
    return payload


def _declared_total(payload: dict) -> int:
    try:
        return int(payload.get("totalAnnouncement") or 0)
    except (TypeError, ValueError):
        raise ValueError("totalAnnouncement is not an integer: %r" % payload.get("totalAnnouncement"))


def _absorb(
    rows: List[dict],
    collected: Dict[str, Announcement],
    order: List[str],
    dropped: List[DroppedItem],
    code: str,
    source_kind: str,
    stats: PageStats,
) -> None:
    """Parse a page into ``collected`` keyed by id, preserving first-seen order.

    A repeated id is counted once. That matters for the reconciliation that
    follows: the endpoint can shift pages while new filings arrive, so a row may
    appear on two pages; counting it twice would mask a genuinely missing row.
    """

    for row in rows:
        if not isinstance(row, dict):
            dropped.append(
                DroppedItem(reason="malformed_row", identifier=code or "market")
            )
            continue
        announcement, drop = parse_row(
            row, code, source_kind, exchange_date_from_epoch_ms
        )
        if announcement is None:
            dropped.append(drop)
            continue
        if announcement.announcement_id in collected:
            stats.duplicates += 1
            continue
        collected[announcement.announcement_id] = announcement
        order.append(announcement.announcement_id)
