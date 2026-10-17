"""M5 archive: path/size/time recording, idempotency, extraction states.

Every expected value here is recomputed from something frozen — the policy
table in ``data/README.md`` section 4, the documented layout, or the bytes this
test itself handed to the transport. Nothing is transcribed from a previous run
of the archive code.

The test double is a synthetic PDF (it starts with the ``%PDF-`` magic and
contains high bytes and CRLF, so a text-mode write would be caught). It is
labelled as synthetic on purpose: the project does not invent announcement
content, and no real filing is stored in the repository.

Grouped by DoD line:

* path, byte size and time are recorded, and the file is byte-identical;
* re-archiving the same id performs no download and writes no second row;
* a missing extractor is exit 2 with an install hint and **nothing** written;
* an unusable text layer is ``unavailable`` with an empty excerpt, exit 0;
* ``data/pdf/`` is outside git's tracking, with a positive control;
* the excerpt is never a classification input.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

from cnwatch import db
from cnwatch.archive import (
    TEXT_EXTRACTED,
    TEXT_UNAVAILABLE,
    ArchiveRepository,
    ArchiveRequest,
    ArchiveTarget,
    ExtractionFailed,
    ExtractionUnavailable,
    PdftotextExtractor,
    PdfDownloadError,
    PdfDownloader,
    RecordingPdfTransport,
    TextExtractor,
    UnavailableExtractor,
    UrllibPdfTransport,
    archive_path,
    archive_targets,
    load_config,
    require_extractor,
)
from cnwatch.cli import STAGE_NOT_READY, main
from cnwatch.clock import fixed_clock_for
from cnwatch.errors import UsageError
from cnwatch.exit_codes import EXIT_DEGRADED, EXIT_OK, EXIT_USAGE
from cnwatch.fetch import transport as fetch_transport
from cnwatch.fetch.transport import (
    FakeMonotonic,
    NullSleep,
    RateLimiter,
    RecordingSleep,
    Response,
    TransportError,
)
from cnwatch.paths import Paths

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "cnwatch"
ARCHIVE_SRC = SRC / "archive"
DATA = REPO / "data"
SAMPLE = DATA / "fixtures" / "watchlist-sample.csv"
SNAPSHOTS = DATA / "snapshots"
DAY = "2026-10-16"
STAMP = "2026-10-16T12:00:00Z"

# Synthetic, not a real filing: high bytes + CRLF make a text-mode write fail.
PDF_BYTES = b"%PDF-1.4\r\n% synthetic test fixture\r\n\x00\x80\xff\r\n%%EOF\r\n"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _announcement(connection, announcement_id, code, title, day, url, source_kind="watchlist"):
    connection.execute(
        """
        INSERT INTO announcements (
            announcement_id, code, name, title, announce_date,
            adjunct_url, adjunct_type, announcement_type, source_kind,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'PDF', '', ?, ?, ?)
        """,
        (announcement_id, code, "测试股份", title, day, url, source_kind,
         "2026-10-16T00:00:00Z", "2026-10-16T00:00:00Z"),
    )


def _classification(connection, announcement_id, category, risk, tags=(), version="v1"):
    connection.execute(
        """
        INSERT INTO classifications (
            announcement_id, category, tags_json, risk_level, rules_version, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (announcement_id, category, json.dumps(list(tags), ensure_ascii=False), risk,
         version, "2026-10-16T00:00:00Z"),
    )


def _seed(connection, rows):
    """rows: (announcement_id, code, title, day, url, category, risk)."""

    for announcement_id, code, title, day, url, category, risk in rows:
        _announcement(connection, announcement_id, code, title, day, url)
        _classification(connection, announcement_id, category, risk)
    connection.commit()


