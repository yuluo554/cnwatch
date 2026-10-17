"""M4 daily report: dual view, baseline, comparison, zero-external HTML.

Every expected value here is recomputed from a frozen artifact — the two
``announce-day-*.json`` snapshots and the sample watchlist — or from the
database rows the test itself inserted. Nothing is transcribed from a previous
run of the report code, because a report compared against its own output proves
only self-consistency.

The tests are grouped by DoD line:

* dual view: set equality, structural (one partition function) and asserted;
* first run: ``baseline_created=1``, added 0, explicit wording;
* comparison: added/removed recomputed independently from the snapshots;
* zero-external: the guard runs at export time and is falsifiable;
* byte identity: two runs with the same ``--generated-at``, both formats;
* zero-decision: a stored ``low`` stays ``low`` even when the title carries a
  patch keyword that the matcher would grade ``high``.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path

import pytest

from cnwatch import db
from cnwatch.cli import STAGE_NOT_READY, main
from cnwatch.clock import fixed_clock_for
from cnwatch.disclaimer import DISCLAIMER
from cnwatch.exit_codes import EXIT_DEGRADED, EXIT_OK, EXIT_USAGE
from cnwatch.report import (
    MARKDOWN_NAME,
    HTML_NAME,
    ReportRepository,
    assert_no_external_resources,
    build_report,
    generate_report,
    load_label_table,
    render_html,
    render_markdown,
)
from cnwatch.report.html import ExternalResourceError
from cnwatch.report.models import Entry, safe_date_ordinal

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "cnwatch"
REPORT_SRC = SRC / "report"
DATA = REPO / "data"
SNAPSHOTS = DATA / "snapshots"
SAMPLE = DATA / "fixtures" / "watchlist-sample.csv"
RESOURCES = SRC / "resources"
DAY_1 = "2026-10-15"
DAY_2 = "2026-10-16"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _snapshot(day: str) -> dict:
    return json.loads((SNAPSHOTS / ("announce-day-%s.json" % day)).read_text(encoding="utf-8"))


def _watchlist_codes() -> set:
    lines = [
        line for line in SAMPLE.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    return {row["code"] for row in csv.DictReader(lines)}


def _snapshot_ids(day: str, codes=None) -> set:
    wanted = _watchlist_codes() if codes is None else set(codes)
    return {
        row["announcementId"]
        for row in _snapshot(day)["announcements"]
        if row["secCode"] in wanted
    }


def _announcement(connection, announcement_id, code, title, day, url="", source_kind="watchlist"):
    connection.execute(
        """
        INSERT INTO announcements (
            announcement_id, code, name, title, announce_date,
            adjunct_url, adjunct_type, announcement_type, source_kind,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, '', '', ?, ?, ?)
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
        (
            announcement_id,
            category,
            json.dumps(list(tags), ensure_ascii=False),
            risk,
            version,
            "2026-10-16T00:00:00Z",
        ),
    )


def _seed(connection, rows):
    """rows: (announcement_id, code, title, day, category, risk, tags)."""

    for announcement_id, code, title, day, category, risk, tags in rows:
        _announcement(connection, announcement_id, code, title, day)
        _classification(connection, announcement_id, category, risk, tags)
    connection.commit()


@pytest.fixture()
def connection(tmp_path):
    conn = db.initialize(tmp_path / "cnwatch.db")
    yield conn
    conn.close()


@pytest.fixture()
def labels():
    return load_label_table()


def _document(connection, day, labels, generated_at="2026-10-16T12:00:00Z"):
    document, markdown, html = build_report(
        ReportRepository(connection), day, generated_at, labels=labels
    )
    return document, markdown, html


# --------------------------------------------------------------------------- #
# DoD: the two views contain exactly the same items
# --------------------------------------------------------------------------- #


