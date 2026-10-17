"""Markdown rendering of a ``ReportDocument``.

Formatting only. Every number printed here was computed in ``assembly``; this
module counts nothing and decides nothing.

Escaping is limited to what Markdown actually interprets: ``|`` inside a table
cell and leading ``#``/``-``/``*`` in prose. Titles are plain text from the
exchange, but a title containing a pipe would otherwise break the table, and a
broken table is a silently unreadable report.
"""

from __future__ import annotations

from typing import List, Sequence

from ..disclaimer import DATA_SOURCE, DISCLAIMER
from .assembly import BASELINE_NOTE, tag_index
from .models import Entry, ReportDocument

TAG_ORDER = ("lawsuit", "reduction", "buyback", "pledge")


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _link(entry: Entry) -> str:
    if not entry.url:
        return "—"
    return "[原文](%s)" % entry.url


def _labels(entry: Entry) -> str:
    parts = [entry.category_name]
    if entry.tag_names:
        parts.append(" / ".join(entry.tag_names))
    return " · ".join(parts)


def _entry_line(entry: Entry) -> str:
    """One item, one line: risk, date, title, label, link.

    The risk is printed as the stored value (``high``/``mid``/``low``) rather
    than translated into a judgement. plan/02 forbids wording that implies a
    direction, so the vocabulary stays mechanical.
    """

    return "- `%s` **%s** %s — %s — %s" % (
        entry.risk_level,
        entry.announce_date,
        entry.title,
        _labels(entry),
        _link(entry),
    )


def _header(document: ReportDocument) -> List[str]:
    summary = document.summary
    lines = ["# 公告日报 %s" % summary.report_date, ""]
    lines.append("- 生成时间：%s" % summary.generated_at)
    lines.append("- 条目总数：%d" % summary.item_count)
    lines.append("- 数据来源：%s" % DATA_SOURCE)
    if summary.rules_versions:
        lines.append("- 规则版本：%s" % "、".join(summary.rules_versions))
    if not summary.complete:
        lines.append(
            "- **本次抓取不完整：%d 只股票失败（%s），结果可能缺少条目。**"
            % (summary.failed_stocks, "、".join(document.failed_codes) or "-")
        )
    lines.append("")
    return lines


def _summary(document: ReportDocument) -> List[str]:
    summary = document.summary
    lines = ["## 摘要", ""]
    lines.append(
        "| 风险 | 高 | 中 | 低 | 未分类 | 新增 | 消失 |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(
        "| 条数 | %d | %d | %d | %d | %d | %d |"
        % (
            summary.high,
            summary.mid,
            summary.low,
            summary.unclassified,
            summary.added,
            summary.removed,
        )
    )
    lines.append("")
    if summary.baseline_created:
        lines.append("> %s：没有更早的报告日可比，本日全部条目计入基线，新增为 0。" % BASELINE_NOTE)
    else:
        lines.append("> 对比基线：%s" % summary.baseline_date)
    lines.append("")
    return lines


def _stock_view(document: ReportDocument) -> List[str]:
    lines = ["## 视图 A：按股票", ""]
    if not document.by_stock:
        lines.extend(["本日无条目。", ""])
        return lines
    for group in document.by_stock:
        lines.append("### %s（%d 条）" % (group.label, len(group.entries)))
        lines.append("")
        for entry in group.entries:
            lines.append(_entry_line(entry))
        lines.append("")
    return lines


def _label_view(document: ReportDocument) -> List[str]:
    lines = ["## 视图 B：按类别", ""]
    if not document.by_label:
        lines.extend(["本日无条目。", ""])
        return lines
    for group in document.by_label:
        lines.append("### %s（%d 条）" % (group.label, len(group.entries)))
        lines.append("")
        for entry in group.entries:
            lines.append(_entry_line(entry))
        lines.append("")

    tags = tag_index(document, TAG_ORDER)
    if tags:
        lines.extend(["### 关键词标签索引", ""])
        lines.append("> 标签是附加索引，条目仍归属其官方类别，不重复计入视图 B。")
        lines.append("")
        for tag, entries in tags:
            name = entries[0].tag_names[entries[0].tags.index(tag)] if entries else tag
            lines.append("#### %s（%d 条）" % (name, len(entries)))
            lines.append("")
            for entry in entries:
                lines.append(_entry_line(entry))
            lines.append("")
    return lines


