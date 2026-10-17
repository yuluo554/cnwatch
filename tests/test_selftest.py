"""Self-test must be able to fail; a green-always check proves nothing."""

import json
from pathlib import Path

from cnwatch.paths import Paths
from cnwatch.selftest import default_resources_dir, run_selftest


def test_selftest_passes_with_bundled_resources(tmp_path):
    report = run_selftest(Paths(home=tmp_path))
    assert report.ok, [c for c in report.checks if not c.ok]


def test_selftest_fails_when_resources_are_missing(tmp_path):
    report = run_selftest(Paths(home=tmp_path), resources_dir=tmp_path / "nope")
    assert not report.ok
    failed = {check.name for check in report.failures()}
    assert "resources_dir" in failed
    assert "category_table" in failed


def test_selftest_fails_when_category_table_is_absent(tmp_path):
    resources = tmp_path / "resources"
    resources.mkdir()
    report = run_selftest(Paths(home=tmp_path), resources_dir=resources)
    assert not report.ok
    assert {check.name for check in report.failures()} == {"category_table", "rule_package"}


def test_selftest_fails_when_the_rule_package_is_absent(tmp_path):
    """The rule package ships in the wheel; a selftest that ignores it is blind."""

    resources = tmp_path / "resources"
    (resources / "rules").mkdir(parents=True)
    (resources / "official-categories.json").write_text(
        default_resources_dir().joinpath("official-categories.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    report = run_selftest(Paths(home=tmp_path), resources_dir=resources)
    assert not report.ok
    assert {check.name for check in report.failures()} == {"rule_package"}


def test_selftest_rejects_a_truncated_rule_package(tmp_path):
    resources = tmp_path / "resources"
    (resources / "rules").mkdir(parents=True)
    (resources / "official-categories.json").write_text(
        default_resources_dir().joinpath("official-categories.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bundled = default_resources_dir() / "rules" / "rules-v1.json"
    payload = json.loads(bundled.read_text(encoding="utf-8"))
    payload["official_categories"] = payload["official_categories"][:3]
    (resources / "rules" / "rules-v1.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    report = run_selftest(Paths(home=tmp_path), resources_dir=resources)
    assert not report.ok
    failed = {check.name: check for check in report.failures()}
    assert "rule_package" in failed
    assert "categories=3" in failed["rule_package"].detail


def test_selftest_creates_database_in_home(tmp_path):
    report = run_selftest(Paths(home=tmp_path))
    assert report.ok
    assert report.db_path == tmp_path / "cnwatch.db"
    assert report.db_path.is_file()


def test_default_resources_dir_ships_category_table():
    root = default_resources_dir()
    assert (root / "official-categories.json").is_file()
    assert Path(root).name == "resources"
