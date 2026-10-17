"""The single announcement model shared by fetch, classify and report.

``title`` is already clean: HTML tags (notably the ``<em>`` that ``searchkey``
injects) are stripped and whitespace is collapsed, so downstream layers never
have to think about markup. ``announce_date`` is date-only because the upstream
timestamp is unusable as a precision clock (see ``data/README.md`` 约束 2).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional

_TAG_RE = re.compile(r"</?[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
# A title is a short line of text. Anything longer is a payload, not a title;
# the limit exists so a malformed response cannot bloat the database.
MAX_TITLE_LENGTH = 500

STATIC_BASE = "http://static.cninfo.com.cn/"


def clean_title(raw: Optional[str]) -> str:
    """Strip tags, unescape entities, collapse whitespace.

    Order matters: unescape after stripping, so ``&lt;em&gt;`` in a title is
    unescaped once and never re-interpreted as markup.
    """

    if raw is None:
        return ""
    text = _TAG_RE.sub("", str(raw))
    text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:MAX_TITLE_LENGTH]


def has_markup(text: str) -> bool:
    return bool(_TAG_RE.search(text or ""))


@dataclass(frozen=True)
class Announcement:
    announcement_id: str
    code: str
    name: str
    title: str
    announce_date: str
    adjunct_url: str
    adjunct_type: str
    announcement_type: str
    source_kind: str

    @property
    def source_url(self) -> str:
        if not self.adjunct_url:
            return ""
        if self.adjunct_url.startswith("http://") or self.adjunct_url.startswith("https://"):
            return self.adjunct_url
        return STATIC_BASE + self.adjunct_url.lstrip("/")


@dataclass(frozen=True)
class DroppedItem:
    """An entry rejected before it could reach the database."""

    reason: str
    identifier: str
    detail: str = ""


def parse_row(
    row: dict,
    code: str,
    source_kind: str,
    date_of,
) -> tuple:
    """Parse one ``announcements[]`` entry into ``(Announcement, None)`` or
    ``(None, DroppedItem)``.

    ``date_of`` converts epoch milliseconds to a date string; it is injected so
    this module never reads the clock.
    """

    announcement_id = str(row.get("announcementId") or "").strip()
    title = clean_title(row.get("announcementTitle"))
    sec_code = str(row.get("secCode") or "").strip() or code
    date_raw = row.get("announcementTime")

    if not announcement_id:
        return None, DroppedItem(
            reason="missing_id",
            identifier="%s@%s" % (sec_code, _date_or_unknown(date_raw, date_of)),
            detail="row has no announcementId",
        )
    if not title:
        return None, DroppedItem(
            reason="empty_title",
            identifier=announcement_id,
            detail="title is empty after cleaning",
        )
    try:
        announce_date = date_of(date_raw)
    except Exception as exc:  # noqa: BLE001 - a bad timestamp is data, not a crash
        return None, DroppedItem(
            reason="bad_timestamp", identifier=announcement_id, detail=str(exc)
        )

    return (
        Announcement(
            announcement_id=announcement_id,
            code=sec_code,
            name=str(row.get("secName") or "").strip(),
            title=title,
            announce_date=announce_date,
            adjunct_url=str(row.get("adjunctUrl") or "").strip(),
            adjunct_type=str(row.get("adjunctType") or "").strip(),
            announcement_type=str(row.get("announcementType") or "").strip(),
            source_kind=source_kind,
        ),
        None,
    )


def _date_or_unknown(value, date_of) -> str:
    try:
        return date_of(value)
    except Exception:  # noqa: BLE001 - only used to name a dropped row
        return "unknown-date"
