"""The four channels, and the injection point that makes "off" provable.

``none`` is not a channel that "does nothing quietly" — it is a channel that is
*not enabled*, and the service returns before it composes, reads or sends
anything. Every other channel is built only when it is selected, and each one
takes its outward-facing action through an injected callable:

===============  ==============================  =========================
channel          production action               what a test injects
===============  ==============================  =========================
``desktop``      an OS command (notify-send /    a recording runner
                 osascript / PowerShell toast)
``email``        ``smtplib.SMTP``                a recording sender
``webhook``      ``Transport.post``              ``RecordingTransport``
===============  ==============================  =========================

That is what lets a test assert the default channel performs *zero* external
calls: with ``none`` selected, none of the three objects above is ever built.
A send that fails raises ``NotifyError``; the service logs it and the CLI maps
it to exit 1. Nothing here writes to the database.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from ..errors import UsageError
from ..fetch.transport import Transport, TransportError
from .models import NotifyMessage

# A desktop toast is a courtesy, not a delivery guarantee: cap the text and the
# wait so a stuck OS helper cannot hold the round open.
DESKTOP_BODY_LIMIT = 250
DESKTOP_TIMEOUT_SECONDS = 30
WEBHOOK_TIMEOUT_SECONDS = 20


class NotifyError(Exception):
    """The message could not be delivered. Only the run log records it."""


class Notifier:
    """Interface: ``enabled`` says whether the channel is on at all."""

    name = "none"
    enabled = False

    def send(self, message: NotifyMessage) -> None:  # pragma: no cover - interface
        raise RuntimeError("Notifier.send() must be provided by a concrete channel")


class NullNotifier(Notifier):
    """The default. Not merely silent — not enabled, so nothing is even composed."""

    name = "none"
    enabled = False

    def send(self, message: NotifyMessage) -> None:
        return None


@dataclass
class DesktopNotifier(Notifier):
    """Hand the message to the operating system's own notification tool."""

    name = "desktop"
    enabled = True
    runner: Callable[[Sequence[str]], None] = None  # type: ignore[assignment]
    platform: str = field(default_factory=lambda: sys.platform)

    def send(self, message: NotifyMessage) -> None:
        argv = desktop_command(self.platform, message.title, message.body)
        if argv is None:
            raise NotifyError(
                "no desktop notification command is known for platform %r"
                % self.platform
            )
        runner = self.runner if self.runner is not None else run_command
        try:
            runner(argv)
        except NotifyError:
            raise
        except Exception as exc:  # noqa: BLE001 - any OS failure is one failure
            raise NotifyError("the desktop notification command failed: %s" % exc)


