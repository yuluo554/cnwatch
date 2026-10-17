#!/usr/bin/env python
"""Generate the truth ledgers from frozen snapshots.

Truth is *derived*, never transcribed: every expected value below is computed
from the frozen response bytes, so a reviewer can recompute it independently.
That is also why the output is forbidden from carrying ``observed_*`` fields —
such a field would turn the benchmark into a comparison of two precomputed
answers, which stays green even when the engine is entirely broken.

Two ledgers:

``data/fixtures/truth/fetch-v1.json``
    Per-day id sets, page arithmetic at ``pageSize=30``, and the two-day
    added/removed diff.

``data/fixtures/truth/classify-v1.json``
    Input titles plus the expected ``category / tags / risk_level`` computed by
    the rule package, each with the rule that produced it as ``evidence``.
    Also carries the reverse cases: titles that contain a patch keyword but must
    not trigger it, and titles that match no class at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO / "data" / "snapshots"
TRUTH = REPO / "data" / "fixtures" / "truth"
RULES_PATH = REPO / "src" / "cnwatch" / "resources" / "rules" / "rules-v1.json"

PAGE_SIZE = 30

# Titles selected as reverse cases. Each is a real snapshot title that carries a
# patch keyword but must not raise the patch, or that matches no official class.
NEGATIVE_SELECTION = (
    "1225609144",  # 减持计划期限届满未减持
    "1225608655",  # 自愿承诺未来6个月不减持
    "1225606126",  # 部分股份解除质押
    "1225606101",  # 股份解除质押
    "1225606033",  # 部分股份解除质押
)


class TruthError(RuntimeError):
    pass


def load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def load_day(day: str) -> dict:
    path = SNAPSHOTS / ("announce-day-%s.json" % day)
    if not path.is_file():
        raise TruthError("missing snapshot %s" % path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def load_all_titles() -> Dict[str, dict]:
    """Every frozen title, keyed by announcement id."""

    index: Dict[str, dict] = {}
    for path in sorted(SNAPSHOTS.glob("announce-day-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload["announcements"]:
            index[row["announcementId"]] = row
    for path in sorted(SNAPSHOTS.glob("category-samples-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for sample in payload["samples"]:
            for row in sample["titles"]:
                index.setdefault(
                    row["announcement_id"],
                    {
                        "announcementId": row["announcement_id"],
                        "secCode": row["sec_code"],
                        "secName": row["sec_name"],
                        "announcementTitle": row["title"],
                    },
                )
    return index


def build_fetch_truth(today: str, previous: str, generated_from: Sequence[str]) -> dict:
    current = load_day(today)
    earlier = load_day(previous)

    def day_record(payload: dict) -> dict:
        rows = payload["announcements"]
        ids = [row["announcementId"] for row in rows]
        if len(set(ids)) != len(ids):
            raise TruthError("duplicate ids in %s" % payload["source"]["se_date"])
        return {
            "date": payload["source"]["se_date"].split("~")[0],
            "announcement_ids": ids,
            "pages_at_page_size_30": (len(ids) + PAGE_SIZE - 1) // PAGE_SIZE,
            "total_announcement": len(ids),
            "unique_ids": len(set(ids)),
        }

    today_record = day_record(current)
    previous_record = day_record(earlier)
    today_ids = set(today_record["announcement_ids"])
    previous_ids = set(previous_record["announcement_ids"])

    if today_record["total_announcement"] != len(today_ids):
        raise TruthError("daily id count disagrees with the unique set")

    return {
        "format": "cnwatch.fetch_truth",
        "format_version": 1,
        "generated_from": list(generated_from),
        "page_size": PAGE_SIZE,
        "days": [previous_record, today_record],
        "diff": {
            "previous_date": previous_record["date"],
            "today_date": today_record["date"],
            "added": sorted(today_ids - previous_ids),
            "removed": sorted(previous_ids - today_ids),
            "retained": len(today_ids & previous_ids),
            "baseline_created": False,
        },
    }


def label(title: str, rules: dict) -> dict:
    """Recompute the expected label from the title alone."""

    from gen_rules import first_match, ordered_categories, patch_matches

    ordered = ordered_categories(
        [
            (rule["id"], rule["name"], rule["priority"], rule["patterns"])
            for rule in rules["official_categories"]
        ]
    )
    match = first_match(title, ordered)
    category = match["id"] if match else "unclassified"
    tags = patch_matches(title)

    risk_by_id = {}
    for level in ("high", "mid"):
        for rule_id in rules["risk"][level]:
            risk_by_id[rule_id] = level
    levels = [risk_by_id.get(category, "low")] + [risk_by_id.get(tag, "low") for tag in tags]
    risk_level = "high" if "high" in levels else ("mid" if "mid" in levels else "low")

    evidence = []
    if match:
        hit = next(
            pattern for pattern in match["patterns"] if pattern in title.replace(" ", "")
        )
        evidence.append("%s via %r" % (category, hit))
    else:
        evidence.append("no official class matched")
    for tag in tags:
        evidence.append("%s tag" % tag)
    if not tags:
        evidence.append("no keyword patch")

    return {"category": category, "tags": tags, "risk_level": risk_level, "evidence": "; ".join(evidence)}


def build_classify_truth(rules: dict, sample_per_category: int = 3) -> dict:
    from gen_rules import first_match, ordered_categories, patch_matches

    ordered = ordered_categories(
        [
            (rule["id"], rule["name"], rule["priority"], rule["patterns"])
            for rule in rules["official_categories"]
        ]
    )

    samples = []
    seen = set()

    def add(row: dict, kind: str) -> None:
        identifier = str(row["announcementId"])
        if identifier in seen:
            return
        seen.add(identifier)
        expected = label(row["announcementTitle"], rules)
        samples.append(
            {
                "announcement_id": identifier,
                "code": row.get("secCode", ""),
                "title": row["announcementTitle"],
                "case": kind,
                "expect": {
                    "category": expected["category"],
                    "tags": expected["tags"],
                    "risk_level": expected["risk_level"],
                },
                "evidence": expected["evidence"],
            }
        )

    # Positive cases: k titles per class that the class actually wins.
    calibration = json.loads(
        (SNAPSHOTS / "category-samples-2026-01-01_2026-10-17.json").read_text(encoding="utf-8")
    )
    for sample in calibration["samples"]:
        wanted = sample["category_id"]
        taken = 0
        for row in sample["titles"]:
            if taken >= sample_per_category:
                break
            match = first_match(row["title"], ordered)
            if match and match["id"] == wanted:
                add(
                    {
                        "announcementId": row["announcement_id"],
                        "secCode": row["sec_code"],
                        "secName": row["sec_name"],
                        "announcementTitle": row["title"],
                    },
                    "positive",
                )
                taken += 1
        if taken == 0:
            raise TruthError("no positive truth sample for %s" % wanted)

    # Reverse cases: real titles carrying patch keywords that must NOT fire.
    # Each is recorded with its computed expectation, which must show no tag;
    # if the rule package starts firing on one of them this generator fails
    # instead of quietly blessing the regression.
    index = load_all_titles()
    for identifier in NEGATIVE_SELECTION:
        row = index.get(identifier)
        if row is None:
            raise TruthError("negative sample %s is not in the frozen snapshots" % identifier)
        fired = patch_matches(row["announcementTitle"])
        if fired:
            raise TruthError(
                "negative sample %s now matches %s; the patch rule regressed"
                % (identifier, ",".join(fired))
            )
        add(row, "negative")

    # Unclassified: titles that match no class and no patch.
    for identifier, row in sorted(index.items()):
        if len([s for s in samples if s["case"] == "unclassified"]) >= 3:
            break
        title = row["announcementTitle"]
        if first_match(title, ordered) is None and not patch_matches(title):
            add(row, "unclassified")

    counts = {"positive": 0, "negative": 0, "unclassified": 0}
    for sample in samples:
        counts[sample["case"]] += 1
    if counts["negative"] < 2:
        raise TruthError("reverse cases are too thin: %d" % counts["negative"])
    if counts["unclassified"] < 1:
        raise TruthError("no unclassified sample")

    return {
        "format": "cnwatch.classify_truth",
        "format_version": 1,
        "rules_version": rules["rules_version"],
        "rules_source": "src/cnwatch/resources/rules/rules-v1.json",
        "counts": counts,
        "samples": sorted(samples, key=lambda item: item["announcement_id"]),
    }


def canonical_json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def generate(today: str, previous: str) -> Dict[Path, str]:
    rules = load_rules()
    fetch_truth = build_fetch_truth(
        today,
        previous,
        [
            "data/snapshots/announce-day-%s.json" % previous,
            "data/snapshots/announce-day-%s.json" % today,
        ],
    )
    classify_truth = build_classify_truth(rules)
    return {
        TRUTH / "fetch-v1.json": canonical_json(fetch_truth),
        TRUTH / "classify-v1.json": canonical_json(classify_truth),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the truth ledgers")
    parser.add_argument("--check", action="store_true", help="compare without writing")
    parser.add_argument("--date", default="2026-10-16")
    parser.add_argument("--previous-date", default="2026-10-15")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        outputs = generate(args.date, args.previous_date)
    except (TruthError, KeyError, IndexError) as exc:
        print("truth: %s" % exc, file=sys.stderr)
        return 1

    if args.check:
        problems = []
        for path, text in outputs.items():
            if not path.is_file():
                problems.append("%s is missing" % path.name)
            elif path.read_text(encoding="utf-8") != text:
                problems.append("%s is stale" % path.name)
        if problems:
            for problem in problems:
                print("FAIL %s" % problem, file=sys.stderr)
            return 1
        print("truth OK: %d ledgers byte-identical" % len(outputs))
        return 0

    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    print("truth written: %s" % ", ".join(sorted(path.name for path in outputs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
