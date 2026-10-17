"""Schema contract: the dedupe key, the group CHECK, and idempotent init."""

import sqlite3

import pytest

from cnwatch import db


@pytest.fixture()
def connection(tmp_path):
    conn = db.initialize(tmp_path / "cnwatch.db")
    yield conn
    conn.close()


def test_initialize_is_idempotent(tmp_path):
    path = tmp_path / "cnwatch.db"
    first = db.initialize(path)
    first.close()
    second = db.initialize(path)
    assert db.schema_version(second) == db.SCHEMA_VERSION
    second.close()


def test_expected_tables_exist(connection):
    assert db.table_names(connection) == set(db.EXPECTED_TABLES)


def test_write_setting_pragmas_are_on(connection):
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _insert_announcement(connection, announcement_id="1225601360", title="关于副行长任职资格核准的公告"):
    connection.execute(
        """
        INSERT INTO announcements (
            announcement_id, code, name, title, announce_date,
            adjunct_url, adjunct_type, announcement_type, source_kind,
            first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            announcement_id,
            "000001",
            "平安银行",
            title,
            "2026-10-13",
            "finalpage/2026-10-13/1225601360.PDF",
            "PDF",
            "01010503||010112||012303",
            "watchlist",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
        ),
    )


def test_announcement_id_is_unique(connection):
    _insert_announcement(connection)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_announcement(connection, title="同一编号的另一标题")


def test_announcement_id_cannot_be_blank(connection):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_announcement(connection, announcement_id="   ")


def test_watchlist_group_is_restricted(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO watchlist (code, name, pinyin, group_name, org_id, added_at, source)
            VALUES ('600519', '贵州茅台', 'gzmt', '随便', 'gssh0600519',
                    '2026-10-17T00:00:00Z', 'manual')
            """
        )


def test_watchlist_accepts_documented_groups(connection):
    for index, group in enumerate(("持仓", "观察", "风险")):
        connection.execute(
            """
            INSERT INTO watchlist (code, name, pinyin, group_name, org_id, added_at, source)
            VALUES (?, ?, '', ?, ?, '2026-10-17T00:00:00Z', 'manual')
            """,
            ("60000%d" % index, "示例%d" % index, group, "gssh06000%d" % index),
        )
    assert connection.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 3


def test_classification_requires_known_risk(connection):
    _insert_announcement(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO classifications
                (announcement_id, category, tags_json, risk_level, rules_version, classified_at)
            VALUES ('1225601360', 'category_dshgg_szsh', '[]', 'critical', 'v1', '2026-10-17T00:00:00Z')
            """
        )


def test_classification_cascades_on_announcement_delete(connection):
    _insert_announcement(connection)
    connection.execute(
        """
        INSERT INTO classifications
            (announcement_id, category, tags_json, risk_level, rules_version, classified_at)
        VALUES ('1225601360', 'category_dshgg_szsh', '[]', 'low', 'v1', '2026-10-17T00:00:00Z')
        """
    )
    connection.execute("DELETE FROM announcements WHERE announcement_id = '1225601360'")
    assert connection.execute("SELECT COUNT(*) FROM classifications").fetchone()[0] == 0


def test_baseline_items_are_unique_per_day(connection):
    connection.execute(
        "INSERT INTO report_baseline_items VALUES ('2026-10-16', '1225601360')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO report_baseline_items VALUES ('2026-10-16', '1225601360')"
        )
