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
from pathlib import Path
from typing import Optional, Sequence

from . import __version__, db
from .clock import Clock, SystemClock, resolve_clock
from .datadir import resolve_data_dir, stock_master_path
from .encoding import ensure_utf8_stdio
from .errors import UsageError
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

    _add_watchlist_parser(subparsers)
    _add_fetch_parser(subparsers)

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


def _add_watchlist_parser(subparsers) -> None:
    watchlist_parser = subparsers.add_parser(
        "watchlist",
        help="manage the self-selected stock list (M2)",
        description="Maintain the watchlist. Groups are restricted to 持仓 / 观察 / 风险 "
        "and orgId always comes from the frozen stock master.",
    )
    watchlist_parser.add_argument(
        "action",
        choices=["import", "add", "remove", "edit", "list", "search", "sync-master"],
    )
    watchlist_parser.add_argument(
        "--data-dir",
        metavar="PATH",
        default=None,
        help="directory holding fixtures/ and snapshots/ (default: this checkout's data/)",
    )
    watchlist_parser.add_argument("--file", metavar="CSV", default=None, help="CSV for import")
    watchlist_parser.add_argument("--code", metavar="CODE", default=None)
    watchlist_parser.add_argument(
        "--group", metavar="GROUP", default=None, help="持仓 / 观察 / 风险"
    )
    watchlist_parser.add_argument("--note", metavar="TEXT", default=None)
    watchlist_parser.add_argument("--name", metavar="TEXT", default=None)
    watchlist_parser.add_argument(
        "--update",
        action="store_true",
        help="with add: overwrite an existing entry instead of refusing",
    )
    watchlist_parser.add_argument("--query", metavar="TEXT", default=None)
    watchlist_parser.add_argument(
        "--audit",
        action="store_true",
        help="with list/search: show the audit trail instead of the table",
    )


def _add_fetch_parser(subparsers) -> None:
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="incrementally fetch announcements, one stock per request (M2)",
        description="Fetch announcements for the watchlist. One request per stock; "
        "the window starts one day before the last successful run.",
    )
    fetch_parser.add_argument(
        "--all", action="store_true", help="market-wide fallback for one explicit date"
    )
    fetch_parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    fetch_parser.add_argument(
        "--today",
        metavar="YYYY-MM-DD",
        default=None,
        help="override 'today' for the window (reproducible runs and tests)",
    )
    fetch_parser.add_argument(
        "--data-dir",
        metavar="PATH",
        default=None,
        help="directory holding data/README.md, fixtures/ and snapshots/",
    )
    fetch_parser.add_argument(
        "--snapshots-dir",
        metavar="PATH",
        default=None,
        help="replay frozen announce-day-*.json files instead of using the network",
    )
    fetch_parser.add_argument(
        "--show-config",
        action="store_true",
        help="print the policy values parsed from data/README.md section 4",
    )


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


# --------------------------------------------------------------------------- #
# watchlist
# --------------------------------------------------------------------------- #


def cmd_watchlist(paths: Paths, args: argparse.Namespace) -> int:
    from .table import render_table
    from .watchlist import WatchlistService, load_stock_master
    from .watchlist.repository import WatchlistRepository

    data_dir = resolve_data_dir(args.data_dir, paths)
    master = load_stock_master(stock_master_path(data_dir))
    paths.ensure_runtime_dirs()
    connection = db.initialize(paths.db_path)
    try:
        service = WatchlistService(
            WatchlistRepository(connection),
            master,
            getattr(args, "clock", None) or SystemClock(),
        )
        action = args.action
        if action == "import":
            if not args.file:
                raise UsageError("import needs --file <csv>")
            outcome = service.import_csv(Path(args.file))
            print(
                "imported: added %d, updated %d, unchanged %d, rejected %d"
                % (outcome.added, outcome.updated, outcome.skipped, len(outcome.rejected))
            )
            for line, code, reason in outcome.rejected:
                print("  rejected line %d (%s): %s" % (line, code or "-", reason))
            return EXIT_DEGRADED if outcome.rejected else EXIT_OK

        if action == "add":
            if not args.code or not args.group:
                raise UsageError("add needs --code and --group")
            entry = service.add(
                args.code,
                args.group,
                note=args.note or "",
                name=args.name,
                update=args.update,
            )
            print(
                "added %s %s (%s, %s)"
                % (entry.code, entry.name, entry.group, entry.org_id)
            )
            return EXIT_OK

        if action == "remove":
            if not args.code:
                raise UsageError("remove needs --code")
            entry = service.remove(args.code)
            print("removed %s %s" % (entry.code, entry.name))
            return EXIT_OK

        if action == "edit":
            if not args.code:
                raise UsageError("edit needs --code")
            entry = service.edit(args.code, group=args.group, note=args.note)
            print("updated %s: group=%s note=%s" % (entry.code, entry.group, entry.note))
            return EXIT_OK

        if action == "sync-master":
            report = service.sync_master()
            print(
                "master %d codes; checked %d, refreshed %d, marked missing %d"
                % (
                    report["master_size"],
                    report["checked"],
                    report["refreshed"],
                    report["marked_missing"],
                )
            )
            return EXIT_OK

        if action in ("list", "search"):
            if action == "search":
                if not args.query:
                    raise UsageError("search needs --query")
                entries = service.search(args.query)
            else:
                entries = service.list_entries(args.group)
            if args.audit:
                return _print_audit(service, entries)
            if not entries:
                print("watchlist is empty" if action == "list" else "no matches")
                return EXIT_OK
            rows = [
                (
                    entry.code,
                    entry.name,
                    entry.group,
                    entry.org_id,
                    "yes" if entry.in_stock_master else "NO",
                    entry.note,
                )
                for entry in entries
            ]
            print(
                render_table(
                    ("code", "name", "group", "orgId", "in-master", "note"), rows
                )
            )
            return EXIT_OK

        raise UsageError("unhandled watchlist action %r" % (action,))
    finally:
        connection.close()