def _comparison(document: ReportDocument) -> List[str]:
    summary = document.summary
    lines = ["## 与上一报告日对比", ""]
    if summary.baseline_created:
        lines.append(
            "%s。新增 %d 条，消失 %d 条——没有历史可比，"
            "不把存量公告报成“今日新增”。" % (BASELINE_NOTE, summary.added, summary.removed)
        )
        lines.append("")
        return lines

    lines.append("基线日：%s。新增 %d 条，消失 %d 条。" % (summary.baseline_date, summary.added, summary.removed))
    lines.append("")

    lines.extend(["### 新增（%d）" % summary.added, ""])
    if document.added:
        for entry in document.added:
            lines.append(_entry_line(entry))
    else:
        lines.append("无新增条目。")
    lines.append("")

    lines.extend(["### 消失（%d）" % summary.removed, ""])
    if document.removed:
        lines.append("> 消失只计数与列清单，不删除历史记录。")
        lines.append("")
        for item in document.removed:
            title = item.title or "（库中无此条目）"
            date = item.announce_date or "—"
            lines.append("- `%s` %s %s" % (item.announcement_id, date, title))
    else:
        lines.append("无消失条目。")
    lines.append("")
    return lines


def _anomalies(document: ReportDocument) -> List[str]:
    summary = document.summary
    lines = ["## 未分类与异常", ""]

    lines.append("### 未分类（%d）" % summary.unclassified)
    lines.append("")
    if document.unclassified:
        lines.append("> 未命中任何官方类别，也未命中四个关键词补丁。原样列出，不强行归类。")
        lines.append("")
        for entry in document.unclassified:
            lines.append(_entry_line(entry))
    else:
        lines.append("无未分类条目。")
    lines.append("")

    lines.append("### 抓取失败（%d）" % summary.failed_stocks)
    lines.append("")
    if document.failed_codes:
        lines.append("失败股票不参与本次对比；这些股票的条目可能缺失，不代表没有公告。")
        lines.append("")
        lines.append("、".join(document.failed_codes))
    else:
        lines.append("本次抓取无失败股票。")
    lines.append("")

    lines.append("### 丢弃条目（%d）" % summary.dropped)
    lines.append("")
    if summary.dropped:
        lines.append("上游返回但未能入库的条目数（缺少编号、标题为空或时间不可解析）。")
    else:
        lines.append("无丢弃条目。")
    lines.append("")

    lines.append("### 未打标条目（%d）" % summary.unlabelled)
    lines.append("")
    if summary.unlabelled:
        lines.append(
            "这些公告已入库但没有分类结果，因此不出现在上面两个视图里。"
            "运行 `cnwatch classify` 后重新生成即可补全。"
        )
    else:
        lines.append("无未打标条目。")
    lines.append("")
    return lines


def _footer() -> List[str]:
    return [
        "---",
        "",
        "> %s" % DISCLAIMER,
        "",
        "数据来源：%s。原文链接指向巨潮资讯网，内容以披露方原文为准。" % DATA_SOURCE,
        "",
    ]


def render_markdown(document: ReportDocument) -> str:
    """The complete Markdown report. Ends with exactly one newline."""

    lines: List[str] = []
    lines.extend(_header(document))
    lines.extend(_summary(document))
    lines.extend(_stock_view(document))
    lines.extend(_label_view(document))
    lines.extend(_comparison(document))
    lines.extend(_anomalies(document))
    lines.extend(_footer())
    return "\n".join(lines).rstrip("\n") + "\n"


__all__ = ["TAG_ORDER", "render_markdown"]
