"""M3 service and CLI: pending selection, one-transaction writes, exit codes.

The DoD lines covered here:

* the rule version is written into every row;
* running twice on the same input leaves ``classifications`` row-identical;
* a missing or truncated package exits 2 and writes **nothing** — not an empty
  table, not a half-written one;
* unclassified rows are stored like any other row, because a skipped row would
  make the daily report's "未分类" count permanently zero.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from cnwatch import db
from cnwatch.classify import (
    ClassifyRepository,
    classify_pending,
    load_rule_package,
)
from cnwatch.classify.rules import RulePackageError
from cnwatch.cli import main
from cnwatch.clock import fixed_clock_for
from cnwatch.exit_codes import EXIT_OK, EXIT_USAGE

REPO = Path(__file__).resolve().parents[1]
RESOURCES = REPO / "src" / "cnwatch" / "resources"
RULES = RESOURCES / "rules" / "rules-v1.json"
ENUM = RESOURCES / "official-categories.json"
DATA = REPO / "data"
SNAPSHOTS = DATA / "snapshots"
SAMPLE = DATA / "fixtures" / "watchlist-sample.csv"
CLOCK = fixed_clock_for("2026-10-16")


def _insert(connection, rows):
    """Seed announcements directly: the service under test is classify, not fetch."""

    for announcement_id, code, title, day in rows:
        connection.execute(
            """
            INSERT INTO announcements (
                announcement_id, code, name, title, announce_date,
                adjunct_url, adjunct_type, announcement_type, source_kind,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, '', '', '', 'watchlist', ?, ?)
            """,
            (
                announcement_id,
                code,
                "测试股份",
                title,
                day,
                "2026-10-16T00:00:00Z",
                "2026-10-16T00:00:00Z",
            ),
        )
    connection.commit()


@pytest.fixture()
def connection(tmp_path):
    conn = db.initialize(tmp_path / "cnwatch.db")
    yield conn
    conn.close()


SEED = [
    ("1001", "000001", "某某股份有限公司2025年年度报告", "2026-10-16"),
    ("1002", "000001", "关于控股股东部分股份质押的公告", "2026-10-16"),
    ("1003", "000002", "关于公司董事、高级管理人员减持计划期限届满未减持的公告", "2026-10-16"),
    ("1004", "000002", "2025 Annual Report", "2026-10-15"),
    ("1005", "000003", "关于召开2026年第二次临时股东会的通知", "2026-10-15"),
]


# --------------------------------------------------------------------------- #
# pending selection
# --------------------------------------------------------------------------- #


def test_first_run_labels_every_stored_row(connection):
    _insert(connection, SEED)
    repository = ClassifyRepository(connection)
    summary = classify_pending(repository, load_rule_package(), CLOCK)

    assert summary.pending == len(SEED) == 5
    assert summary.classified == 5
    assert repository.counts() == {"announcements": 5, "classifications": 5}


def test_second_run_has_nothing_pending(connection):
    _insert(connection, SEED)
    repository = ClassifyRepository(connection)
    package = load_rule_package()
    classify_pending(repository, package, CLOCK)

    again = classify_pending(repository, package, CLOCK)
    assert again.pending == 0
    assert again.classified == 0
    # Nothing is relabelled, so the counts are zero too; the totals still show.
    assert again.announcements_total == 5
    assert again.classifications_total == 5


def test_rows_labelled_with_an_older_version_are_relabelled(connection):
    """'upgrade the package, re-run classify' must work without a manual reset."""

    _insert(connection, SEED)
    repository = ClassifyRepository(connection)
    package = load_rule_package()
    classify_pending(repository, package, CLOCK)

    connection.execute("UPDATE classifications SET rules_version = 'v0'")
    connection.commit()

    summary = classify_pending(repository, package, CLOCK)
    assert summary.pending == 5
    assert summary.stale == 5
    versions = {row.rules_version for row in repository.all()}
    assert versions == {package.rules_version}


def test_since_limits_the_window_without_touching_older_rows(connection):
    _insert(connection, SEED)
    repository = ClassifyRepository(connection)
    package = load_rule_package()

    summary = classify_pending(repository, package, CLOCK, since="2026-10-16")
    assert summary.pending == 3
    assert repository.counts()["classifications"] == 3

    rest = classify_pending(repository, package, CLOCK)
    assert rest.pending == 2


# --------------------------------------------------------------------------- #
# what lands in the table
# --------------------------------------------------------------------------- #


def test_every_row_carries_the_rule_version_and_a_risk_level(connection):
    _insert(connection, SEED)
    repository = ClassifyRepository(connection)
    package = load_rule_package()
    classify_pending(repository, package, CLOCK)

    for row in repository.all():
        assert row.rules_version == package.rules_version
        assert row.risk_level in ("high", "mid", "low")
        assert row.classified_at, row.announcement_id
        assert isinstance(row.tags, tuple)


def test_unclassified_rows_are_stored_not_skipped(connection):
    _insert(connection, [("1004", "000002", "2025 Annual Report", "2026-10-15")])
    repository = ClassifyRepository(connection)
    summary = classify_pending(repository, load_rule_package(), CLOCK)

    stored = repository.get("1004")
    assert stored is not None, "an unclassified row must still have a row"
    assert stored.category == "unclassified"
    assert stored.risk_level == "low"
    assert summary.unclassified == 1


def test_tags_are_stored_as_canonical_json(connection):
    _insert(connection, [("1002", "000001", "关于控股股东部分股份质押的公告", "2026-10-16")])
    repository = ClassifyRepository(connection)
    classify_pending(repository, load_rule_package(), CLOCK)

    raw = connection.execute(
        "SELECT tags_json FROM classifications WHERE announcement_id = '1002'"
    ).fetchone()[0]
    assert raw == '["pledge"]'
    assert json.loads(raw) == ["pledge"]


def test_the_summary_counts_what_it_wrote(connection):
    _insert(connection, SEED)
    summary = classify_pending(ClassifyRepository(connection), load_rule_package(), CLOCK)

    counted = sum(summary.by_risk.values())
    assert counted == summary.classified
    assert sum(summary.by_category.values()) == summary.classified
    assert summary.unclassified == summary.by_category.get("unclassified", 0)
    # The reduction reverse case must not raise the tag or the risk.
    assert summary.by_risk.get("high", 0) == 1  # only the pledge row


# --------------------------------------------------------------------------- #
# determinism: same input twice, same table
# --------------------------------------------------------------------------- #


def _table_snapshot(connection):
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT announcement_id, category, tags_json, risk_level, rules_version "
            "FROM classifications ORDER BY announcement_id"
        )
    ]


def test_two_runs_on_a_copy_of_the_same_database_agree(connection, tmp_path):
    _insert(connection, SEED)
    package = load_rule_package()
    classify_pending(ClassifyRepository(connection), package, CLOCK)
    first = _table_snapshot(connection)

    copy_path = tmp_path / "copy.db"
    target = sqlite3.connect(str(copy_path))
    connection.backup(target)
    target.close()

    other = db.initialize(copy_path)
    try:
        classify_pending(ClassifyRepository(other), load_rule_package(), CLOCK)
        assert _table_snapshot(other) == first
    finally:
        other.close()


def test_relabelling_the_same_rows_twice_produces_identical_rows(connection):
    """Force a relabel of the same input: the written rows must not drift."""

    _insert(connection, SEED)
    repository = ClassifyRepository(connection)
    package = load_rule_package()
    classify_pending(repository, package, CLOCK)
    first = _table_snapshot(connection)

    connection.execute("DELETE FROM classifications")
    connection.commit()
    classify_pending(repository, package, CLOCK)

    assert _table_snapshot(connection) == first


# --------------------------------------------------------------------------- #
# package validation: exit 2 and nothing written
# --------------------------------------------------------------------------- #


def _resources_copy(tmp_path, *, categories=None, patch_ids=None):
    root = tmp_path / "resources"
    (root / "rules").mkdir(parents=True)
    shutil.copy(ENUM, root / "official-categories.json")
    payload = json.loads(RULES.read_text(encoding="utf-8"))
    if categories is not None:
        payload["official_categories"] = categories
    if patch_ids is not None:
        payload["keyword_patches"] = [
            patch for patch in payload["keyword_patches"] if patch["id"] in patch_ids
        ]
    (root / "rules" / "rules-v1.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_missing_rule_package_is_a_usage_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RulePackageError) as excinfo:
        load_rule_package(empty)
    assert "missing" in str(excinfo.value)


def test_truncated_category_table_is_refused(tmp_path):
    payload = json.loads(RULES.read_text(encoding="utf-8"))
    root = _resources_copy(tmp_path, categories=payload["official_categories"][:25])
    with pytest.raises(RulePackageError) as excinfo:
        load_rule_package(root)
    assert "expected 26" in str(excinfo.value)


def test_a_package_with_the_right_count_but_a_wrong_id_is_refused(tmp_path):
    """Count alone is not the gate; the id set must equal the bundled enum."""

    payload = json.loads(RULES.read_text(encoding="utf-8"))
    categories = [dict(rule) for rule in payload["official_categories"]]
    categories[0]["id"] = "category_made_up_szsh"
    root = _resources_copy(tmp_path, categories=categories)
    with pytest.raises(RulePackageError) as excinfo:
        load_rule_package(root)
    message = str(excinfo.value)
    assert "differ from the bundled enum" in message
    assert "category_made_up_szsh" in message


def test_missing_patch_is_refused(tmp_path):
    root = _resources_copy(tmp_path, patch_ids={"lawsuit", "reduction"})
    with pytest.raises(RulePackageError) as excinfo:
        load_rule_package(root)
    assert "keyword patches" in str(excinfo.value)


def test_package_that_claims_announcement_type_is_refused(tmp_path):
    root = _resources_copy(tmp_path)
    path = root / "rules" / "rules-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["match"]["announcement_type_used"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RulePackageError) as excinfo:
        load_rule_package(root)
    assert "announcement_type_used" in str(excinfo.value)


def test_a_broken_package_writes_no_classification(connection, tmp_path):
    """The DoD line: 规则包缺失/26 类不全 → 退出码 2 且不写任何分类结果."""

    _insert(connection, SEED)
    payload = json.loads(RULES.read_text(encoding="utf-8"))
    root = _resources_copy(tmp_path, categories=payload["official_categories"][:10])

    with pytest.raises(RulePackageError):
        classify_pending(ClassifyRepository(connection), load_rule_package(root), CLOCK)

    assert ClassifyRepository(connection).counts()["classifications"] == 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _seed_via_replay(home: Path, capsys) -> None:
    """Populate a runtime home through the M2 offline path, then drop output."""

    assert main(["--home", str(home), "watchlist", "import", "--file", str(SAMPLE)]) == EXIT_OK
    assert (
        main(
            [
                "--home", str(home), "fetch",
                "--snapshots-dir", str(SNAPSHOTS),
                "--today", "2026-10-16",
            ]
        )
        == EXIT_OK
    )
    capsys.readouterr()


def test_cli_classify_labels_the_replayed_rows(tmp_path, capsys):
    home = tmp_path / "home"
    _seed_via_replay(home, capsys)

    code = main(["--home", str(home), "classify"])
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "rules_version=v1" in out
    assert "pending 13" in out
    assert "database: announcements 13, classifications 13" in out

    connection = sqlite3.connect(str(home / "cnwatch.db"))
    try:
        rows = connection.execute(
            "SELECT COUNT(*) FROM classifications WHERE rules_version = 'v1'"
        ).fetchone()[0]
        assert rows == 13
    finally:
        connection.close()


def test_cli_classify_on_a_clean_home_is_a_no_op_exit_zero(tmp_path, capsys):
    home = tmp_path / "home"
    assert main(["--home", str(home), "classify"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "pending 0" in out
    assert "nothing pending" in out


def test_cli_classify_exits_two_when_the_package_is_missing(tmp_path, capsys):
    home = tmp_path / "home"
    empty = tmp_path / "empty-resources"
    empty.mkdir()

    code = main(["--home", str(home), "classify", "--resources", str(empty)])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "rule package is missing" in err
    # Exit 2 means nothing was produced: the runtime root was never created.
    assert not home.exists()


def test_cli_classify_exits_two_on_a_truncated_package_and_writes_nothing(tmp_path, capsys):
    home = tmp_path / "home"
    _seed_via_replay(home, capsys)
    payload = json.loads(RULES.read_text(encoding="utf-8"))
    root = _resources_copy(tmp_path, categories=payload["official_categories"][:20])

    code = main(["--home", str(home), "classify", "--resources", str(root)])
    assert code == EXIT_USAGE

    connection = sqlite3.connect(str(home / "cnwatch.db"))
    try:
        assert connection.execute("SELECT COUNT(*) FROM classifications").fetchone()[0] == 0
    finally:
        connection.close()


def test_cli_classify_rejects_a_malformed_since_date(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(["--home", str(home), "classify", "--since", "2026-1-1"])
    assert code == EXIT_USAGE
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_cli_classify_with_since_only_touches_the_window(tmp_path, capsys):
    """The replayed window spans 10-15 and 10-16; ``--since`` must cut the older day."""

    home = tmp_path / "home"
    _seed_via_replay(home, capsys)

    assert main(["--home", str(home), "classify", "--since", "2026-10-16"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "database: announcements 13, classifications 5" in out

    connection = sqlite3.connect(str(home / "cnwatch.db"))
    try:
        dates = {
            row[0]
            for row in connection.execute(
                "SELECT a.announce_date FROM classifications c "
                "JOIN announcements a ON a.announcement_id = c.announcement_id"
            )
        }
    finally:
        connection.close()
    assert dates == {"2026-10-16"}


def test_cli_classify_writes_the_also_matched_log(tmp_path, capsys):
    home = tmp_path / "home"
    _seed_via_replay(home, capsys)
    assert main(["--home", str(home), "classify"]) == EXIT_OK
    capsys.readouterr()

    log_path = home / "out" / "logs" / "classify-v1.log"
    assert log_path.is_file()
    text = log_path.read_text(encoding="utf-8")
    assert "rules_version=v1" in text
    assert "also_matched=category_zj_szsh" in text


def test_the_classify_log_keeps_earlier_runs(tmp_path, capsys):
    """A no-op run after a real one must not erase the diagnostics that explain it."""

    home = tmp_path / "home"
    _seed_via_replay(home, capsys)
    assert main(["--home", str(home), "classify"]) == EXIT_OK
    capsys.readouterr()
    assert main(["--home", str(home), "classify"]) == EXIT_OK
    capsys.readouterr()

    text = (home / "out" / "logs" / "classify-v1.log").read_text(encoding="utf-8")
    assert "pending=13" in text
    assert "pending=0" in text
    assert "also_matched=category_zj_szsh" in text
    # Two runs, two headers: the file is a history, not a snapshot.
    assert text.count("rules_version=v1") == 2


def test_cli_classify_twice_reports_nothing_pending(tmp_path, capsys):
    home = tmp_path / "home"
    _seed_via_replay(home, capsys)

    assert main(["--home", str(home), "classify"]) == EXIT_OK
    capsys.readouterr()
    assert main(["--home", str(home), "classify"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "pending 0" in out
    assert "nothing pending" in out