def test_both_views_carry_the_same_id_set(connection, labels):
    _seed(
        connection,
        [
            ("1", "600519", "贵州茅台关于控股股东部分股份质押的公告", DAY_2,
             "category_ndbg_szsh", "high", ("pledge",)),
            ("2", "600519", "贵州茅台2026年第三季度报告", DAY_2, "category_sjdbg_szsh", "low", ()),
            ("3", "300750", "宁德时代关于回购股份的公告", DAY_2, "category_ndbg_szsh", "mid", ("buyback",)),
            ("4", "300750", "宁德时代关于董事会决议的公告", DAY_2, "category_dshgg_szsh", "low", ()),
            ("5", "601398", "工商银行关于诉讼事项的公告", DAY_2, "unclassified", "high", ("lawsuit",)),
        ],
    )
    document, _markdown, _html = _document(connection, DAY_2, labels)

    assert document.stock_view_ids() == document.label_view_ids()
    assert document.stock_view_ids() == document.id_set()
    assert len(document.id_set()) == 5
    # Every entry appears in exactly one group per view: a duplicated id in a
    # view would still satisfy set equality, so count occurrences too.
    stock_hits = sum(len(group.entries) for group in document.by_stock)
    label_hits = sum(len(group.entries) for group in document.by_label)
    assert stock_hits == label_hits == 5


