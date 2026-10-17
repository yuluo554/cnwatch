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


# --------------------------------------------------------------------------- #
# schema v2: the fetch run ledger
# --------------------------------------------------------------------------- #


def test_fetch_run_columns_cover_the_m2_fields(connection):
    columns = set(db.column_names(connection, "fetch_runs"))
    for name in (
        "source_kind",
        "pages",
        "dropped",
        "baseline_created",
        "stocks_total",
        "stocks_ok",
        "stocks_failed",
        "requests",
        "inserted",
        "status",
    ):
        assert name in columns


def test_fetch_run_source_kind_is_restricted(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO fetch_runs
                (started_at, window_start, window_end, source_kind, status)
            VALUES ('t', '2026-10-16', '2026-10-16', 'sideways', 'running')
            """
        )


def test_a_v1_database_is_upgraded_in_place(tmp_path):
    """A user who ran M0/M1 must keep their rows, not be told to start over."""

    path = tmp_path / "cnwatch.db"
    connection = db.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_info (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);
        CREATE TABLE fetch_runs (
            run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at   TEXT NOT NULL,
            finished_at  TEXT,
            window_start TEXT NOT NULL,
            window_end   TEXT NOT NULL,
            stocks_total INTEGER NOT NULL DEFAULT 0,
            stocks_ok    INTEGER NOT NULL DEFAULT 0,
            stocks_failed INTEGER NOT NULL DEFAULT 0,
            requests     INTEGER NOT NULL DEFAULT 0,
            inserted     INTEGER NOT NULL DEFAULT 0,
            status       TEXT NOT NULL
        );
        INSERT INTO schema_info VALUES ('schema_version', '1');
        INSERT INTO fetch_runs
            (started_at, window_start, window_end, inserted, status)
        VALUES ('2026-10-16T00:00:00Z', '2026-10-15', '2026-10-16', 7, 'complete');
        """
    )
    connection.commit()
    connection.close()

    upgraded = db.initialize(path)
    assert db.schema_version(upgraded) == db.SCHEMA_VERSION == "2"
    columns = set(db.column_names(upgraded, "fetch_runs"))
    assert {"source_kind", "pages", "dropped", "baseline_created"} <= columns
    row = upgraded.execute("SELECT inserted, source_kind, baseline_created FROM fetch_runs").fetchone()
    assert row["inserted"] == 7
    assert row["source_kind"] == "watchlist"
    assert row["baseline_created"] == 0
    upgraded.close()


def test_repairing_is_idempotent_after_migration(tmp_path):
    path = tmp_path / "cnwatch.db"
    db.initialize(path).close()
    second = db.initialize(path)
    assert db.schema_version(second) == "2"
    second.close()


def test_an_unknown_future_version_is_refused(tmp_path):
    path = tmp_path / "cnwatch.db"
    connection = db.initialize(path)
    connection.execute("UPDATE schema_info SET value = '9' WHERE key = 'schema_version'")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError) as excinfo:
        db.initialize(path)
    assert "cannot be upgraded" in str(excinfo.value)
