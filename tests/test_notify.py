"""M5 notify: the default is off, a failure is contained, the message is honest.

The two DoD lines this file exists for are easy to fake, so both are tested
against a mechanism that could actually catch a violation:

* **zero external calls by default** — every outward path is replaced with a
  callable that raises, and a *positive control* proves the replacement is live
  by sending through the webhook channel with a recording transport;
* **a failed send touches nothing** — the report's bytes and the database's row
  counts are captured before the failure and compared after it.

No real network, no real SMTP, no real desktop notification is ever attempted.
"""

from __future__ import annotations

import hashlib
import json
import smtplib
from pathlib import Path

import pytest

from cnwatch import db
from cnwatch.cli import STAGE_NOT_READY, main
from cnwatch.disclaimer import DISCLAIMER
from cnwatch.errors import UsageError
from cnwatch.exit_codes import EXIT_DEGRADED, EXIT_OK, EXIT_USAGE
from cnwatch.fetch import transport as fetch_transport
from cnwatch.fetch.transport import RecordingTransport, Response
from cnwatch.notify import (
    CHANNELS,
    DISABLED,
    DRY_RUN,
    FAILED,
    MAX_BODY_ITEMS,
    NOTHING_TO_SEND,
    SENT,
    AlertRow,
    DesktopNotifier,
    EmailNotifier,
    NotifyError,
    NotifyRepository,
    NotifyRequest,
    NullNotifier,
    SmtpSettings,
    WebhookNotifier,
    build_notifier,
    compose_message,
    desktop_command,
    load_config,
    notify_day,
    parse_config,
)
from cnwatch.notify import channels as notify_channels

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "cnwatch"
NOTIFY_SRC = SRC / "notify"
DATA = REPO / "data"
SAMPLE = DATA / "fixtures" / "watchlist-sample.csv"
SNAPSHOTS = DATA / "snapshots"
DAY = "2026-10-16"
OTHER_DAY = "2026-10-15"
STAMP = "2026-10-16T12:00:00Z"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _announcement(connection, announcement_id, code, title, day, source_kind="watchlist"):
    connection.execute(
        """
        INSERT INTO announcements (
            announcement_id, code, name, title, announce_date,
            adjunct_url, adjunct_type, announcement_type, source_kind,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, '', '', '', ?, ?, ?)
        """,
        (announcement_id, code, "测试股份", title, day, source_kind,
         "2026-10-16T00:00:00Z", "2026-10-16T00:00:00Z"),
    )


def _classification(connection, announcement_id, category, risk):
    connection.execute(
        """
        INSERT INTO classifications (
            announcement_id, category, tags_json, risk_level, rules_version, classified_at
        ) VALUES (?, ?, '[]', ?, 'v1', '2026-10-16T00:00:00Z')
        """,
        (announcement_id, category, risk),
    )


def _seed(connection, rows):
    """rows: (announcement_id, code, title, risk, day, source_kind)."""

    for announcement_id, code, title, risk, day, source_kind in rows:
        _announcement(connection, announcement_id, code, title, day, source_kind)
        _classification(connection, announcement_id, "category_ndbg_szsh", risk)
    connection.commit()


def _alert(identifier, risk="low", day=DAY):
    return AlertRow(
        announcement_id=identifier,
        code="600519",
        name="测试股份",
        title="公告 %s" % identifier,
        announce_date=day,
        category="category_ndbg_szsh",
        risk_level=risk,
    )


def _recording_transport():
    return RecordingTransport(responder=lambda url, body, headers: Response(status=200, body=b"{}"))


class Boom:
    """Any external call reaching this fails the test in which it was planted."""

    def __init__(self, label):
        self.label = label
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("an external call was attempted through %s" % self.label)


@pytest.fixture()
def connection(tmp_path):
    conn = db.initialize(tmp_path / "cnwatch.db")
    yield conn
    conn.close()