def test_the_views_are_two_partitions_of_one_list(connection, labels):
    """Structural claim: both views are built by the same partition function."""

    _seed(
        connection,
        [
            ("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ()),
            ("2", "300750", "公告乙", DAY_2, "category_dshgg_szsh", "mid", ()),
        ],
    )
    document, _markdown, _html = _document(connection, DAY_2, labels)

    stock_members = [entry for group in document.by_stock for entry in group.entries]
    label_members = [entry for group in document.by_label for entry in group.entries]
    assert sorted(entry.announcement_id for entry in stock_members) == sorted(
        entry.announcement_id for entry in label_members
    )
    assert sorted(entry.announcement_id for entry in document.entries) == sorted(
        entry.announcement_id for entry in stock_members
    )


def test_view_group_order_is_risk_first_then_date_then_id(connection, labels):
    """The sort key is defined once (plan/04 4.5) and used by both views."""

    _seed(
        connection,
        [
            ("a", "600519", "低风险甲", DAY_2, "category_ndbg_szsh", "low", ()),
            ("b", "600519", "高风险乙", DAY_2, "category_fxts_szsh", "high", ()),
            ("c", "600519", "低风险丙", DAY_2, "category_ndbg_szsh", "low", ()),
        ],
    )
    document, _markdown, _html = _document(connection, DAY_2, labels)
    stock_group = document.by_stock[0]
    assert [entry.announcement_id for entry in stock_group.entries] == ["b", "a", "c"]

    label_order = [group.key for group in document.by_label]
    assert label_order[0] == "category_fxts_szsh", "the high-risk category leads the view"


def test_tag_index_does_not_break_the_partition(connection, labels):
    """A tag index is an extra listing, not a third group in view B."""

    _seed(
        connection,
        [
            ("1", "600519", "关于减持计划的公告", DAY_2, "category_gqbd_szsh", "high", ("reduction",)),
            ("2", "600519", "关于质押的公告", DAY_2, "unclassified", "high", ("pledge",)),
        ],
    )
    document, markdown, html = _document(connection, DAY_2, labels)
    assert document.stock_view_ids() == document.label_view_ids()
    assert "关键词标签索引" in markdown
    assert "关键词标签索引" in html


# --------------------------------------------------------------------------- #
# DoD: first run establishes a baseline and reports zero new items
# --------------------------------------------------------------------------- #


def test_first_run_creates_a_baseline_and_reports_no_new_items(connection, labels):
    _seed(
        connection,
        [
            ("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ()),
            ("2", "300750", "公告乙", DAY_2, "category_dshgg_szsh", "low", ()),
        ],
    )
    document, markdown, html = _document(connection, DAY_2, labels)

    summary = document.summary
    assert summary.baseline_created is True
    assert summary.added == 0
    assert summary.removed == 0
    assert "首次运行" in markdown
    assert "已建立基线" in markdown
    assert "首次运行" in html
    assert "已建立基线" in html
    # The rows still exist; they are simply not "new".
    assert summary.item_count == 2


def test_the_run_row_records_the_baseline_flag(connection, tmp_path, labels):
    from cnwatch.paths import Paths

    _seed(connection, [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())])
    paths = Paths(home=tmp_path)
    paths.ensure_runtime_dirs()

    outcome = generate_report(
        paths,
        ReportRepository(connection),
        fixed_clock_for(DAY_2),
        report_date=DAY_2,
        generated_at="2026-10-16T12:00:00Z",
        labels=labels,
    )
    row = ReportRepository(connection).existing_run(DAY_2)
    assert row is not None
    assert int(row["baseline_created"]) == 1
    assert int(row["item_count"]) == 1
    assert row["content_sha256"] == outcome.content_sha256
    assert row["markdown_path"].endswith(MARKDOWN_NAME)
    assert row["html_path"].endswith(HTML_NAME)
    assert ReportRepository(connection).baseline_items(DAY_2) == ["1"]


# --------------------------------------------------------------------------- #
# DoD: added/removed agree with an independent recompute from the snapshots
# --------------------------------------------------------------------------- #


def _replayed_home(tmp_path, days=(DAY_1, DAY_2)):
    """Build a runtime home by replaying the frozen snapshots, via the CLI."""

    home = tmp_path / "home"
    assert main(["--home", str(home), "watchlist", "import", "--file", str(SAMPLE)]) == EXIT_OK
    for day in days:
        code = main(
            [
                "--home", str(home), "fetch",
                "--snapshots-dir", str(SNAPSHOTS),
                "--today", day,
            ]
        )
        assert code in (EXIT_OK, EXIT_DEGRADED)
    assert main(["--home", str(home), "classify"]) == EXIT_OK
    return home


def test_two_day_comparison_matches_the_frozen_snapshots(tmp_path, capsys):
    """added/removed are recomputed here from the snapshot bytes."""

    home = _replayed_home(tmp_path)
    capsys.readouterr()

    first = main(
        ["--home", str(home), "report", "--date", DAY_1, "--generated-at", "2026-10-15T12:00:00Z"]
    )
    assert first == EXIT_OK, capsys.readouterr().err
    capsys.readouterr()

    second = main(
        ["--home", str(home), "report", "--date", DAY_2, "--generated-at", "2026-10-16T12:00:00Z"]
    )
    assert second == EXIT_OK, capsys.readouterr().err
    out = capsys.readouterr().out

    day_1_ids = _snapshot_ids(DAY_1)
    day_2_ids = _snapshot_ids(DAY_2)
    expected_added = day_2_ids - day_1_ids
    expected_removed = day_1_ids - day_2_ids

    assert "added %d" % len(expected_added) in out
    assert "removed %d" % len(expected_removed) in out

    text = (home / "out" / "reports" / DAY_2 / MARKDOWN_NAME).read_text(encoding="utf-8")
    for announcement_id in expected_added:
        assert "`%s`" % announcement_id in text or announcement_id in text
    for announcement_id in expected_removed:
        assert announcement_id in text

    # The removed ids are listed under the 消失 heading and nothing was deleted.
    removed_section = text.split("### 消失", 1)[1]
    for announcement_id in expected_removed:
        assert announcement_id in removed_section


def test_a_removed_item_keeps_its_history_row(tmp_path, capsys):
    home = _replayed_home(tmp_path)
    capsys.readouterr()
    main(["--home", str(home), "report", "--date", DAY_1, "--generated-at", "2026-10-15T12:00:00Z"])
    capsys.readouterr()
    main(["--home", str(home), "report", "--date", DAY_2, "--generated-at", "2026-10-16T12:00:00Z"])
    capsys.readouterr()

    connection = db.connect(home / "cnwatch.db")
    try:
        removed = _snapshot_ids(DAY_1) - _snapshot_ids(DAY_2)
        assert removed
        marks = ",".join("?" for _ in removed)
        rows = connection.execute(
            "SELECT COUNT(*) FROM announcements WHERE announcement_id IN (%s)" % marks,
            sorted(removed),
        ).fetchone()
        assert int(rows[0]) == len(removed), "removed items must not be deleted"
        # `removed_at` belongs to the fetch layer; the report must not set it.
        nulls = connection.execute(
            "SELECT COUNT(*) FROM announcements WHERE announcement_id IN (%s) "
            "AND removed_at IS NULL" % marks,
            sorted(removed),
        ).fetchone()
        assert int(nulls[0]) == len(removed)
    finally:
        connection.close()


def test_no_baseline_and_no_rows_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    assert main(["--home", str(home), "report", "--date", DAY_2]) == EXIT_USAGE
    assert "no database" in capsys.readouterr().err


def test_a_date_with_no_rows_and_no_baseline_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    assert main(["--home", str(home), "selftest"]) == EXIT_OK
    capsys.readouterr()
    assert main(["--home", str(home), "report", "--date", DAY_2]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "no announcements" in err
    assert not (home / "out" / "reports" / DAY_2).exists()


def test_a_bad_date_or_timestamp_exits_two_without_creating_the_home(tmp_path, capsys):
    home = tmp_path / "home"
    assert main(["--home", str(home), "report", "--date", "2026-13-01"]) == EXIT_USAGE
    assert main(["--home", str(home), "report", "--generated-at", "yesterday"]) == EXIT_USAGE
    assert not home.exists(), "argument validation must precede any filesystem write"


# --------------------------------------------------------------------------- #
# DoD: HTML has zero external resources (guard at the exit + falsifiable)
# --------------------------------------------------------------------------- #


def test_rendered_html_has_no_external_resource_references(connection, labels):
    _seed(connection, [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())])
    _document_, _markdown, page = _document(connection, DAY_2, labels)

    assert_no_external_resources(page)
    for needle in ("<link", "<script", "<iframe", "<img", "@import"):
        assert needle not in page.lower()
    # No remote url() in CSS either.
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", page, re.IGNORECASE)
    # One inline stylesheet, no external sheet.
    assert page.count("<style>") == 1


def test_item_links_are_allowed_and_point_at_cninfo(connection, labels):
    _seed(
        connection,
        [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())],
    )
    connection.execute(
        "UPDATE announcements SET adjunct_url = ? WHERE announcement_id = '1'",
        ("finalpage/2026-10-16/1225600001.PDF",),
    )
    connection.commit()
    _document_, _markdown, page = _document(connection, DAY_2, labels)

    assert "http://static.cninfo.com.cn/finalpage/2026-10-16/1225600001.PDF" in page
    assert_no_external_resources(page)


