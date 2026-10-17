"""Getting PDF bytes: one HTTP GET per filing, under the round's policy.

The archive path reuses the same rate limiter as the announcement query
(plan/04 §2.6: every request in a round — list pages, stock master, PDFs —
shares one minimum interval), and the same retry/backoff and timeout numbers
from ``data/README.md`` section 4. Only the transport differs: a PDF is a GET
of a static file, not a POST of a search form.

Three checks run before bytes count as a download, because the failure they
catch is invisible in a size-only record:

* the response status must be 200;
* the body must not be empty;
* the body must start with ``%PDF-``.

An error page saved as ``<id>.PDF`` would otherwise be recorded as a successful
byte count, and the user would discover it when they tried to open it.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List

from ..fetch.transport import RateLimiter, Response, TransportError

PDF_MAGIC = b"%PDF-"

PDF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cnwatch/0.1",
    "Accept": "application/pdf,*/*",
}


class PdfDownloadError(Exception):
    """One filing could not be downloaded. ``retryable`` decides about backoff."""

    def __init__(self, reason: str, detail: str, retryable: bool = False) -> None:
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail
        self.retryable = retryable


class PdfTransport:
    """Interface: one call = one GET, no retry inside."""

    def get(self, url: str, headers: dict, timeout: float) -> Response:
        raise RuntimeError("PdfTransport.get() must be provided by a concrete transport")


class UrllibPdfTransport(PdfTransport):
    def get(self, url: str, headers: dict, timeout: float) -> Response:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Response(status=int(response.status), body=response.read())
        except urllib.error.HTTPError as exc:
            raise TransportError(
                "HTTP %d for %s" % (exc.code, url), retryable=exc.code >= 500, status=exc.code
            )
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise TransportError("%s for %s" % (exc, url), retryable=True)


@dataclass
class RecordingPdfTransport(PdfTransport):
    """Test transport: answers from a responder and records every request."""

    responder: Callable[[str, dict], Response]
    calls: List[dict] = field(default_factory=list)

    def get(self, url: str, headers: dict, timeout: float) -> Response:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self.responder(url, headers)


class PdfDownloader:
    """One GET per filing with the documented interval, retries and checks."""

    def __init__(
        self,
        transport: PdfTransport,
        limiter: RateLimiter,
        timeout_seconds: float,
        retry_delays=(),
        sleeper=None,
    ) -> None:
        self._transport = transport
        self._limiter = limiter
        self._timeout = timeout_seconds
        self._delays = tuple(retry_delays)
        self._sleep = sleeper
        self.attempts = 0
        self.retries = 0

    def fetch(self, url: str) -> bytes:
        if not url:
            raise PdfDownloadError("missing_url", "the stored row has no adjunct_url")

        attempt = 0
        while True:
            self._limiter.wait()
            self.attempts += 1
            if attempt:
                self.retries += 1
            try:
                response = self._transport.get(url, PDF_HEADERS, self._timeout)
            except TransportError as exc:
                if not exc.retryable:
                    raise PdfDownloadError("http_error", exc.message)
                if attempt >= len(self._delays):
                    raise PdfDownloadError("retries_exhausted", exc.message, retryable=True)
                delay = self._delays[attempt]
                if self._sleep is not None:
                    self._sleep(delay)
                attempt += 1
                continue

            if response.status != 200:
                retryable = response.status >= 500
                if not retryable or attempt >= len(self._delays):
                    raise PdfDownloadError(
                        "http_error", "unexpected status %d" % response.status,
                        retryable=retryable,
                    )
                delay = self._delays[attempt]
                if self._sleep is not None:
                    self._sleep(delay)
                attempt += 1
                continue

            body = response.body or b""
            if not body:
                raise PdfDownloadError("empty_body", "the server returned 0 bytes")
            if not body.startswith(PDF_MAGIC):
                raise PdfDownloadError(
                    "not_a_pdf", "response does not start with %s" % PDF_MAGIC.decode("ascii")
                )
            return body


__all__ = [
    "PDF_HEADERS",
    "PDF_MAGIC",
    "PdfDownloadError",
    "PdfDownloader",
    "PdfTransport",
    "RecordingPdfTransport",
    "UrllibPdfTransport",
]
