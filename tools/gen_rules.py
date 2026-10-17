#!/usr/bin/env python
"""Generate the frozen classification rule package.

The rule table below is the single source of truth. The generator does three
things and refuses to write if any of them fails:

1. the 26 official categories in the frozen enum fragment are reproduced
   exactly, in the enum's own order;
2. every category has at least one real calibration title for which it is the
   *first* match under the documented precedence (``priority`` ascending, ties
   by ``id``) — a class with no hit is a hole, not a rule;
3. every keyword patch matches at least one real title, and the classes that
   are seasonally empty inside the 30-day window say so explicitly instead of
   being padded with invented samples.

``--check`` re-generates in memory and compares bytes with the committed file.
Nothing here reads the clock or uses randomness.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO / "data" / "snapshots"
OUTPUT = REPO / "src" / "cnwatch" / "resources" / "rules" / "rules-v1.json"

ENUM_SNAPSHOT = SNAPSHOTS / "new-notice.js"
CALIBRATION_SNAPSHOT = SNAPSHOTS / "category-samples-2026-01-01_2026-10-17.json"
VOLUME_SNAPSHOT = SNAPSHOTS / "volume-ledger-2026-09-17_2026-10-17.json"

# (id, name, priority, patterns)
# Priority ascending == tried first. The ordering is what keeps "半年度报告"
# from being claimed by 年报 and "回购注销" from being claimed by 补充更正.
CATEGORY_RULES: List[tuple] = [
    ("category_jshgg_szsh", "监事会", 5, ["监事会"]),
    ("category_bndbg_szsh", "半年报", 10, ["半年度报告", "半年报"]),
    ("category_yjdbg_szsh", "一季报", 12, ["第一季度报告", "一季度报告", "一季报"]),
    ("category_sjdbg_szsh", "三季报", 13, ["三季度报告", "第三季度报告", "三季报"]),
    ("category_ndbg_szsh", "年报", 20, ["年度报告", "年报"]),
    ("category_yjygjxz_szsh", "业绩预告", 25, ["业绩预告", "业绩预亏", "业绩预增", "业绩快报"]),
    ("category_qyfpxzcs_szsh", "权益分派", 30, ["权益分派", "利润分配", "分红派息", "差异化分红"]),
    ("category_tszlq_szsh", "退市整理期", 34, ["退市整理期"]),
    ("category_tbclts_szsh", "特别处理和退市", 35, ["终止上市", "终止挂牌", "退市风险警示"]),
    ("category_cqdq_szsh", "澄清致歉", 40, ["澄清", "致歉"]),
    ("category_fxts_szsh", "风险提示", 45, ["风险提示", "异常波动"]),
    ("category_kzzq_szsh", "可转债", 50, ["可转债", "可转换公司债券", "转债", "转股价格"]),
    ("category_pg_szsh", "配股", 52, ["配股"]),    ("category_zf_szsh", "增发", 54, [
        "向特定对象发行",
        "向不特定对象发行",
        "非公开发行",
        "增发",
        "募集说明书",
        "发行股份购买资产",
        "发行股份及支付现金",
    ]),
    ("category_sf_szsh", "首发", 56, [
        "首次公开发行",
        "招股说明书",
        "招股意向书",
        "向不特定合格投资者公开发行",
        "上市公告书",
    ]),
    ("category_gszq_szsh", "公司债", 58, ["公司债", "公司债券"]),
    ("category_qtrz_szsh", "其他融资", 59, ["中期票据", "短期融资券", "超短期融资券", "债务融资工具"]),
    ("category_gqjl_szsh", "股权激励", 62, ["股权激励", "限制性股票", "股票期权", "员工持股计划"]),
    ("category_jj_szsh", "解禁", 64, ["解除限售", "限售股上市流通", "限售股份上市流通", "上市流通"]),
    ("category_gqbd_szsh", "股权变动", 66, [
        "权益变动",
        "增持",
        "减持",
        "回购",
        "司法拍卖",
        "协议转让",
        "控制权变更",
        "询价转让",
        "股权转让",
        "要约收购",
        "股权收购",
        "收购报告书",
        "质押",
    ]),
    ("category_bcgz_szsh", "补充更正", 68, [
        "更正公告",
        "补充公告",
        "更正后",
        "更正版",
        "补充更正",
        "更正及",
        "修订稿",
    ]),
    ("category_gszl_szsh", "公司治理", 70, [
        "公司章程",
        "议事规则",
        "管理制度",
        "工作细则",
        "独立董事",
        "审计制度",
        "投资者关系",
        "信息披露",
        "内幕信息",
        "管理办法",
    ]),
    ("category_zj_szsh", "中介报告", 72, ["法律意见书", "审计报告", "核查意见", "保荐书", "专项说明", "评估报告", "财务顾问", "鉴证报告", "审阅报告"]),
    ("category_dshgg_szsh", "董事会", 78, ["董事会"]),
    ("category_gddh_szsh", "股东会", 79, ["股东会", "股东大会"]),
    ("category_rcjy_szsh", "日常经营", 80, [
        "日常经营",
        "日常关联交易",
        "重大合同",
        "中标",
        "生产经营",
        "药品注册证书",
        "临床试验批准",
        "授信额度",
        "委托理财",
        "现金管理",
        "对外担保",
        "提供担保",
        "套期保值",
        "框架协议",
        "重大资产购买",
        "重大资产出售",
        "重大资产重组",
        "行政处罚",
        "行政监管措施",
        "警示函",
        "监管函",
        "辞职",
        "离任",
        "换届",
        "工商变更",
        "营业执照",
        "募集资金",
        "募集资金专户",
        "募集资金置换",
        "财务资助",
        "关联交易",
        "购买资产",
        "投资建设",
        "对外投资",
        "担保",
        "药品",
        "临床试验",
        "项目进展",
        "获得批准",
    ]),
]

# Fixed order: 诉讼·仲裁, 减持, 回购, 质押 (plan/04 section 3.4).
# ``negations`` suppress the patch outright; ``neutralizers`` are removed
# before matching so "质押及解除质押" still counts as a pledge while
# "解除质押" alone does not. Both lists carry real titles as evidence.
PATCH_RULES: List[tuple] = [
    ("lawsuit", "诉讼·仲裁", "high", ["诉讼", "仲裁"],
     ["无诉讼", "不存在诉讼", "未涉及诉讼", "不涉及诉讼"], []),
    ("reduction", "减持", "high", ["减持"],
     ["不减持", "未减持", "不存在减持"], []),
    ("buyback", "回购", "mid", ["回购"],
     ["不回购", "未回购"], []),
    ("pledge", "质押", "high", ["质押"],
     ["不质押", "未质押"], ["解除质押", "解除股份质押", "解除部分股份质押"]),
]

# Substrings that contain a pattern but denote something else entirely.
# "超额配股权" is an over-allotment option, not a rights issue.
CATEGORY_NEUTRALIZERS: Dict[str, List[str]] = {
    "category_pg_szsh": ["超额配股权"],
}

RISK_HIGH = [
    "category_tbclts_szsh",
    "category_tszlq_szsh",
    "category_fxts_szsh",
    "category_cqdq_szsh",
    "lawsuit",
    "reduction",
    "pledge",
]
RISK_MID = [
    "category_yjygjxz_szsh",
    "category_zf_szsh",
    "category_pg_szsh",
    "category_kzzq_szsh",
    "category_gqbd_szsh",
    "category_jj_szsh",
    "category_bcgz_szsh",
    "buyback",
]

PATCH_ORDER = [rule[0] for rule in PATCH_RULES]


class RuleError(RuntimeError):
    pass


def load_enum_ids() -> List[str]:
    text = ENUM_SNAPSHOT.read_text(encoding="utf-8")
    start = text.find("var categoryEnum = [")
    if start < 0:
        raise RuleError("frozen enum fragment is missing or malformed")
    start = text.find("[", start)
    depth = 0
    for offset, char in enumerate(text[start:], start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return [entry["key"] for entry in json.loads(text[start : offset + 1])]
    raise RuleError("unbalanced enum array in the frozen fragment")


def load_calibration() -> Dict[str, List[str]]:
    payload = json.loads(CALIBRATION_SNAPSHOT.read_text(encoding="utf-8"))
    return {
        row["category_id"]: [title["title"] for title in row["titles"]]
        for row in payload["samples"]
    }


def load_volumes() -> Dict[str, int]:
    payload = json.loads(VOLUME_SNAPSHOT.read_text(encoding="utf-8"))
    return {
        row["category_id"]: row["total_announcement"] for row in payload["per_class_30d"]
    }


def normalize(title: str) -> str:
    """Same normalization the engine applies before matching (plan/04 3.4)."""

    table = {0x3000: " "}
    for full, half in zip(
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）",
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz()",
    ):
        table[ord(full)] = half
    cleaned = title.translate(table)
    cleaned = cleaned.replace("<em>", "").replace("</em>", "")
    return "".join(cleaned.split())


def ordered_categories(rules: Sequence[tuple]) -> List[dict]:
    """Matching order: priority ascending, ties by id."""

    defined = [
        {"id": cid, "name": name, "patterns": patterns, "priority": priority}
        for cid, name, priority, patterns in rules
    ]
    return sorted(defined, key=lambda rule: (rule["priority"], rule["id"]))


def enum_order(ordered: Sequence[dict], enum_ids: Sequence[str]) -> List[dict]:
    """Disclosure order: the enum's own order, which the package must mirror."""

    by_id = {rule["id"]: rule for rule in ordered}
    return [by_id[category_id] for category_id in enum_ids]


