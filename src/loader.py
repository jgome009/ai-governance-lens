"""
Schema + insertion primitives for the normalized data model (CLAUDE.md
section 4), shared by:
  - generate_synthetic_data.py (phase 1, this repo's only caller today)
  - future real-data adapters (phase 5), which should reuse this module
    instead of re-deriving the DDL, so there is exactly one definition of
    the schema that every data source writes through.

This module knows nothing about synthetic generation, random seeds, or
planted violations - it only knows how to create the tables and insert
rows. Keeping it that separate is the point of CLAUDE.md phase 2
("Schema + loader"): a real parser in phase 5 should be able to import
this file and nothing else from src/ to get data into the database.
"""
import sqlite3

SCHEMA_SQL = """
    DROP TABLE IF EXISTS messages;
    DROP TABLE IF EXISTS flags;
    DROP TABLE IF EXISTS tools;
    DROP TABLE IF EXISTS planted_violations;

    CREATE TABLE tools (
        source      TEXT PRIMARY KEY,
        vendor      TEXT NOT NULL,
        sanctioned  INTEGER NOT NULL CHECK (sanctioned IN (0, 1)),
        plan        TEXT
    );

    CREATE TABLE messages (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        source           TEXT NOT NULL REFERENCES tools(source),
        user_id          TEXT NOT NULL,
        conversation_id  TEXT NOT NULL,
        ts               TEXT NOT NULL,   -- ISO 8601
        role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        model            TEXT NOT NULL,
        char_count       INTEGER NOT NULL,
        token_count      INTEGER,
        tokens_estimated INTEGER NOT NULL CHECK (tokens_estimated IN (0, 1)),
        text_hash        TEXT NOT NULL
    );

    -- Phase-3 detector output. Populated only by the detection engine -
    -- no loader or generator should ever insert rows here directly.
    CREATE TABLE flags (
        message_id          INTEGER NOT NULL REFERENCES messages(id),
        rule_id              INTEGER NOT NULL,
        category             TEXT NOT NULL CHECK (category IN ('pii', 'credential', 'financial', 'health')),
        matched_pattern_name TEXT NOT NULL
    );

    -- Test-only ground truth (synthetic datasets only - a real-data
    -- adapter never writes this table). See RULES.md for what each
    -- column means per rule_id/trigger_type.
    CREATE TABLE planted_violations (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id          INTEGER NOT NULL,
        trigger_type     TEXT NOT NULL CHECK (trigger_type IN ('message_pattern', 'aggregate_monthly')),
        category         TEXT CHECK (category IS NULL OR category IN ('pii', 'credential', 'financial', 'health')),
        user_id          TEXT NOT NULL,
        message_id       INTEGER REFERENCES messages(id),  -- NULL for aggregate_monthly (no single message)
        week_number      INTEGER,
        month            TEXT,
        metric_name      TEXT,     -- e.g. 'monthly_spend_usd' for aggregate_monthly rows
        metric_value     REAL,     -- the actual value achieved
        threshold_value  REAL,     -- the threshold it was planted against
        expected_trigger INTEGER CHECK (expected_trigger IS NULL OR expected_trigger IN (0, 1)),
        corpus_id        TEXT,     -- links to data/fixtures/corpus_dev.jsonl entry id (message_pattern only)
        corpus_text_hash TEXT,     -- sha256 of that corpus entry's text, for integrity checking
        injected_snippet TEXT,
        note             TEXT
    );

    CREATE INDEX idx_messages_user ON messages(user_id);
    CREATE INDEX idx_messages_source ON messages(source);
    CREATE INDEX idx_messages_ts ON messages(ts);
    CREATE INDEX idx_flags_message ON flags(message_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def load_tools(conn: sqlite3.Connection, tools: dict) -> None:
    """tools: source -> (vendor, sanctioned, plan)."""
    conn.executemany(
        "INSERT INTO tools (source, vendor, sanctioned, plan) VALUES (?, ?, ?, ?)",
        [(src, vendor, sanctioned, plan) for src, (vendor, sanctioned, plan) in tools.items()],
    )


def insert_message(conn: sqlite3.Connection, source, user_id, conversation_id, ts, role,
                    model, char_count, token_count, tokens_estimated, text_hash) -> int:
    cur = conn.execute(
        """INSERT INTO messages
           (source, user_id, conversation_id, ts, role, model,
            char_count, token_count, tokens_estimated, text_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (source, user_id, conversation_id, ts, role, model,
         char_count, token_count, tokens_estimated, text_hash),
    )
    return cur.lastrowid


def insert_flag(conn: sqlite3.Connection, message_id: int, rule_id: int, category: str,
                 matched_pattern_name: str) -> None:
    """Detector output only: rule_id/category/matched_pattern_name - never
    the matched text itself (CLAUDE.md: raw text is never persisted)."""
    conn.execute(
        "INSERT INTO flags (message_id, rule_id, category, matched_pattern_name) VALUES (?, ?, ?, ?)",
        (message_id, rule_id, category, matched_pattern_name),
    )
