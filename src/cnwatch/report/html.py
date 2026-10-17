"""Single-file HTML rendering of a ``ReportDocument``.

Formatting only, same as the Markdown renderer, and driven by the same
``ReportDocument`` — that is why the two formats cannot disagree about content.

Three constraints shape this file:

1. **Single file, zero external resources.** No ``<link>``, no ``<script>``, no
   ``<img>``, no ``<iframe>``, no ``@import``, no web fonts. The stylesheet is a
   string constant inlined into one ``<style>`` block. A report that opens on a
   laptop with no network is the whole point of the product.
2. **Item links are content, not resources.** ``<a href>`` pointing at the
   original filing is allowed (plan/01 module 4). It is emitted only from the
   stored ``adjunct_url``.
3. **The guard is at the exit, not in the test.** ``assert_no_external_resources``
   runs inside ``render_html`` itself, so any future edit that adds a remote
   asset fails at generation time rather than in a test someone may not run.
"""

from __future__ import annotations

import html
import re
from typing import List, Sequence

from ..disclaimer import DATA_SOURCE, DISCLAIMER
from .assembly import BASELINE_NOTE, tag_index
from .models import Entry, ReportDocument
from .markdown import TAG_ORDER

# Tags that pull in a remote asset when they carry a URL. ``a`` is absent on
# purpose: item links are content.
_FORBIDDEN_TAGS = ("link", "script", "img", "iframe", "object", "embed", "source", "video", "audio")

_FORBIDDEN_PATTERNS = (
    re.compile(r"<link\b", re.IGNORECASE),
    re.compile(r"@import", re.IGNORECASE),
    re.compile(r"url\(\s*['\"]?(?:https?:)?//", re.IGNORECASE),
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"<iframe\b", re.IGNORECASE),
    re.compile(r"<img\b", re.IGNORECASE),
)


class ExternalResourceError(RuntimeError):
    """The rendered page references something outside itself."""


def assert_no_external_resources(document: str) -> None:
    """Raise if the page would load anything remote. Runs on every render."""

    for pattern in _FORBIDDEN_PATTERNS:
        match = pattern.search(document)
        if match:
            raise ExternalResourceError(
                "the HTML report must be self-contained; found %r" % match.group(0)
            )
    for tag in _FORBIDDEN_TAGS:
        if re.search(r"<%s\b" % tag, document, re.IGNORECASE):
            raise ExternalResourceError("the HTML report must not contain <%s>" % tag)


_CSS = """
:root {
  --ink: #1b1d21;
  --muted: #62676f;
  --line: #d8dbe0;
  --bg: #ffffff;
  --panel: #f6f7f9;
  --high: #b3261e;
  --mid: #8a5a00;
  --low: #2f5d50;
}
* { box-sizing: border-box; }
body {
  margin: 0 auto;
  padding: 32px 24px 64px;
  max-width: 1040px;
  color: var(--ink);
  background: var(--bg);
  font: 15px/1.65 "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",
        "PingFang SC", "Hiragino Sans GB", system-ui, sans-serif;
}
h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: 0.01em; }
h2 {
  font-size: 19px; margin: 36px 0 12px; padding-bottom: 6px;
  border-bottom: 1px solid var(--line);
}
h3 { font-size: 16px; margin: 22px 0 8px; }
h4 { font-size: 14px; margin: 16px 0 6px; color: var(--muted); }
ul { margin: 6px 0 14px; padding-left: 20px; }
li { margin: 4px 0; }
a { color: #1a4f8b; text-decoration: none; }
a:hover { text-decoration: underline; }
.meta { color: var(--muted); font-size: 13px; margin: 0 0 2px; }
.banner {
  margin: 16px 0 0; padding: 10px 14px; border-left: 3px solid var(--mid);
  background: var(--panel); font-size: 14px;
}
.banner.warn { border-left-color: var(--high); }
table { border-collapse: collapse; margin: 8px 0 4px; font-size: 14px; }
th, td { border: 1px solid var(--line); padding: 5px 12px; text-align: right; }
th:first-child, td:first-child { text-align: left; }
th { background: var(--panel); font-weight: 600; }
.risk { display: inline-block; min-width: 46px; font-size: 12px; letter-spacing: 0.04em; }
.risk-high { color: var(--high); font-weight: 700; }
.risk-mid { color: var(--mid); font-weight: 600; }
.risk-low { color: var(--low); }
.date { color: var(--muted); font-variant-numeric: tabular-nums; }
.label { color: var(--muted); font-size: 13px; }
.note { color: var(--muted); font-size: 13px; margin: 4px 0 10px; }
.empty { color: var(--muted); font-size: 14px; }
footer {
  margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 13px;
}
footer .disclaimer { color: var(--ink); font-size: 14px; margin-bottom: 6px; }
""".strip()


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _risk_class(level: str) -> str:
    return "risk risk-%s" % (level if level in ("high", "mid", "low") else "low")


