"""The fetch round: window resolution, per-stock isolation, and the summary.

Order of operations is the contract:

1. resolve the window from the injected clock and the stored cursor;
2. open a ``running`` run row *before* the first request, so an interrupted run
   is visible;
3. fetch and store one stock at a time, isolating failures;
4. close the run as ``complete`` / ``degraded`` / ``failed``.

A stock that fails contributes nothing to the database; a stock that succeeds
contributes its rows in a single transaction. "Declared total disagreed with
what we collected" is a failure of that stock, never a silent partial write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..clock import Clock
from ..errors import UsageError
from .client import AnnouncementClient, StockFailed
from .config import FetchConfig, window_start
from .models import DroppedItem
from .repository import FetchRepository

WATCHLIST = "watchlist"
MARKET = "market"


@dataclass(frozen=True)
class FetchTarget:
    code: str
    org_id: str
    name: str = ""


@dataclass
class StockOutcome:
    code: str
    status: str  # ok | failed
    inserted: int = 0
    seen: int = 0
    dropped: int = 0
    pages: int = 0
    first_requests: int = 0
    page_requests: int = 0
    retries: int = 0
    reason: str = ""
    detail: str = ""

    @property
    def requests(self) -> int:
        return self.first_requests + self.page_requests + self.retries


@dataclass
class FetchSummary:
    run_id: int
    window_start: str
    window_end: str
    source_kind: str
    status: str
    baseline_created: bool
    stocks_total: int
    stocks_ok: int
    stocks_failed: int
    first_requests: int
    page_requests: int
    retries: int
    pages: int
    stored: int
    already_present: int
    dropped: int
    outcomes: List[StockOutcome] = field(default_factory=list)
    dropped_items: List[DroppedItem] = field(default_factory=list)

    @property
    def requests(self) -> int:
        return self.first_requests + self.page_requests + self.retries

    @property
    def new(self) -> int:
        """Items reported as new. A baseline run reports none by definition."""

        return 0 if self.baseline_created else self.stored

    @property
    def complete(self) -> bool:
        return self.stocks_failed == 0

    def failed_codes(self) -> List[str]:
        return [outcome.code for outcome in self.outcomes if outcome.status == "failed"]

    def render(self) -> str:
        lines = []
        lines.append(
            "fetch  window %s..%s  (%s)"
            % (self.window_start, self.window_end, self.source_kind)
        )
        if self.baseline_created:
            lines.append(
                "first run: baseline established, %d announcements stored; "
                "nothing is reported as new" % self.stored
            )
        lines.append(
            "stocks %d (ok %d, failed %d)  requests %d (first %d, pages %d, retries %d)"
            % (
                self.stocks_total,
                self.stocks_ok,
                self.stocks_failed,
                self.requests,
                self.first_requests,
                self.page_requests,
                self.retries,
            )
        )
        lines.append("pages %d" % self.pages)
        lines.append(
            "new %d  already known %d  dropped %d"
            % (self.new, self.already_present, self.dropped)
        )
        failed = [outcome for outcome in self.outcomes if outcome.status == "failed"]
        if failed:
            lines.append("failures:")
            for outcome in failed:
                lines.append(
                    "  %s  %s  attempts=%d  %s"
                    % (outcome.code, outcome.reason, outcome.requests, outcome.detail)
                )
        lines.append(
            "result: %s"
            % ("complete" if self.status == "complete" else "incomplete (%s)" % self.status)
        )
        return "\n".join(lines)


def resolve_targets(entries) -> List[FetchTarget]:
    """Watchlist rows are already filtered to ``in_stock_master = 1``."""

    targets = []
    for entry in entries:
        if not entry.org_id:
            raise UsageError(
                "watchlist row %s has no orgId" % entry.code,
                hint="run `cnwatch watchlist sync-master`",
            )
        targets.append(FetchTarget(code=entry.code, org_id=entry.org_id, name=entry.name))
    return targets


class FetchService:
    def __init__(
        self,
        repository: FetchRepository,
        client: AnnouncementClient,
        config: FetchConfig,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._client = client
        self._config = config
        self._clock = clock

    def run_watchlist(self, targets: List[FetchTarget]) -> FetchSummary:
        if not targets:
            raise UsageError(
                "the watchlist is empty",
                hint="import one with `cnwatch watchlist import --file <csv>`",
            )
        today = self._clock.exchange_today().strftime("%Y-%m-%d")
        last = self._repository.last_successful_window_end(WATCHLIST)
        baseline = last is None
        start = window_start(last, today, self._config)
        return self._run(targets, start, today, WATCHLIST, baseline)

    def run_market(self, date: str) -> FetchSummary:
        """Market-wide sweep for one explicit date; never touches the watchlist cursor."""

        target_date = date
        se_date = "%s~%s" % (target_date, target_date)
        started = self._clock.timestamp()
        run_id = self._repository.start_run(
            started, target_date, target_date, MARKET, 1, False
        )
        inserted = 0
        already = 0
        dropped: List[DroppedItem] = []
        outcome: StockOutcome
        try:
            result = self._client.fetch_market_day(se_date, MARKET)
        except StockFailed as exc:
            self._repository.record_failure(
                run_id, exc.code, exc.attempts, "%s: %s" % (exc.reason, exc.detail)
            )
            self._repository.finish_run(
                run_id, self._clock.timestamp(), "failed", 0, 1,
                exc.attempts, 0, 0, 0,
            )
            outcome = StockOutcome(
                code="market",
                status="failed",
                first_requests=exc.first_requests,
                page_requests=exc.page_requests,
                retries=exc.retries,
                reason=exc.reason,
                detail=exc.detail,
            )
            return FetchSummary(
                run_id=run_id,
                window_start=target_date,
                window_end=target_date,
                source_kind=MARKET,
                status="failed",
                baseline_created=False,
                stocks_total=1,
                stocks_ok=0,
                stocks_failed=1,
                first_requests=exc.first_requests,
                page_requests=exc.page_requests,
                retries=exc.retries,
                pages=0,
                stored=0,
                already_present=0,
                dropped=0,
                outcomes=[outcome],
            )

        inserted, already = self._repository.store(
            result.announcements, self._clock.timestamp()
        )
        dropped = result.dropped
        self._repository.finish_run(
            run_id,
            self._clock.timestamp(),
            "complete",
            1,
            0,
            result.requests,
            result.pages,
            inserted,
            len(dropped),
        )
        outcome = StockOutcome(
            code="market",
            status="ok",
            inserted=inserted,
            seen=already,
            dropped=len(dropped),
            pages=result.pages,
            first_requests=result.first_requests,
            page_requests=result.page_requests,
            retries=result.retries,
        )
        return FetchSummary(
            run_id=run_id,
            window_start=target_date,
            window_end=target_date,
            source_kind=MARKET,
            status="complete",
            baseline_created=False,
            stocks_total=1,
            stocks_ok=1,
            stocks_failed=0,
            first_requests=result.first_requests,
            page_requests=result.page_requests,
            retries=result.retries,
            pages=result.pages,
            stored=inserted,
            already_present=already,
            dropped=len(dropped),
            outcomes=[outcome],
            dropped_items=dropped,
        )

    # -- internals --------------------------------------------------------- #

    def _run(
        self,
        targets: List[FetchTarget],
        start: str,
        end: str,
        source_kind: str,
        baseline: bool,
    ) -> FetchSummary:
        se_date = "%s~%s" % (start, end)
        run_id = self._repository.start_run(
            self._clock.timestamp(), start, end, source_kind, len(targets), baseline
        )
        outcomes: List[StockOutcome] = []
        dropped_items: List[DroppedItem] = []
        total_first = 0
        total_page_requests = 0
        total_retries = 0
        total_pages = 0
        inserted_total = 0
        already_total = 0
        dropped_total = 0
        ok = 0
        failed = 0

        for target in targets:
            try:
                result = self._client.fetch_stock(
                    target.code, target.org_id, se_date, source_kind
                )
            except StockFailed as exc:
                failed += 1
                total_first += exc.first_requests
                total_page_requests += exc.page_requests
                total_retries += exc.retries
                self._repository.record_failure(
                    run_id,
                    exc.code,
                    exc.attempts,
                    "%s: %s" % (exc.reason, exc.detail),
                )
                outcomes.append(
                    StockOutcome(
                        code=target.code,
                        status="failed",
                        first_requests=exc.first_requests,
                        page_requests=exc.page_requests,
                        retries=exc.retries,
                        reason=exc.reason,
                        detail=exc.detail,
                    )
                )
                continue

            inserted, already = self._repository.store(
                result.announcements, self._clock.timestamp()
            )
            ok += 1
            total_first += result.first_requests
            total_page_requests += result.page_requests
            total_retries += result.retries
            total_pages += result.pages
            inserted_total += inserted
            already_total += already
            dropped_total += len(result.dropped)
            dropped_items.extend(result.dropped)
            outcomes.append(
                StockOutcome(
                    code=target.code,
                    status="ok",
                    inserted=inserted,
                    seen=already,
                    dropped=len(result.dropped),
                    pages=result.pages,
                    first_requests=result.first_requests,
                    page_requests=result.page_requests,
                    retries=result.retries,
                )
            )

        if ok == 0:
            status = "failed"
        elif failed:
            status = "degraded"
        else:
            status = "complete"
        self._repository.finish_run(
            run_id,
            self._clock.timestamp(),
            status,
            ok,
            failed,
            total_first + total_page_requests + total_retries,
            total_pages,
            inserted_total,
            dropped_total,
        )
        return FetchSummary(
            run_id=run_id,
            window_start=start,
            window_end=end,
            source_kind=source_kind,
            status=status,
            baseline_created=baseline,
            stocks_total=len(targets),
            stocks_ok=ok,
            stocks_failed=failed,
            first_requests=total_first,
            page_requests=total_page_requests,
            retries=total_retries,
            pages=total_pages,
            stored=inserted_total,
            already_present=already_total,
            dropped=dropped_total,
            outcomes=outcomes,
            dropped_items=dropped_items,
        )
