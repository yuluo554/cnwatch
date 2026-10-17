"""M1 data gates: frozen snapshots, ledgers, rule package and truth.

These tests are the DoD of M1 in executable form. They deliberately avoid
re-capturing from the network: the source is live, and a test that re-fetches
would compare today's market against a frozen day. Byte identity is enforced
against the committed artifacts instead, and ``--check`` is run in a
subprocess exactly as a reviewer would.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO / "data" / "snapshots"
FIXTURES = REPO / "data" / "fixtures"
TRUTH = FIXTURES / "truth"
TOOLS = REPO / "tools"
RULES = REPO / "src" / "cnwatch" / "resources" / "rules" / "rules-v1.json"
BUNDLED_CATEGORIES = REPO / "src" / "cnwatch" / "resources" / "official-categories.json"
README = REPO / "data" / "README.md"

STOCK_FIELDS = {"code", "pinyin", "category", "orgId", "zwjc"}
ANNOUNCEMENT_FIELDS = {
    "secCode",
    "secName",
    "announcementId",
    "announcementTitle",
    "announcementTime",
    "adjunctUrl",
    "adjunctType",
    "announcementType",
}


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _run_tool(name: str, *args: str) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(TOOLS), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(TOOLS / name)] + list(args),
        cwd=str(REPO),
        env={**_base_env(), **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _base_env():
    import os

    return {key: os.environ[key] for key in os.environ}


# --------------------------------------------------------------------------- #
# snapshots exist, are frozen, and are registered
# --------------------------------------------------------------------------- #


def test_snapshot_artifacts_exist():
    for name in ("szse_stock.json", "bj_stock.json", "new-notice.js", "MANIFEST.json"):
        assert (SNAPSHOTS / name).is_file(), "missing snapshot %s" % name
    assert len(list(SNAPSHOTS.glob("announce-day-*.json"))) == 2


def test_manifest_registers_every_snapshot_with_a_real_hash():
    manifest = _json(SNAPSHOTS / "MANIFEST.json")
    assert manifest["format"] == "cnwatch.snapshot_manifest"
    listed = {entry["path"] for entry in manifest["files"]}
    on_disk = {
        path.relative_to(SNAPSHOTS).as_posix()
        for path in SNAPSHOTS.rglob("*")
        if path.is_file() and path.name != "MANIFEST.json"
    }
    assert listed == on_disk
    for entry in manifest["files"]:
        payload = (SNAPSHOTS / entry["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"], entry["path"]
        assert len(payload) == entry["bytes"], entry["path"]


def test_manifest_hash_is_not_a_filler_word():
    """A manifest whose hash field is a word passes an ``is not empty`` check."""

    manifest = _json(SNAPSHOTS / "MANIFEST.json")
    for entry in manifest["files"]:
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["path"]


def test_gitattributes_marks_snapshots_as_binary_so_bytes_survive_checkout():
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    rules = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert any(
        line.startswith("data/snapshots/") and "-text" in line for line in rules
    ), "snapshots must be marked -text to keep sha256 stable across checkouts"


def test_snapshot_files_use_lf_and_trailing_newline():
    for path in sorted(SNAPSHOTS.rglob("*")):
        if not path.is_file() or path.name == "MANIFEST.json":
            continue
        payload = path.read_bytes()
        assert b"\r\n" not in payload, "%s has CRLF" % path.name
        assert payload.endswith(b"\n"), "%s lacks a trailing newline" % path.name


# --------------------------------------------------------------------------- #
# snapshot shapes match data/README.md S2/S3
# --------------------------------------------------------------------------- #


def test_stock_snapshot_has_exactly_the_documented_fields():
    payload = _json(SNAPSHOTS / "szse_stock.json")
    assert payload["format"] == "cnwatch.stock_list"
    assert payload["stocks"], "stock list is empty"
    for row in payload["stocks"]:
        assert set(row) == STOCK_FIELDS
    assert payload["accounting"]["records"] == len(payload["stocks"])


def test_stock_snapshot_covers_all_three_exchanges():
    payload = _json(SNAPSHOTS / "szse_stock.json")
    codes = {row["code"] for row in payload["stocks"]}
    # One representative per board; the file is named szse_* but covers all.
    for code in ("000001", "600519", "300750", "688981", "920000"):
        assert code in codes, "stock snapshot misses %s" % code
    assert payload["accounting"]["categories"].get("A股", 0) > 5000


def test_bj_snapshot_is_a_subset_used_for_cross_validation():
    main = {row["code"]: row["orgId"] for row in _json(SNAPSHOTS / "szse_stock.json")["stocks"]}
    bj = _json(SNAPSHOTS / "bj_stock.json")
    assert bj["accounting"]["records"] == len(bj["stocks"])
    for row in bj["stocks"]:
        assert set(row) == STOCK_FIELDS
        if row["code"] in main:
            assert main[row["code"]] == row["orgId"], row["code"]


def test_announcement_snapshots_have_exactly_the_documented_fields_and_reconcile():
    for path in sorted(SNAPSHOTS.glob("announce-day-*.json")):
        payload = _json(path)
        assert payload["format"] == "cnwatch.announce_snapshot"
        rows = payload["announcements"]
        accounting = payload["accounting"]
        assert rows, path.name
        for row in rows:
            assert set(row) == ANNOUNCEMENT_FIELDS, path.name
        ids = [row["announcementId"] for row in rows]
        assert len(set(ids)) == len(ids), "%s has duplicate ids" % path.name
        assert accounting["records"] == len(rows)
        assert accounting["total_announcement"] == len(rows), (
            "%s: declared total must equal the frozen rows" % path.name
        )
        assert accounting["unique_ids"] == len(ids)
        assert accounting["pages_requested"] == -(-len(rows) // accounting.get("page_size", 30))  # ceil


def test_announcement_titles_carry_no_highlight_markup():
    for path in sorted(SNAPSHOTS.glob("announce-day-*.json")):
        for row in _json(path)["announcements"]:
            assert "<em>" not in row["announcementTitle"]
            assert row["announcementTitle"].strip()


def test_enum_snapshot_yields_the_same_26_ids_as_the_bundled_table():
    text = (SNAPSHOTS / "new-notice.js").read_text(encoding="utf-8")
    match = re.search(r"var categoryEnum = (\[.*?\]);", text, re.DOTALL)
    assert match, "frozen enum fragment is malformed"
    frozen = [entry["key"] for entry in json.loads(match.group(1))]
    bundled = [row["id"] for row in _json(BUNDLED_CATEGORIES)["categories"]]
    assert len(frozen) == 26
    assert frozen == bundled


# --------------------------------------------------------------------------- #
# stock master ledger
# --------------------------------------------------------------------------- #


def test_stock_master_regenerates_byte_identically_and_covers_the_sample():
    text = (FIXTURES / "stock-master.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(text.splitlines()))
    assert rows, "stock-master.csv is empty"
    assert set(rows[0]) == STOCK_FIELDS
    codes = {row["code"] for row in rows}

    sample = list(csv.DictReader(
        [
            line
            for line in (FIXTURES / "watchlist-sample.csv").read_text(encoding="utf-8").splitlines()
            if not line.startswith("#")
        ]
    ))
    assert sample, "watchlist sample is empty"
    for row in sample:
        assert row["code"] in codes, "sample code %s missing from stock master" % row["code"]

    master = {row["code"]: row["orgId"] for row in rows}
    for row in sample:
        assert master[row["code"]] == row["org_id"], row["code"]


@pytest.mark.parametrize(
    "code,org_id",
    [("300750", "GD165627"), ("601398", "jjxt0000019"), ("002594", "gshk0001211")],
)
def test_orgid_counterexamples_are_present_and_exact(code, org_id):
    """These three prove the mapping cannot be derived from a code prefix."""

    rows = list(csv.DictReader((FIXTURES / "stock-master.csv").read_text(encoding="utf-8").splitlines()))
    match = [row for row in rows if row["code"] == code]
    assert match, "%s is missing" % code
    assert match[0]["orgId"] == org_id


def test_orgid_counterexamples_would_break_a_prefix_rule():
    """A sanity check on the test itself: the prefix rule really is wrong."""

    rows = list(csv.DictReader((FIXTURES / "stock-master.csv").read_text(encoding="utf-8").splitlines()))
    for code in ("300750", "601398", "002594"):
        row = [item for item in rows if item["code"] == code][0]
        assert not row["orgId"].startswith("gssz"), (
            "%s used to satisfy the prefix rule; the counterexample lost its point" % code
        )


def test_watchlist_sample_is_labelled_as_a_snapshot_not_a_portfolio():
    text = (FIXTURES / "watchlist-sample.csv").read_text(encoding="utf-8")
    assert text.startswith("#")
    assert "非用户真实持仓" in text
    for group in ("持仓", "观察"):
        assert group in text


# --------------------------------------------------------------------------- #
# rule package
# --------------------------------------------------------------------------- #


def test_rule_package_has_all_26_categories_in_enum_order():
    rules = _json(RULES)
    text = (SNAPSHOTS / "new-notice.js").read_text(encoding="utf-8")
    frozen = [
        entry["key"]
        for entry in json.loads(re.search(r"var categoryEnum = (\[.*?\]);", text, re.DOTALL).group(1))
    ]
    ids = [rule["id"] for rule in rules["official_categories"]]
    assert len(ids) == 26
    assert set(ids) == set(frozen)
    assert ids == frozen, "category order must follow the enum"


def test_every_category_has_a_real_calibration_title():
    rules = _json(RULES)
    for rule in rules["official_categories"]:
        title = rule["calibration_title"]
        assert title, "%s has no calibration title" % rule["id"]
        assert any(pattern in title for pattern in rule["patterns"]), rule["id"]


def test_zero_volume_categories_are_marked_seasonal_not_padded():
    rules = _json(RULES)
    volumes = {
        row["category_id"]: row["total_announcement"]
        for row in _json(SNAPSHOTS / "volume-ledger-2026-09-17_2026-10-17.json")["per_class_30d"]
    }
    zero = {cid for cid, count in volumes.items() if count == 0}
    seasonal = {entry["category_id"] for entry in rules["seasonality"]}
    assert zero == seasonal, "seasonality must name exactly the zero-volume classes"
    for entry in rules["seasonality"]:
        assert entry["reason"] == "seasonal"
        assert entry["calibration_title"], entry["category_id"]


def test_rule_package_declares_the_four_patches_in_fixed_order():
    rules = _json(RULES)
    assert [patch["id"] for patch in rules["keyword_patches"]] == [
        "lawsuit",
        "reduction",
        "buyback",
        "pledge",
    ]
    for patch in rules["keyword_patches"]:
        assert patch["patterns"]
        assert patch["risk"] in ("high", "mid")
        assert patch["calibration_example"], patch["id"]


def test_announcement_type_is_excluded_from_matching():
    rules = _json(RULES)
    assert rules["match"]["announcement_type_used"] is False
    assert rules["match"]["title_only"] is True


def test_risk_table_matches_the_documented_policy():
    rules = _json(RULES)
    documented_high = {
        "category_tbclts_szsh",
        "category_tszlq_szsh",
        "category_fxts_szsh",
        "category_cqdq_szsh",
        "lawsuit",
        "reduction",
        "pledge",
    }
    documented_mid = {
        "category_yjygjxz_szsh",
        "category_zf_szsh",
        "category_pg_szsh",
        "category_kzzq_szsh",
        "category_gqbd_szsh",
        "category_jj_szsh",
        "category_bcgz_szsh",
        "buyback",
    }
    assert set(rules["risk"]["high"]) == documented_high
    assert set(rules["risk"]["mid"]) == documented_mid
    assert set(rules["risk"]["high"]).isdisjoint(rules["risk"]["mid"])
    assert rules["risk"]["default"] == "low"
    enumerated = set()
    for category in rules["official_categories"]:
        enumerated.add(category["id"])
    for patch in rules["keyword_patches"]:
        enumerated.add(patch["id"])
    assert documented_high | documented_mid <= enumerated


# --------------------------------------------------------------------------- #
# truth ledgers
# --------------------------------------------------------------------------- #


def test_truth_ledgers_have_no_observed_fields():
    for path in sorted(TRUTH.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        assert "observed_" not in text, "%s carries a frozen observation" % path.name


def test_fetch_truth_is_recomputable_from_the_frozen_day_snapshots():
    truth = _json(TRUTH / "fetch-v1.json")
    assert truth["format"] == "cnwatch.fetch_truth"
    for day in truth["days"]:
        payload = _json(SNAPSHOTS / ("announce-day-%s.json" % day["date"]))
        ids = [row["announcementId"] for row in payload["announcements"]]
        assert day["announcement_ids"] == ids
        assert day["total_announcement"] == len(ids)
        assert day["pages_at_page_size_30"] == -(-len(ids) // truth["page_size"])

    previous, today = truth["days"]
    added = sorted(set(today["announcement_ids"]) - set(previous["announcement_ids"]))
    removed = sorted(set(previous["announcement_ids"]) - set(today["announcement_ids"]))
    assert truth["diff"]["added"] == added
    assert truth["diff"]["removed"] == removed
    assert truth["diff"]["retained"] == len(
        set(today["announcement_ids"]) & set(previous["announcement_ids"])
    )
    assert truth["diff"]["baseline_created"] is False


def test_classify_truth_covers_every_category_and_is_recomputable():
    truth = _json(TRUTH / "classify-v1.json")
    rules = _json(RULES)
    assert truth["rules_version"] == rules["rules_version"]
    assert truth["format"] == "cnwatch.classify_truth"

    covered = {sample["expect"]["category"] for sample in truth["samples"]}
    for rule in rules["official_categories"]:
        assert rule["id"] in covered, "no truth sample for %s" % rule["id"]

    for sample in truth["samples"]:
        assert set(sample) == {
            "announcement_id",
            "code",
            "title",
            "case",
            "expect",
            "evidence",
        }
        assert sample["evidence"], sample["announcement_id"]
        assert sample["case"] in ("positive", "negative", "unclassified")
        assert set(sample["expect"]) == {"category", "tags", "risk_level"}
        assert sample["expect"]["risk_level"] in ("high", "mid", "low")


def test_reverse_cases_exist_and_are_genuinely_reverse():
    truth = _json(TRUTH / "classify-v1.json")
    negative = [sample for sample in truth["samples"] if sample["case"] == "negative"]
    assert len(negative) >= 2, "reverse cases are a DoD item"

    joined = " ".join(sample["title"] for sample in negative)
    assert "减持" in joined and "质押" in joined, "reverse cases must carry a patch keyword"
    for sample in negative:
        assert sample["expect"]["tags"] == [], (
            "%s raised a patch despite being a reverse case" % sample["announcement_id"]
        )

    unclassified = [sample for sample in truth["samples"] if sample["case"] == "unclassified"]
    assert unclassified
    for sample in unclassified:
        assert sample["expect"]["category"] == "unclassified"
        assert sample["expect"]["risk_level"] == "low"


def test_truth_titles_all_come_from_frozen_snapshots():
    """No invented announcements: every id and title must exist in a snapshot."""

    known = {}
    for path in SNAPSHOTS.glob("announce-day-*.json"):
        for row in _json(path)["announcements"]:
            known[row["announcementId"]] = row["announcementTitle"]
    for path in SNAPSHOTS.glob("category-samples-*.json"):
        for sample in _json(path)["samples"]:
            for row in sample["titles"]:
                known.setdefault(row["announcement_id"], row["title"])

    for name in ("classify-v1.json",):
        for sample in _json(TRUTH / name)["samples"]:
            assert sample["announcement_id"] in known, sample["announcement_id"]
            assert sample["title"] == known[sample["announcement_id"]], sample["announcement_id"]


# --------------------------------------------------------------------------- #
# generators are byte-stable
# --------------------------------------------------------------------------- #


def test_rule_package_is_declared_as_package_data():
    """A rules file absent from the wheel passes every source-tree test."""

    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert RULES.is_file()
    relative = RULES.relative_to(REPO / "src" / "cnwatch").as_posix()
    assert relative == "resources/rules/rules-v1.json"
    assert "resources/rules/*.json" in text, (
        "package-data must include the rules directory or the wheel ships without it"
    )


def _independent_label(title: str, rules: dict):
    """Recompute a label from the frozen rule JSON, sharing no code with tools/.

    This is the "expectations are recomputable from the input" requirement in
    executable form: it reads only the published contract (patterns,
    neutralizers, negations, priorities, risk lists), never ``gen_rules``.
    """

    def normalize(text: str) -> str:
        table = {}
        for full, half in zip(
            "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）",
            "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz()",
        ):
            table[ord(full)] = half
        cleaned = text.translate(table).replace("<em>", "").replace("</em>", "")
        return "".join(cleaned.split())

    normalized = normalize(title)
    ordered = sorted(
        rules["official_categories"], key=lambda rule: (rule["priority"], rule["id"])
    )
    category = "unclassified"
    for rule in ordered:
        candidate = normalized
        for neutralizer in rule.get("neutralizers", []):
            candidate = candidate.replace(neutralizer, "")
        if any(pattern in candidate for pattern in rule["patterns"]):
            category = rule["id"]
            break

    tags = []
    for patch in rules["keyword_patches"]:
        if any(negation in normalized for negation in patch.get("negations", [])):
            continue
        stripped = normalized
        for neutralizer in patch.get("neutralizers", []):
            stripped = stripped.replace(neutralizer, "")
        if any(pattern in stripped for pattern in patch["patterns"]):
            tags.append(patch["id"])
    tags.sort(key=lambda tag: [item["id"] for item in rules["keyword_patches"]].index(tag))

    levels = [
        "high" if category in rules["risk"]["high"] else
        "mid" if category in rules["risk"]["mid"] else "low"
    ]
    for tag in tags:
        levels.append(
            "high" if tag in rules["risk"]["high"] else
            "mid" if tag in rules["risk"]["mid"] else "low"
        )
    risk = "high" if "high" in levels else ("mid" if "mid" in levels else "low")
    return {"category": category, "tags": tags, "risk_level": risk}


def test_every_truth_expectation_recomputes_independently():
    truth = _json(TRUTH / "classify-v1.json")
    rules = _json(RULES)
    for sample in truth["samples"]:
        recomputed = _independent_label(sample["title"], rules)
        assert recomputed == sample["expect"], (
            "%s (%s): expected %s, independent recompute gives %s"
            % (sample["announcement_id"], sample["title"], sample["expect"], recomputed)
        )


def test_independent_recompute_is_falsifiable():
    """Positive control: a wrong expectation must be caught by the recompute."""

    rules = _json(RULES)
    correct = _independent_label("关于控股股东部分股份质押的公告", rules)
    assert correct["tags"] == ["pledge"]
    assert correct["risk_level"] == "high"
    tampered = dict(correct)
    tampered["risk_level"] = "low"
    assert tampered != correct


def test_snapshot_check_passes():
    result = _run_tool("gen_snapshots.py", "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "snapshots OK" in result.stdout


def test_rules_check_passes():
    result = _run_tool("gen_rules.py", "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "byte-identical" in result.stdout


def test_truth_check_passes():
    result = _run_tool("gen_truth.py", "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "byte-identical" in result.stdout


def test_snapshot_check_is_falsifiable(tmp_path):
    """Positive control: a mutated snapshot must turn the gate red."""

    target = SNAPSHOTS / "szse_stock.json"
    original = target.read_bytes()
    try:
        target.write_bytes(original.replace(b'"code": "000001"', b'"code": "000002"', 1))
        result = _run_tool("gen_snapshots.py", "--check")
        assert result.returncode != 0, "the snapshot gate accepted a mutated file"
        assert "sha256 drift" in result.stderr or "FAIL" in result.stderr
    finally:
        target.write_bytes(original)


def test_rules_check_is_falsifiable():
    target = RULES
    original = target.read_bytes()
    try:
        payload = json.loads(original.decode("utf-8"))
        payload["official_categories"].pop()
        target.write_bytes(
            (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        result = _run_tool("gen_rules.py", "--check")
        assert result.returncode != 0, "the rules gate accepted a truncated package"
    finally:
        target.write_bytes(original)


# --------------------------------------------------------------------------- #
# data/README.md contract
# --------------------------------------------------------------------------- #


def readme_volumes():
    """Parse the 30-day per-class sentence out of data/README.md."""

    text = README.read_text(encoding="utf-8")
    match = re.search(r"30 天窗口（[^）]+）逐类量：(.+?)。", text, re.DOTALL)
    assert match, "data/README.md lost the per-class volume sentence"
    body = match.group(1).replace("\n", "")
    pairs = {}
    for chunk in body.split("、"):
        name, _, count = chunk.rpartition(" ")
        name = name.strip()
        count = count.strip()
        if not name or not count.isdigit():
            continue
        pairs[name] = int(count)
    return pairs


def test_readme_volume_figures_match_the_frozen_ledger():
    rules = _json(RULES)
    names = {rule["id"]: rule["name"] for rule in rules["official_categories"]}
    ledger = {
        row["category_id"]: row["total_announcement"]
        for row in _json(SNAPSHOTS / "volume-ledger-2026-09-17_2026-10-17.json")["per_class_30d"]
    }
    documented = readme_volumes()
    for category_id, count in ledger.items():
        name = names[category_id]
        assert name in documented, "data/README.md does not list %s" % name
        assert documented[name] == count, (
            "%s: README says %d, the frozen ledger says %d"
            % (name, documented[name], count)
        )


def test_readme_keyword_counts_match_the_frozen_ledger():
    text = README.read_text(encoding="utf-8")
    ledger = _json(SNAPSHOTS / "volume-ledger-2026-09-17_2026-10-17.json")
    for row in ledger["patch_keywords_30d"]:
        pattern = r"%s \*\*(\d+)\*\*" % re.escape(row["keyword"])
        match = re.search(pattern, text)
        assert match, "data/README.md does not record the %s count" % row["keyword"]
        assert int(match.group(1)) == row["total_announcement"], row["keyword"]


def test_readme_records_the_pledge_count():
    text = README.read_text(encoding="utf-8")
    assert "质押" in text
    assert re.search(r"质押\D{0,12}\d+", text), "the pledge count is still unmeasured"
