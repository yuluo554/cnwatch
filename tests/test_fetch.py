"""M2 fetch contract: request count, pagination, dedupe, retry, and cleaning.

The transport here is in-memory. The frozen snapshots drive the full-chain
tests in ``test_fetch_replay.py``; this file pins the behaviours that must hold
for any response, including the ones no snapshot contains (5xx, declared-total
mismatch, malformed rows).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cnwatch import db
from cnwatch.clock import FixedClock, exchange_date_from_epoch_ms, fixed_clock_for
from cnwatch.datadir import MASTER_RELPATH
from cnwatch.errors import UsageError
from cnwatch.fetch import (
    AnnouncementClient,
    FetchService,
    FetchTarget,
    clean_title,
    load_config,
    parse_config,
    window_start,
)
from cnwatch.fetch.client import StockFailed, build_body
from cnwatch.fetch.models import parse_row
from cnwatch.fetch.repository import FetchRepository
from cnwatch.fetch.transport import (
    FakeMonotonic,
    RateLimiter,
    RecordingSleep,
    Response,
    TransportError,
)
from cnwatch.watchlist import WatchlistService, load_stock_master
from cnwatch.watchlist.repository import WatchlistRepository

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
SNAPSHOTS = DATA / "snapshots"
NEGATIVE = DATA / "fixtures" / "negative"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def row(**overrides) -> dict:
    base = {
        "secCode": "600519",
        "secName": "贵州茅台",
        "announcementId": "1225601360",
        "announcementTitle": "关于副行长任职资格核准的公告",
        "announcementTime": 1792153331000,
        "adjunctUrl": "finalpage/2026-10-16/1225601360.PDF",
        "adjunctType": "PDF",
        "announcementType": "01010503||010112",
    }
    base.update(overrides)
    return base


class ScriptedTransport:
    """Replays a per-page script, and optionally raises for chosen pages."""

    def __init__(self, pages, fail_attempts=None, status=503, fail_all=False):
        self.pages = list(pages)
        self.fail_attempts = fail_attempts
        self.fail_all = fail_all
        self.status = status
        self.requests = []

    def post(self, url, body, headers, timeout):
        self.requests.append(body)
        if self.fail_all or (
            self.fail_attempts is not None and len(self.requests) <= self.fail_attempts
        ):
            raise TransportError("HTTP %d (injected)" % self.status, retryable=True)
        page = int(_field(body, "pageNum"))
        payload = self.pages[page - 1] if page - 1 < len(self.pages) else {"announcements": []}
        return Response(status=200, body=json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _field(body: bytes, name: str) -> str:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(body.decode("utf-8"))).get(name, "")


def make_client(transport, config, sleep=None):
    clock = FakeMonotonic()
    if sleep is None:
        sleep = RecordingSleep(clock=clock)
    else:
        sleep.clock = clock
    limiter = RateLimiter(config.min_interval_seconds, sleep, clock)
    return AnnouncementClient(transport, limiter, config, sleeper=sleep)


@pytest.fixture()
def config():
    return load_config(DATA)


@pytest.fixture()
def connection(tmp_path):
    conn = db.initialize(tmp_path / "cnwatch.db")
    yield conn
    conn.close()


@pytest.fixture()
def master():
    return load_stock_master(DATA / MASTER_RELPATH)


# --------------------------------------------------------------------------- #
# config comes from the single documented source (K7)
# --------------------------------------------------------------------------- #


def test_config_parses_the_documented_policy_table(config):
    assert config.min_interval_seconds == 1.0
    assert config.page_size == 30
    assert config.retry_delays == (1, 2, 4)
    assert config.max_attempts == 4
    assert config.first_run_lookback_days == 7
    assert config.request_timeout_seconds == 40


def test_config_rejects_a_table_missing_a_row():
    with pytest.raises(UsageError) as excinfo:
        parse_config("## 四、数值口径\n\n| 口径 | 值 |\n|---|---|\n| `pageSize` | 30 |\n")
    assert "missing rows" in excinfo.value.message


def test_config_refuses_a_contradictory_retry_row():
    text = (
        "## 四、数值口径\n\n| 口径 | 值 | 说明 |\n|---|---|---|\n"
        "| 单轮全局请求间隔 | ≥1s | x |\n"
        "| 重试退避 | 1s → 2s → 4s，最多重试 5 次 | x |\n"
        "| `pageSize` | 30 | x |\n"
        "| 增量窗口 | 上次成功日期 − 1 天 → 今天 | x |\n"
        "| 首次回看天数 | 7 天 | x |\n"
        "| 请求超时 | 40s | x |\n"
    )
    with pytest.raises(UsageError) as excinfo:
        parse_config(text)
    assert "inconsistent" in excinfo.value.message


def test_window_uses_the_documented_overlap(config):
    assert window_start("2026-10-16", "2026-10-17", config) == "2026-10-15"


def test_window_without_cursor_uses_the_documented_lookback(config):
    assert window_start(None, "2026-10-17", config) == "2026-10-10"


def test_business_code_carries_no_policy_literals(config):
    """K7: the numbers exist in data/README.md and are read, not restated.

    Scope is deliberate: the gate flags a policy *binding* — a module-level
    constant, a default argument, or a keyword argument whose name is one of the
    policy names — holding a number equal to a documented value. It does not
    flag the same number used as an ordinary count (``page + 1``), which is why
    a plain "no 30 anywhere" scan would have been both noisy and easy to dodge.
    """

    import ast

    policy_names = {
        "page_size",
        "min_interval_seconds",
        "retry_delays",
        "request_timeout_seconds",
        "first_run_lookback_days",
        "overlap_days",
    }

    def is_policy_name(name):
        return name.lower() in policy_names

    policy_values = {float(config.page_size), float(config.min_interval_seconds),
                     float(config.request_timeout_seconds)}
    policy_values |= {float(delay) for delay in config.retry_delays}

    def is_policy_number(node):
        return (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
            and float(node.value) in policy_values
        )

    offenders = []
    for module in ("client.py", "service.py", "config.py", "models.py", "repository.py"):
        path = REPO / "src" / "cnwatch" / "fetch" / module
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if any(is_policy_name(name) for name in names) and is_policy_number(node.value):
                    offenders.append("%s:%d module constant" % (module, node.lineno))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg, default in zip(node.args.args[-len(node.args.defaults):],
                                        node.args.defaults):
                    if is_policy_name(arg.arg) and is_policy_number(default):
                        offenders.append("%s:%d default arg %s" % (module, default.lineno, arg.arg))
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg and is_policy_name(keyword.arg) and is_policy_number(keyword.value):
                        offenders.append("%s:%d kwarg %s" % (module, keyword.value.lineno, keyword.arg))
    assert offenders == [], (
        "policy values are restated in code instead of read from data/README.md: %s"
        % ", ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# request shape
# --------------------------------------------------------------------------- #


def test_request_body_matches_the_measured_contract():
    from urllib.parse import parse_qsl

    fields = dict(parse_qsl(build_body("300750", "GD165627", "2026-10-15~2026-10-16", 2, 30).decode()))
    assert fields["stock"] == "300750,GD165627"
    assert fields["pageNum"] == "2"
    assert fields["pageSize"] == "30"
    assert fields["column"] == "szse"
    assert fields["seDate"] == "2026-10-15~2026-10-16"
    assert fields["isHLtitle"] == "true"
    assert fields["tabName"] == "fulltext"


def test_one_request_per_stock(config):
    transport = ScriptedTransport([{"announcements": [], "totalAnnouncement": 0}])
    client = make_client(transport, config)
    for code, org in (("600519", "gssh0600519"), ("300750", "GD165627"), ("601398", "jjxt0000019")):
        client.fetch_stock(code, org, "2026-10-16~2026-10-16")
    assert len(transport.requests) == 3
    stocks = [_field(body, "stock") for body in transport.requests]
    assert stocks == ["600519,gssh0600519", "300750,GD165627", "601398,jjxt0000019"]


# --------------------------------------------------------------------------- #
# pagination
# --------------------------------------------------------------------------- #


def test_65_rows_need_three_pages(config):
    rows = [row(announcementId=str(1300000000 + index)) for index in range(65)]
    pages = [
        {"announcements": rows[0:30], "totalAnnouncement": 65},
        {"announcements": rows[30:60], "totalAnnouncement": 65},
        {"announcements": rows[60:65], "totalAnnouncement": 65},
    ]
    transport = ScriptedTransport(pages)
    result = make_client(transport, config).fetch_stock(
        "600519", "gssh0600519", "2026-10-16~2026-10-16"
    )
    assert result.pages == 3
    assert result.declared_total == 65
    assert result.collected == 65
    assert [_field(body, "pageNum") for body in transport.requests] == ["1", "2", "3"]


def test_pagination_stops_at_the_declared_total(config):
    rows = [row(announcementId=str(1400000000 + index)) for index in range(30)]
    pages = [{"announcements": rows, "totalAnnouncement": 30}]
    transport = ScriptedTransport(pages)
    make_client(transport, config).fetch_stock(
        "600519", "gssh0600519", "2026-10-16~2026-10-16"
    )
    assert len(transport.requests) == 1


def test_a_repeated_row_cannot_mask_a_missing_row(config):
    """A duplicate id must be counted once, or a shortfall looks like a match.

    The live endpoint can shift pages while filings arrive, so the same row can
    appear twice; counting rows instead of unique ids would reconcile 3 seen
    rows against a declared 3 and silently lose one announcement.
    """

    duplicate = row(announcementId="1225609999")
    pages = [{"announcements": [duplicate, row(announcementId="1225609998"), duplicate],
              "totalAnnouncement": 3}]
    transport = ScriptedTransport(pages)
    with pytest.raises(StockFailed) as excinfo:
        make_client(transport, config).fetch_stock(
            "600519", "gssh0600519", "2026-10-16~2026-10-16"
        )
    assert excinfo.value.reason == "total_mismatch"


def test_a_repeated_row_is_stored_once_when_the_total_agrees(config):
    duplicate = row(announcementId="1225609999")
    pages = [{"announcements": [duplicate, duplicate], "totalAnnouncement": 1}]
    transport = ScriptedTransport(pages)
    result = make_client(transport, config).fetch_stock(
        "600519", "gssh0600519", "2026-10-16~2026-10-16"
    )
    assert result.collected == 1
    assert len(result.announcements) == 1


def test_shorter_than_declared_total_fails_the_stock(config):
    rows = [row(announcementId=str(1500000000 + index)) for index in range(3)]
    pages = [
        {"announcements": rows, "totalAnnouncement": 40},
        {"announcements": [], "totalAnnouncement": 40},
    ]
    transport = ScriptedTransport(pages)
    with pytest.raises(StockFailed) as excinfo:
        make_client(transport, config).fetch_stock(
            "600519", "gssh0600519", "2026-10-16~2026-10-16"
        )
    assert excinfo.value.reason == "total_mismatch"


def test_mismatch_stores_nothing(connection, config):
    rows = [row(announcementId=str(1600000000 + index)) for index in range(3)]
    transport = ScriptedTransport(
        [
            {"announcements": rows, "totalAnnouncement": 40},
            {"announcements": [], "totalAnnouncement": 40},
        ]
    )
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    summary = service.run_watchlist([FetchTarget("600519", "gssh0600519")])
    assert summary.stocks_failed == 1
    assert summary.status == "failed"
    assert connection.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# dedupe
# --------------------------------------------------------------------------- #


def test_replaying_the_same_response_twice_keeps_the_row_count(connection, config):
    rows = [row(announcementId="1225601360"), row(announcementId="1225601361")]
    transport = ScriptedTransport([{"announcements": rows, "totalAnnouncement": 2}])
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    target = FetchTarget("600519", "gssh0600519")
    first = service.run_watchlist([target])
    second = service.run_watchlist([target])

    assert first.baseline_created is True
    assert first.stored == 2
    assert second.baseline_created is False
    assert second.new == 0
    assert second.already_present == 2
    assert connection.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 2


def test_reinsert_does_not_overwrite_a_stored_title(connection, config):
    original = row(announcementId="1225601360", announcementTitle="原始标题")
    changed = row(announcementId="1225601360", announcementTitle="上游改写后的标题")
    targets = [FetchTarget("600519", "gssh0600519")]
    service = FetchService(
        FetchRepository(connection),
        make_client(ScriptedTransport([{"announcements": [original], "totalAnnouncement": 1}]), config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    service.run_watchlist(targets)
    service2 = FetchService(
        FetchRepository(connection),
        make_client(ScriptedTransport([{"announcements": [changed], "totalAnnouncement": 1}]), config),
        config,
        fixed_clock_for("2026-10-17"),
    )
    service2.run_watchlist(targets)
    title = connection.execute(
        "SELECT title FROM announcements WHERE announcement_id = '1225601360'"
    ).fetchone()[0]
    assert title == "原始标题"


# --------------------------------------------------------------------------- #
# retry and isolation
# --------------------------------------------------------------------------- #


def test_5xx_is_retried_the_configured_number_of_times(connection, config):
    transport = ScriptedTransport([], fail_all=True)
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    summary = service.run_watchlist([FetchTarget("600519", "gssh0600519")])

    assert len(transport.requests) == config.max_attempts
    assert summary.stocks_failed == 1
    assert summary.failed_codes() == ["600519"]
    assert summary.status == "failed"
    row_ = connection.execute("SELECT attempts, last_error FROM fetch_failures").fetchone()
    assert row_["attempts"] == config.max_attempts
    assert "503" in row_["last_error"]


def test_one_failing_stock_does_not_affect_the_others(connection, config):
    healthy = [row(announcementId="1225602000", secCode="600519")]

    class Selective:
        def __init__(self):
            self.requests = []

        def post(self, url, body, headers, timeout):
            self.requests.append(body)
            if _field(body, "stock").startswith("300750"):
                raise TransportError("HTTP 502 (injected)", retryable=True)
            return Response(
                status=200,
                body=json.dumps(
                    {"announcements": healthy, "totalAnnouncement": 1}, ensure_ascii=False
                ).encode("utf-8"),
            )

    transport = Selective()
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    summary = service.run_watchlist(
        [FetchTarget("300750", "GD165627"), FetchTarget("600519", "gssh0600519")]
    )
    assert summary.status == "degraded"
    assert summary.stocks_ok == 1
    assert summary.stocks_failed == 1
    assert summary.failed_codes() == ["300750"]
    assert summary.stored == 1
    assert connection.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 1
    # rendered summary names the failure instead of hiding it
    assert "300750" in summary.render()
    assert "incomplete" in summary.render()


def test_a_4xx_is_not_retried(connection, config):
    class ClientError:
        def __init__(self):
            self.requests = []

        def post(self, url, body, headers, timeout):
            self.requests.append(body)
            raise TransportError("HTTP 400", retryable=False, status=400)

    transport = ClientError()
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    summary = service.run_watchlist([FetchTarget("600519", "gssh0600519")])
    assert len(transport.requests) == 1
    assert summary.stocks_failed == 1


def test_backoff_waits_are_the_configured_delays(config):
    clock = FakeMonotonic()
    sleep = RecordingSleep(clock=clock)
    transport = ScriptedTransport([], fail_all=True)
    client = AnnouncementClient(
        transport,
        RateLimiter(config.min_interval_seconds, sleep, clock),
        config,
        sleeper=sleep,
    )
    with pytest.raises(StockFailed):
        client.fetch_stock("600519", "gssh0600519", "2026-10-16~2026-10-16")
    # The fake clock advances by each sleep, so a rate-limit wait of 1s after a
    # 1s backoff is not needed; only genuinely late requests appear.
    assert [delay for delay in sleep.durations if delay in config.retry_delays] == list(
        config.retry_delays
    )


# --------------------------------------------------------------------------- #
# cleaning and dropped rows
# --------------------------------------------------------------------------- #


def test_clean_title_strips_em_and_collapses_whitespace():
    assert clean_title("关于<em>减持</em>计划的  公告") == "关于减持计划的 公告"
    assert clean_title("<em>回购</em>&amp; 注销") == "回购& 注销"
    assert clean_title(None) == ""


def test_negative_cases_store_the_expected_titles(connection, config):
    cases = json.loads((NEGATIVE / "announce-responses.json").read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in cases["cases"]}

    em = by_id["em_highlight"]
    stored, dropped = parse_row(em["rows"][0], "600519", "watchlist", exchange_date_from_epoch_ms)
    assert dropped is None
    assert stored.title == em["expect"]["title"]
    assert "<em>" not in stored.title

    entities = by_id["em_with_entities"]
    stored, _ = parse_row(entities["rows"][0], "600519", "watchlist", exchange_date_from_epoch_ms)
    assert stored.title == entities["expect"]["title"]

    for case_id in ("missing_announcement_id", "empty_title"):
        case = by_id[case_id]
        announcement, drop = parse_row(
            case["rows"][0], "600519", "watchlist", exchange_date_from_epoch_ms
        )
        assert announcement is None
        assert drop.reason == case["expect"]["dropped_reason"]


def test_em_titles_reach_the_database_without_markup(connection, config):
    payload = json.loads((NEGATIVE / "announce-responses.json").read_text(encoding="utf-8"))
    rows = payload["cases"][0]["rows"] + payload["cases"][1]["rows"]
    transport = ScriptedTransport([{"announcements": rows, "totalAnnouncement": len(rows)}])
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    service.run_watchlist([FetchTarget("600519", "gssh0600519")])
    titles = [
        item[0]
        for item in connection.execute("SELECT title FROM announcements ORDER BY announcement_id")
    ]
    assert titles == ["关于减持计划的公告", "回购& 注销 股份"]
    assert all("<" not in title and ">" not in title for title in titles)


def test_dropped_rows_are_counted_and_named(connection, config):
    rows = [
        row(announcementId="", announcementTitle="无编号"),
        row(announcementId="1700000001", announcementTitle="<em></em>"),
        row(announcementId="1700000002", announcementTitle="正常标题"),
    ]
    transport = ScriptedTransport([{"announcements": rows, "totalAnnouncement": 3}])
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    summary = service.run_watchlist([FetchTarget("600519", "gssh0600519")])
    assert summary.stored == 1
    assert summary.dropped == 2
    reasons = sorted(item.reason for item in summary.dropped_items)
    assert reasons == ["empty_title", "missing_id"]


# --------------------------------------------------------------------------- #
# window, baseline, runs
# --------------------------------------------------------------------------- #


def test_first_run_is_baseline_and_reports_no_new_items(connection, config):
    transport = ScriptedTransport(
        [{"announcements": [row(announcementId="1800000001")], "totalAnnouncement": 1}]
    )
    summary = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    ).run_watchlist([FetchTarget("600519", "gssh0600519")])

    assert summary.baseline_created is True
    assert summary.new == 0
    assert summary.stored == 1
    assert "baseline" in summary.render()


def test_second_run_widens_no_further_than_one_overlap_day(connection, config):
    transport = ScriptedTransport([{"announcements": [], "totalAnnouncement": 0}])
    clock = FixedClock
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    service.run_watchlist([FetchTarget("600519", "gssh0600519")])

    service2 = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-17"),
    )
    summary = service2.run_watchlist([FetchTarget("600519", "gssh0600519")])
    assert summary.window_start == "2026-10-15"
    assert summary.window_end == "2026-10-17"


def test_a_failed_run_does_not_advance_the_cursor(connection, config):
    failing = ScriptedTransport([], fail_all=True)
    service = FetchService(
        FetchRepository(connection),
        make_client(failing, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    service.run_watchlist([FetchTarget("600519", "gssh0600519")])

    healthy = ScriptedTransport([{"announcements": [], "totalAnnouncement": 0}])
    service2 = FetchService(
        FetchRepository(connection),
        make_client(healthy, config),
        config,
        fixed_clock_for("2026-10-17"),
    )
    summary = service2.run_watchlist([FetchTarget("600519", "gssh0600519")])
    # No successful cursor exists yet, so this is still the first run.
    assert summary.baseline_created is True
    assert summary.window_start == "2026-10-10"


def test_run_row_records_counts(connection, config):
    transport = ScriptedTransport(
        [{"announcements": [row(announcementId="1900000001")], "totalAnnouncement": 1}]
    )
    summary = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    ).run_watchlist([FetchTarget("600519", "gssh0600519")])

    record = FetchRepository(connection).run(summary.run_id)
    assert record["status"] == "complete"
    assert record["stocks_total"] == 1
    assert record["requests"] == 1
    assert record["pages"] == 1
    assert record["inserted"] == 1
    assert record["baseline_created"] == 1
    assert record["source_kind"] == "watchlist"
    assert record["finished_at"] is not None


def test_retries_are_counted_apart_from_first_requests(connection, config):
    """'requests == watchlist length' must stay literally true.

    If retries or page requests were folded into the first-request count, a
    retry storm would look like a healthy per-stock round and a batch request
    could hide behind the total.
    """

    class FailThenSucceed:
        def __init__(self):
            self.requests = []

        def post(self, url, body, headers, timeout):
            self.requests.append(body)
            if len(self.requests) == 1:
                raise TransportError("HTTP 503 (injected)", retryable=True)
            return Response(
                status=200,
                body=json.dumps(
                    {"announcements": [row(announcementId="2100000001")],
                     "totalAnnouncement": 1},
                    ensure_ascii=False,
                ).encode("utf-8"),
            )

    transport = FailThenSucceed()
    summary = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    ).run_watchlist([FetchTarget("600519", "gssh0600519")])

    assert summary.stocks_total == 1
    assert summary.first_requests == 1
    assert summary.retries == 1
    assert summary.requests == 2
    assert len(transport.requests) == 2

    record = FetchRepository(connection).run(summary.run_id)
    assert record["requests"] == 2


def test_empty_watchlist_is_a_usage_error(connection, config):
    service = FetchService(
        FetchRepository(connection),
        make_client(ScriptedTransport([]), config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    with pytest.raises(UsageError):
        service.run_watchlist([])


def test_market_run_is_labelled_and_does_not_become_the_cursor(connection, config):
    rows = [row(announcementId="2000000001", secCode="688072")]
    transport = ScriptedTransport([{"announcements": rows, "totalAnnouncement": 1}])
    service = FetchService(
        FetchRepository(connection),
        make_client(transport, config),
        config,
        fixed_clock_for("2026-10-16"),
    )
    summary = service.run_market("2026-10-16")
    assert summary.source_kind == "market"
    assert summary.window_start == summary.window_end == "2026-10-16"

    repository = FetchRepository(connection)
    assert repository.last_successful_window_end("watchlist") is None

    service2 = FetchService(
        repository, make_client(ScriptedTransport([]), config), config,
        fixed_clock_for("2026-10-17"),
    )
    assert service2.run_watchlist(
        [FetchTarget("600519", "gssh0600519")]
    ).baseline_created is True


# --------------------------------------------------------------------------- #
# clock discipline
# --------------------------------------------------------------------------- #


def test_fetch_reads_no_system_clock():
    """M2 DoD: 'today' is injectable; the module never asks the OS."""

    for name in ("client.py", "service.py", "models.py", "repository.py", "replay.py"):
        text = (REPO / "src" / "cnwatch" / "fetch" / name).read_text(encoding="utf-8")
        assert "datetime.now(" not in text, name
        assert "time.time(" not in text, name
        assert "date.today()" not in text, name


def test_exchange_dates_use_the_exchange_timezone():
    """Deriving in UTC would move 922 of 1097 rows of 2026-10-15 to the day before."""

    payload = json.loads(
        (SNAPSHOTS / "announce-day-2026-10-15.json").read_text(encoding="utf-8")
    )
    dates = {exchange_date_from_epoch_ms(item["announcementTime"]) for item in payload["announcements"]}
    assert dates == {"2026-10-15"}