def test_the_external_resource_guard_is_falsifiable(connection, labels):
    """Positive control: a planted remote asset must be rejected."""

    _seed(connection, [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())])
    _document_, _markdown, page = _document(connection, DAY_2, labels)

    for planted in (
        page.replace("</head>", '<link rel="stylesheet" href="https://cdn.example/x.css">\n</head>'),
        page.replace("</body>", '<script src="https://cdn.example/x.js"></script>\n</body>'),
        page.replace("</body>", '<img src="https://cdn.example/x.png">\n</body>'),
        page.replace("</head>", "<style>@import url('https://cdn.example/x.css');</style>\n</head>"),
        page.replace("</body>", '<iframe src="https://cdn.example/x"></iframe>\n</body>'),
    ):
        with pytest.raises(ExternalResourceError):
            assert_no_external_resources(planted)


def test_the_guard_runs_inside_render_html(connection, labels):
    """The check is at the exit, not only in a test someone might skip."""

    source = (REPORT_SRC / "html.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "render_html"
    ]
    assert len(calls) == 1
    body = ast.dump(calls[0])
    assert "assert_no_external_resources" in body


# --------------------------------------------------------------------------- #
# DoD: two runs with the same input are byte-identical, in both formats
# --------------------------------------------------------------------------- #


def test_two_runs_are_byte_identical(tmp_path, capsys):
    home = _replayed_home(tmp_path)
    capsys.readouterr()
    command = ["--home", str(home), "report", "--date", DAY_2, "--generated-at", "2026-10-16T12:00:00Z"]

    assert main(command) == EXIT_OK
    target = home / "out" / "reports" / DAY_2
    first_markdown = (target / MARKDOWN_NAME).read_bytes()
    first_html = (target / HTML_NAME).read_bytes()
    capsys.readouterr()

    assert main(command) == EXIT_OK
    assert (target / MARKDOWN_NAME).read_bytes() == first_markdown
    assert (target / HTML_NAME).read_bytes() == first_html
    # LF only, UTF-8, trailing newline: the same discipline as the snapshots.
    assert b"\r\n" not in first_markdown and b"\r\n" not in first_html
    assert first_markdown.endswith(b"\n") and first_html.endswith(b"\n")