def _truncate(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _quote(text: str) -> str:
    """Single-quote for the OS tools we call.

    Both PowerShell and AppleScript take single-quoted string literals and both
    escape an embedded quote by doubling it, so one helper serves both.
    """

    return "'%s'" % (text or "").replace("'", "''")


def desktop_command(platform: str, title: str, body: str) -> Optional[List[str]]:
    """The per-platform argv, or ``None`` when this platform has no known tool."""

    text = _truncate(body, DESKTOP_BODY_LIMIT)
    if platform.startswith("linux"):
        return ["notify-send", title, text]
    if platform == "darwin":
        return [
            "osascript",
            "-e",
            "display notification %s with title %s"
            % (_quote(text), _quote(title)),
        ]
    if platform.startswith("win"):
        script = (
            "[reflection.assembly]::LoadWithPartialName('System.Windows.Forms') > $null;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            "$n.ShowBalloonTip(10000, %s, %s, 'Info');"
            "Start-Sleep -Seconds 3; $n.Dispose()"
            % (_quote(title), _quote(text))
        )
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    return None


def run_command(argv: Sequence[str]) -> None:
    """Production runner: run the OS tool, fail loudly on a non-zero exit."""

    try:
        completed = subprocess.run(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=DESKTOP_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        raise NotifyError("could not run %r: %s" % (argv[0], exc))
    except subprocess.TimeoutExpired:
        raise NotifyError("%r did not finish within %ds" % (argv[0], DESKTOP_TIMEOUT_SECONDS))
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise NotifyError(
            "%r exited %d%s"
            % (
                argv[0],
                completed.returncode,
                "" if not stderr else ": %s" % stderr.splitlines()[-1],
            )
        )


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    sender: str
    recipients: Tuple[str, ...]
    username: str = ""
    password: str = ""
    use_tls: bool = True


def send_via_smtp(settings: SmtpSettings, message: NotifyMessage) -> None:
    """Production email sender. Kept small; the seam is what tests use."""

    import smtplib
    from email.message import EmailMessage

    mail = EmailMessage()
    mail["Subject"] = message.title
    mail["From"] = settings.sender
    mail["To"] = ", ".join(settings.recipients)
    mail.set_content("%s\n%s" % (message.title, message.body))

    try:
        with smtplib.SMTP(settings.host, settings.port, timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            if settings.use_tls:
                client.starttls()
            if settings.username:
                client.login(settings.username, settings.password)
            client.send_message(mail)
    except Exception as exc:  # noqa: BLE001 - smtplib raises a wide family
        raise NotifyError("SMTP delivery to %s failed: %s" % (settings.host, exc))


@dataclass
class EmailNotifier(Notifier):
    name = "email"
    enabled = True
    settings: SmtpSettings = None  # type: ignore[assignment]
    sender: Callable[[SmtpSettings, NotifyMessage], None] = None  # type: ignore[assignment]

    def send(self, message: NotifyMessage) -> None:
        if self.settings is None:
            raise NotifyError("email delivery needs an SMTP host and a recipient")
        sender = self.sender if self.sender is not None else send_via_smtp
        try:
            sender(self.settings, message)
        except NotifyError:
            raise
        except Exception as exc:  # noqa: BLE001 - injected senders may raise anything
            raise NotifyError("email delivery failed: %s" % exc)


@dataclass
class WebhookNotifier(Notifier):
    name = "webhook"
    enabled = True
    url: str = ""
    transport: Optional[Transport] = None
    timeout_seconds: float = WEBHOOK_TIMEOUT_SECONDS

    def payload(self, message: NotifyMessage) -> bytes:
        return json.dumps(
            {
                "at": message.at,
                "body": message.body,
                "channel": self.name,
                "source": "cnwatch",
                "title": message.title,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")

    def send(self, message: NotifyMessage) -> None:
        if not self.url:
            raise NotifyError("webhook delivery needs a URL")
        if self.transport is None:
            raise NotifyError("webhook delivery needs a transport")
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "cnwatch/0.1",
        }
        try:
            response = self.transport.post(
                self.url, self.payload(message), headers, self.timeout_seconds
            )
        except TransportError as exc:
            raise NotifyError("webhook delivery to %s failed: %s" % (self.url, exc))
        if response.status >= 400:
            raise NotifyError(
                "webhook delivery to %s returned HTTP %d" % (self.url, response.status)
            )


def build_notifier(
    channel: str,
    *,
    transport: Optional[Transport] = None,
    webhook_url: Optional[str] = None,
    smtp: Optional[SmtpSettings] = None,
    desktop_runner: Optional[Callable[[Sequence[str]], None]] = None,
    platform: Optional[str] = None,
) -> Notifier:
    """The one place a channel becomes an object. ``none`` builds nothing else."""

    if channel == "none":
        return NullNotifier()
    if channel == "webhook":
        if not webhook_url:
            raise UsageError(
                "--channel webhook needs --webhook-url URL",
                hint="nothing was sent; add the URL you want the alert posted to",
            )
        return WebhookNotifier(url=webhook_url, transport=transport)
    if channel == "email":
        if smtp is None:
            raise UsageError(
                "--channel email needs --email-host and --email-to",
                hint="nothing was sent; the SMTP host, sender and recipient are required",
            )
        return EmailNotifier(settings=smtp)
    if channel == "desktop":
        return DesktopNotifier(
            runner=desktop_runner,
            platform=platform if platform is not None else sys.platform,
        )
    raise UsageError("unknown notification channel %r" % (channel,))


__all__ = [
    "DESKTOP_BODY_LIMIT",
    "DESKTOP_TIMEOUT_SECONDS",
    "WEBHOOK_TIMEOUT_SECONDS",
    "DesktopNotifier",
    "EmailNotifier",
    "NotifyError",
    "Notifier",
    "NullNotifier",
    "SmtpSettings",
    "WebhookNotifier",
    "build_notifier",
    "desktop_command",
    "run_command",
    "send_via_smtp",
]