def _replayed_home(home, capsys, days=(OTHER_DAY, DAY)):
    """A runtime home built from the frozen snapshots, no network."""

    assert main(["--home", str(home), "watchlist", "import", "--file", str(SAMPLE)]) == EXIT_OK
    for day in days:
        assert main(
            ["--home", str(home), "fetch", "--snapshots-dir", str(SNAPSHOTS), "--today", day]
        ) in (EXIT_OK, EXIT_DEGRADED)
    assert main(["--home", str(home), "classify"]) == EXIT_OK
    capsys.readouterr()
    return home


def _report_bytes(home, day):
    target = home / "out" / "reports" / day
    return {
        "md": hashlib.sha256((target / "daily.md").read_bytes()).hexdigest(),
        "html": hashlib.sha256((target / "daily.html").read_bytes()).hexdigest(),
    }


def _checkpoint(home):
    connection = db.connect(home / "cnwatch.db")
    try:
        return NotifyRepository(connection).checkpoint()
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# DoD: the default is none, and none means zero external calls
# --------------------------------------------------------------------------- #


def test_the_documented_default_channel_is_none():
    """Pinned so the README cannot silently switch outbound calls on."""

    config = load_config(DATA)
    assert config.default_channel == "none"


def test_the_default_is_read_from_the_policy_table_not_hard_coded():
    text = (
        "## 四、数值口径\n\n| 口径 | 值 | 说明 |\n|---|---|---|\n"
        "| `通知默认值` | `webhook`（关闭） | x |\n"
    )
    assert parse_config(text).default_channel == "webhook"
    with pytest.raises(UsageError):
        parse_config(
            "## 四、数值口径\n\n| 口径 | 值 |\n|---|---|\n| `通知默认值` | 稍后再说 |\n"
        )


def test_the_channel_choices_are_the_three_documented_ones_plus_none():
    assert CHANNELS == ("none", "desktop", "email", "webhook")
    with pytest.raises(SystemExit) as excinfo:
        main(["notify", "--channel", "carrier-pigeon"])
    assert excinfo.value.code == EXIT_USAGE


def test_the_default_channel_makes_zero_external_calls(tmp_path, capsys, monkeypatch):
    """Every outward path is replaced by something that would fail loudly."""

    post = Boom("transport.post")
    command = Boom("desktop command")
    smtp = Boom("smtplib.SMTP")

    monkeypatch.setattr(fetch_transport.UrllibTransport, "post", post)
    monkeypatch.setattr(notify_channels, "run_command", command)
    monkeypatch.setattr(smtplib, "SMTP", smtp)

    home = tmp_path / "home"
    code = main(["--home", str(home), "notify", "--data-dir", str(DATA)])
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "channel=none" in out
    assert "no external call was made" in out
    assert post.calls == [] and command.calls == [] and smtp.calls == []
    assert not home.exists(), "an off channel must not even create the runtime root"