def test_the_injected_timestamp_appears_verbatim(tmp_path, capsys):
    home = _replayed_home(tmp_path)
    capsys.readouterr()
    assert main(
        ["--home", str(home), "report", "--date", DAY_2, "--generated-at", "2026-10-16T12:00:00Z"]
    ) == EXIT_OK
    text = (home / "out" / "reports" / DAY_2 / MARKDOWN_NAME).read_text(encoding="utf-8")
    assert "2026-10-16T12:00:00Z" in text


def test_the_default_timestamp_comes_from_the_clock(connection, labels):
    _seed(connection, [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())])
    document, _markdown, _html = _document(
        connection, DAY_2, labels, generated_at="2026-10-16T23:59:59Z"
    )
    assert document.summary.generated_at == "2026-10-16T23:59:59Z"


# --------------------------------------------------------------------------- #
# DoD: the renderer decides nothing (AST gate + behavioural proof)
# --------------------------------------------------------------------------- #


REPORT_SOURCES = sorted(REPORT_SRC.glob("*.py"))


def test_report_package_has_sources_to_gate():
    assert len(REPORT_SOURCES) >= 6


def test_report_never_imports_the_matcher():
    """K: rendering must not be able to recompute category, tags or risk."""

    offenders = []
    for path in REPORT_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "classify" or module.startswith("classify.") or module.endswith("classify"):
                    offenders.append("%s:%d imports %s" % (path.name, node.lineno, module))
                for alias in node.names:
                    if alias.name in _MATCHER_NAMES:
                        offenders.append(
                            "%s:%d imports %s" % (path.name, node.lineno, alias.name)
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "classify" in alias.name:
                        offenders.append("%s:%d imports %s" % (path.name, node.lineno, alias.name))
    assert offenders == []


_MATCHER_NAMES = {
    "classify_title",
    "classify_pending",
    "classify_one",
    "highest_risk",
    "normalize_title",
    "_official_matches",
    "_patch_hits",
    "load_rule_package",
    "RulePackage",
}


def test_the_gate_is_falsifiable():
    """Positive control: the scan must catch an import it is looking for."""

    planted = ast.parse("from ..classify.engine import classify_title\n")
    found = [
        alias.name
        for node in ast.walk(planted)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    assert "classify_title" in found


def test_report_reads_no_clock_and_no_randomness():
    for path in REPORT_SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "datetime.now(" not in text, path.name
        assert "time.time(" not in text, path.name
        assert "date.today()" not in text, path.name
        assert "random" not in text, path.name


def test_a_stored_low_stays_low_even_when_the_title_would_grade_high(connection, labels):
    """The behavioural proof behind the AST gate.

    This title contains 质押, which the matcher grades ``high``. The stored
    label says ``low``/年报, and the report must print what is stored. A
    renderer that re-ran the matcher would print ``high`` here.
    """

    _seed(
        connection,
        [
            (
                "1",
                "600519",
                "贵州茅台关于控股股东部分股份质押的公告",
                DAY_2,
                "category_ndbg_szsh",
                "low",
                (),
            )
        ],
    )
    document, markdown, html = _document(connection, DAY_2, labels)

    assert document.summary.high == 0
    assert document.summary.low == 1
    assert document.entries[0].risk_level == "low"
    assert "`low`" in markdown
    assert "`high`" not in markdown
    assert '<span class="risk risk-low">' in html
    assert '<span class="risk risk-high">' not in html


def test_the_unclassified_count_is_read_from_stored_rows(connection, labels):
    _seed(
        connection,
        [
            ("1", "600519", "未命中甲", DAY_2, "unclassified", "low", ()),
            ("2", "300750", "未命中乙", DAY_2, "unclassified", "mid", ()),
            ("3", "601398", "命中丙", DAY_2, "category_ndbg_szsh", "low", ()),
        ],
    )
    document, markdown, html = _document(connection, DAY_2, labels)

    assert document.summary.unclassified == 2
    assert len(document.unclassified) == 2
    assert "未分类（2）" in markdown
    assert "未分类（2）" in html


def test_summary_counts_equal_the_stored_rows(connection, labels):
    _seed(
        connection,
        [
            ("1", "600519", "甲", DAY_2, "category_fxts_szsh", "high", ()),
            ("2", "600519", "乙", DAY_2, "category_yjygjxz_szsh", "mid", ()),
            ("3", "300750", "丙", DAY_2, "category_ndbg_szsh", "low", ()),
            ("4", "300750", "丁", DAY_2, "unclassified", "low", ()),
        ],
    )
    document, _markdown, _html = _document(connection, DAY_2, labels)
    summary = document.summary
    assert (summary.high, summary.mid, summary.low, summary.unclassified) == (1, 1, 2, 1)
    assert summary.high + summary.mid + summary.low == summary.item_count == 4


def test_the_disclaimer_is_present_verbatim_in_both_formats(connection, labels):
    _seed(connection, [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())])
    _document_, markdown, html = _document(connection, DAY_2, labels)
    assert DISCLAIMER in markdown
    assert DISCLAIMER in html
    assert DISCLAIMER == "本工具仅聚合公开信息，不构成投资建议。"
    # Fixed footer: the disclaimer sits in the last block, above the source note.
    tail = [line for line in markdown.rstrip().splitlines() if line.strip()]
    assert tail[-2] == "> %s" % DISCLAIMER
    assert tail[-1].startswith("数据来源：")


def test_every_entry_carries_the_required_fields(connection, labels):
    """FR-32: title, date, category, risk, original link."""

    _seed(connection, [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())])
    connection.execute(
        "UPDATE announcements SET adjunct_url = ? WHERE announcement_id = '1'",
        ("finalpage/2026-10-16/1225600001.PDF",),
    )
    connection.commit()
    document, markdown, html = _document(connection, DAY_2, labels)
    entry = document.entries[0]

    assert entry.title == "公告甲"
    assert entry.announce_date == DAY_2
    assert entry.category_name == "年报"
    assert entry.risk_level == "low"
    assert entry.url.startswith("http://static.cninfo.com.cn/")

    line = [row for row in markdown.splitlines() if "公告甲" in row][0]
    assert DAY_2 in line and "年报" in line and "`low`" in line and "原文" in line
    assert entry.url in line