def _entry_item(entry: Entry) -> str:
    label_parts = [_esc(entry.category_name)]
    if entry.tag_names:
        label_parts.append(_esc(" / ".join(entry.tag_names)))
    link = (
        '<a href="%s">原文</a>' % _esc(entry.url) if entry.url else '<span class="note">无链接</span>'
    )
    return (
        "<li>"
        '<span class="%s">%s</span> '
        '<span class="date">%s</span> '
        "%s "
        '<span class="label">%s</span> '
        "%s"
        "</li>"
        % (
            _risk_class(entry.risk_level),
            _esc(entry.risk_level),
            _esc(entry.announce_date),
            _esc(entry.title),
            " · ".join(label_parts),
            link,
        )
    )


def _header(document: ReportDocument) -> List[str]:
    summary = document.summary
    parts = [
        "<header>",
        "<h1>公告日报 %s</h1>" % _esc(summary.report_date),
        '<p class="meta">生成时间：%s</p>' % _esc(summary.generated_at),
        '<p class="meta">条目总数：%d · 数据来源：%s</p>'
        % (summary.item_count, _esc(DATA_SOURCE)),
    ]
    if summary.rules_versions:
        parts.append(
            '<p class="meta">规则版本：%s</p>' % _esc("、".join(summary.rules_versions))
        )
    if not summary.complete:
        parts.append(
            '<p class="banner warn">本次抓取不完整：%d 只股票失败（%s），结果可能缺少条目。</p>'
            % (summary.failed_stocks, _esc("、".join(document.failed_codes) or "-"))
        )
    parts.append("</header>")
    return parts


def _summary(document: ReportDocument) -> List[str]:
    summary = document.summary
    parts = [
        "<section>",
        "<h2>摘要</h2>",
        "<table>",
        "<thead><tr><th>风险</th><th>高</th><th>中</th><th>低</th>"
        "<th>未分类</th><th>新增</th><th>消失</th></tr></thead>",
        "<tbody><tr><td>条数</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td></tr></tbody>"
        % (
            summary.high,
            summary.mid,
            summary.low,
            summary.unclassified,
            summary.added,
            summary.removed,
        ),
        "</table>",
    ]
    if summary.baseline_created:
        parts.append(
            '<p class="banner">%s：没有更早的报告日可比，本日全部条目计入基线，新增为 0。</p>'
            % _esc(BASELINE_NOTE)
        )
    else:
        parts.append('<p class="note">对比基线：%s</p>' % _esc(summary.baseline_date))
    parts.append("</section>")
    return parts


def _stock_view(document: ReportDocument) -> List[str]:
    parts = ["<section>", "<h2>视图 A：按股票</h2>"]
    if not document.by_stock:
        parts.append('<p class="empty">本日无条目。</p>')
    for group in document.by_stock:
        parts.append("<h3>%s（%d 条）</h3>" % (_esc(group.label), len(group.entries)))
        parts.append("<ul>")
        parts.extend(_entry_item(entry) for entry in group.entries)
        parts.append("</ul>")
    parts.append("</section>")
    return parts


def _label_view(document: ReportDocument) -> List[str]:
    parts = ["<section>", "<h2>视图 B：按类别</h2>"]
    if not document.by_label:
        parts.append('<p class="empty">本日无条目。</p>')
    for group in document.by_label:
        parts.append("<h3>%s（%d 条）</h3>" % (_esc(group.label), len(group.entries)))
        parts.append("<ul>")
        parts.extend(_entry_item(entry) for entry in group.entries)
        parts.append("</ul>")

    tags = tag_index(document, TAG_ORDER)
    if tags:
        parts.append("<h3>关键词标签索引</h3>")
        parts.append(
            '<p class="note">标签是附加索引，条目仍归属其官方类别，不重复计入视图 B。</p>'
        )
        for tag, entries in tags:
            name = entries[0].tag_names[entries[0].tags.index(tag)] if entries else tag
            parts.append("<h4>%s（%d 条）</h4>" % (_esc(name), len(entries)))
            parts.append("<ul>")
            parts.extend(_entry_item(entry) for entry in entries)
            parts.append("</ul>")
    parts.append("</section>")
    return parts


