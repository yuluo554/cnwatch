"""M2 watchlist: master lookup, groups, audit, search order, master refresh.

The three ``orgId`` counter-examples are the point of this file. If any of them
could be derived from the code, the "always look it up" rule would be
decoration; instead every one of them differs from what a prefix rule would
guess.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cnwatch import db
from cnwatch.clock import fixed_clock_for
from cnwatch.datadir import MASTER_RELPATH
from cnwatch.errors import UsageError
from cnwatch.watchlist import WatchlistService, load_stock_master
from cnwatch.watchlist.models import StockMaster, validate_code, validate_group
from cnwatch.watchlist.repository import WatchlistRepository
from cnwatch.watchlist.service import import_rows_from_csv

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
NEGATIVE = DATA / "fixtures" / "negative"
SAMPLE = DATA / "fixtures" / "watchlist-sample.csv"

COUNTER_EXAMPLES = {
    "300750": "GD165627",
    "601398": "jjxt0000019",
    "002594": "gshk0001211",
}


@pytest.fixture()
def master():
    return load_stock_master(DATA / MASTER_RELPATH)


@pytest.fixture()
def connection(tmp_path):
    conn = db.initialize(tmp_path / "cnwatch.db")
    yield conn
    conn.close()


@pytest.fixture()
def service(connection, master):
    return WatchlistService(
        WatchlistRepository(connection), master, fixed_clock_for("2026-10-17")
    )


# --------------------------------------------------------------------------- #
# orgId comes from the master
# --------------------------------------------------------------------------- #


def test_the_three_counter_examples_resolve_from_the_master(master):
    for code, org_id in COUNTER_EXAMPLES.items():
        assert code in master, code
        assert master[code].org_id == org_id
        # None of them follows a "prefix + code" rule, which is why deriving is
        # forbidden rather than merely discouraged.
        assert org_id != "gssz" + code
        assert org_id != "gssh" + code


def test_the_sample_csv_agrees_with_the_master(master):
    rows = import_rows_from_csv(SAMPLE)
    for row in rows:
        assert row["org_id"] == master[row["code"]].org_id, row["code"]


def test_add_uses_the_master_org_id(service):
    entry = service.add("300750", "观察")
    assert entry.org_id == "GD165627"
    assert entry.name == "宁德时代"
    assert entry.pinyin == "ndsd"
    assert entry.in_stock_master is True


def test_add_rejects_a_code_outside_the_master(service):
    with pytest.raises(UsageError) as excinfo:
        service.add("999999", "观察")
    assert "not in the current stock master" in excinfo.value.message
    assert service.count() == 0


def test_import_rejects_an_org_id_that_contradicts_the_master(service):
    outcome = service.import_rows(
        [{"code": "300750", "group": "观察", "org_id": "gssz0000750"}]
    )
    assert outcome.added == 0
    assert "does not match" in outcome.rejected[0][2]


# --------------------------------------------------------------------------- #
# groups and code format
# --------------------------------------------------------------------------- #


def test_only_the_three_groups_are_accepted(service):
    for group in ("持仓", "观察", "风险"):
        service.add("600519" if group == "持仓" else "600519", group, update=True)
    with pytest.raises(UsageError):
        service.add("600519", "重点", update=True)


def test_group_validation_matches_the_schema_check(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO watchlist (code, name, pinyin, group_name, org_id, added_at, source)"
            " VALUES ('600519','贵州茅台','gzmt','重点','gssh0600519','t','manual')"
        )


def test_code_must_be_six_digits():
    for bad in ("60051", "6005190", "60051A", "", "60 0519"):
        with pytest.raises(UsageError):
            validate_code(bad)
    # Surrounding whitespace from a CSV cell or a shell quote is normalised, not
    # rejected; the six digits themselves are what is validated.
    assert validate_code(" 600519 ") == "600519"


def test_group_validator_names_the_allowed_values():
    with pytest.raises(UsageError) as excinfo:
        validate_group("自选")
    assert "持仓" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# CSV import
# --------------------------------------------------------------------------- #


def test_import_the_sample_file(service):
    outcome = service.import_csv(SAMPLE)
    assert outcome.added == 20
    assert outcome.rejected == ()
    assert service.count() == 20


def test_import_is_idempotent(service):
    service.import_csv(SAMPLE)
    again = service.import_csv(SAMPLE)
    assert again.added == 0
    assert again.skipped == 20
    assert service.count() == 20


def test_bad_rows_are_rejected_by_line_and_the_rest_land(service):
    outcome = service.import_csv(NEGATIVE / "watchlist-bad-rows.csv")
    assert outcome.added == 2
    assert len(outcome.rejected) == 3
    reasons = " ".join(reason for _line, _code, reason in outcome.rejected)
    assert "unknown group" in reasons
    assert "six digits" in reasons
    assert "not in the current stock master" in reasons
    assert service.count() == 2


def test_missing_required_column_rejects_the_whole_file(service):
    with pytest.raises(UsageError) as excinfo:
        service.import_csv(NEGATIVE / "watchlist-missing-column.csv")
    assert "group" in excinfo.value.message
    assert service.count() == 0


def test_non_utf8_csv_is_refused_without_importing_anything(tmp_path, service):
    path = tmp_path / "gbk.csv"
    path.write_bytes("code,group\n600519,持仓\n".encode("gbk"))
    with pytest.raises(UsageError) as excinfo:
        service.import_csv(path)
    assert "UTF-8" in excinfo.value.message
    assert service.count() == 0


def test_empty_csv_is_refused(tmp_path, service):
    path = tmp_path / "empty.csv"
    path.write_text("code,group\n", encoding="utf-8")
    with pytest.raises(UsageError):
        service.import_csv(path)


# --------------------------------------------------------------------------- #
# mutation and audit
# --------------------------------------------------------------------------- #


def test_duplicate_add_is_refused_unless_update_is_given(service):
    service.add("600519", "持仓", note="first")
    with pytest.raises(UsageError):
        service.add("600519", "观察")
    updated = service.add("600519", "观察", note="second", update=True)
    assert updated.group == "观察"
    assert updated.note == "second"


def test_every_change_is_auditable(service):
    service.add("600519", "持仓")
    service.edit("600519", group="风险")
    service.remove("600519")

    records = service.audit("600519")
    assert [record.action for record in records] == ["add", "edit", "remove"]
    assert records[1].old_value and "持仓" in records[1].old_value
    assert "风险" in records[1].new_value
    assert '"name": "贵州茅台"' in records[2].old_value


def test_import_writes_audit_rows(service):
    service.import_csv(SAMPLE)
    records = service.audit("000001")
    assert records and records[0].action == "import"


def test_removing_an_absent_code_is_an_error(service):
    with pytest.raises(UsageError):
        service.remove("600519")


def test_edit_without_changes_is_an_error(service):
    service.add("600519", "持仓")
    with pytest.raises(UsageError):
        service.edit("600519")


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def test_search_order_is_code_then_name_then_pinyin(service):
    service.import_csv(SAMPLE)

    by_code = service.search("6005")
    assert by_code[0].code == "600519"
    assert all(entry.code.startswith("6005") for entry in by_code)

    by_name = service.search("茅台")
    assert [entry.code for entry in by_name] == ["600519"]

    by_pinyin = service.search("gzmt")
    assert [entry.code for entry in by_pinyin] == ["600519"]


def test_search_ties_break_by_code(service):
    service.import_csv(SAMPLE)
    results = service.search("600")
    codes = [entry.code for entry in results if entry.code.startswith("600")]
    assert codes == sorted(codes)


def test_search_is_empty_for_no_match(service):
    service.import_csv(SAMPLE)
    assert service.search("绝不存在") == []


# --------------------------------------------------------------------------- #
# master refresh
# --------------------------------------------------------------------------- #


def test_sync_master_keeps_user_fields_and_marks_disappeared_codes(service, master):
    service.import_csv(SAMPLE)
    service.edit("600519", note="长期持有")

    smaller = dict(master)
    smaller.pop("600519")
    smaller["000001"] = StockMaster(
        code="000001", pinyin="payh", category="A股",
        org_id="gssz0000001", name="平安银行(改名)",
    )

    report = service.sync_master(smaller)
    assert report["marked_missing"] == 1
    assert report["refreshed"] == 1

    gone = service.search("600519")[0]
    assert gone.in_stock_master is False
    assert gone.group == "持仓"
    assert gone.note == "长期持有"

    renamed = service.search("000001")[0]
    assert renamed.name == "平安银行(改名)"


def test_fetchable_entries_exclude_codes_missing_from_the_master(service, master):
    service.import_csv(SAMPLE)
    smaller = dict(master)
    smaller.pop("600519")
    service.sync_master(smaller)

    codes = [entry.code for entry in service.fetchable_entries()]
    assert "600519" not in codes
    assert len(codes) == 19