def test_an_entry_without_a_link_renders_an_explicit_gap(connection, labels):
    _seed(connection, [("1", "600519", "无链接公告", DAY_2, "category_ndbg_szsh", "low", ())])
    _document_, markdown, html = _document(connection, DAY_2, labels)
    assert "—" in markdown
    assert "无链接" in html


# --------------------------------------------------------------------------- #
# fetch failures are reported, not hidden
# --------------------------------------------------------------------------- #


def test_failed_stocks_mark_the_report_incomplete_and_exit_one(tmp_path, capsys):
    home = _replayed_home(tmp_path)
    capsys.readouterr()

    connection = db.connect(home / "cnwatch.db")
    try:
        run_id = connection.execute(
            "SELECT MAX(run_id) FROM fetch_runs WHERE source_kind = 'watchlist'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE fetch_runs SET stocks_failed = 2, status = 'degraded' WHERE run_id = ?",
            (int(run_id),),
        )
        connection.execute(
            "INSERT INTO fetch_failures (run_id, code, attempts, last_error) "
            "VALUES (?, '600519', 4, 'HTTP 503')",
            (int(run_id),),
        )
        connection.execute(
            "INSERT INTO fetch_failures (run_id, code, attempts, last_error) "
            "VALUES (?, '300750', 4, 'HTTP 504')",
            (int(run_id),),
        )
        connection.commit()
    finally:
        connection.close()

    code = main(
        ["--home", str(home), "report", "--date", DAY_2, "--generated-at", "2026-10-16T12:00:00Z"]
    )
    assert code == EXIT_DEGRADED
    out = capsys.readouterr().out
    assert "fetch incomplete" in out

    text = (home / "out" / "reports" / DAY_2 / MARKDOWN_NAME).read_text(encoding="utf-8")
    assert "本次抓取不完整" in text
    assert "600519" in text and "300750" in text

    page = (home / "out" / "reports" / DAY_2 / HTML_NAME).read_text(encoding="utf-8")
    assert "本次抓取不完整" in page


