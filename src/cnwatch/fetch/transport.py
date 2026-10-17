"""HTTP transport and the global rate limiter.

``Transport`` is the injection point that makes offline replay possible: the
tests hand over frozen response bytes, production hands over ``urllib``. The
limiter is shared by every request in a round (list pages, the stock master,
PDFs later), so a "one second" policy is per request, not per endpoint.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional


class TransportError(Exception):
    """A request failed. ``retryable`` decides whether the backoff applies."""

    def __init__(self, message: str, retryable: bool, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status = status


@dataclass
class Response:
    status: int
    body: bytes

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")


class Transport:
    """Interface: one call = one HTTP request, no retry inside."""

    def post(self, url: str, body: bytes, headers: dict, timeout: float) -> Response:
        raise RuntimeError("Transport.post() must be provided by a concrete transport")


class UrllibTransport(Transport):
    def post(self, url: str, body: bytes, headers: dict, timeout: float) -> Response:
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Response(status=int(response.status), body=response.read())
        except urllib.error.HTTPError as exc:
            retryable = exc.code >= 500
            raise TransportError(
                "HTTP %d for %s" % (exc.code, url), retryable=retryable, status=exc.code
            )
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise TransportError("%s for %s" % (exc, url), retryable=True)


@dataclass
class RecordingTransport(Transport):
    """Test transport: returns queued responses and records every request."""

    responder: Callable[[str, bytes, dict], Response]
    calls: List[dict] = field(default_factory=list)

    def post(self, url: str, body: bytes, headers: dict, timeout: float) -> Response:
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        return self.responder(url, body, headers)


class ClockedSleep:
    """Injectable sleep: production sleeps, tests only record."""

    def __call__(self, duration: float) -> None:  # pragma: no cover - interface
        raise RuntimeError("Sleep must be provided by a concrete implementation")


@dataclass
class RealSleep(ClockedSleep):
    def __call__(self, duration: float) -> None:
        if duration > 0:
            time.sleep(duration)


@dataclass
class RecordingSleep(ClockedSleep):
    durations: List[float] = field(default_factory=list)
    clock: Optional["FakeMonotonic"] = None

    def __call__(self, duration: float) -> None:
        self.durations.append(duration)
        if self.clock is not None:
            self.clock.advance(duration)


@dataclass
class NullSleep(ClockedSleep):
    """Records the wait without sleeping.

    Only for replay: the request interval exists to protect the upstream
    service, and a replay from disk makes no request. The limiter still runs, so
    the waits are still computed and visible.
    """

    durations: List[float] = field(default_factory=list)

    def __call__(self, duration: float) -> None:
        self.durations.append(duration)


class MonotonicSource:
    def __call__(self) -> float:  # pragma: no cover - interface
        raise RuntimeError("a monotonic source is required")


@dataclass
class SystemMonotonic(MonotonicSource):
    def __call__(self) -> float:
        return time.monotonic()


@dataclass
class FakeMonotonic(MonotonicSource):
    """Deterministic clock advanced by sleeps, so tests can assert the gaps."""

    value: float = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class RateLimiter:
    """Keeps at least ``min_interval`` between the starts of two requests.

    The wait is computed from a monotonic source and performed through the
    injected sleep, so a test sees the exact durations without waiting.
    """

    def __init__(self, min_interval: float, sleep, monotonic=None) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must be >= 0")
        self._min_interval = float(min_interval)
        self._sleep = sleep
        self._monotonic = monotonic if monotonic is not None else SystemMonotonic()
        self._last: Optional[float] = None
        self.waits: List[float] = []

    @property
    def min_interval(self) -> float:
        return self._min_interval

    def wait(self) -> float:
        now = self._monotonic()
        waited = 0.0
        if self._last is not None:
            elapsed = now - self._last
            if elapsed < self._min_interval:
                waited = self._min_interval - elapsed
                self._sleep(waited)
                now = now + waited
        self._last = now
        if waited:
            self.waits.append(waited)
        return waited
