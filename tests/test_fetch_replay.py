"""M2 offline replay: the frozen response bytes drive the whole chain.

Nothing here talks to the network. The replay transport re-slices the frozen
day per ``pageSize`` and answers with the real ``totalAnnouncement``, so the
client's pagination and reconciliation run against the same bytes a live fetch
would receive. Assertions are derived from the snapshots themselves (id sets
and per-stock counts), never transcribed from an earlier run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cnwatch import db
from cnwatch.clock import exchange_date_from_epoch_ms, fixed_clock_for
from cnwatch.fetch import AnnouncementClient, FetchService, FetchTarget, load_config
from cnwatch.fetch.replay import load_day, snapshot_transport
from cnwatch.fetch.repository import FetchRepository
from cnwatch.fetch.transport import (
    FakeMonotonic,
    RateLimiter,
    RecordingSleep,
)
from cnwatch.watchlist import load_stock_master, WatchlistService
from cnwatch.watchlist.repository import WatchlistRepository

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
SNAPSHOTS = DATA / "snapshots"
SAMPLE = DATA / "fixtures" / "watchlist-sample.csv"
DAY_1 = "2026-10-15"
DAY_2 = "2026-10-16"


def _snapshot(day: str) -> dict:
    return json.loads((SNAPSHOTS / ("announce-day-%s.json" % day)).read_text(encoding="utf-8"))


def _ids_for(day: str, codes) -> set:
    wanted = set(codes)
    payload = _snapshot(day)
    return {
        row["announcementId"]
        for row in payload["announcements"]
        if row["secCode"] in wanted
    }


def _client(transport):
    config = load_config(DATA)
    clock = FakeMonotonic()
    sleep = RecordingSleep(clock=clock)
    limiter = RateLimiter(config.min_interval_seconds, sleep, clock)
    return AnnouncementClient(transport, limiter, config, sleeper=sleep), config


@pytest.fixture()
def config():
    return load_config(DATA)


@pytest.fixture()
def connection(tmp_path):
    conn = db.initialize(tmp_path / "cnwatch.db")
    yield conn
    conn.close()


@pytest.fixture()
def watchlist(connection):
    master = load_stock_master(DATA / "fixtures" / "stock-master.csv")
    service = WatchlistService(
        WatchlistRepository(connection), master, fixed_clock_for(DAY_1)
    )
    service.import_csv(SAMPLE)
    return [entry.code for entry in service.fetchable_entries()]


def _service(connection, transport, config, today):
    client, _config = _client(transport)
    return FetchService(
        FetchRepository(connection), client, config, fixed_clock_for(today)
    )


# --------------------------------------------------------------------------- #
# the snapshot is the truth, not a fixture copy of an answer
# --------------------------------------------------------------------------- #


def test_replay_answers_from_the_frozen_bytes():
    transport = snapshot_transport(SNAPSHOTS, [DAY_1, DAY_2])
    client, _config = _client(transport)
    result = client.fetch_stock("601398", "jjxt0000019", "%s~%s" % (DAY_1, DAY_2))

    expected = _ids_for(DAY_1, ["601398"]) | _ids_for(DAY_2, ["601398"])
    assert {item.announcement_id for item in result.announcements} == expected
    assert result.declared_total == len(expected)
    assert result.collected == len(expected)


def test_one_stock_with_many_rows_pages_correctly():
    """The busiest frozen stock exercises real pagination, not a single page."""

    payload = _snapshot(DAY_2)
    counts = {}
    for row in payload["announcements"]:
        counts[row["secCode"]] = counts.get(row["secCode"], 0) + 1
    busiest = max(counts, key=counts.get)
    expected_pages = (counts[busiest] + 29) // 30

    transport = snapshot_transport(SNAPSHOTS, [DAY_2])
    client, _config = _client(transport)
    result = client.fetch_stock(busiest, "any", "%s~%s" % (DAY_2, DAY_2))
    assert result.pages == expected_pages
    assert result.collected == counts[busiest]


# --------------------------------------------------------------------------- #
# the DoD rows
# --------------------------------------------------------------------------- #


def test_request_count_equals_watchlist_length(connection, watchlist, config):
    transport = snapshot_transport(SNAPSHOTS, [DAY_1, DAY_2])
    summary = _service(connection, transport, config, DAY_2).run_watchlist(
        _targets(watchlist)
    )
    assert summary.stocks_total == len(watchlist) == 20
    # The DoD is about first requests: one per stock. Pages and retries are
    # counted separately, so folding them in cannot mask a batch shortcut.
    assert summary.first_requests == len(watchlist)
    assert summary.requests == len(watchlist)
    # Every stock fits on one page on these two days.
    assert summary.pages == len(watchlist)
    assert summary.page_requests == 0
    assert summary.retries == 0


def _targets(codes):
    master = load_stock_master(DATA / "fixtures" / "stock-master.csv")
    return [FetchTarget(code=code, org_id=master[code].org_id, name=master[code].name) for code in codes]


def test_replaying_twice_keeps_the_row_count(connection, watchlist, config):
    transport = snapshot_transport(SNAPSHOTS, [DAY_1, DAY_2])
    first = _service(connection, transport, config, DAY_2).run_watchlist(_targets(watchlist))
    rows_after_first = connection.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    assert rows_after_first == first.stored

    second = _service(connection, transport, config, DAY_2).run_watchlist(_targets(watchlist))
    rows_after_second = connection.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]

    assert second.new == 0
    assert rows_after_second == rows_after_first


def test_cross_day_overlap_produces_no_duplicates(connection, watchlist, config):
    day1 = _service(
        connection, snapshot_transport(SNAPSHOTS, [DAY_1, DAY_2]), config, DAY_1
    ).run_watchlist(_targets(watchlist))
    day2 = _service(
        connection, snapshot_transport(SNAPSHOTS, [DAY_1, DAY_2]), config, DAY_2
    ).run_watchlist(_targets(watchlist))

    expected_day1 = _ids_for(DAY_1, watchlist)
    expected_day2 = _ids_for(DAY_2, watchlist)

    assert day1.stored == len(expected_day1)
    assert day2.already_present == len(expected_day1)
    assert day2.new == len(expected_day2)
    # Day 1 ran with window_end = 10-15, so day 2 starts one day before that and
    # the two windows genuinely overlap (10-15 is requested twice).
    assert day2.window_start == "2026-10-14"
    assert day2.window_start < day1.window_end

    stored = {
        item[0]
        for item in connection.execute("SELECT announcement_id FROM announcements")
    }
    assert stored == expected_day1 | expected_day2
    assert len(stored) == connection.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]


def test_missing_declared_rows_fails_only_that_stock(connection, watchlist, config):
    transport = snapshot_transport(
        SNAPSHOTS, [DAY_1, DAY_2], mismatch_codes={"300750"}
    )
    summary = _service(connection, transport, config, DAY_2).run_watchlist(
        _targets(watchlist)
    )
    assert summary.status == "degraded"
    assert summary.stocks_failed == 1
    failed = [outcome for outcome in summary.outcomes if outcome.status == "failed"][0]
    assert failed.code == "300750"
    assert failed.reason == "total_mismatch"
    # Nothing from the failed stock reached the database...
    assert connection.execute(
        "SELECT COUNT(*) FROM announcements WHERE code = '300750'"
    ).fetchone()[0] == 0
    # ...while every other stock is complete.
    expected = _ids_for(DAY_1, set(watchlist) - {"300750"}) | _ids_for(
        DAY_2, set(watchlist) - {"300750"}
    )
    stored = {
        item[0] for item in connection.execute("SELECT announcement_id FROM announcements")
    }
    assert stored == expected


def test_injected_5xx_is_retried_and_named(connection, watchlist, config):
    transport = snapshot_transport(SNAPSHOTS, [DAY_1, DAY_2], fail_codes={"002594"})
    summary = _service(connection, transport, config, DAY_2).run_watchlist(
        _targets(watchlist)
    )

    # 19 stocks x 1 first request, plus one first request and every retry for
    # the failing stock.
    assert summary.first_requests == 20
    assert summary.retries == config.max_attempts - 1
    assert summary.requests == 19 + config.max_attempts
    assert summary.failed_codes() == ["002594"]
    assert summary.status == "degraded"

    record = connection.execute(
        "SELECT attempts FROM fetch_failures WHERE code = '002594'"
    ).fetchone()
    assert record["attempts"] == config.max_attempts
    assert "002594" in summary.render()


def test_stored_titles_never_contain_markup(connection, watchlist, config):
    _service(
        connection, snapshot_transport(SNAPSHOTS, [DAY_1, DAY_2]), config, DAY_2
    ).run_watchlist(_targets(watchlist))
    titles = [item[0] for item in connection.execute("SELECT title FROM announcements")]
    assert titles
    assert all("<" not in title and ">" not in title for title in titles)
    assert all(title == title.strip() for title in titles)


def test_every_stored_date_matches_its_frozen_day(connection, watchlist, config):
    _service(
        connection, snapshot_transport(SNAPSHOTS, [DAY_1, DAY_2]), config, DAY_2
    ).run_watchlist(_targets(watchlist))
    dates = {item[0] for item in connection.execute("SELECT DISTINCT announce_date FROM announcements")}
    assert dates == {DAY_1, DAY_2}


def test_round_trip_from_bytes_matches_a_direct_parse(watchlist, config):
    """The end-to-end row set equals a parse of the frozen file.

    If the client dropped or invented a row, this comparison would catch it
    without relying on any value written down by hand.
    """

    snapshot = load_day(SNAPSHOTS, DAY_2)
    expected = {
        row["announcementId"]
        for row in snapshot.rows
        if row["secCode"] in set(watchlist)
    }
    transport = snapshot_transport(SNAPSHOTS, [DAY_2])
    client, _config = _client(transport)
    got = set()
    for code in watchlist:
        result = client.fetch_stock(code, "any", "%s~%s" % (DAY_2, DAY_2))
        got.update(item.announcement_id for item in result.announcements)
    assert got == expected


def test_frozen_day_is_fully_accounted_for():
    """Account for all 1313/1097 rows: no row lost to an unparsed field."""

    for day in (DAY_1, DAY_2):
        snapshot = load_day(SNAPSHOTS, day)
        assert len(snapshot.rows) == snapshot.total_announcement
        for row in snapshot.rows:
            assert row["announcementId"]
            assert row["announcementTitle"].strip()
            assert exchange_date_from_epoch_ms(row["announcementTime"]) == day
