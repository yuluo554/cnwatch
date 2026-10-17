"""Shared exception types.

The CLI is the only place that turns an exception into an exit code, so the
business layer raises these and never prints. ``UsageError`` means nothing was
produced (exit 2); ``PartialFailure`` means the work finished but part of the
input was rejected (exit 1).
"""

from __future__ import annotations

from typing import List, Optional


class CnwatchError(Exception):
    """Base class; never raised directly."""


class UsageError(CnwatchError):
    """Bad usage or unusable input. The caller must produce nothing."""

    def __init__(self, message: str, hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return "%s\n  hint: %s" % (self.message, self.hint)
        return self.message


class PartialFailure(CnwatchError):
    """The run completed, but some rows/stocks were rejected or failed."""

    def __init__(self, message: str, details: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = list(details or [])

    def __str__(self) -> str:
        return self.message
