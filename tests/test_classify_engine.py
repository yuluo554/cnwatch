"""M3 engine: the truth ledger, the four patches, risk precedence, determinism.

Every assertion here is derived from a frozen artifact (the 86-row truth ledger,
the enum fragment, the calibration snapshots) or from a title the engine reads
itself. Nothing is transcribed from a previous run of the engine, because a
ledger written by the code under test proves only that the code agrees with
itself.

The one semantic reading this file pins down, and which the ledger settles:

    risk = max over (the *winning* official category, the patch tags)

not "max over every official class that matched". ``南京晶升…第一季度报告（更正版）``
is a 一季报 (low) that also matches 补充更正 (mid); the ledger says low, and
plan/04 3.4 says the other matches are recorded for the log only.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from cnwatch.classify import (
    DROP_EMPTY_TITLE,
    DROP_TITLE_TOO_LONG,
    UNCLASSIFIED,
    classify_title,
    highest_risk,
    load_rule_package,
    normalize_title,
)
from cnwatch.classify.text import is_usable_title

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO / "data" / "snapshots"
TRUTH = REPO / "data" / "fixtures" / "truth" / "classify-v1.json"
RULES = REPO / "src" / "cnwatch" / "resources" / "rules" / "rules-v1.json"
ENUM = REPO / "src" / "cnwatch" / "resources" / "official-categories.json"
CALIBRATION = SNAPSHOTS / "category-samples-2026-01-01_2026-10-17.json"
KEYWORDS = SNAPSHOTS / "keyword-samples-2026-01-01_2026-10-17.json"
ENUM_JS = SNAPSHOTS / "new-notice.js"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def package():
    return load_rule_package()


@pytest.fixture(scope="module")
def truth():
    return _json(TRUTH)


def _all_snapshot_titles():
    """Every real title available in the frozen snapshots.

    Calibration titles and the two frozen market days are both needed: the
    reverse cases the ledger uses come from the day files, whose composition is
    different from the per-category calibration sample.
    """

    for row in _json(CALIBRATION)["samples"]:
        for title in row["titles"]:
            yield title["title"]
    for path in sorted(SNAPSHOTS.glob("announce-day-*.json")):
        for row in _json(path)["announcements"]:
            yield row["announcementTitle"]


# --------------------------------------------------------------------------- #
# DoD: the 26-category set equals the enum fragment, item by item
# --------------------------------------------------------------------------- #


def _enum_ids_from_js():
    text = ENUM_JS.read_text(encoding="utf-8")
    match = re.search(r"var categoryEnum = (\[.*?\]);", text, re.DOTALL)
    assert match, "frozen enum fragment is malformed"
    return [entry["key"] for entry in json.loads(match.group(1))]


def test_rule_package_category_set_equals_the_frozen_enum(package):
    """Set equality, not a count: 26 wrong ids would pass a length check."""

    frozen = set(_enum_ids_from_js())
    bundled = {rule.id for rule in package.categories}
    assert bundled == frozen
    assert len(bundled) == 26
    assert {row["id"] for row in _json(ENUM)["categories"]} == frozen


def test_the_engine_can_emit_every_category_it_declares(package, truth):
    """A declared class nobody can reach is a hole in the rule table."""

    reachable = {row["expect"]["category"] for row in truth["samples"]}
    missing = {rule.id for rule in package.categories} - reachable
    assert missing == set(), "no ledger sample reaches %s" % sorted(missing)


# --------------------------------------------------------------------------- #
# DoD: the 86-row truth ledger, row by row, naming the id that differs
# --------------------------------------------------------------------------- #


def test_truth_ledger_has_86_rows_in_three_cases(truth):
    assert len(truth["samples"]) == 86
    counts = truth["counts"]
    assert counts == {"positive": 78, "negative": 5, "unclassified": 3}
    assert sum(counts.values()) == len(truth["samples"])


def test_every_truth_row_matches_and_divergence_names_the_id(package, truth):
    """The DoD line: 逐条命中；差异点名 announcement_id."""

    divergences = []
    for row in truth["samples"]:
        result = classify_title(row["title"], package)
        expected = row["expect"]
        observed = {
            "category": result.category,
            "tags": list(result.tags),
            "risk_level": result.risk_level,
        }
        wanted = {
            "category": expected["category"],
            "tags": expected["tags"],
            "risk_level": expected["risk_level"],
        }
        if observed != wanted:
            divergences.append(
                "%s  expected=%s  observed=%s  title=%s"
                % (row["announcement_id"], wanted, observed, row["title"])
            )
    assert divergences == [], "\n".join(divergences)


def test_truth_positive_rows_land_on_their_announced_category(package, truth):
    for row in truth["samples"]:
        if row["case"] != "positive":
            continue
        result = classify_title(row["title"], package)
        assert result.category == row["expect"]["category"], row["announcement_id"]
        assert not result.is_unclassified, row["announcement_id"]


def test_truth_unclassified_rows_are_stored_not_skipped(package, truth):
    unclassified = [row for row in truth["samples"] if row["case"] == "unclassified"]
    assert len(unclassified) == 3
    for row in unclassified:
        result = classify_title(row["title"], package)
        assert result.category == UNCLASSIFIED
        assert result.tags == ()
        assert result.risk_level == "low"
        assert result.dropped_title == "", "a readable title is not a dropped title"


# --------------------------------------------------------------------------- #
# DoD: the four patches, positive and negative, neutralizers included
# --------------------------------------------------------------------------- #

PATCH_PATTERNS = {
    "lawsuit": ("诉讼", "仲裁"),
    "reduction": ("减持",),
    "buyback": ("回购",),
    "pledge": ("质押",),
}


def test_each_patch_fires_on_its_real_calibration_titles(package):
    """Positive side, driven by the frozen keyword samples, not by literals."""

    payload = _json(KEYWORDS)
    seen = {}
    for sample in payload["samples"]:
        keyword = sample["keyword"]
        for row in sample["titles"]:
            result = classify_title(row["title"], package)
            for patch_id in result.tags:
                seen.setdefault(patch_id, []).append(row["title"])
                assert keyword in row["title"] or patch_id != "lawsuit", (
                    "patch %s fired on a title from the %r sample" % (patch_id, keyword)
                )
    # 诉讼 and 仲裁 are two searches feeding one patch.
    for patch_id in ("lawsuit", "reduction", "buyback", "pledge"):
        assert seen.get(patch_id), "patch %s never fired on real samples" % patch_id


def test_patch_order_is_fixed_regardless_of_match_order(package):
    """All four fire -> tags come out in package order, not scan order."""

    title = "关于控股股东减持及股份质押、回购进展暨涉及诉讼的公告"
    result = classify_title(title, package)
    assert list(result.tags) == ["lawsuit", "reduction", "buyback", "pledge"]
    assert [rule.order for rule in package.patches] == [0, 1, 2, 3]


@pytest.mark.parametrize(
    "title, forbidden",
    [
        ("关于控股股东及实际控制人自愿承诺未来6个月不减持公司股份的公告", "reduction"),
        ("关于公司董事、高级管理人员减持计划期限届满未减持的公告", "reduction"),
        ("关于控股股东部分股份解除质押的公告", "pledge"),
        ("关于控股股东股份解除质押的公告", "pledge"),
        ("关于控股股东、实际控制人部分股份解除质押的公告", "pledge"),
    ],
)
def test_negations_and_neutralizers_suppress_the_patch(package, title, forbidden):
    """The five reverse cases in the ledger, asserted one by one."""

    result = classify_title(title, package)
    assert forbidden not in result.tags, title
    assert result.tags == (), title


def test_a_pledge_that_also_releases_a_pledge_still_counts_as_a_pledge(package):
    """Neutralizers are removed, not vetoes: '质押及解除质押' is still a pledge."""

    result = classify_title("关于控股股东部分股份质押及解除质押的公告", package)
    assert "pledge" in result.tags


def test_patch_fires_under_an_official_category_without_inventing_one(package):
    title = "广州安凯微电子股份有限公司关于增加以集中竞价交易方式回购公司股份资金总额的公告"
    result = classify_title(title, package)
    assert result.category == "category_gqbd_szsh"
    assert result.tags == ("buyback",)


def test_reverse_cases_come_from_the_frozen_snapshots(truth):
    """The negatives must be real titles, or the reverse test proves nothing."""

    known = set(_all_snapshot_titles())
    negatives = [row for row in truth["samples"] if row["case"] == "negative"]
    assert len(negatives) == 5
    for row in negatives:
        assert row["title"] in known, row["announcement_id"]
    joined = " ".join(row["title"] for row in negatives)
    assert "减持" in joined and "质押" in joined


# --------------------------------------------------------------------------- #
# DoD: risk precedence, conflicts, and unclassified's explicit level
# --------------------------------------------------------------------------- #


def test_risk_precedence_is_high_over_mid_over_low():
    assert highest_risk([]) == "low"
    assert highest_risk(["low", "low"]) == "low"
    assert highest_risk(["low", "mid"]) == "mid"
    assert highest_risk(["mid", "high"]) == "high"
    assert highest_risk(["high", "low", "mid"]) == "high"


def test_risk_conflict_takes_the_higher_level(package):
    """年报 (low) + 诉讼 patch (high) -> high; a single low hit stays low."""

    quiet = classify_title("某某股份有限公司2025年年度报告", package)
    assert quiet.category == "category_ndbg_szsh"
    assert quiet.risk_level == "low"

    contested = classify_title("某某股份有限公司2025年年度报告涉及诉讼的进展公告", package)
    assert contested.category == "category_zj_szsh" or contested.tags == ("lawsuit",)
    assert contested.risk_level == "high"


def test_risk_uses_the_winning_category_not_every_match(package):
    """The ledger's reading, asserted directly on the sample that shows it.

    This title matches 一季报 (low, priority 12) and 补充更正 (mid, 68). The
    stored risk is low because only the winner counts; the loser is recorded as
    also_matched for the log.
    """

    title = "南京晶升装备股份有限公司2025年第一季度报告（更正版）"
    result = classify_title(title, package)
    assert result.category == "category_yjdbg_szsh"
    assert result.also_matched == ("category_bcgz_szsh",)
    assert result.risk_level == "low"

    # And the same rule through the ledger: every row whose expectation is low
    # must stay low even though 21 of the 86 rows match more than one class.
    truth = _json(TRUTH)
    multi = [
        row
        for row in truth["samples"]
        if len(classify_title(row["title"], package).also_matched) > 0
    ]
    assert len(multi) == 21
    for row in multi:
        assert row["expect"]["risk_level"] == classify_title(
            row["title"], package
        ).risk_level, row["announcement_id"]


def test_unclassified_has_an_explicit_risk_level(package):
    result = classify_title("2025 Annual Report", package)
    assert result.category == UNCLASSIFIED
    assert result.risk_level in ("high", "mid", "low")


def test_unclassified_can_still_be_high_through_a_patch(package):
    """No official class, but 减持 is still a 减持."""

    title = "关于公司股东减持股份计划期限届满的公告"
    result = classify_title(title, package)
    assert result.category in ("category_gqbd_szsh", UNCLASSIFIED)
    assert result.tags == ("reduction",)
    assert result.risk_level == "high"


def test_every_label_carries_one_of_the_three_risk_levels(package, truth):
    for row in truth["samples"]:
        assert classify_title(row["title"], package).risk_level in ("high", "mid", "low")


# --------------------------------------------------------------------------- #
# DoD: announcementType is not in the decision path (static gate)
# --------------------------------------------------------------------------- #


CLASSIFY_SOURCES = sorted((REPO / "src" / "cnwatch" / "classify").glob("*.py"))


def test_classify_package_has_sources_to_gate():
    assert len(CLASSIFY_SOURCES) >= 5


def test_announcement_type_never_appears_in_the_decision_path():
    """K12: the numeric code is not an input. Checked in the AST, not by grep."""

    offenders = []
    for path in CLASSIFY_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "announcement_type":
                offenders.append("%s:%d attribute" % (path.name, node.lineno))
            if isinstance(node, ast.Name) and node.id == "announcement_type":
                offenders.append("%s:%d name" % (path.name, node.lineno))
            if isinstance(node, ast.Constant) and node.value == "announcement_type":
                offenders.append("%s:%d literal" % (path.name, node.lineno))
    assert offenders == []


def test_the_package_declares_the_same_contract_the_gate_checks(package):
    payload = _json(RULES)
    assert payload["match"]["announcement_type_used"] is False
    assert payload["match"]["title_only"] is True
    assert payload["rules_version"] == package.rules_version


def test_classify_reads_no_clock_and_no_randomness():
    for path in CLASSIFY_SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "datetime.now(" not in text, path.name
        assert "time.time(" not in text, path.name
        assert "date.today()" not in text, path.name
        assert "random" not in text, path.name


# --------------------------------------------------------------------------- #
# normalization: one transform, shared with the generator that calibrated it
# --------------------------------------------------------------------------- #


def test_normalizer_agrees_with_the_generator_on_every_frozen_title():
    """A drift between the two copies would silently uncalibrate the package."""

    import sys

    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from gen_rules import normalize as generator_normalize

    titles = list(_all_snapshot_titles())
    assert len(titles) > 4000
    mismatched = [
        title
        for title in titles
        if generator_normalize(title) != normalize_title(title)
    ]
    assert mismatched[:5] == [], "%d titles normalize differently" % len(mismatched)


def test_normalizer_folds_full_width_and_strips_markup():
    assert normalize_title("２０２６年年度报告") == "2026年年度报告"
    assert normalize_title("关于<em>减持</em>的公告") == "关于减持的公告"
    assert normalize_title("半 年 度 报 告") == "半年度报告"
    assert normalize_title("（更正后）") == "(更正后)"
    assert normalize_title("  \t\n ") == ""


def test_is_usable_title_rejects_blank_and_oversized():
    assert is_usable_title("正常标题", 500) is True
    assert is_usable_title("   ", 500) is False
    assert is_usable_title("", 500) is False
    assert is_usable_title("标" * 501, 500) is False
    assert is_usable_title("标" * 500, 500) is True


def test_unusable_titles_are_reported_as_dropped_not_classified(package):
    blank = classify_title("   ", package)
    assert blank.category == UNCLASSIFIED
    assert blank.dropped_title == DROP_EMPTY_TITLE
    assert blank.risk_level == package.default_risk

    oversized = classify_title("减持" * 300, package)
    assert oversized.category == UNCLASSIFIED
    assert oversized.dropped_title == DROP_TITLE_TOO_LONG


# --------------------------------------------------------------------------- #
# counter-examples the generator would have caught, checked from this side too
# --------------------------------------------------------------------------- #


def test_over_allotment_option_is_not_a_rights_issue(package):
    """'超额配股权' (over-allotment) contains '配股'; the neutralizer must hold.

    Both titles are real, and both were filed under category_pg_szsh upstream —
    the point of the case is that the *engine* must not reach 配股 through
    them either, which it would if the neutralizer were dropped.
    """

    titles = [title for title in _all_snapshot_titles() if "超额配股权" in title]
    assert len(titles) == 2
    for title in titles:
        result = classify_title(title, package)
        assert result.category != "category_pg_szsh", title


def test_a_real_rights_issue_still_matches(package):
    for title in (
        "广东鸿特科技股份有限公司配股提示性公告",
        "广东鸿特科技股份有限公司配股发行结果公告",
    ):
        result = classify_title(title, package)
        assert result.category == "category_pg_szsh"
        assert result.risk_level == "mid"


def test_half_year_report_is_not_claimed_by_the_annual_report_rule(package):
    """'半年度报告' contains '年度报告'; priority 10 must beat priority 20."""

    for title in ("2026年半年度报告（英文版）", "2026年半年度报告（更正后）"):
        result = classify_title(title, package)
        assert result.category == "category_bndbg_szsh", title


def test_priority_is_read_from_the_package_not_from_file_order(package):
    """The package is stored in enum order; match order is derived from priority."""

    assert [rule.id for rule in package.categories][:2] == [
        "category_ndbg_szsh",
        "category_bndbg_szsh",
    ]
    assert package.match_order[0].id == "category_jshgg_szsh"
    priorities = [(rule.priority, rule.id) for rule in package.match_order]
    assert priorities == sorted(priorities)


def test_determinism_same_title_twice_gives_identical_output(package, truth):
    for row in truth["samples"]:
        first = classify_title(row["title"], package)
        second = classify_title(row["title"], package)
        assert first == second, row["announcement_id"]