class FakeExtractor(TextExtractor):
    """Hands back canned text. Synthetic content, like every other fixture here."""

    name = "fake"

    def __init__(self, text="", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def available(self):
        return True

    def extract(self, data, pages):
        self.calls.append({"bytes": len(data), "pages": pages})
        if self.error is not None:
            raise self.error
        return self.text


class BrokenExtractor(TextExtractor):
    """An extractor whose backend is gone: the configuration error case."""

    name = "broken"

    def available(self):
        return False

    def extract(self, data, pages):
        raise ExtractionUnavailable("planted missing dependency")


def _responder(body=PDF_BYTES, status=200):
    def answer(url, headers):
        return Response(status=status, body=body)

    return answer


def _downloader(transport, config, sleeper=None):
    limiter = RateLimiter(config.min_interval_seconds, NullSleep(), FakeMonotonic())
    return PdfDownloader(
        transport,
        limiter,
        config.request_timeout_seconds,
        config.retry_delays,
        sleeper=sleeper if sleeper is not None else RecordingSleep(),
    )


def _request(paths, connection, config, transport, **overrides):
    repository = ArchiveRepository(connection)
    fields = {
        "paths": paths,
        "repository": repository,
        "downloader": _downloader(transport, config),
        "clock": fixed_clock_for(DAY),
        "config": config,
        "targets": [],
        "extractor": None,
        "text_requested": False,
    }
    fields.update(overrides)
    return ArchiveRequest(**fields)


def _targets(connection, **filters):
    return ArchiveRepository(connection).targets(**filters)


@pytest.fixture()
def connection(tmp_path):
    conn = db.initialize(tmp_path / "cnwatch.db")
    yield conn
    conn.close()


@pytest.fixture()
def paths(tmp_path):
    return Paths(home=tmp_path / "home")


@pytest.fixture()
def config():
    return load_config(DATA)


@pytest.fixture()
def one_row(connection):
    _seed(
        connection,
        [
            (
                "1225601360",
                "600519",
                "贵州茅台关于控股股东部分股份质押的公告",
                DAY,
                "finalpage/2026-10-16/1225601360.PDF",
                "category_ndbg_szsh",
                "low",
            )
        ],
    )
    return "1225601360"


# --------------------------------------------------------------------------- #
# DoD: path, byte size and time are recorded
# --------------------------------------------------------------------------- #


def test_the_record_carries_path_size_and_time(connection, paths, config, one_row):
    transport = RecordingPdfTransport(responder=_responder())
    summary = archive_targets(
        _request(paths, connection, config, transport, targets=_targets(connection, announcement_ids=[one_row]))
    )

    assert summary.archived == 1
    assert summary.failed == 0
    assert summary.bytes_total == len(PDF_BYTES)

    expected = (
        paths.home / "data" / "pdf" / "600519" / DAY / ("1225601360%s" % config.suffix)
    )
    assert expected.is_file()
    # Byte-exact: a text-mode write would corrupt the high bytes and the CRLF.
    assert expected.read_bytes() == PDF_BYTES
    assert expected.suffix == ".PDF", "the documented suffix is uppercase"

    record = ArchiveRepository(connection).existing(one_row)
    assert record.local_path == str(expected)
    assert record.byte_size == len(PDF_BYTES) == expected.stat().st_size
    assert record.downloaded_at == STAMP
    assert record.text_status == TEXT_UNAVAILABLE
    assert record.text_excerpt == ""


def test_the_layout_is_the_documented_one(connection, paths, config, one_row):
    """The path is built from code/date/id, and the suffix comes from section 4."""

    assert config.suffix == ".PDF"
    built = archive_path(paths.pdf_dir, "600519", DAY, "1225601360", config.suffix)
    assert built.parts[-4:] == ("pdf", "600519", DAY, "1225601360.PDF")


def test_the_request_goes_to_the_stored_source_url(connection, paths, config, one_row):
    transport = RecordingPdfTransport(responder=_responder())
    archive_targets(
        _request(paths, connection, config, transport, targets=_targets(connection, announcement_ids=[one_row]))
    )
    assert transport.calls[0]["url"] == (
        "http://static.cninfo.com.cn/finalpage/2026-10-16/1225601360.PDF"
    )


def test_the_downloaded_at_time_can_be_pinned(connection, paths, config, one_row):
    """Two runs with the same pinned instant store the same value."""

    clock = fixed_clock_for(DAY)
    transport = RecordingPdfTransport(responder=_responder())
    archive_targets(
        _request(
            paths, connection, config, transport,
            clock=clock, targets=_targets(connection, announcement_ids=[one_row]),
        )
    )
    record = ArchiveRepository(connection).existing(one_row)
    assert record.downloaded_at == clock.timestamp() == STAMP


# --------------------------------------------------------------------------- #
# DoD: re-archiving the same id is idempotent
# --------------------------------------------------------------------------- #


def test_re_archiving_the_same_id_downloads_nothing(connection, paths, config, one_row):
    transport = RecordingPdfTransport(responder=_responder())
    targets = _targets(connection, announcement_ids=[one_row])

    first = archive_targets(_request(paths, connection, config, transport, targets=targets))
    assert first.archived == 1
    assert len(transport.calls) == 1
    before = ArchiveRepository(connection).existing(one_row)

    second = archive_targets(_request(paths, connection, config, transport, targets=targets))

    assert second.archived == 0
    assert second.skipped == 1
    assert len(transport.calls) == 1, "the second run must not download again"
    assert ArchiveRepository(connection).count() == 1, "no second row for the same id"
    after = ArchiveRepository(connection).existing(one_row)
    assert after == before


def test_a_damaged_file_is_repaired_not_reported_as_archived(
    connection, paths, config, one_row
):
    """Same state = no-op; damaged state = repair. Both are idempotent."""

    transport = RecordingPdfTransport(responder=_responder())
    targets = _targets(connection, announcement_ids=[one_row])
    archive_targets(_request(paths, connection, config, transport, targets=targets))

    stored = Path(ArchiveRepository(connection).existing(one_row).local_path)
    stored.write_bytes(b"")

    summary = archive_targets(_request(paths, connection, config, transport, targets=targets))

    assert summary.archived == 1
    assert len(transport.calls) == 2
    assert stored.read_bytes() == PDF_BYTES
    assert ArchiveRepository(connection).count() == 1


def test_text_can_be_recovered_from_the_local_file_without_downloading(
    connection, paths, config, one_row
):
    """A first run without --text, then a run with it: no second download."""

    transport = RecordingPdfTransport(responder=_responder())
    targets = _targets(connection, announcement_ids=[one_row])
    archive_targets(_request(paths, connection, config, transport, targets=targets))
    assert len(transport.calls) == 1

    extractor = FakeExtractor("质押公告正文摘录")
    summary = archive_targets(
        _request(
            paths, connection, config, transport,
            targets=targets, extractor=extractor, text_requested=True,
        )
    )

    assert summary.skipped == 1
    assert summary.text_extracted == 1
    assert len(transport.calls) == 1, "the bytes were already on disk"
    assert ArchiveRepository(connection).count() == 1
    record = ArchiveRepository(connection).existing(one_row)
    assert record.text_status == TEXT_EXTRACTED
    assert record.text_excerpt == "质押公告正文摘录"
    assert record.downloaded_at == STAMP, "the download time is not rewritten"


# --------------------------------------------------------------------------- #
# DoD: a missing extractor is exit 2, an unusable text layer is exit 0
# --------------------------------------------------------------------------- #


def test_a_missing_extractor_is_a_usage_error_before_anything_is_written(
    connection, paths, config, one_row
):
    transport = RecordingPdfTransport(responder=_responder())
    with pytest.raises(UsageError) as excinfo:
        archive_targets(
            _request(
                paths, connection, config, transport,
                targets=_targets(connection, announcement_ids=[one_row]),
                extractor=UnavailableExtractor(), text_requested=True,
            )
        )
    message = str(excinfo.value)
    assert "cnwatch[pdf]" in message and "pdftotext" in message
    # Refused before the first request: no download, no directory, no row.
    assert transport.calls == []
    assert not paths.pdf_dir.exists()
    assert ArchiveRepository(connection).count() == 0


def test_require_extractor_ignores_the_check_when_text_was_not_requested():
    require_extractor(UnavailableExtractor(), False)
    with pytest.raises(UsageError):
        require_extractor(UnavailableExtractor(), True)
    require_extractor(FakeExtractor(), True)


def test_cli_archive_with_text_and_no_backend_exits_two_and_creates_nothing(
    tmp_path, capsys, monkeypatch
):
    import cnwatch.archive as archive_package

    home = tmp_path / "home"
    monkeypatch.setattr(archive_package, "resolve_extractor", lambda *a, **k: UnavailableExtractor())

    code = main(
        [
            "--home", str(home), "archive", "--code", "600519", "--text",
            "--data-dir", str(DATA),
        ]
    )

    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "cnwatch[pdf]" in err and "pdftotext" in err
    assert not home.exists(), "a missing dependency must not create the runtime root"


def test_cli_archive_without_text_does_not_need_a_backend(tmp_path, capsys, monkeypatch):
    """The same command without --text gets past the extractor gate."""

    import cnwatch.archive as archive_package

    home = tmp_path / "home"
    monkeypatch.setattr(archive_package, "resolve_extractor", lambda *a, **k: UnavailableExtractor())

    code = main(
        ["--home", str(home), "archive", "--code", "600519", "--data-dir", str(DATA)]
    )

    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "no database" in err, "it proceeds to the database check instead"


def test_an_unusable_text_layer_is_unavailable_and_the_run_succeeds(
    connection, paths, config, one_row
):
    transport = RecordingPdfTransport(responder=_responder())
    extractor = FakeExtractor(error=ExtractionFailed("garbled text layer"))

    summary = archive_targets(
        _request(
            paths, connection, config, transport,
            targets=_targets(connection, announcement_ids=[one_row]),
            extractor=extractor, text_requested=True,
        )
    )

    assert summary.complete is True
    assert summary.failed == 0
    assert summary.text_unavailable == 1
    record = ArchiveRepository(connection).existing(one_row)
    assert record.text_status == TEXT_UNAVAILABLE
    assert record.text_excerpt == "", "no excerpt is invented for a failed extraction"
    assert Path(record.local_path).read_bytes() == PDF_BYTES


def test_an_empty_extraction_is_unavailable_not_a_success(
    connection, paths, config, one_row
):
    """A backend that returns nothing is the S5 'no text layer' case."""

    transport = RecordingPdfTransport(responder=_responder())
    for canned in ("", "   \n\t ", "\x00\x00"):
        summary = archive_targets(
            _request(
                paths, connection, config, transport,
                targets=_targets(connection, announcement_ids=[one_row]),
                extractor=FakeExtractor(canned), text_requested=True,
            )
        )
        record = ArchiveRepository(connection).existing(one_row)
        assert record.text_status == TEXT_UNAVAILABLE, canned
        assert record.text_excerpt == ""
        assert summary.text_unavailable == 1


def test_the_extractor_is_asked_for_the_documented_number_of_pages(
    connection, paths, config, one_row
):
    transport = RecordingPdfTransport(responder=_responder())
    extractor = FakeExtractor("正文")
    archive_targets(
        _request(
            paths, connection, config, transport,
            targets=_targets(connection, announcement_ids=[one_row]),
            extractor=extractor, text_requested=True,
        )
    )
    assert config.pages == 3
    assert extractor.calls == [{"bytes": len(PDF_BYTES), "pages": 3}]


def test_the_excerpt_is_capped_by_the_documented_length(connection, paths, config, one_row):
    transport = RecordingPdfTransport(responder=_responder())
    # Whitespace-heavy output, the shape a two-column PDF produces.
    extractor = FakeExtractor(("摘 要 正 文 " * 200) + "\n\n" + ("尾" * 1000))

    archive_targets(
        _request(
            paths, connection, config, transport,
            targets=_targets(connection, announcement_ids=[one_row]),
            extractor=extractor, text_requested=True,
        )
    )

    record = ArchiveRepository(connection).existing(one_row)
    assert config.excerpt_chars
    assert record.text_status == TEXT_EXTRACTED
    assert len(record.text_excerpt) == config.excerpt_chars
    assert "\n" not in record.text_excerpt and "  " not in record.text_excerpt


def test_cli_archive_with_text_succeeds_when_an_extractor_exists(
    tmp_path, capsys, monkeypatch
):
    """End to end through the CLI with the network and the extractor injected."""

    import cnwatch.archive as archive_package

    home = tmp_path / "home"
    _replayed_home(home, capsys)
    monkeypatch.setattr(
        UrllibPdfTransport,
        "get",
        lambda self, url, headers, timeout: Response(status=200, body=PDF_BYTES),
    )
    monkeypatch.setattr(fetch_transport, "RealSleep", NullSleep)
    monkeypatch.setattr(
        archive_package,
        "resolve_extractor",
        lambda *a, **k: FakeExtractor("公告正文摘录若干字"),
    )
    capsys.readouterr()

    code = main(
        [
            "--home", str(home), "archive", "--code", "300750", "--text",
            "--downloaded-at", STAMP, "--data-dir", str(DATA),
        ]
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "archived" in out and "text  extracted" in out

    connection = db.connect(home / "cnwatch.db")
    try:
        records = ArchiveRepository(connection).records()
    finally:
        connection.close()
    assert records, "the frozen day has a 300750 filing to archive"
    assert all(record.text_status == TEXT_EXTRACTED for record in records)
    assert all(record.downloaded_at == STAMP for record in records)
    assert all(Path(record.local_path).is_file() for record in records)
    assert all(record.text_excerpt for record in records)


# --------------------------------------------------------------------------- #
# failure isolation and the pre-record checks
# --------------------------------------------------------------------------- #


def test_one_failed_download_does_not_stop_the_others(connection, paths, config):
    _seed(
        connection,
        [
            ("1", "600519", "甲公告", DAY, "finalpage/2026-10-16/1.PDF", "category_ndbg_szsh", "low"),
            ("2", "300750", "乙公告", DAY, "finalpage/2026-10-16/2.PDF", "category_ndbg_szsh", "low"),
        ],
    )

    def answer(url, headers):
        if url.endswith("/2.PDF"):
            raise TransportError("HTTP 404 (injected)", retryable=False, status=404)
        return Response(status=200, body=PDF_BYTES)

    transport = RecordingPdfTransport(responder=answer)
    summary = archive_targets(
        _request(paths, connection, config, transport, targets=_targets(connection))
    )

    assert summary.archived == 1
    assert summary.failed == 1
    assert summary.complete is False
    failed = summary.failed_items()[0]
    assert failed.announcement_id == "2"
    assert failed.reason == "http_error"
    assert ArchiveRepository(connection).count() == 1
    assert ArchiveRepository(connection).existing("2") is None


def test_an_error_page_is_not_recorded_as_a_download(connection, paths, config, one_row):
    transport = RecordingPdfTransport(
        responder=_responder(body=b"<html>503 Service Unavailable</html>")
    )
    summary = archive_targets(
        _request(paths, connection, config, transport, targets=_targets(connection, announcement_ids=[one_row]))
    )
    assert summary.failed == 1
    assert summary.failed_items()[0].reason == "not_a_pdf"
    assert ArchiveRepository(connection).count() == 0
    assert not paths.pdf_dir.exists()


def test_an_empty_body_is_a_failure(connection, paths, config, one_row):
    transport = RecordingPdfTransport(responder=_responder(body=b""))
    summary = archive_targets(
        _request(paths, connection, config, transport, targets=_targets(connection, announcement_ids=[one_row]))
    )
    assert summary.failed_items()[0].reason == "empty_body"
    assert ArchiveRepository(connection).count() == 0


def test_a_row_without_a_url_is_a_named_failure(connection, paths, config):
    _seed(
        connection,
        [("1", "600519", "无链接公告", DAY, "", "category_ndbg_szsh", "low")],
    )
    transport = RecordingPdfTransport(responder=_responder())
    summary = archive_targets(
        _request(paths, connection, config, transport, targets=_targets(connection))
    )
    assert summary.failed_items()[0].reason == "missing_url"
    assert transport.calls == []


def test_retryable_failures_use_the_documented_backoff(connection, paths, config, one_row):
    attempts = {"count": 0}

    def flaky(url, headers):
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise TransportError("HTTP 503 (injected)", retryable=True, status=503)
        return Response(status=200, body=PDF_BYTES)

    transport = RecordingPdfTransport(responder=flaky)
    sleeper = RecordingSleep()
    summary = archive_targets(
        _request(
            paths, connection, config, transport,
            targets=_targets(connection, announcement_ids=[one_row]),
            downloader=_downloader(transport, config, sleeper=sleeper),
        )
    )

    assert summary.archived == 1
    assert attempts["count"] == 3
    assert list(config.retry_delays) == [1, 2, 4]
    assert sleeper.durations == [1, 2]
    assert (summary.requests, summary.retries) == (3, 2)


# --------------------------------------------------------------------------- #
# target selection
# --------------------------------------------------------------------------- #


def test_filters_are_watchlist_scoped_but_an_explicit_id_is_not(connection):
    _seed(
        connection,
        [("1", "600519", "自选高风险", DAY, "finalpage/2026-10-16/1.PDF", "category_fxts_szsh", "high")],
    )
    _announcement(connection, "9001", "000002", "全市场高风险", DAY, "finalpage/2026-10-16/9001.PDF",
                  source_kind="market")
    _classification(connection, "9001", "category_fxts_szsh", "high")
    connection.commit()

    by_risk = _targets(connection, risk="high")
    assert [target.announcement_id for target in by_risk] == ["1"], (
        "a --risk filter must not reach the market sweep (FR-18)"
    )

    by_id = _targets(connection, announcement_ids=["9001"])
    assert [target.announcement_id for target in by_id] == ["9001"], (
        "an explicitly named id is the user pointing at a row"
    )


def test_a_risk_filter_ignores_unlabelled_rows(connection):
    _seed(
        connection,
        [("1", "600519", "已打标", DAY, "finalpage/2026-10-16/1.PDF", "category_fxts_szsh", "high")],
    )
    _announcement(connection, "2", "300750", "未打标", DAY, "finalpage/2026-10-16/2.PDF")
    connection.commit()

    assert [t.announcement_id for t in _targets(connection, risk="high")] == ["1"]


def test_targets_are_ordered_deterministically(connection):
    _seed(
        connection,
        [
            ("b", "600519", "乙", DAY, "finalpage/2026-10-16/b.PDF", "category_ndbg_szsh", "low"),
            ("a", "600519", "甲", DAY, "finalpage/2026-10-16/a.PDF", "category_ndbg_szsh", "low"),
            ("c", "300750", "丙", DAY, "finalpage/2026-10-16/c.PDF", "category_ndbg_szsh", "low"),
        ],
    )
    assert [(t.code, t.announcement_id) for t in _targets(connection, risk="low")] == [
        ("300750", "c"),
        ("600519", "a"),
        ("600519", "b"),
    ]


def test_an_all_high_batch_is_reported_before_it_downloads(connection, paths, config):
    """`--all-high` is an explicit switch: the summary says how many it selected."""

    _seed(
        connection,
        [
            ("1", "600519", "高风险甲", DAY, "finalpage/2026-10-16/1.PDF", "category_fxts_szsh", "high"),
            ("2", "300750", "高风险乙", DAY, "finalpage/2026-10-16/2.PDF", "category_fxts_szsh", "high"),
            ("3", "601398", "低风险丙", DAY, "finalpage/2026-10-16/3.PDF", "category_ndbg_szsh", "low"),
        ],
    )
    transport = RecordingPdfTransport(responder=_responder())
    summary = archive_targets(
        _request(paths, connection, config, transport, targets=_targets(connection, risk="high"))
    )
    assert summary.targets == 2
    assert summary.archived == 2
    assert len(transport.calls) == 2


# --------------------------------------------------------------------------- #
# CLI contract
# --------------------------------------------------------------------------- #


def test_archive_is_no_longer_stage_gated():
    assert "archive" not in STAGE_NOT_READY


def test_cli_archive_without_a_filter_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    assert main(["--home", str(home), "archive", "--data-dir", str(DATA)]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "name" in err
    assert not home.exists()


def test_cli_archive_without_a_database_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    code = main(["--home", str(home), "archive", "--code", "600519", "--data-dir", str(DATA)])
    assert code == EXIT_USAGE
    assert "no database" in capsys.readouterr().err


def test_cli_archive_with_no_match_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    assert main(["--home", str(home), "selftest"]) == EXIT_OK
    capsys.readouterr()
    code = main(["--home", str(home), "archive", "--code", "600519", "--data-dir", str(DATA)])
    assert code == EXIT_USAGE
    assert "no stored announcement matches" in capsys.readouterr().err


def test_cli_archive_exits_zero_when_the_text_layer_is_unusable(
    tmp_path, capsys, monkeypatch
):
    """DoD: an unusable text layer is a degradation, not a failure of the round."""

    import cnwatch.archive as archive_package

    home = tmp_path / "home"
    _replayed_home(home, capsys)
    monkeypatch.setattr(
        UrllibPdfTransport,
        "get",
        lambda self, url, headers, timeout: Response(status=200, body=PDF_BYTES),
    )
    monkeypatch.setattr(fetch_transport, "RealSleep", NullSleep)
    monkeypatch.setattr(
        archive_package,
        "resolve_extractor",
        lambda *a, **k: FakeExtractor(error=ExtractionFailed("planted garbled text layer")),
    )
    capsys.readouterr()

    code = main(
        [
            "--home", str(home), "archive", "--code", "300750", "--text",
            "--downloaded-at", STAMP, "--data-dir", str(DATA),
        ]
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "result: complete" in out

    connection = db.connect(home / "cnwatch.db")
    try:
        records = ArchiveRepository(connection).records()
    finally:
        connection.close()
    assert records
    # The count is taken from what was stored, not from a transcribed number:
    # the frozen day's row count is a property of the snapshot.
    match = re.search(r"text  extracted (\d+)  unavailable (\d+)", out)
    assert match, out
    assert match.group(1) == "0"
    assert int(match.group(2)) == len(records)
    for record in records:
        assert record.text_status == TEXT_UNAVAILABLE
        assert record.text_excerpt == ""
        assert Path(record.local_path).is_file(), "the PDF is still saved"
        assert Path(record.local_path).stat().st_size == record.byte_size


def test_cli_archive_show_config_echoes_the_parsed_policy(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(["--home", str(home), "archive", "--show-config", "--data-dir", str(DATA)])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "pages: 3" in out
    assert "excerpt_chars: 400" in out
    assert "suffix: .PDF" in out
    assert "default_extract: off" in out


def test_cli_archive_rejects_a_bad_downloaded_at(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(
        [
            "--home", str(home), "archive", "--code", "600519",
            "--downloaded-at", "yesterday", "--data-dir", str(DATA),
        ]
    )
    assert code == EXIT_USAGE
    assert "ISO-8601" in capsys.readouterr().err
    assert not home.exists()


def test_cli_archive_exits_one_when_one_filing_fails(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    _replayed_home(home, capsys)

    seen = {"calls": 0}

    def answer(self, url, headers, timeout):
        seen["calls"] += 1
        if seen["calls"] == 1:
            raise TransportError("HTTP 404 (injected)", retryable=False, status=404)
        return Response(status=200, body=PDF_BYTES)

    monkeypatch.setattr(UrllibPdfTransport, "get", answer)
    monkeypatch.setattr(fetch_transport, "RealSleep", NullSleep)
    capsys.readouterr()

    code = main(
        [
            "--home", str(home), "archive", "--code", "601318",
            "--downloaded-at", STAMP, "--data-dir", str(DATA),
        ]
    )

    out = capsys.readouterr().out
    assert seen["calls"] >= 2, "the frozen day has two filings for this code"
    assert code == EXIT_DEGRADED
    assert "result: incomplete" in out
    assert "failures:" in out
    log = home / "out" / "logs" / "archive.log"
    assert log.is_file(), "per-filing detail belongs in out/logs"
    assert "requested=" in log.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# DoD: data/pdf is outside git's tracking (with a positive control)
# --------------------------------------------------------------------------- #


def _git(*args):
    return subprocess.run(
        ["git"] + list(args), cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="not a git checkout")
def test_data_pdf_is_git_ignored():
    assert (REPO / ".gitignore").is_file()
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/data/pdf/" in text

    ignored = _git("check-ignore", "-q", "--no-index", "--", "data/pdf/600519/2026-10-16/1.PDF")
    assert ignored.returncode == 0, "a downloaded PDF must be ignored by git"

    tracked = _git("ls-files", "data/pdf")
    assert tracked.stdout.strip() == b"", "no archived PDF may ever be tracked"


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="not a git checkout")
def test_the_ignore_gate_is_falsifiable():
    """Positive control: a frozen fixture is *not* ignored.

    Without this, a `.gitignore` that swallowed all of `data/` would make the
    check above pass for the wrong reason.
    """

    kept = _git(
        "check-ignore", "-q", "--no-index", "--",
        "data/snapshots/announce-day-2026-10-15.json",
    )
    assert kept.returncode == 1, "frozen snapshots must stay tracked"


# --------------------------------------------------------------------------- #
# the excerpt is never a classification input
# --------------------------------------------------------------------------- #


ARCHIVE_SOURCES = sorted(ARCHIVE_SRC.glob("*.py"))


def test_archive_package_has_sources_to_gate():
    assert len(ARCHIVE_SOURCES) >= 6


def test_the_archive_package_never_imports_the_matcher():
    """plan/04 5.3: the summary is for reading, not for deciding."""

    offenders = []
    for path in ARCHIVE_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "classify" or module.startswith("classify.") or module.endswith("classify"):
                    offenders.append("%s:%d imports %s" % (path.name, node.lineno, module))
                for alias in node.names:
                    if alias.name in ("classify_title", "classify_pending", "load_rule_package"):
                        offenders.append("%s:%d imports %s" % (path.name, node.lineno, alias.name))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "classify" in alias.name:
                        offenders.append("%s:%d imports %s" % (path.name, node.lineno, alias.name))
    assert offenders == []


def test_archiving_does_not_change_a_stored_classification(connection, paths, config, one_row):
    """The behavioural proof behind the AST gate."""

    before = connection.execute(
        "SELECT category, tags_json, risk_level, rules_version, classified_at "
        "FROM classifications WHERE announcement_id = ?",
        (one_row,),
    ).fetchone()

    transport = RecordingPdfTransport(responder=_responder())
    archive_targets(
        _request(
            paths, connection, config, transport,
            targets=_targets(connection, announcement_ids=[one_row]),
            extractor=FakeExtractor("质押 减持 诉讼 回购"), text_requested=True,
        )
    )

    after = connection.execute(
        "SELECT category, tags_json, risk_level, rules_version, classified_at "
        "FROM classifications WHERE announcement_id = ?",
        (one_row,),
    ).fetchone()
    assert tuple(before) == tuple(after)
    rows = connection.execute("SELECT COUNT(*) FROM classifications").fetchone()
    assert int(rows[0]) == 1


def test_the_archive_module_reads_no_clock_and_no_randomness():
    for path in ARCHIVE_SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "datetime.now(" not in text, path.name
        assert "time.time(" not in text, path.name
        assert "date.today()" not in text, path.name
        assert "random" not in text, path.name


def test_the_pdf_extractors_declare_their_dependency_state():
    """Whatever this machine has, the answer is a boolean, never an exception."""

    for extractor in (PdftotextExtractor(),):
        assert isinstance(extractor.available(), bool)
    assert UnavailableExtractor().available() is False
    with pytest.raises(ExtractionUnavailable):
        UnavailableExtractor().extract(b"%PDF-1.4", 3)


def test_a_download_error_carries_a_reason_and_a_retry_flag():
    error = PdfDownloadError("not_a_pdf", "response is HTML", retryable=False)
    assert error.reason == "not_a_pdf"
    assert error.retryable is False
    assert "not_a_pdf" in str(error)


def test_archive_target_model_defaults_are_empty_strings():
    target = ArchiveTarget(
        announcement_id="1",
        code="600519",
        name="测试",
        title="t",
        announce_date=DAY,
        source_url="http://example.invalid/1.PDF",
    )
    assert target.category == "" and target.risk_level == ""


def test_the_summary_render_names_every_outcome(connection, paths, config, one_row):
    transport = RecordingPdfTransport(responder=_responder())
    targets = _targets(connection, announcement_ids=[one_row])
    first = archive_targets(_request(paths, connection, config, transport, targets=targets))
    second = archive_targets(_request(paths, connection, config, transport, targets=targets))

    text = first.render()
    assert "archived 1" in text and "failed 0" in text and "result: complete" in text
    assert "text  not requested" in text
    assert "%d B" % len(PDF_BYTES) in text
    assert "requests 1 (retries 0)" in text

    text = second.render()
    assert "already present 1" in text and "already archived" in text
    assert "requests 0" in text, "a skipped filing makes no request"


# --------------------------------------------------------------------------- #
# offline replay helper (the frozen two days, no network)
# --------------------------------------------------------------------------- #


def _replayed_home(home, capsys):
    """A runtime home whose announcements come from the frozen snapshots."""

    assert main(["--home", str(home), "watchlist", "import", "--file", str(SAMPLE)]) == EXIT_OK
    assert main(
        [
            "--home", str(home), "fetch",
            "--snapshots-dir", str(SNAPSHOTS), "--today", DAY,
        ]
    ) in (EXIT_OK, EXIT_DEGRADED)
    assert main(["--home", str(home), "classify"]) == EXIT_OK
    capsys.readouterr()
    return home


def test_the_frozen_day_really_has_filings_for_the_cli_tests():
    """Guard for the two CLI tests above: the replayed row set is not empty."""

    snapshot = json.loads(
        (SNAPSHOTS / ("announce-day-%s.json" % DAY)).read_text(encoding="utf-8")
    )
    rows = [row for row in snapshot["announcements"] if row["secCode"] == "300750"]
    assert rows, "the frozen day must carry 300750 filings"
    assert all(row["adjunctUrl"].endswith(".PDF") for row in rows)

    two = [row for row in snapshot["announcements"] if row["secCode"] == "601318"]
    assert len(two) >= 2, "the partial-failure test needs two filings for one code"