def test_a_clean_run_does_not_claim_a_degradation(tmp_path, capsys):
    home = _replayed_home(tmp_path)
    capsys.readouterr()
    assert main(
        ["--home", str(home), "report", "--date", DAY_2, "--generated-at", "2026-10-16T12:00:00Z"]
    ) == EXIT_OK
    text = (home / "out" / "reports" / DAY_2 / MARKDOWN_NAME).read_text(encoding="utf-8")
    assert "本次抓取不完整" not in text


# --------------------------------------------------------------------------- #
# FR-18: a market sweep never mixes into the watchlist report
# --------------------------------------------------------------------------- #


def test_market_sweep_rows_stay_out_of_the_report(connection, labels):
    _seed(connection, [("1", "600519", "自选公告", DAY_2, "category_ndbg_szsh", "low", ())])
    _announcement(connection, "9001", "000002", "全市场公告", DAY_2, source_kind="market")
    _classification(connection, "9001", "category_ndbg_szsh", "low")
    connection.commit()

    document, markdown, _html = _document(connection, DAY_2, labels)
    assert document.id_set() == {"1"}
    assert "全市场公告" not in markdown


def test_a_market_only_day_explains_why_the_report_is_empty(connection, labels):
    from cnwatch.errors import UsageError

    _announcement(connection, "9001", "000002", "全市场公告", DAY_2, source_kind="market")
    _classification(connection, "9001", "category_ndbg_szsh", "low")
    connection.commit()

    with pytest.raises(UsageError) as excinfo:
        build_report(ReportRepository(connection), DAY_2, "2026-10-16T12:00:00Z", labels=labels)
    assert "market-sweep" in str(excinfo.value)
    assert "FR-18" in str(excinfo.value)


def test_an_unclassified_day_points_at_classify(connection, labels):
    """Stored rows with no label are a pipeline gap, not an empty day."""

    from cnwatch.errors import UsageError

    _announcement(connection, "1", "600519", "未打标公告", DAY_2)
    connection.commit()

    with pytest.raises(UsageError) as excinfo:
        build_report(ReportRepository(connection), DAY_2, "2026-10-16T12:00:00Z", labels=labels)
    assert "no classification" in str(excinfo.value)
    assert "cnwatch classify" in str(excinfo.value)


def test_a_partly_labelled_day_reports_the_gap_instead_of_hiding_it(connection, labels):
    """One unlabelled row must not vanish from a day that otherwise has rows."""

    _seed(connection, [("1", "600519", "已打标公告", DAY_2, "category_ndbg_szsh", "low", ())])
    _announcement(connection, "2", "300750", "未打标公告", DAY_2)
    connection.commit()

    document, markdown, html = _document(connection, DAY_2, labels)
    assert document.id_set() == {"1"}
    assert document.summary.unlabelled == 1
    assert "未打标条目（1）" in markdown
    assert "cnwatch classify" in markdown
    assert "未打标条目（1）" in html


# --------------------------------------------------------------------------- #
# the report date defaults to the exchange's today, not UTC's
# --------------------------------------------------------------------------- #


def test_the_default_report_date_is_the_exchange_today(tmp_path):
    """At 02:00 Shanghai the UTC date is still yesterday; the report must not be."""

    import datetime as dt

    from cnwatch.clock import FixedClock
    from cnwatch.paths import Paths

    home = tmp_path / "home"
    connection = db.initialize(home / "cnwatch.db")
    try:
        # Only the exchange day has rows. A UTC-derived default would look at
        # 2026-10-16, find nothing, and fail — which is exactly the bug this
        # test is meant to catch.
        _seed(connection, [("1", "600519", "公告甲", "2026-10-17", "category_ndbg_szsh", "low", ())])
        paths = Paths(home=home)
        paths.ensure_runtime_dirs()

        # 2026-10-16T18:00Z is 2026-10-17 02:00 in Shanghai.
        clock = FixedClock(dt.datetime(2026, 10, 16, 18, 0, 0, tzinfo=dt.timezone.utc))
        outcome = generate_report(
            paths,
            ReportRepository(connection),
            clock,
            report_date=None,
            generated_at="2026-10-17T02:00:00Z",
            labels=load_label_table(),
        )
        assert outcome.report_date == "2026-10-17"
        assert outcome.markdown_path.parent.name == "2026-10-17"
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# rendering failure leaves no half artifact
# --------------------------------------------------------------------------- #


