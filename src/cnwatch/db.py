"""SQLite schema and connection helpers.

The DDL below is the single source of truth for the on-disk shape. It must
stay byte-identical to ``plan/03-架构与技术选型.md`` §五; tests compare the
table/column contract rather than trusting prose.

All timestamps are ISO-8601 strings produced by an injected clock. Nothing in
this module reads the system time.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = "1"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    key   TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    code            TEXT PRIMARY KEY NOT NULL CHECK (length(code) = 6),
    name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
    pinyin          TEXT NOT NULL DEFAULT '',
    group_name      TEXT NOT NULL CHECK (group_name IN ('持仓', '观察', '风险')),
    org_id          TEXT NOT NULL CHECK (length(trim(org_id)) > 0),
    added_at        TEXT NOT NULL,
    source          TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    in_stock_master INTEGER NOT NULL DEFAULT 1 CHECK (in_stock_master IN (0, 1))
);

CREATE TABLE IF NOT EXISTS watchlist_audit (
    audit_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    action    TEXT NOT NULL CHECK (action IN ('add', 'remove', 'edit', 'import')),
    code      TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements (
    announcement_id   TEXT PRIMARY KEY NOT NULL CHECK (length(trim(announcement_id)) > 0),
    code              TEXT NOT NULL CHECK (length(code) = 6),
    name              TEXT NOT NULL,
    title             TEXT NOT NULL CHECK (length(trim(title)) > 0),
    announce_date     TEXT NOT NULL CHECK (length(announce_date) = 10),
    adjunct_url       TEXT NOT NULL DEFAULT '',
    adjunct_type      TEXT NOT NULL DEFAULT '',
    announcement_type TEXT NOT NULL DEFAULT '',
    source_kind       TEXT NOT NULL CHECK (source_kind IN ('watchlist', 'market')),
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    removed_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_announcements_code_date
    ON announcements(code, announce_date DESC);

CREATE INDEX IF NOT EXISTS idx_announcements_date
    ON announcements(announce_date DESC);

CREATE TABLE IF NOT EXISTS classifications (
    announcement_id TEXT PRIMARY KEY NOT NULL
                    REFERENCES announcements(announcement_id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    tags_json       TEXT NOT NULL DEFAULT '[]',
    risk_level      TEXT NOT NULL CHECK (risk_level IN ('high', 'mid', 'low')),
    rules_version   TEXT NOT NULL,
    classified_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    window_start  TEXT NOT NULL,
    window_end    TEXT NOT NULL,
    stocks_total  INTEGER NOT NULL DEFAULT 0,
    stocks_ok     INTEGER NOT NULL DEFAULT 0,
    stocks_failed INTEGER NOT NULL DEFAULT 0,
    requests      INTEGER NOT NULL DEFAULT 0,
    inserted      INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL CHECK (status IN ('running', 'complete', 'degraded', 'failed'))
);

CREATE TABLE IF NOT EXISTS fetch_failures (
    run_id     INTEGER NOT NULL REFERENCES fetch_runs(run_id) ON DELETE CASCADE,
    code       TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL,
    PRIMARY KEY (run_id, code)
);

CREATE TABLE IF NOT EXISTS report_runs (
    report_date      TEXT PRIMARY KEY NOT NULL,
    generated_at     TEXT NOT NULL,
    markdown_path    TEXT NOT NULL DEFAULT '',
    html_path        TEXT NOT NULL DEFAULT '',
    item_count       INTEGER NOT NULL DEFAULT 0,
    baseline_created INTEGER NOT NULL DEFAULT 0 CHECK (baseline_created IN (0, 1)),
    failed_stocks    INTEGER NOT NULL DEFAULT 0,
    content_sha256   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS report_baseline_items (
    report_date     TEXT NOT NULL,
    announcement_id TEXT NOT NULL,
    PRIMARY KEY (report_date, announcement_id)
);

CREATE TABLE IF NOT EXISTS archive_records (
    announcement_id TEXT PRIMARY KEY NOT NULL,
    local_path      TEXT NOT NULL,
    byte_size       INTEGER NOT NULL CHECK (byte_size >= 0),
    downloaded_at   TEXT NOT NULL,
    text_status     TEXT NOT NULL CHECK (text_status IN ('extracted', 'unavailable')),
    text_excerpt    TEXT NOT NULL DEFAULT ''
);
"""

EXPECTED_TABLES = (
    "announcements",
    "archive_records",
    "classifications",
    "fetch_failures",
    "fetch_runs",
    "report_baseline_items",
    "report_runs",
    "schema_info",
    "watchlist",
    "watchlist_audit",
)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with the pragmas the project relies on."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize(db_path: Path) -> sqlite3.Connection:
    """Create the schema and stamp the version. Idempotent."""

    connection = connect(db_path)
    connection.executescript(SCHEMA_SQL)
    connection.execute(
        "INSERT OR REPLACE INTO schema_info(key, value) VALUES('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    connection.commit()
    return connection


def schema_version(connection: sqlite3.Connection) -> Optional[str]:
    try:
        row = connection.execute(
            "SELECT value FROM schema_info WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else str(row["value"])


def table_names(connection: sqlite3.Connection) -> set:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row["name"]) for row in rows}


def column_names(connection: sqlite3.Connection, table: str) -> list:
    rows = connection.execute("PRAGMA table_info(%s)" % table).fetchall()
    return [str(row["name"]) for row in rows]
