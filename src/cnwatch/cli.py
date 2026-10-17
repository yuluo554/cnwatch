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
from .clock import (
    Clock,
    ClockError,
    PinnedTimestamp,
    SystemClock,
    parse_date,
    resolve_clock,
)
from .datadir import resolve_data_dir, stock_master_path
from .disclaimer import DISCLAIMER
from .encoding import ensure_utf8_stdio
from .errors import UsageError
from .exit_codes import (
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_STAGE_NOT_READY,
    EXIT_USAGE,
)
from .paths import Paths, paths_for
from .policy import README_RELPATH
from .selftest import format_report, run_selftest

# command -> (milestone, one-line reason)
STAGE_NOT_READY = {
    # Every other command ships through v0.1.0. The end-to-end `run` shortcut
    # chains commands that already work one by one, but no milestone DoD
    # specified what a round should do with the results, so it keeps the stage
    # gate rather than pretending to orchestrate a round nobody specified
    # (plan/06 M5 deviation). The label is not a milestone number on purpose:
    # v0.1.0 is released without this command.
    "run": ("a later milestone", "端到端编排"),
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
        "classify",
        help="tag stored announcements with the frozen rule package (M3)",
        description="Label stored announcements as {category, tags, risk}. Reads "
        "the title only; announcementType never takes part in the decision.",
    )
    classify_parser.add_argument(
        "--since", metavar="YYYY-MM-DD", default=None, help="only announcements on or after this date"
    )
    classify_parser.add_argument(
        "--resources",
        metavar="PATH",
        default=None,
        help="override the bundled resources directory (used by tests)",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="generate the Markdown and single-file HTML daily report (M4)",
        description="Render the stored announcements of one report day as Markdown "
        "and a self-contained HTML page, and record the day's baseline. The report "
        "renders existing labels; it never recomputes category or risk.",
        epilog=DISCLAIMER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    report_parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="report day (default: today in the exchange timezone, UTC+8)",
    )
    report_parser.add_argument(
        "--generated-at",
        metavar="ISO8601",
        default=None,
        help="pin the timestamp printed in the report, so two runs are byte-identical",
    )
    report_parser.add_argument(
        "--resources",
        metavar="PATH",
        default=None,
        help="override the bundled resources directory (used by tests)",
    )

    archive_parser = subparsers.add_parser(
        "archive",
        help="download PDFs on demand and optionally extract their first pages (M5)",
        description="Download the filings the filters name into "
        "data/pdf/<code>/<date>/<announcement_id>.PDF and record local path, byte "
        "size and time. Nothing is downloaded unless a filter names it. The "
        "extracted text is for reading an alert; it is never a classification "
        "input.",
    )
    archive_parser.add_argument(
        "--announcement-id",
        action="append",
        default=[],
        metavar="ID",
        help="one filing by id (repeatable); not limited to the watchlist",
    )
    archive_parser.add_argument(
        "--code", action="append", default=[], metavar="CODE", help="one stock code (repeatable)"
    )
    archive_parser.add_argument(
        "--category", metavar="CATEGORY", default=None, help="official category id"
    )
    archive_parser.add_argument(
        "--risk", choices=["high", "mid", "low"], default=None, help="stored risk level"
    )
    archive_parser.add_argument(
        "--all-high",
        action="store_true",
        help="explicit batch: every stored watchlist filing graded high",
    )
    archive_parser.add_argument(
        "--text",
        action="store_true",
        help="also extract the first pages; needs PyMuPDF or pdftotext (exit 2 if neither)",
    )
    archive_parser.add_argument(
        "--downloaded-at",
        metavar="ISO8601",
        default=None,
        help="pin the recorded download time, so a re-run is reproducible",
    )
    archive_parser.add_argument(
        "--data-dir",
        metavar="PATH",
        default=None,
        help="directory holding data/README.md (the policy table)",
    )
    archive_parser.add_argument(
        "--show-config",
        action="store_true",
        help="print the archive policy parsed from data/README.md section 4",
    )

    notify_parser = subparsers.add_parser(
        "notify",
        help="send an alert through the configured channel; none by default (M5)",
        description="Compose one message from the stored items of a day and send "
        "it through desktop, email or webhook. The default channel is none, and "
        "none performs no external call at all. A failed send is logged and exits "
        "1; it never touches the report or the database.",
    )
    notify_parser.add_argument(
        "--channel",
        choices=["none", "desktop", "email", "webhook"],
        default=None,
        help="none (default, read from data/README.md), desktop, email or webhook",
    )
    notify_parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="day to report on (default: today in the exchange timezone, UTC+8)",
    )
    notify_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compose and print the message without sending it",
    )
    notify_parser.add_argument("--webhook-url", metavar="URL", default=None)
    notify_parser.add_argument("--email-host", metavar="HOST", default=None)
    notify_parser.add_argument("--email-port", metavar="PORT", type=int, default=25)
    notify_parser.add_argument("--email-from", metavar="ADDRESS", default="")
    notify_parser.add_argument("--email-to", metavar="ADDRESS", action="append", default=[])
    notify_parser.add_argument("--email-user", metavar="USER", default="")
    notify_parser.add_argument("--email-password", metavar="SECRET", default="")
    notify_parser.add_argument(
        "--data-dir",
        metavar="PATH",
        default=None,
        help="directory holding data/README.md (the policy table)",
    )
    notify_parser.add_argument(
        "--show-config",
        action="store_true",
        help="print the notify policy parsed from data/README.md section 4",
    )

    run_parser = subparsers.add_parser(
        "run", help="fetch, classify and report in one pass (not implemented)"
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
# classify
# --------------------------------------------------------------------------- #


def cmd_classify(paths: Paths, args: argparse.Namespace) -> int:
    from .classify import ClassifyRepository, classify_pending, load_rule_package

    # The package is validated before the database is opened: a broken package
    # must exit 2 without creating or touching a runtime directory.
    package = load_rule_package(args.resources)

    since = args.since
    if since is not None:
        try:
            parse_date(since)
        except ClockError as exc:
            raise UsageError(str(exc))

    paths.ensure_runtime_dirs()
    connection = db.initialize(paths.db_path)
    try:
        clock: Clock = resolve_clock(None, getattr(args, "clock", None))
        summary = classify_pending(
            ClassifyRepository(connection), package, clock, since=since
        )
        print(summary.render())
        _write_classify_log(paths, summary, summary.classified_at)
        return EXIT_OK
    finally:
        connection.close()


def _write_classify_log(paths: Paths, summary, at: str) -> None:
    """Per-row diagnostics (``also_matched``, dropped titles) go to out/logs.

    The console summary stays short; this file is where a reviewer checks why a
    specific announcement was labelled the way it was.

    The log appends rather than overwrites: a no-op run right after a real one
    would otherwise erase the diagnostics that explained the labels. Each run
    writes one header line plus its own rows, so the file is a readable history
    of which run produced which labels.
    """

    log_path = paths.logs_dir / ("classify-%s.log" % summary.rules_version)
    lines = summary.log_lines()
    header = "# %s  rules_version=%s  pending=%d  rows with diagnostics=%d\n" % (
        at,
        summary.rules_version,
        summary.pending,
        len(lines),
    )
    body = header + "".join(line + "\n" for line in lines)
    # ``Path.write_text(newline=...)`` needs 3.10; open explicitly so 3.8 gets
    # the same LF bytes as 3.12.
    with open(str(log_path), "a", encoding="utf-8", newline="\n") as handle:
        handle.write(body)


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


def cmd_report(paths: Paths, args: argparse.Namespace) -> int:
    from .report import (
        ReportRepository,
        generate_report,
        load_label_table,
        parse_generated_at,
    )

    # Both arguments are validated before anything is opened: a bad --date or
    # --generated-at must exit 2 with no runtime directory created, the same
    # way a broken rule package does for `classify` (K17).
    if args.date is not None:
        try:
            parse_date(args.date)
        except ClockError as exc:
            raise UsageError(str(exc))
    if args.generated_at is not None:
        parse_generated_at(args.generated_at)

    if not paths.db_path.is_file():
        raise UsageError(
            "no database at %s" % paths.db_path,
            hint="run `cnwatch fetch` and `cnwatch classify` before generating a report",
        )

    resources = getattr(args, "resources", None)
    labels = load_label_table(Path(resources) if resources else None)

    paths.ensure_runtime_dirs()
    connection = db.initialize(paths.db_path)
    try:
        clock: Clock = resolve_clock(None, getattr(args, "clock", None))
        outcome = generate_report(
            paths,
            ReportRepository(connection),
            clock,
            report_date=args.date,
            generated_at=args.generated_at,
            labels=labels,
        )
        print(outcome.render())
        return EXIT_OK if outcome.complete else EXIT_DEGRADED
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# archive
# --------------------------------------------------------------------------- #


def cmd_archive(paths: Paths, args: argparse.Namespace) -> int:
    from .archive import (
        ArchiveRepository,
        ArchiveRequest,
        archive_targets,
        load_config as load_archive_config,
        resolve_extractor,
    )
    from .archive.config import table_rows
    from .archive.service import require_extractor
    from .archive.transport import PdfDownloader, UrllibPdfTransport
    from .fetch.transport import RateLimiter, RealSleep, SystemMonotonic

    # This command needs the policy table, not the stock master; say so, so an
    # install outside a checkout is told which file is actually missing.
    data_dir = resolve_data_dir(args.data_dir, paths, require=README_RELPATH)
    config = load_archive_config(data_dir)
    if args.show_config:
        print("\n".join(table_rows(config)))
        return EXIT_OK

    risk = "high" if args.all_high else args.risk
    if not args.announcement_id and not args.code and not args.category and not risk:
        raise UsageError(
            "archive downloads only what you name",
            hint="pass --announcement-id, --code, --category, --risk or --all-high",
        )

    # ``--text`` is the explicit request; the documented default for the row is
    # off. Validated before anything is created, so a missing backend writes
    # nothing at all (plan/04 5.3).
    text_requested = bool(args.text) or config.default_extract
    extractor = resolve_extractor()
    require_extractor(extractor, text_requested)

    clock: Clock = resolve_clock(None, getattr(args, "clock", None))
    if args.downloaded_at is not None:
        try:
            clock = PinnedTimestamp(clock, args.downloaded_at)
        except ClockError as exc:
            raise UsageError(
                "--downloaded-at is not an ISO-8601 timestamp: %r" % (args.downloaded_at,),
                hint="for example 2026-10-16T12:00:00Z",
            )

    if not paths.db_path.is_file():
        raise UsageError(
            "no database at %s" % paths.db_path,
            hint="run `cnwatch fetch` first; archive works on stored rows",
        )

    paths.ensure_runtime_dirs()
    connection = db.initialize(paths.db_path)
    try:
        repository = ArchiveRepository(connection)
        targets = repository.targets(
            announcement_ids=args.announcement_id,
            codes=args.code,
            category=args.category,
            risk=risk,
        )
        if not targets:
            raise UsageError(
                "no stored announcement matches the given filters",
                hint="run `cnwatch fetch` (and `cnwatch classify` for --risk/--category) "
                "before archiving",
            )

        sleep = RealSleep()
        limiter = RateLimiter(config.min_interval_seconds, sleep, SystemMonotonic())
        downloader = PdfDownloader(
            UrllibPdfTransport(),
            limiter,
            config.request_timeout_seconds,
            config.retry_delays,
            sleeper=sleep,
        )
        summary = archive_targets(
            ArchiveRequest(
                paths=paths,
                repository=repository,
                downloader=downloader,
                clock=clock,
                config=config,
                targets=targets,
                extractor=extractor,
                text_requested=text_requested,
            )
        )
        print(summary.render())
        _write_archive_log(paths, summary, clock.timestamp())
        return EXIT_OK if summary.complete else EXIT_DEGRADED
    finally:
        connection.close()


def _write_archive_log(paths: Paths, summary, at: str) -> None:
    """Per-filing detail (byte sizes, text outcome, download failures) to out/logs.

    Appended like the classify log: a later run must not erase the record of
    which filing failed and why.
    """

    log_path = paths.logs_dir / "archive.log"
    lines = []
    for item in summary.items:
        lines.append(
            "%s %s %s bytes=%d text=%s %s"
            % (
                item.announcement_id,
                item.code,
                item.status,
                item.byte_size,
                item.text_status or "-",
                item.reason or item.detail or "-",
            )
        )
    header = "# %s  requested=%d archived=%d already=%d failed=%d text=%s\n" % (
        at,
        summary.targets,
        summary.archived,
        summary.skipped,
        summary.failed,
        "requested" if summary.text_requested else "off",
    )
    body = header + "".join(line + "\n" for line in lines)
    with open(str(log_path), "a", encoding="utf-8", newline="\n") as handle:
        handle.write(body)


# --------------------------------------------------------------------------- #
# notify
# --------------------------------------------------------------------------- #


def cmd_notify(paths: Paths, args: argparse.Namespace) -> int:
    from .fetch.transport import UrllibTransport
    from .notify import (
        NotifyRepository,
        NotifyRequest,
        SmtpSettings,
        build_notifier,
        load_config as load_notify_config,
        log_line,
        notify_day,
    )
    from .notify.config import table_rows

    data_dir = resolve_data_dir(args.data_dir, paths, require=README_RELPATH)
    config = load_notify_config(data_dir)
    if args.show_config:
        print("\n".join(table_rows(config)))
        return EXIT_OK

    channel = args.channel or config.default_channel

    clock: Clock = resolve_clock(None, getattr(args, "clock", None))
    if args.date is not None:
        try:
            parse_date(args.date)
        except ClockError as exc:
            raise UsageError(str(exc))
        day = args.date
    else:
        day = clock.exchange_today().strftime("%Y-%m-%d")

    # The documented default is off, and "off" means nothing is composed, read
    # or contacted — not even the runtime directories are touched.
    if channel == "none":
        print(
            "notify  channel=none  day=%s\ndisabled: the channel is none; "
            "no external call was made" % day
        )
        return EXIT_OK

    smtp = None
    if channel == "email":
        if not args.email_host or not args.email_to:
            raise UsageError(
                "--channel email needs --email-host and --email-to",
                hint="nothing was sent; SMTP host and at least one recipient are required",
            )
        smtp = SmtpSettings(
            host=args.email_host,
            port=int(args.email_port),
            sender=args.email_from or args.email_to[0],
            recipients=tuple(args.email_to),
            username=args.email_user,
            password=args.email_password,
        )

    notifier = build_notifier(
        channel,
        transport=UrllibTransport(),
        webhook_url=args.webhook_url,
        smtp=smtp,
    )

    if not paths.db_path.is_file():
        raise UsageError(
            "no database at %s" % paths.db_path,
            hint="run `cnwatch fetch` first; notify reports on stored rows",
        )

    connection = db.connect(paths.db_path)
    try:
        outcome = notify_day(
            NotifyRequest(
                repository=NotifyRepository(connection),
                notifier=notifier,
                clock=clock,
                day=day,
                channel=channel,
                dry_run=bool(args.dry_run),
            )
        )
    finally:
        connection.close()

    print(outcome.render(show_message=bool(args.dry_run) or outcome.failed))
    if outcome.failed:
        _write_notify_log(paths, outcome, clock.timestamp())
        return EXIT_DEGRADED
    return EXIT_OK


def _write_notify_log(paths: Paths, outcome, at: str) -> None:
    """One appended line per failed send. The only trace a failure is allowed."""

    from .notify import log_line

    paths.ensure_runtime_dirs()
    log_path = paths.logs_dir / "notify.log"
    with open(str(log_path), "a", encoding="utf-8", newline="\n") as handle:
        handle.write("%s  %s\n" % (at, log_line(outcome)))


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
        if command == "classify":
            return cmd_classify(paths, args)
        if command == "report":
            return cmd_report(paths, args)
        if command == "archive":
            return cmd_archive(paths, args)
        if command == "notify":
            return cmd_notify(paths, args)
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
