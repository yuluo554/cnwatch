"""Environment and installation self-check.

The check must be falsifiable: pointing it at a directory without the bundled
resources has to fail. A self-test that always returns True is worse than no
self-test, because it turns "not verified" into "verified".
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import __version__, db
from .paths import Paths

RESOURCE_CATEGORIES = "official-categories.json"


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class SelfTestReport:
    checks: List[CheckResult]
    version: str
    python: str
    platform_name: str
    resources_dir: Path
    db_path: Path

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def failures(self) -> List[CheckResult]:
        return [check for check in self.checks if not check.ok]


def default_resources_dir() -> Path:
    return Path(__file__).resolve().parent / "resources"


def run_selftest(paths: Paths, resources_dir: Optional[Path] = None) -> SelfTestReport:
    """Run checks against explicit paths; never guesses a data root."""

    root = Path(resources_dir) if resources_dir is not None else default_resources_dir()
    checks: List[CheckResult] = []

    checks.append(
        CheckResult(
            "python_version",
            sys.version_info >= (3, 8),
            "%d.%d.%d" % sys.version_info[:3],
        )
    )

    categories_file = root / RESOURCE_CATEGORIES
    checks.append(
        CheckResult(
            "resources_dir",
            root.is_dir(),
            str(root) if root.is_dir() else "missing resources directory: %s" % root,
        )
    )
    checks.append(
        CheckResult(
            "category_table",
            categories_file.is_file(),
            str(categories_file)
            if categories_file.is_file()
            else "missing %s" % categories_file,
        )
    )

    try:
        paths.out_dir.mkdir(parents=True, exist_ok=True)
        probe = paths.out_dir / ".selftest"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
        detail = str(paths.out_dir)
    except OSError as exc:
        writable = False
        detail = "cannot write %s: %s" % (paths.out_dir, exc)
    checks.append(CheckResult("output_writable", writable, detail))

    try:
        connection = db.initialize(paths.db_path)
        found = db.schema_version(connection)
        missing = sorted(set(db.EXPECTED_TABLES) - db.table_names(connection))
        connection.close()
        checks.append(
            CheckResult(
                "database_schema",
                found == db.SCHEMA_VERSION and not missing,
                "schema=%s missing_tables=%s" % (found, ",".join(missing) or "none"),
            )
        )
    except Exception as exc:  # noqa: BLE001 - a broken DB must be reported, not raised
        checks.append(CheckResult("database_schema", False, str(exc)))

    return SelfTestReport(
        checks=checks,
        version=__version__,
        python=platform.python_version(),
        platform_name=platform.platform(),
        resources_dir=root,
        db_path=paths.db_path,
    )


def format_report(report: SelfTestReport) -> str:
    lines = [
        "cnwatch selftest  version=%s  python=%s" % (report.version, report.python),
        "platform: %s" % report.platform_name,
        "resources: %s" % report.resources_dir,
        "database: %s" % report.db_path,
    ]
    for check in report.checks:
        mark = "OK  " if check.ok else "FAIL"
        lines.append("[%s] %-18s %s" % (mark, check.name, check.detail))
    lines.append("result: %s" % ("pass" if report.ok else "fail"))
    return "\n".join(lines)
