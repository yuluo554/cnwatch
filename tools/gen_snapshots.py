#!/usr/bin/env python
"""Freeze the M1 data snapshots and derive the stock-master ledger.

Two modes:

``--capture``
    Talks to cninfo, trims every response to the fields this project uses, and
    writes the frozen artifacts plus ``MANIFEST.json``. Requires network and an
    explicit ``--date`` (the announcement day) and ``--captured-on`` (the
    provenance date); neither is read from the system clock.

``--check``
    Offline. Re-serializes each frozen artifact from its parsed content and
    asserts the bytes are identical, the sha256 in the manifest matches the
    file, every record carries exactly the whitelisted fields, and the
    per-file accounting reconciles. This is what CI runs.

A network re-capture is deliberately *not* part of ``--check``: the source is
live and today's response would differ from the frozen day. Determinism of the
capture itself is verified by capturing twice on the same day and comparing
(recorded in ``data/README.md``).

Rate limits and page size mirror ``data/README.md`` section 4 (K7);
``tests/test_m1_data.py`` asserts the two agree.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO / "data" / "snapshots"
FIXTURES = REPO / "data" / "fixtures"

# Mirrors data/README.md section 4.
PAGE_SIZE = 30
MIN_INTERVAL = 1.0
RETRY_DELAYS = (1, 2, 4)

STOCK_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
BJ_STOCK_URL = "http://www.cninfo.com.cn/new/data/bj_stock.json"
NOTICE_JS_URL = "https://static.cninfo.com.cn/new/js/app/disclosure/notice/new-notice.js"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
PAGE_REFERER = "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"

STOCK_FIELDS = ("code", "pinyin", "category", "orgId", "zwjc")
ANNOUNCEMENT_FIELDS = (
    "secCode",
    "secName",
    "announcementId",
    "announcementTitle",
    "announcementTime",
    "adjunctUrl",
    "adjunctType",
    "announcementType",
)

# 30-day window used for the per-class volume ledger (data/README.md section 2).
WINDOW_START = "2026-09-17"
WINDOW_END = "2026-10-17"
# Wide window used to find a real positive title for every class, including the
# classes that are seasonally empty inside the 30-day window.
CALIBRATION_START = "2026-01-01"
CALIBRATION_END = "2026-10-17"
CALIBRATION_PAGES = 6

PATCH_KEYWORDS = ("诉讼", "仲裁", "减持", "回购", "质押")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cnwatch/0.1",
    "Referer": PAGE_REFERER,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

_last_request_at = [0.0]


class CaptureError(RuntimeError):
    pass


def _sleep_to_rate_limit() -> None:
    elapsed = time.monotonic() - _last_request_at[0]
    if _last_request_at[0] and elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)


def _request(url: str, body: Optional[Dict[str, str]] = None) -> bytes:
    """One HTTP request with the documented retry and rate-limit policy."""

    data = None
    if body is not None:
        data = urllib.parse.urlencode(body).encode("utf-8")
    attempt = 0
    while True:
        _sleep_to_rate_limit()
        try:
            request = urllib.request.Request(url, data=data, headers=_HEADERS)
            with urllib.request.urlopen(request, timeout=40) as response:
                payload = response.read()
            _last_request_at[0] = time.monotonic()
            return payload
        except urllib.error.HTTPError as exc:
            _last_request_at[0] = time.monotonic()
            if exc.code < 500 or attempt >= len(RETRY_DELAYS):
                raise CaptureError("HTTP %s for %s" % (exc.code, url))
            time.sleep(RETRY_DELAYS[attempt])
            attempt += 1
        except (urllib.error.URLError, OSError) as exc:
            _last_request_at[0] = time.monotonic()
            if attempt >= len(RETRY_DELAYS):
                raise CaptureError("%s for %s" % (exc, url))
            time.sleep(RETRY_DELAYS[attempt])
            attempt += 1


def _query_body(page: int, se_date: str, **overrides: str) -> Dict[str, str]:
    body = {
        "pageNum": str(page),
        "pageSize": str(PAGE_SIZE),
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": se_date,
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    body.update(overrides)
    return body


def _query_payload(page: int, se_date: str, **overrides: str) -> dict:
    raw = _request(QUERY_URL, _query_body(page, se_date, **overrides))
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------- #
# trimming
# --------------------------------------------------------------------------- #


def trim_stock(row: dict) -> dict:
    return {field: str(row.get(field, "")) for field in STOCK_FIELDS}


def trim_announcement(row: dict) -> dict:
    trimmed = {}
    for field in ANNOUNCEMENT_FIELDS:
        value = row.get(field)
        trimmed[field] = "" if value is None else (
            str(value) if not isinstance(value, int) else int(value)
        )
    return trimmed


def canonical_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #


def capture_stock_list(url: str, label: str, captured_on: str) -> dict:
    rows = json.loads(_request(url).decode("utf-8"))["stockList"]
    before = len(rows)
    trimmed = sorted((trim_stock(row) for row in rows), key=lambda item: item["code"])
    if len(trimmed) != before:
        raise CaptureError("%s: record count changed while trimming" % label)
    if any(not item["code"] or not item["orgId"] for item in trimmed):
        raise CaptureError("%s: a row is missing code or orgId" % label)
    codes = [item["code"] for item in trimmed]
    if len(set(codes)) != len(codes):
        raise CaptureError("%s: duplicate stock codes" % label)
    return {
        "format": "cnwatch.stock_list",
        "format_version": 1,
        "source": {"url": url, "captured_on": captured_on},
        "accounting": {
            "records": len(trimmed),
            "categories": _count(trimmed, "category"),
        },
        "stocks": trimmed,
    }


def _count(rows: Iterable[dict], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row[field]] = counts.get(row[field], 0) + 1
    return dict(sorted(counts.items()))


def capture_enum(js_text: str, captured_on: str) -> str:
    """Keep only the disclosure-category array, verbatim."""

    marker = '"category": ['
    index = js_text.find(marker)
    if index < 0:
        raise CaptureError("category array not found in new-notice.js")
    start = index + len(marker) - 1
    depth = 0
    end = -1
    for offset, char in enumerate(js_text[start:], start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = offset + 1
                break
    if end < 0:
        raise CaptureError("unbalanced category array in new-notice.js")
    array_text = js_text[start:end]
    entries = json.loads(array_text)
    seen = [entry["key"] for entry in entries]
    if len(seen) != len(set(seen)):
        raise CaptureError("duplicate category key in new-notice.js")
    header = (
        "// Frozen extract of the disclosure category enum.\n"
        "// source: %s\n"
        "// captured_on: %s\n"
        "// verbatim slice of filterDataMap.szse.category\n"
        "var categoryEnum = %s;\n"
    ) % (NOTICE_JS_URL, captured_on, array_text)
    return header


def capture_day(day: str, captured_on: str) -> dict:
    """Fetch every page of one full-market day and reconcile the count."""

    se_date = "%s~%s" % (day, day)
    first = _query_payload(1, se_date)
    total = int(first.get("totalAnnouncement") or 0)
    if total <= 0:
        raise CaptureError("no announcements declared for %s" % day)

    ordered: List[dict] = []
    seen = set()
    pages = 0
    page = 1
    while True:
        payload = first if page == 1 else _query_payload(page, se_date)
        pages += 1
        rows = payload.get("announcements") or []
        if not rows:
            break
        for row in rows:
            identifier = str(row.get("announcementId") or "")
            if not identifier:
                raise CaptureError("%s page %d: row without announcementId" % (day, page))
            if identifier in seen:
                continue
            seen.add(identifier)
            ordered.append(trim_announcement(row))
        if len(ordered) >= total:
            break
        if pages > 200:
            raise CaptureError("%s: pagination did not terminate" % day)
        page += 1

    if len(ordered) != total:
        raise CaptureError(
            "%s: declared %d but collected %d unique rows; not writing"
            % (day, total, len(ordered))
        )
    if any(not row["secCode"] or not row["announcementTitle"] for row in ordered):
        raise CaptureError("%s: a row is missing secCode or title" % day)

    return {
        "format": "cnwatch.announce_snapshot",
        "format_version": 1,
        "source": {
            "url": QUERY_URL,
            "method": "POST",
            "captured_on": captured_on,
            "se_date": se_date,
            "column": "szse",
            "page_size": PAGE_SIZE,
        },
        "accounting": {
            "total_announcement": total,
            "pages_requested": pages,
            "records": len(ordered),
            "unique_ids": len(seen),
            "first_id": ordered[0]["announcementId"],
            "last_id": ordered[-1]["announcementId"],
        },
        "announcements": ordered,
    }


def capture_class_samples(categories: Sequence[str], captured_on: str) -> dict:
    """Several real pages per category, over a wide window.

    One page is not enough: the exchange's broad classes (日常经营, 公司治理)
    interleave with issuance paperwork, and a single page can be entirely
    dominated by a neighbouring class. Six pages per class gives every class a
    genuine calibration pool without inventing titles.
    """

    se_date = "%s~%s" % (CALIBRATION_START, CALIBRATION_END)
    samples = []
    for category_id in categories:
        titles: List[dict] = []
        seen = set()
        total = 0
        for page in range(1, CALIBRATION_PAGES + 1):
            payload = _query_payload(page, se_date, category=category_id)
            total = int(payload.get("totalAnnouncement") or 0)
            rows = payload.get("announcements") or []
            if not rows:
                break
            for row in rows:
                identifier = str(row.get("announcementId") or "")
                if identifier in seen:
                    continue
                seen.add(identifier)
                titles.append(
                    {
                        "announcement_id": identifier,
                        "sec_code": str(row.get("secCode") or ""),
                        "sec_name": str(row.get("secName") or ""),
                        "title": str(row.get("announcementTitle") or "").replace("<em>", "").replace("</em>", ""),
                    }
                )
            if len(titles) >= total:
                break
        samples.append(
            {
                "category_id": category_id,
                "total_announcement": total,
                "pages_read": min(CALIBRATION_PAGES, max(1, (len(titles) + PAGE_SIZE - 1) // PAGE_SIZE)),
                "titles": titles,
            }
        )
    return {
        "format": "cnwatch.category_samples",
        "format_version": 1,
        "source": {
            "url": QUERY_URL,
            "captured_on": captured_on,
            "calibration_window": se_date,
            "page_size": PAGE_SIZE,
            "pages_per_category": CALIBRATION_PAGES,
        },
        "samples": samples,
    }


def capture_keyword_samples(captured_on: str) -> dict:
    """Real titles per patch keyword, so each patch has corroborating evidence."""

    se_date = "%s~%s" % (CALIBRATION_START, CALIBRATION_END)
    samples = []
    for keyword in PATCH_KEYWORDS:
        payload = _query_payload(1, se_date, searchkey=keyword)
        rows = payload.get("announcements") or []
        samples.append(
            {
                "keyword": keyword,
                "total_announcement": int(payload.get("totalAnnouncement") or 0),
                "titles": [
                    {
                        "announcement_id": str(row.get("announcementId") or ""),
                        "sec_code": str(row.get("secCode") or ""),
                        "sec_name": str(row.get("secName") or ""),
                        "title": re.sub(r"</?em>", "", str(row.get("announcementTitle") or "")),
                    }
                    for row in rows
                ],
            }
        )
    return {
        "format": "cnwatch.keyword_samples",
        "format_version": 1,
        "source": {
            "url": QUERY_URL,
            "captured_on": captured_on,
            "window": se_date,
            "page_size": PAGE_SIZE,
        },
        "samples": samples,
    }


def capture_counts(categories: Sequence[str], captured_on: str) -> dict:
    """Per-class volume in the 30-day window plus the patch keyword counts."""

    se_date = "%s~%s" % (WINDOW_START, WINDOW_END)
    per_class = []
    for category_id in categories:
        payload = _query_payload(1, se_date, category=category_id)
        per_class.append(
            {
                "category_id": category_id,
                "total_announcement": int(payload.get("totalAnnouncement") or 0),
            }
        )
    keywords = []
    for keyword in PATCH_KEYWORDS:
        payload = _query_payload(1, se_date, searchkey=keyword)
        keywords.append(
            {
                "keyword": keyword,
                "total_announcement": int(payload.get("totalAnnouncement") or 0),
            }
        )
    equity_reduction = _query_payload(1, se_date, category="category_gqbd_szsh", searchkey="减持")
    return {
        "format": "cnwatch.volume_ledger",
        "format_version": 1,
        "source": {"url": QUERY_URL, "captured_on": captured_on, "window": se_date},
        "per_class_30d": per_class,
        "patch_keywords_30d": keywords,
        "equity_change_and_reduction_30d": int(
            equity_reduction.get("totalAnnouncement") or 0
        ),
    }


def enum_ids_from_js(js_text: str) -> List[str]:
    array_text = re.search(r"var categoryEnum = (\[.*?\]);", js_text, re.DOTALL)
    if not array_text:
        raise CaptureError("category enum not found in frozen JS fragment")
    return [entry["key"] for entry in json.loads(array_text.group(1))]


# --------------------------------------------------------------------------- #
# derived ledgers
# --------------------------------------------------------------------------- #


def build_stock_master(stocks: Sequence[dict]) -> str:
    from io import StringIO

    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["code", "pinyin", "category", "orgId", "zwjc"])
    for row in stocks:
        writer.writerow([row["code"], row["pinyin"], row["category"], row["orgId"], row["zwjc"]])
    return buffer.getvalue()


SAMPLE_CODES = (
    "000001",  # 平安银行 — 深主板
    "300750",  # 宁德时代 — 创业板；orgId 是 GD 前缀，反例 1
    "601398",  # 工商银行 — 沪主板；orgId 是 jjxt 前缀，反例 2
    "002594",  # 比亚迪 — 深主板；orgId 是 gshk 前缀，反例 3
    "600519",  # 贵州茅台
    "000858",  # 五粮液
    "601318",  # 中国平安
    "600036",  # 招商银行
    "002415",  # 海康威视
    "300059",  # 东方财富
    "601899",  # 紫金矿业
    "600900",  # 长江电力
    "000333",  # 美的集团
    "002230",  # 科大讯飞
    "688981",  # 中芯国际 — 科创板
    "920000",  # 安徽凤凰 — 北交所
    "600030",  # 中信证券
    "601888",  # 中国中免
    "000651",  # 格力电器
    "002352",  # 顺丰控股
)


def build_watchlist_sample(stocks: Sequence[dict]) -> str:
    from io import StringIO

    by_code = {row["code"]: row for row in stocks}
    missing = [code for code in SAMPLE_CODES if code not in by_code]
    if missing:
        raise CaptureError("sample codes missing from the stock master: %s" % ",".join(missing))
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["code", "org_id", "name", "group"])
    groups = ("持仓", "观察")
    for index, code in enumerate(SAMPLE_CODES):
        row = by_code[code]
        writer.writerow([row["code"], row["orgId"], row["zwjc"], groups[index % 2]])
    header = (
        "# 快照样本，非用户真实持仓。代码与 orgId 均取自 cnwatch.stock_list 快照。\n"
    )
    return header + buffer.getvalue()


# --------------------------------------------------------------------------- #
# manifest and checks
# --------------------------------------------------------------------------- #


def _manifest_entries(paths: Sequence[Path]) -> List[dict]:
    entries = []
    for path in sorted(paths):
        payload = path.read_bytes()
        entry = {
            "path": path.relative_to(SNAPSHOTS).as_posix(),
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
        }
        records = _record_count(payload)
        if records is not None:
            entry["records"] = records
        entries.append(entry)
    return entries


def _record_count(payload: bytes) -> Optional[int]:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    for key in ("stocks", "announcements", "samples", "categories", "files"):
        if isinstance(parsed.get(key), list):
            return len(parsed[key])
    return None


def build_manifest(captured_on: str) -> dict:
    files = [
        path
        for path in SNAPSHOTS.rglob("*")
        if path.is_file() and path.name != "MANIFEST.json" and not path.name.startswith(".")
    ]
    return {
        "format": "cnwatch.snapshot_manifest",
        "format_version": 1,
        "captured_on": captured_on,
        "files": _manifest_entries(files),
    }


def check_canonical(path: Path, label: str) -> List[str]:
    problems: List[str] = []
    text = path.read_text(encoding="utf-8")
    if "\r" in text:
        problems.append("%s: contains CR (must be LF only)" % label)
    if not text.endswith("\n"):
        problems.append("%s: missing trailing newline" % label)
    if path.suffix == ".json":
        parsed = json.loads(text)
        if canonical_json(parsed) != text:
            problems.append("%s: not in canonical JSON form (re-serializing differs)" % label)
    return problems


def check_snapshot_shapes() -> List[str]:
    problems: List[str] = []

    stock_path = SNAPSHOTS / "szse_stock.json"
    stocks = json.loads(stock_path.read_text(encoding="utf-8"))
    for row in stocks["stocks"]:
        if tuple(sorted(row)) != tuple(sorted(STOCK_FIELDS)):
            problems.append("szse_stock.json: unexpected field set %s" % sorted(row))
            break
    if stocks["accounting"]["records"] != len(stocks["stocks"]):
        problems.append("szse_stock.json: accounting does not match the row count")
    codes = [row["code"] for row in stocks["stocks"]]
    if codes != sorted(codes):
        problems.append("szse_stock.json: stocks are not sorted by code")
    if len(set(codes)) != len(codes):
        problems.append("szse_stock.json: duplicate codes")

    for day_path in sorted(SNAPSHOTS.glob("announce-day-*.json")):
        payload = json.loads(day_path.read_text(encoding="utf-8"))
        rows = payload["announcements"]
        accounting = payload["accounting"]
        for row in rows:
            if tuple(sorted(row)) != tuple(sorted(ANNOUNCEMENT_FIELDS)):
                problems.append("%s: unexpected field set %s" % (day_path.name, sorted(row)))
                break
        if accounting["records"] != len(rows):
            problems.append("%s: accounting records != len(announcements)" % day_path.name)
        if accounting["total_announcement"] != len(rows):
            problems.append("%s: declared total != frozen rows" % day_path.name)
        ids = [row["announcementId"] for row in rows]
        if len(set(ids)) != len(ids):
            problems.append("%s: duplicate announcementId" % day_path.name)
        if any("<em>" in row["announcementTitle"] for row in rows):
            problems.append("%s: title still contains <em>" % day_path.name)

    enum_path = SNAPSHOTS / "new-notice.js"
    categories = enum_ids_from_js(enum_path.read_text(encoding="utf-8"))
    if len(categories) != 26:
        problems.append("new-notice.js: expected 26 categories, found %d" % len(categories))

    bundled = REPO / "src" / "cnwatch" / "resources" / "official-categories.json"
    table = json.loads(bundled.read_text(encoding="utf-8"))
    if [row["id"] for row in table["categories"]] != categories:
        problems.append("official-categories.json disagrees with the frozen enum order")

    return problems


def check_manifest() -> List[str]:
    problems: List[str] = []
    manifest_path = SNAPSHOTS / "MANIFEST.json"
    if not manifest_path.is_file():
        return ["MANIFEST.json is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    listed = {entry["path"]: entry for entry in manifest["files"]}
    actual = {
        path.relative_to(SNAPSHOTS).as_posix(): path
        for path in SNAPSHOTS.rglob("*")
        if path.is_file() and path.name != "MANIFEST.json"
    }
    for name in sorted(set(listed) - set(actual)):
        problems.append("manifest lists a missing file: %s" % name)
    for name in sorted(set(actual) - set(listed)):
        problems.append("snapshot not registered in the manifest: %s" % name)
    for name, entry in sorted(listed.items()):
        if name not in actual:
            continue
        payload = actual[name].read_bytes()
        if sha256_bytes(payload) != entry["sha256"]:
            problems.append("%s: sha256 drift" % name)
        if len(payload) != entry["bytes"]:
            problems.append("%s: byte length drift" % name)
    return problems


def check_derived_ledgers() -> List[str]:
    problems: List[str] = []
    stocks = json.loads(
        (SNAPSHOTS / "szse_stock.json").read_text(encoding="utf-8")
    )["stocks"]
    expected_master = build_stock_master(stocks)
    master_path = FIXTURES / "stock-master.csv"
    if not master_path.is_file():
        problems.append("stock-master.csv is missing")
    else:
        actual = master_path.read_text(encoding="utf-8")
        if actual != expected_master:
            problems.append("stock-master.csv does not regenerate byte-identically")
        rows = list(csv.DictReader(actual.splitlines()))
        for code in SAMPLE_CODES:
            if not any(row["code"] == code for row in rows):
                problems.append("stock-master.csv misses sample code %s" % code)

    watchlist_path = FIXTURES / "watchlist-sample.csv"
    if not watchlist_path.is_file():
        problems.append("watchlist-sample.csv is missing")
    else:
        try:
            expected_watchlist = build_watchlist_sample(stocks)
        except CaptureError as exc:
            # The sample codes are missing from a corrupted stock snapshot; the
            # manifest/hash check above already reports the corruption, so this
            # stays a problem line rather than a traceback.
            problems.append("watchlist-sample.csv cannot be rederived: %s" % exc)
        else:
            if watchlist_path.read_text(encoding="utf-8") != expected_watchlist:
                problems.append("watchlist-sample.csv does not regenerate byte-identically")

    # The three orgId counterexamples are the reason the ledger cannot be
    # derived from a code-prefix rule; they must be present and exact.
    by_code = {row["code"]: row["orgId"] for row in stocks}
    for code, org_id in (
        ("300750", "GD165627"),
        ("601398", "jjxt0000019"),
        ("002594", "gshk0001211"),
    ):
        if by_code.get(code) != org_id:
            problems.append("%s: expected orgId %s, found %s" % (code, org_id, by_code.get(code)))
    return problems


def run_check() -> int:
    problems: List[str] = []
    for path in sorted(SNAPSHOTS.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.json":
            problems.extend(check_canonical(path, path.relative_to(SNAPSHOTS).as_posix()))
    problems.extend(check_manifest())
    problems.extend(check_snapshot_shapes())
    problems.extend(check_derived_ledgers())
    if problems:
        for problem in problems:
            print("FAIL %s" % problem, file=sys.stderr)
        print("%d problem(s)" % len(problems), file=sys.stderr)
        return 1
    print("snapshots OK: canonical bytes, manifest sha256, shapes and ledgers agree")
    return 0


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def capture(day: str, previous_day: str, captured_on: str) -> None:
    categories: List[str] = []
    print("capturing stock lists")
    stock_payload = capture_stock_list(STOCK_URL, "szse_stock", captured_on)
    write_text(SNAPSHOTS / "szse_stock.json", canonical_json(stock_payload))
    bj_payload = capture_stock_list(BJ_STOCK_URL, "bj_stock", captured_on)
    write_text(SNAPSHOTS / "bj_stock.json", canonical_json(bj_payload))

    print("capturing category enum")
    enum_text = capture_enum(_request(NOTICE_JS_URL).decode("utf-8"), captured_on)
    write_text(SNAPSHOTS / "new-notice.js", enum_text)
    categories = enum_ids_from_js(enum_text)
    if len(categories) != 26:
        raise CaptureError("expected 26 categories, found %d" % len(categories))

    print("capturing announcements for %s and %s" % (previous_day, day))
    for label in (previous_day, day):
        payload = capture_day(label, captured_on)
        write_text(
            SNAPSHOTS / ("announce-day-%s.json" % label),
            canonical_json(payload),
        )
        print("  %s: %d records over %d pages" % (
            label,
            payload["accounting"]["records"],
            payload["accounting"]["pages_requested"],
        ))

    print("capturing per-class calibration samples")
    samples = capture_class_samples(categories, captured_on)
    write_text(
        SNAPSHOTS / ("category-samples-%s_%s.json" % (CALIBRATION_START, CALIBRATION_END)),
        canonical_json(samples),
    )
    missing = [row["category_id"] for row in samples["samples"] if not row["titles"]]
    if missing:
        raise CaptureError("no calibration title for: %s" % ",".join(missing))

    print("capturing patch keyword samples")
    keyword_samples = capture_keyword_samples(captured_on)
    write_text(
        SNAPSHOTS / ("keyword-samples-%s_%s.json" % (CALIBRATION_START, CALIBRATION_END)),
        canonical_json(keyword_samples),
    )

    print("capturing 30-day volume ledger")
    counts = capture_counts(categories, captured_on)
    write_text(
        SNAPSHOTS / ("volume-ledger-%s_%s.json" % (WINDOW_START, WINDOW_END)),
        canonical_json(counts),
    )

    print("writing derived ledgers")
    write_text(FIXTURES / "stock-master.csv", build_stock_master(stock_payload["stocks"]))
    write_text(
        FIXTURES / "watchlist-sample.csv", build_watchlist_sample(stock_payload["stocks"])
    )

    manifest = build_manifest(captured_on)
    write_text(SNAPSHOTS / "MANIFEST.json", canonical_json(manifest))
    print("manifest: %d files" % len(manifest["files"]))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze and verify the M1 snapshots")
    parser.add_argument("--check", action="store_true", help="offline verification only")
    parser.add_argument("--capture", action="store_true", help="fetch from cninfo and rewrite")
    parser.add_argument("--date", default="2026-10-16", metavar="YYYY-MM-DD")
    parser.add_argument("--previous-date", default="2026-10-15", metavar="YYYY-MM-DD")
    parser.add_argument("--captured-on", default="2026-10-17", metavar="YYYY-MM-DD")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.check and not args.capture:
        parser.error("choose --capture or --check")
    if args.capture:
        try:
            capture(args.date, args.previous_date, args.captured_on)
        except CaptureError as exc:
            print("capture failed: %s" % exc, file=sys.stderr)
            return 1
    if args.check:
        return run_check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
