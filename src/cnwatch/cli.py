"""Command-line entry point.

Responsibilities, in order: parse arguments, map failures onto the documented
exit codes, dispatch. No business rules live here.

Commands for milestones that have not landed yet are listed in
``STAGE_NOT_READY``. They print a short explanation and exit 3 without
touching the filesystem. Each milestone removes its own entries; when the map
is empty the mechanism is retired together with ``EXIT_STAGE_NOT_READY``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import __version__
from .encoding import ensure_utf8_stdio
from .exit_codes import (
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_STAGE_NOT_READY,
    EXIT_USAGE,
)
from .paths import Paths, paths_for
from .selftest import format_report, run_selftest

# command -> (milestone, one-line reason)
STAGE_NOT_READY = {
    "watchlist": ("M2", "自选清单管理"),
    "fetch": ("M2", "增量抓取"),
    "classify": ("M3", "类别规则引擎"),
    "report": ("M4", "本地日报"),
    "archive": ("M5", "按需归档"),
    "notify": ("M5", "提醒渠道"),
    "run": ("M4", "端到端编排"),
}

EXIT_CODE_HELP = """exit codes:
  0  completed
  1  completed with a reported degradation (partial results)
  2  bad usage or unusable input; nothing was produced
  3  the command belongs to a milestone that is not implemented yet
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cnwatch",
        description="A-share announcement watchboard: self-selected codes, "
        "incremental fetch, rule-based tagging, local daily report.",
        epilog=EXIT_CODE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--home",
        metavar="PATH",
        default=None,
        help="runtime root (default: $CNWATCH_HOME or the current directory)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="cnwatch %s" % __version__,
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    selftest_parser = subparsers.add_parser(
        "selftest",
        help="check the environment and the installation surface",
        description="Run falsifiable environment checks and print a report.",
    )
    selftest_parser.add_argument(
        "--resources",
        metavar="PATH",
        default=None,
        help="override the bundled resources directory (used by tests)",
    )

    watchlist_parser = subparsers.add_parser(
        "watchlist", help="manage the self-selected stock list (M2)"
    )
    watchlist_parser.add_argument(
        "action",
        nargs="?",
        choices=["import", "add", "remove", "edit", "list", "search", "sync-master"],
    )

    fetch_parser = subparsers.add_parser(
        "fetch", help="incrementally fetch announcements, one stock per request (M2)"
    )
    fetch_parser.add_argument("--all", action="store_true", help="market-wide fallback")
    fetch_parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)

    classify_parser = subparsers.add_parser(
        "classify", help="tag stored announcements with the rule package (M3)"
    )
    classify_parser.add_argument("--since", metavar="YYYY-MM-DD", default=None)

    report_parser = subparsers.add_parser(
        "report", help="generate the Markdown and single-file HTML daily report (M4)"
    )
    report_parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    report_parser.add_argument("--generated-at", metavar="ISO8601", default=None)

    archive_parser = subparsers.add_parser(
        "archive", help="download PDFs on demand and extract the first pages (M5)"
    )
    archive_parser.add_argument("--announcement-id", action="append", default=[])
    archive_parser.add_argument("--code", action="append", default=[])
    archive_parser.add_argument("--risk", choices=["high", "mid", "low"], default=None)

    notify_parser = subparsers.add_parser(
        "notify", help="send alerts through the configured channel (M5)"
    )
    notify_parser.add_argument(
        "--channel", choices=["none", "desktop", "email", "webhook"], default="none"
    )

    run_parser = subparsers.add_parser(
        "run", help="fetch, classify and report in one pass (M4)"
    )
    run_parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)

    return parser


def _print_stage_not_ready(command: str) -> int:
    milestone, label = STAGE_NOT_READY[command]
    print(
        "cnwatch: %s (%s) is not implemented yet; planned for %s."
        % (command, label, milestone),
        file=sys.stderr,
    )
    print(
        "cnwatch: nothing was written. Run `cnwatch selftest` to verify the "
        "environment.",
        file=sys.stderr,
    )
    return EXIT_STAGE_NOT_READY


def cmd_selftest(paths: Paths, args: argparse.Namespace) -> int:
    resources = args.resources
    report = run_selftest(paths, resources_dir=resources)
    print(format_report(report))
    return EXIT_OK if report.ok else EXIT_DEGRADED


def main(argv: Optional[Sequence[str]] = None) -> int:
    ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE

    command = args.command
    if command in STAGE_NOT_READY:
        return _print_stage_not_ready(command)

    paths = paths_for(args.home)
    if command == "selftest":
        return cmd_selftest(paths, args)

    # Defensive: a command that is neither dispatched nor marked not-ready is
    # a programming error in this module, not a user error.
    parser.error("unhandled command: %s" % command)
    return EXIT_USAGE


def _entry() -> None:  # pragma: no cover - console-script shim
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    _entry()
