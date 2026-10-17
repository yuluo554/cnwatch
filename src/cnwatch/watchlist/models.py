"""Immutable models and the input validators they depend on."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..errors import UsageError

# The three groups are a frozen product decision (P1 / K3). The same tuple is a
# CHECK constraint in ``db.SCHEMA_SQL``; both must change together.
GROUP_NAMES = ("持仓", "观察", "风险")

_CODE_RE = re.compile(r"^[0-9]{6}$")


def validate_code(value: str) -> str:
    """Six ASCII digits, nothing else. No padding, no prefixes."""

    if not isinstance(value, str):
        raise UsageError("stock code must be a string, got %r" % (value,))
    code = value.strip()
    if not _CODE_RE.match(code):
        raise UsageError(
            "invalid stock code %r: expected exactly six digits" % (value,),
            hint="for example 600519",
        )
    return code


def validate_group(value: str) -> str:
    group = (value or "").strip()
    if group not in GROUP_NAMES:
        raise UsageError(
            "unknown group %r" % (value,),
            hint="allowed groups: %s" % " / ".join(GROUP_NAMES),
        )
    return group


@dataclass(frozen=True)
class StockMaster:
    """One row of the frozen stock master."""

    code: str
    pinyin: str
    category: str
    org_id: str
    name: str


@dataclass(frozen=True)
class WatchlistEntry:
    code: str
    name: str
    pinyin: str
    group: str
    org_id: str
    added_at: str
    source: str
    note: str
    in_stock_master: bool

    @property
    def group_name(self) -> str:
        """Alias kept for callers that think in schema terms."""

        return self.group


@dataclass(frozen=True)
class AuditRecord:
    audit_id: int
    at: str
    action: str
    code: str
    old_value: str
    new_value: str
    source: str


@dataclass(frozen=True)
class ImportOutcome:
    added: int
    updated: int
    skipped: int
    rejected: tuple  # tuple[tuple[int, str, str], ...] -> (line, code, reason)

    @property
    def ok(self) -> bool:
        return not self.rejected