def first_match(title: str, ordered: Sequence[dict]) -> Optional[dict]:
    normalized = normalize(title)
    for rule in ordered:
        candidate = normalized
        for neutralizer in CATEGORY_NEUTRALIZERS.get(rule["id"], []):
            candidate = candidate.replace(neutralizer, "")
        if any(pattern in candidate for pattern in rule["patterns"]):
            return rule
    return None


def patch_matches(title: str) -> List[str]:
    normalized = normalize(title)
    hits = []
    for rule_id, _name, _risk, patterns, negations, neutralizers in PATCH_RULES:
        if any(negation in normalized for negation in negations):
            continue
        stripped = normalized
        for neutralizer in neutralizers:
            stripped = stripped.replace(neutralizer, "")
        if any(pattern in stripped for pattern in patterns):
            hits.append(rule_id)
    return hits


def build() -> dict:
    enum_ids = load_enum_ids()
    calibration = load_calibration()
    volumes = load_volumes()
    ordered = ordered_categories(CATEGORY_RULES)

    problems: List[str] = []
    defined_ids = [rule["id"] for rule in ordered]
    if sorted(defined_ids) != sorted(enum_ids):
        missing = sorted(set(enum_ids) - set(defined_ids))
        extra = sorted(set(defined_ids) - set(enum_ids))
        problems.append("rule ids differ from the enum: missing=%s extra=%s" % (missing, extra))
    if len(enum_ids) != 26:
        problems.append("expected 26 official categories, found %d" % len(enum_ids))
    if len(defined_ids) != len(set(defined_ids)):
        problems.append("duplicate category id in the rule table")

    # Every class must win at least one real title under the documented order.
    winners: Dict[str, str] = {}
    for category_id in enum_ids:
        for title in calibration.get(category_id, []):
            match = first_match(title, ordered)
            if match and match["id"] == category_id and category_id not in winners:
                winners[category_id] = title
                break
    unhit = [category_id for category_id in enum_ids if category_id not in winners]
    if unhit:
        problems.append("no first-match calibration title for: %s" % ",".join(unhit))

    # A class whose 30-day volume is zero must be either seasonally explained
    # or genuinely absent as a group; neither is silently skipped.
    seasonality = []
    for category_id in enum_ids:
        if volumes.get(category_id, 0) == 0:
            seasonality.append(
                {
                    "category_id": category_id,
                    "volume_30d": 0,
                    "reason": "seasonal",
                    "note": "0 in the 30-day window; calibrated from the wide window only",
                    "calibration_title": winners.get(category_id, ""),
                }
            )
    unknown_volume = sorted(set(volumes) - set(enum_ids))
    if unknown_volume:
        problems.append("volume ledger has ids outside the enum: %s" % unknown_volume)

    patches = []
    for rule_id, name, risk, patterns, negations, neutralizers in PATCH_RULES:
        matched: List[str] = []
        for titles in calibration.values():
            for title in titles:
                if rule_id in patch_matches(title) and title not in matched:
                    matched.append(title)
        if not matched:
            problems.append("patch %s has no real title match" % rule_id)
        patches.append(
            {
                "id": rule_id,
                "name": name,
                "patterns": list(patterns),
                "negations": list(negations),
                "neutralizers": list(neutralizers),
                "risk": risk,
                "order": PATCH_ORDER.index(rule_id),
                "calibration_hits": len(matched),
                "calibration_example": matched[0] if matched else "",
            }
        )

    if problems:
        raise RuleError("; ".join(problems))

    return {
        "format": "cnwatch.rules",
        "format_version": 1,
        "rules_version": "v1",
        "source": {
            "enum": "data/snapshots/new-notice.js",
            "calibration": "data/snapshots/category-samples-2026-01-01_2026-10-17.json",
            "volumes": "data/snapshots/volume-ledger-2026-09-17_2026-10-17.json",
        },
        "match": {
            "precedence": "priority ascending, ties by id",
            "normalization": "full-width to half-width, strip <em>, collapse whitespace",
            "title_only": True,
            "announcement_type_used": False,
        },
        "official_categories": [
            {
                "id": rule["id"],
                "name": rule["name"],
                "patterns": rule["patterns"],
                "neutralizers": list(CATEGORY_NEUTRALIZERS.get(rule["id"], [])),
                "priority": rule["priority"],
                "calibration_title": winners[rule["id"]],
            }
            for rule in enum_order(ordered, enum_ids)
        ],
        "keyword_patches": patches,
        "risk": {
            "high": list(RISK_HIGH),
            "mid": list(RISK_MID),
            "default": "low",
            "precedence": "high > mid > low",
        },
        "seasonality": seasonality,
    }


def canonical_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the frozen rule package")
    parser.add_argument("--check", action="store_true", help="compare without writing")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        text = canonical_json(build())
    except RuleError as exc:
        print("rules: %s" % exc, file=sys.stderr)
        return 1

    if args.check:
        if not OUTPUT.is_file():
            print("rules: %s is missing" % OUTPUT, file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != text:
            print("rules: %s is stale; regenerate it" % OUTPUT, file=sys.stderr)
            return 1
        print("rules OK: 26 categories, 4 patches, byte-identical")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(text.encode("utf-8"))
    payload = json.loads(text)
    print(
        "rules written: %d categories, %d patches, %d seasonal"
        % (
            len(payload["official_categories"]),
            len(payload["keyword_patches"]),
            len(payload["seasonality"]),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