def _comparison(document: ReportDocument) -> List[str]:
    summary = document.summary
    parts = ["<section>", "<h2>与上一报告日对比</h2>"]
    if summary.baseline_created:
        parts.append(
            '<p class="banner">%s。新增 %d 条，消失 %d 条——没有历史可比，'
            "不把存量公告报成“今日新增”。</p>"
            % (_esc(BASELINE_NOTE), summary.added, summary.removed)
        )
        parts.append("</section>")
        return parts

    parts.append(
        '<p class="note">基线日：%s。新增 %d 条，消失 %d 条。</p>'
        % (_esc(summary.baseline_date), summary.added, summary.removed)
    )

    parts.append("<h3>新增（%d）</h3>" % summary.added)
    if document.added:
        parts.append("<ul>")
        parts.extend(_entry_item(entry) for entry in document.added)
        parts.append("</ul>")
    else:
        parts.append('<p class="empty">无新增条目。</p>')

    parts.append("<h3>消失（%d）</h3>" % summary.removed)
    if document.removed:
        parts.append('<p class="note">消失只计数与列清单，不删除历史记录。</p>')
        parts.append("<ul>")
        for item in document.removed:
            parts.append(
                "<li><code>%s</code> <span class=\"date\">%s</span> %s</li>"
                % (
                    _esc(item.announcement_id),
                    _esc(item.announce_date or "—"),
                    _esc(item.title or "（库中无此条目）"),
                )
            )
        parts.append("</ul>")
    else:
        parts.append('<p class="empty">无消失条目。</p>')
    parts.append("</section>")
    return parts


def _anomalies(document: ReportDocument) -> List[str]:
    summary = document.summary
    parts = ["<section>", "<h2>未分类与异常</h2>"]

    parts.append("<h3>未分类（%d）</h3>" % summary.unclassified)
    if document.unclassified:
        parts.append(
            '<p class="note">未命中任何官方类别，也未命中四个关键词补丁。原样列出，不强行归类。</p>'
        )
        parts.append("<ul>")
        parts.extend(_entry_item(entry) for entry in document.unclassified)
        parts.append("</ul>")
    else:
        parts.append('<p class="empty">无未分类条目。</p>')

    parts.append("<h3>抓取失败（%d）</h3>" % summary.failed_stocks)
    if document.failed_codes:
        parts.append(
            '<p class="note">失败股票不参与本次对比；这些股票的条目可能缺失，不代表没有公告。</p>'
        )
        parts.append("<p>%s</p>" % _esc("、".join(document.failed_codes)))
    else:
        parts.append('<p class="empty">本次抓取无失败股票。</p>')

    parts.append("<h3>丢弃条目（%d）</h3>" % summary.dropped)
    if summary.dropped:
        parts.append(
            '<p class="note">上游返回但未能入库的条目数（缺少编号、标题为空或时间不可解析）。</p>'
        )
    else:
        parts.append('<p class="empty">无丢弃条目。</p>')

    parts.append("<h3>未打标条目（%d）</h3>" % summary.unlabelled)
    if summary.unlabelled:
        parts.append(
            '<p class="note">这些公告已入库但没有分类结果，因此不出现在上面两个视图里。'
            "运行 <code>cnwatch classify</code> 后重新生成即可补全。</p>"
        )
    else:
        parts.append('<p class="empty">无未打标条目。</p>')
    parts.append("</section>")
    return parts


def _footer() -> List[str]:
    return [
        "<footer>",
        '<p class="disclaimer">%s</p>' % _esc(DISCLAIMER),
        "<p>数据来源：%s。原文链接指向巨潮资讯网，内容以披露方原文为准。</p>"
        % _esc(DATA_SOURCE),
        "</footer>",
    ]


def render_html(document: ReportDocument) -> str:
    """The complete single-file page, then the export-time link guard."""

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>公告日报 %s</title>" % _esc(document.summary.report_date),
        "<style>",
        _CSS,
        "</style>",
        "</head>",
        "<body>",
    ]
    parts.extend(_header(document))
    parts.extend(_summary(document))
    parts.extend(_stock_view(document))
    parts.extend(_label_view(document))
    parts.extend(_comparison(document))
    parts.extend(_anomalies(document))
    parts.extend(_footer())
    parts.extend(["</body>", "</html>"])

    page = "\n".join(parts) + "\n"
    assert_no_external_resources(page)
    return page


__all__ = ["ExternalResourceError", "assert_no_external_resources", "render_html"]