def _print_audit(service, entries) -> int:
    for entry in entries:
        records = service.audit(entry.code)
        if not records:
            continue
        print("%s %s" % (entry.code, entry.name))
        for record in records:
            print(
                "  #%d %s %-6s %s -> %s"
                % (
                    record.audit_id,
                    record.at,
                    record.action,
                    record.old_value or "-",
                    record.new_value or "-",
                )
            )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #


def cmd_fetch(paths: Paths, args: argparse.Namespace) -> int:
    from .fetch import (
        AnnouncementClient,
        FetchService,
        load_config,
        resolve_targets,
    )
    from .fetch.config import table_rows
    from .fetch.repository import FetchRepository
    from .fetch.transport import (
        RateLimiter,
        RealSleep,
        SystemMonotonic,
        UrllibTransport,
    )
    from .watchlist import WatchlistService, load_stock_master
    from .watchlist.repository import WatchlistRepository

    data_dir = resolve_data_dir(args.data_dir, paths)
    config = load_config(data_dir)
    if args.show_config:
        print("\n".join(table_rows(config)))
        return EXIT_OK

    clock: Clock = resolve_clock(args.today, getattr(args, "clock", None))
    paths.ensure_runtime_dirs()
    connection = db.initialize(paths.db_path)
    try:
        if args.snapshots_dir:
            from .fetch.replay import snapshot_transport
            from .fetch.transport import NullSleep

            # Replay reads from disk: the interval exists to protect the
            # upstream service, so the wait is computed and recorded but not
            # slept through.
            sleep = NullSleep()
            days = ["2026-10-15", "2026-10-16"]
            snapshots = Path(args.snapshots_dir).expanduser()
            if not snapshots.is_absolute():
                snapshots = Path.cwd() / snapshots
            transport = snapshot_transport(snapshots, days)
        else:
            sleep = RealSleep()
            transport = UrllibTransport()
        limiter = RateLimiter(config.min_interval_seconds, sleep, SystemMonotonic())
        client = AnnouncementClient(transport, limiter, config, sleeper=sleep)
        service = FetchService(
            FetchRepository(connection), client, config, clock
        )

        if args.all:
            if not args.date:
                raise UsageError(
                    "--all needs --date YYYY-MM-DD",
                    hint="the market sweep is explicit about its day; it never "
                    "guesses 'today'",
                )
            summary = service.run_market(args.date)
        else:
            master = load_stock_master(stock_master_path(data_dir))
            watchlist = WatchlistService(
                WatchlistRepository(connection), master, clock
            )
            targets = resolve_targets(watchlist.fetchable_entries())
            summary = service.run_watchlist(targets)

        print(summary.render())
        if summary.status == "complete":
            return EXIT_OK
        return EXIT_DEGRADED
    finally:
        connection.close()


# --------------------------------------------------------------------------- #

COMMANDS = ("selftest", "watchlist", "fetch", "classify", "report", "archive", "notify", "run")


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
    try:
        if command == "selftest":
            return cmd_selftest(paths, args)
        if command == "watchlist":
            return cmd_watchlist(paths, args)
        if command == "fetch":
            return cmd_fetch(paths, args)
    except UsageError as exc:
        print("cnwatch: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    # Defensive: a command that is neither dispatched nor marked not-ready is
    # a programming error in this module, not a user error.
    parser.error("unhandled command: %s" % command)
    return EXIT_USAGE


def _entry() -> None:  # pragma: no cover - console-script shim
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    _entry()