def test_the_zero_call_test_can_actually_catch_a_call(tmp_path, capsys, monkeypatch):
    """Positive control: with webhook selected, the same spies record a call."""

    recorder = _recording_transport()
    monkeypatch.setattr(fetch_transport, "UrllibTransport", lambda *a, **k: recorder)

    home = _replayed_home(tmp_path / "home", capsys)
    code = main(
        [
            "--home", str(home), "notify", "--channel", "webhook",
            "--webhook-url", "http://example.invalid/hook", "--date", DAY,
            "--data-dir", str(DATA),
        ]
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "sent via webhook" in out
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["url"] == "http://example.invalid/hook"
    payload = json.loads(recorder.calls[0]["body"].decode("utf-8"))
    assert payload["source"] == "cnwatch"
    assert payload["channel"] == "webhook"
    assert DISCLAIMER in payload["body"]


def test_nothing_to_send_makes_no_call(tmp_path, capsys, monkeypatch):
    post = Boom("transport.post")
    monkeypatch.setattr(fetch_transport.UrllibTransport, "post", post)

    home = tmp_path / "home"
    assert main(["--home", str(home), "selftest"]) == EXIT_OK
    capsys.readouterr()

    code = main(
        [
            "--home", str(home), "notify", "--channel", "webhook",
            "--webhook-url", "http://example.invalid/hook", "--date", DAY,
            "--data-dir", str(DATA),
        ]
    )
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "nothing to send" in out
    assert post.calls == []


def test_a_dry_run_composes_and_sends_nothing(tmp_path, capsys, monkeypatch):
    post = Boom("transport.post")
    monkeypatch.setattr(fetch_transport.UrllibTransport, "post", post)

    home = _replayed_home(tmp_path / "home", capsys)
    code = main(
        [
            "--home", str(home), "notify", "--channel", "webhook",
            "--webhook-url", "http://example.invalid/hook", "--date", DAY,
            "--dry-run", "--data-dir", str(DATA),
        ]
    )
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "dry run" in out
    assert DISCLAIMER in out
    assert post.calls == []


# --------------------------------------------------------------------------- #
# DoD: a failed send exits 1, logs, and leaves the report and DB alone
# --------------------------------------------------------------------------- #


def test_a_failed_send_leaves_the_report_and_the_database_untouched(
    tmp_path, capsys, monkeypatch
):
    home = _replayed_home(tmp_path / "home", capsys)
    assert main(
        ["--home", str(home), "report", "--date", DAY, "--generated-at", STAMP]
    ) == EXIT_OK
    capsys.readouterr()

    before_report = _report_bytes(home, DAY)
    before_db = _checkpoint(home)

    def explode(self, message):
        raise NotifyError("planted delivery failure")

    monkeypatch.setattr(WebhookNotifier, "send", explode)

    code = main(
        [
            "--home", str(home), "notify", "--channel", "webhook",
            "--webhook-url", "http://example.invalid/hook", "--date", DAY,
            "--data-dir", str(DATA),
        ]
    )
    out = capsys.readouterr().out

    assert code == EXIT_DEGRADED, out
    assert "failed: planted delivery failure" in out
    assert "database are untouched" in out

    log = home / "out" / "logs" / "notify.log"
    assert log.is_file(), "the failure belongs in out/logs"
    logged = log.read_text(encoding="utf-8")
    assert "planted delivery failure" in logged
    assert "channel=webhook" in logged and "result=failed" in logged

    assert _checkpoint(home) == before_db
    # The report still regenerates to exactly the same bytes.
    assert main(
        ["--home", str(home), "report", "--date", DAY, "--generated-at", STAMP]
    ) == EXIT_OK
    capsys.readouterr()
    assert _report_bytes(home, DAY) == before_report


def test_a_successful_send_writes_no_database_row(tmp_path, capsys, monkeypatch):
    recorder = _recording_transport()
    monkeypatch.setattr(fetch_transport, "UrllibTransport", lambda *a, **k: recorder)

    home = _replayed_home(tmp_path / "home", capsys)
    before = _checkpoint(home)

    assert main(
        [
            "--home", str(home), "notify", "--channel", "webhook",
            "--webhook-url", "http://example.invalid/hook", "--date", DAY,
            "--data-dir", str(DATA),
        ]
    ) == EXIT_OK
    capsys.readouterr()

    assert len(recorder.calls) == 1
    assert _checkpoint(home) == before


def test_the_notify_repository_has_no_write_path():
    """FR-43 as a structure, not a promise: no INSERT/UPDATE/DELETE in the layer."""

    text = (NOTIFY_SRC / "repository.py").read_text(encoding="utf-8")
    for keyword in ("INSERT", "UPDATE", "DELETE", "DROP"):
        assert keyword not in text.upper().replace("UPDATED_AT", "")


def test_a_failed_desktop_command_is_only_a_notify_error(connection):
    """The round reports the failure instead of raising it out of notify_day."""

    _seed(connection, [("1", "600519", "公告甲", "high", DAY, "watchlist")])
    runner = Boom("runner")
    notifier = DesktopNotifier(runner=runner, platform="linux")

    outcome = notify_day(
        NotifyRequest(
            repository=NotifyRepository(connection),
            notifier=notifier,
            clock=_fixed_clock(),
            day=DAY,
            channel="desktop",
        )
    )

    assert outcome.reason == FAILED
    assert outcome.delivered is False
    assert outcome.items == 1
    assert "external call was attempted through runner" in outcome.detail
    assert len(runner.calls) == 1


# --------------------------------------------------------------------------- #
# the composed message
# --------------------------------------------------------------------------- #


def test_the_message_counts_the_day_and_carries_the_disclaimer():
    rows = [_alert("1", "high"), _alert("2", "mid"), _alert("3", "low")]
    message = compose_message(DAY, rows, "webhook", STAMP)
    assert "cnwatch %s 公告提醒" % DAY in message.title
    assert "high 1 / mid 1 / low 1" in message.title
    assert "600519 测试股份  [high]  公告 1" in message.body
    assert message.body.rstrip().endswith(DISCLAIMER)
    assert message.at == STAMP


def test_the_body_is_capped_and_summarises_the_rest():
    rows = [_alert(str(index), "high") for index in range(MAX_BODY_ITEMS + 5)]
    message = compose_message(DAY, rows, "webhook", STAMP)
    lines = message.body.splitlines()
    item_lines = [line for line in lines if line.startswith("600519")]
    assert len(item_lines) == MAX_BODY_ITEMS
    assert "其余 5 条见日报" in message.body
    # The counters are complete even though the list is not.
    assert "high %d" % len(rows) in message.title


def test_an_empty_day_composes_nothing():
    assert compose_message(DAY, [], "webhook", STAMP) is None


def test_the_message_is_stored_rows_only_and_watchlist_only(connection):
    _seed(
        connection,
        [
            ("1", "600519", "自选公告", "high", DAY, "watchlist"),
            ("9001", "000002", "全市场公告", "high", DAY, "market"),
            ("2", "300750", "另一天的公告", "high", OTHER_DAY, "watchlist"),
        ],
    )
    rows = NotifyRepository(connection).rows_for_day(DAY)
    assert [row.announcement_id for row in rows] == ["1"]

    message = compose_message(DAY, rows, "desktop", STAMP)
    assert "自选公告" in message.body
    assert "全市场公告" not in message.body
    assert "另一天的公告" not in message.body


def test_risk_orders_the_message(connection):
    _seed(
        connection,
        [
            ("a", "600519", "低", "low", DAY, "watchlist"),
            ("b", "600519", "高", "high", DAY, "watchlist"),
            ("c", "600519", "中", "mid", DAY, "watchlist"),
        ],
    )
    rows = NotifyRepository(connection).rows_for_day(DAY)
    assert [row.risk_level for row in rows] == ["high", "mid", "low"]


def test_an_unclassified_row_never_appears_in_an_alert(connection):
    """INNER JOIN: a row with no label has no risk to alert on."""

    _announcement(connection, "1", "600519", "未打标公告", DAY)
    _announcement(connection, "2", "300750", "已打标公告", DAY)
    _classification(connection, "2", "category_ndbg_szsh", "low")
    connection.commit()

    rows = NotifyRepository(connection).rows_for_day(DAY)
    assert [row.announcement_id for row in rows] == ["2"]


# --------------------------------------------------------------------------- #
# channels
# --------------------------------------------------------------------------- #


def test_the_null_notifier_is_not_enabled():
    notifier = build_notifier("none")
    assert isinstance(notifier, NullNotifier)
    assert notifier.enabled is False
    assert notifier.send(None) is None


def test_the_disabled_channel_never_reads_the_database(connection):
    """``DISABLED`` is returned before the first query."""

    outcome = notify_day(
        NotifyRequest(
            repository=NotifyRepository(connection),
            notifier=build_notifier("none"),
            clock=_fixed_clock(),
            day=DAY,
            channel="none",
        )
    )
    assert outcome.reason == DISABLED
    assert outcome.enabled is False
    assert outcome.items == 0


def test_webhook_without_a_url_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(
        ["--home", str(home), "notify", "--channel", "webhook", "--data-dir", str(DATA)]
    )
    assert code == EXIT_USAGE
    assert "--webhook-url" in capsys.readouterr().err
    assert not home.exists()


def test_email_without_a_host_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(
        [
            "--home", str(home), "notify", "--channel", "email",
            "--email-to", "someone@example.invalid", "--data-dir", str(DATA),
        ]
    )
    assert code == EXIT_USAGE
    assert "--email-host" in capsys.readouterr().err
    assert not home.exists()


def test_an_email_without_a_recipient_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(
        [
            "--home", str(home), "notify", "--channel", "email",
            "--email-host", "smtp.example.invalid", "--data-dir", str(DATA),
        ]
    )
    assert code == EXIT_USAGE
    assert "--email-to" in capsys.readouterr().err


def test_the_email_channel_hands_settings_and_message_to_an_injected_sender():
    seen = {}

    def sender(settings, message):
        seen["settings"] = settings
        seen["message"] = message

    settings = SmtpSettings(
        host="smtp.example.invalid",
        port=2525,
        sender="from@example.invalid",
        recipients=("to@example.invalid",),
    )
    notifier = EmailNotifier(settings=settings, sender=sender)
    message = compose_message(DAY, [_alert("1", "high")], "email", STAMP)
    notifier.send(message)

    assert seen["settings"] is settings
    assert seen["message"] is message
    assert "公告 1" in seen["message"].body


def test_an_email_sender_failure_becomes_a_notify_error():
    def sender(settings, message):
        raise OSError("connection refused")

    notifier = EmailNotifier(
        settings=SmtpSettings(
            host="smtp.example.invalid", port=25, sender="a@b.c", recipients=("d@e.f",)
        ),
        sender=sender,
    )
    with pytest.raises(NotifyError) as excinfo:
        notifier.send(compose_message(DAY, [_alert("1")], "email", STAMP))
    assert "connection refused" in str(excinfo.value)


def test_the_webhook_payload_is_sorted_json_with_the_message():
    notifier = WebhookNotifier(url="http://example.invalid/hook")
    message = compose_message(DAY, [_alert("1", "high")], "webhook", STAMP)
    payload = json.loads(notifier.payload(message).decode("utf-8"))
    assert list(payload) == sorted(payload), "sorted keys keep the payload reproducible"
    assert payload["title"] == message.title
    assert payload["body"] == message.body
    assert payload["at"] == STAMP


def test_the_webhook_reports_an_http_error():
    def responder(url, body, headers):
        return Response(status=500, body=b"nope")

    notifier = WebhookNotifier(
        url="http://example.invalid/hook",
        transport=RecordingTransport(responder=responder),
    )
    with pytest.raises(NotifyError) as excinfo:
        notifier.send(compose_message(DAY, [_alert("1")], "webhook", STAMP))
    assert "HTTP 500" in str(excinfo.value)


def test_the_webhook_reports_a_transport_error():
    from cnwatch.fetch.transport import TransportError

    def responder(url, body, headers):
        raise TransportError("connection refused (injected)", retryable=True)

    notifier = WebhookNotifier(
        url="http://example.invalid/hook",
        transport=RecordingTransport(responder=responder),
    )
    with pytest.raises(NotifyError) as excinfo:
        notifier.send(compose_message(DAY, [_alert("1")], "webhook", STAMP))
    assert "connection refused" in str(excinfo.value)


def test_the_desktop_command_is_per_platform():
    assert desktop_command("linux", "标题", "正文") == ["notify-send", "标题", "正文"]
    mac = desktop_command("darwin", "标题", "正文")
    assert mac[0] == "osascript" and "display notification" in mac[2]
    windows = desktop_command("win32", "标题", "正文")
    assert windows[0] == "powershell"
    assert "ShowBalloonTip" in windows[-1]
    assert desktop_command("plan9", "标题", "正文") is None


def test_the_desktop_body_is_truncated_for_the_os_tool():
    long_body = "正" * 500
    argv = desktop_command("linux", "标题", long_body)
    assert len(argv[2]) <= notify_channels.DESKTOP_BODY_LIMIT


def test_an_unknown_platform_is_a_named_send_failure():
    notifier = DesktopNotifier(runner=Boom("runner"), platform="plan9")
    with pytest.raises(NotifyError) as excinfo:
        notifier.send(compose_message(DAY, [_alert("1")], "desktop", STAMP))
    assert "plan9" in str(excinfo.value)


def test_a_desktop_runner_timeout_is_a_notify_error():
    def runner(argv):
        raise OSError("command not found")

    notifier = DesktopNotifier(runner=runner, platform="linux")
    with pytest.raises(NotifyError) as excinfo:
        notifier.send(compose_message(DAY, [_alert("1")], "desktop", STAMP))
    assert "command not found" in str(excinfo.value)


def test_the_desktop_channel_reaches_the_runner(tmp_path, capsys, monkeypatch):
    """A real send through the desktop channel, with the OS tool replaced."""

    seen = []
    monkeypatch.setattr(notify_channels, "run_command", lambda argv: seen.append(list(argv)))

    home = _replayed_home(tmp_path / "home", capsys)
    code = main(
        [
            "--home", str(home), "notify", "--channel", "desktop", "--date", DAY,
            "--data-dir", str(DATA),
        ]
    )
    out = capsys.readouterr().out

    assert code == EXIT_OK, out
    assert "sent via desktop" in out
    assert len(seen) == 1, "the desktop channel must hand the message to the OS tool"


def test_the_channels_help_lists_the_three_options_and_the_dry_run(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["notify", "--help"])
    assert excinfo.value.code == EXIT_OK
    out = capsys.readouterr().out
    for channel in ("none", "desktop", "email", "webhook"):
        assert channel in out
    assert "--dry-run" in out


def test_notify_show_config_echoes_the_parsed_policy(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(["--home", str(home), "notify", "--show-config", "--data-dir", str(DATA)])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "default_channel: none" in out


def test_notify_is_no_longer_stage_gated():
    assert "notify" not in STAGE_NOT_READY


def test_notify_needs_a_database_when_a_channel_is_on(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    code = main(
        [
            "--home", str(home), "notify", "--channel", "webhook",
            "--webhook-url", "http://example.invalid/hook", "--data-dir", str(DATA),
        ]
    )
    assert code == EXIT_USAGE
    assert "no database" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# the module itself
# --------------------------------------------------------------------------- #


NOTIFY_SOURCES = sorted(NOTIFY_SRC.glob("*.py"))


def test_notify_package_has_sources_to_gate():
    assert len(NOTIFY_SOURCES) >= 5


def test_the_notify_module_reads_no_clock_and_no_randomness():
    for path in NOTIFY_SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "datetime.now(" not in text, path.name
        assert "time.time(" not in text, path.name
        assert "date.today()" not in text, path.name
        assert "random" not in text, path.name


def test_the_outcome_render_names_the_reason():
    from cnwatch.notify import NotifyOutcome

    disabled = NotifyOutcome(
        channel="none", enabled=False, day=DAY, items=0, reason=DISABLED
    )
    assert "no external call" in disabled.render()

    failed = NotifyOutcome(
        channel="webhook", enabled=True, day=DAY, items=3, reason=FAILED,
        detail="planted",
    )
    assert failed.failed is True
    assert "planted" in failed.render()

    nothing = NotifyOutcome(
        channel="desktop", enabled=True, day=DAY, items=0, reason=NOTHING_TO_SEND
    )
    assert "nothing to send" in nothing.render()

    dry = NotifyOutcome(
        channel="email", enabled=True, day=DAY, items=1, reason=DRY_RUN,
        message=compose_message(DAY, [_alert("1")], "email", STAMP),
    )
    assert "dry run" in dry.render()
    assert "公告 1" in dry.render(show_message=True)

    sent = NotifyOutcome(channel="desktop", enabled=True, day=DAY, items=1, reason=SENT,
                         delivered=True)
    assert "sent via desktop" in sent.render()


def _fixed_clock():
    from cnwatch.clock import fixed_clock_for

    return fixed_clock_for(DAY)