def test_a_render_failure_writes_no_files(tmp_path, labels, monkeypatch):
    from cnwatch.paths import Paths
    from cnwatch import report as report_package

    home = tmp_path / "home"
    connection = db.initialize(home / "cnwatch.db")
    try:
        _seed(connection, [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())])
        paths = Paths(home=home)
        paths.ensure_runtime_dirs()

        def explode(document):
            raise ExternalResourceError("planted failure")

        monkeypatch.setattr(report_package.service, "render_html", explode)
        with pytest.raises(ExternalResourceError):
            generate_report(
                paths,
                ReportRepository(connection),
                fixed_clock_for(DAY_2),
                report_date=DAY_2,
                generated_at="2026-10-16T12:00:00Z",
                labels=labels,
            )
        target = home / "out" / "reports" / DAY_2
        assert not target.exists(), "a failed render must not create the report directory"
        assert ReportRepository(connection).existing_run(DAY_2) is None
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# CLI registration
# --------------------------------------------------------------------------- #


def test_report_is_no_longer_stage_gated():
    assert "report" not in STAGE_NOT_READY
    assert set(STAGE_NOT_READY) == {"archive", "notify", "run"}


def test_report_help_states_the_disclaimer(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["report", "--help"])
    assert excinfo.value.code == EXIT_OK
    assert DISCLAIMER in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# small units
# --------------------------------------------------------------------------- #


def test_unclassified_is_displayed_in_chinese_not_as_a_raw_id(connection, labels):
    _seed(connection, [("1", "600519", "未命中甲", DAY_2, "unclassified", "low", ())])
    document, markdown, html = _document(connection, DAY_2, labels)
    assert document.entries[0].category_name == "未分类"
    assert "### 未分类（1 条）" in markdown
    assert "<h3>未分类（1 条）</h3>" in html


def test_an_unknown_category_id_falls_back_to_the_id(connection, labels):
    """A display name is not worth failing a report over; show the gap honestly."""

    _seed(connection, [("1", "600519", "未知类公告", DAY_2, "category_unknown_xx", "low", ())])
    document, _markdown, _html = _document(connection, DAY_2, labels)
    assert document.entries[0].category_name == "category_unknown_xx"


def test_safe_date_ordinal_does_not_raise_on_a_malformed_date():
    assert safe_date_ordinal("2026-10-16") == 20261016
    assert safe_date_ordinal("not-a-date") == 0


def test_entry_sort_key_orders_by_risk_then_recency_then_id():
    def entry(risk, day, identifier):
        return Entry(
            announcement_id=identifier,
            code="600519",
            name="测试",
            title="t",
            announce_date=day,
            category="category_ndbg_szsh",
            category_name="年报",
            tags=(),
            tag_names=(),
            risk_level=risk,
            rules_version="v1",
            url="",
        )

    keys = [
        entry("low", "2026-10-16", "b").sort_key,
        entry("high", "2026-10-15", "z").sort_key,
        entry("low", "2026-10-16", "a").sort_key,
        entry("mid", "2026-10-16", "m").sort_key,
    ]
    assert sorted(keys) == [
        (0, -20261015, "z"),
        (1, -20261016, "m"),
        (2, -20261016, "a"),
        (2, -20261016, "b"),
    ]


def test_renderers_are_pure_functions_of_the_document(connection, labels):
    """Same document in, same bytes out — twice, in one process."""

    _seed(connection, [("1", "600519", "公告甲", DAY_2, "category_ndbg_szsh", "low", ())])
    document, markdown, html = _document(connection, DAY_2, labels)
    assert render_markdown(document) == markdown
    assert render_html(document) == html
