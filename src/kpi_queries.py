"""
Phase 2: one SQL query per KPI (CLAUDE.md section 5), against the
production schema only - `tools`, `messages`, `flags`. None of these
queries may read `planted_violations`: that table is a test-only oracle
(see RULES.md / LIMITATIONS.md) and a real deployment would never have
it. tests/test_kpis.py enforces this with an automated check over the
SQL strings in this module.

KPI 7 (task mix) is deliberately not implemented here - it needs a
message-content classifier that doesn't exist yet (see LIMITATIONS.md).

Pricing (PRICE_BY_MODEL) and the Rule-3 threshold mirror
generate_synthetic_data.py's MODELS / MONTHLY_SPEND_THRESHOLD_USD. In a
real deployment these would come from an admin-configured pricing table,
not be duplicated in the query layer - documented as a known duplication
in LIMITATIONS.md, not fixed here, because this module intentionally has
no dependency on the synthetic generator.
"""
import sqlite3

ANCHOR_MONDAY = "2026-08-03"

# Mirrors generate_synthetic_data.py's MODELS pricing - keep in sync.
PRICE_BY_MODEL = {
    "gpt-4o": 0.0060, "gpt-4o-mini": 0.00030,
    "claude-opus-4": 0.0300, "claude-sonnet-4": 0.0060,
    "gemini-1.5-pro": 0.0025, "gemini-1.5-flash": 0.00015,
}

# Mirrors generate_synthetic_data.py's MONTHLY_SPEND_THRESHOLD_USD - see
# RULES.md "Threshold derivation" for the budget logic behind this number.
RULE3_MONTHLY_SPEND_THRESHOLD_USD = 7.50


def _model_prices_cte() -> str:
    values = ", ".join(f"('{model}', {price})" for model, price in PRICE_BY_MODEL.items())
    return f"model_prices(model, price_per_1k) AS (VALUES {values})"


# ---------------------------------------------------------------------------
# KPI 1: Tool inventory - distinct sources and models in use, active users
# per tool.
# ---------------------------------------------------------------------------
KPI1_TOOL_INVENTORY_SQL = """
    SELECT m.source,
           t.sanctioned,
           COUNT(DISTINCT m.user_id) AS active_users,
           COUNT(DISTINCT m.model)   AS models_used,
           COUNT(*)                 AS messages
    FROM messages m
    JOIN tools t ON t.source = m.source
    GROUP BY m.source
    ORDER BY messages DESC
"""


def kpi1_tool_inventory(conn: sqlite3.Connection):
    return conn.execute(KPI1_TOOL_INVENTORY_SQL).fetchall()


# ---------------------------------------------------------------------------
# KPI 2: Shadow AI rate - share of messages (and active users) on
# unsanctioned tools.
# ---------------------------------------------------------------------------
KPI2_SHADOW_AI_RATE_SQL = """
    SELECT
        ROUND(100.0 * SUM(CASE WHEN t.sanctioned = 0 THEN 1 ELSE 0 END) / COUNT(*), 2)
            AS pct_messages_unsanctioned,
        (SELECT COUNT(DISTINCT m2.user_id) FROM messages m2
         JOIN tools t2 ON t2.source = m2.source WHERE t2.sanctioned = 0) AS unsanctioned_users,
        (SELECT COUNT(DISTINCT user_id) FROM messages) AS total_active_users
    FROM messages m
    JOIN tools t ON t.source = m.source
"""


def kpi2_shadow_ai_rate(conn: sqlite3.Connection):
    return conn.execute(KPI2_SHADOW_AI_RATE_SQL).fetchone()


# ---------------------------------------------------------------------------
# KPI 3: Adoption - weekly active users + messages/user/week (one query),
# and volume concentration from the top-10% of active users by message
# count (a second query: different grouping granularity, per-user not
# per-week, so it can't be the same SQL statement).
# ---------------------------------------------------------------------------
KPI3_WEEKLY_ADOPTION_SQL = """
    SELECT CAST((julianday(substr(ts, 1, 10)) - julianday(?)) / 7 AS INT) AS week_number,
           COUNT(DISTINCT user_id) AS wau,
           COUNT(*) AS messages,
           ROUND(1.0 * COUNT(*) / COUNT(DISTINCT user_id), 2) AS messages_per_active_user
    FROM messages
    GROUP BY week_number
    ORDER BY week_number
"""

KPI3_VOLUME_CONCENTRATION_SQL = """
    WITH per_user AS (
        SELECT user_id, COUNT(*) AS n FROM messages GROUP BY user_id
    ),
    ranked AS (
        SELECT user_id, n, NTILE(10) OVER (ORDER BY n DESC) AS decile FROM per_user
    )
    SELECT
        (SELECT COUNT(*) FROM ranked)                 AS total_active_users,
        (SELECT SUM(n)   FROM ranked)                 AS total_messages,
        (SELECT COUNT(*) FROM ranked WHERE decile = 1) AS top10_user_count,
        (SELECT SUM(n)   FROM ranked WHERE decile = 1) AS top10_messages
"""


def kpi3_weekly_adoption(conn: sqlite3.Connection, anchor_monday: str = ANCHOR_MONDAY):
    return conn.execute(KPI3_WEEKLY_ADOPTION_SQL, (anchor_monday,)).fetchall()


def kpi3_volume_concentration(conn: sqlite3.Connection):
    return conn.execute(KPI3_VOLUME_CONCENTRATION_SQL).fetchone()


# ---------------------------------------------------------------------------
# KPI 4: Sensitive-data exposure - flagged messages per 1,000, split by
# sanctioned vs unsanctioned tool. Reads `flags` (phase-3 detector output,
# empty until phase 3 ships) - never `planted_violations`.
# ---------------------------------------------------------------------------
KPI4_SENSITIVE_EXPOSURE_SQL = """
    SELECT t.sanctioned,
           COUNT(DISTINCT m.id) AS total_messages,
           COUNT(DISTINCT f.message_id) AS flagged_messages,
           ROUND(1000.0 * COUNT(DISTINCT f.message_id) / COUNT(DISTINCT m.id), 3)
               AS flagged_per_1000
    FROM messages m
    JOIN tools t ON t.source = m.source
    LEFT JOIN flags f ON f.message_id = m.id
    GROUP BY t.sanctioned
"""


def kpi4_sensitive_exposure(conn: sqlite3.Connection):
    return conn.execute(KPI4_SENSITIVE_EXPOSURE_SQL).fetchall()


# ---------------------------------------------------------------------------
# KPI 5: Policy violations - count per rule, trend by week/month.
#
# Rule 1/2 are message_pattern: trend by week, from `flags` (detector
# output). Rule 3 is aggregate_monthly: there's no per-message flag to
# count, so its "trend" is the count of users crossing the threshold each
# month, computed live from `messages` + the pricing CTE - NOT from
# `planted_violations`.
# ---------------------------------------------------------------------------
KPI5_MESSAGE_PATTERN_TREND_SQL = """
    SELECT f.rule_id,
           CAST((julianday(substr(m.ts, 1, 10)) - julianday(?)) / 7 AS INT) AS week_number,
           COUNT(DISTINCT f.message_id) AS n
    FROM flags f
    JOIN messages m ON m.id = f.message_id
    GROUP BY f.rule_id, week_number
    ORDER BY f.rule_id, week_number
"""

KPI5_AGGREGATE_MONTHLY_TREND_SQL = f"""
    WITH {_model_prices_cte()},
    monthly_cost AS (
        SELECT user_id, substr(ts, 1, 7) AS month,
               SUM(COALESCE(token_count, 0) / 1000.0 * price_per_1k) AS spend_usd
        FROM messages JOIN model_prices USING (model)
        GROUP BY user_id, month
    )
    SELECT month, COUNT(*) AS users_over_threshold
    FROM monthly_cost
    WHERE spend_usd >= ?
    GROUP BY month
    ORDER BY month
"""


def kpi5_message_pattern_trend(conn: sqlite3.Connection, anchor_monday: str = ANCHOR_MONDAY):
    return conn.execute(KPI5_MESSAGE_PATTERN_TREND_SQL, (anchor_monday,)).fetchall()


def kpi5_aggregate_monthly_trend(conn: sqlite3.Connection,
                                  threshold: float = RULE3_MONTHLY_SPEND_THRESHOLD_USD):
    return conn.execute(KPI5_AGGREGATE_MONTHLY_TREND_SQL, (threshold,)).fetchall()


# ---------------------------------------------------------------------------
# KPI 6: Estimated cost - tokens x public list price per model, labelled
# estimate. Per-user-month spend; percentile summarization is plain
# Python over this result (SQLite has no built-in percentile function).
# ---------------------------------------------------------------------------
KPI6_MONTHLY_SPEND_SQL = f"""
    WITH {_model_prices_cte()}
    SELECT user_id, substr(ts, 1, 7) AS month,
           SUM(COALESCE(token_count, 0) / 1000.0 * price_per_1k) AS spend_usd
    FROM messages JOIN model_prices USING (model)
    GROUP BY user_id, month
    ORDER BY spend_usd DESC
"""


def kpi6_monthly_spend_per_user(conn: sqlite3.Connection):
    return conn.execute(KPI6_MONTHLY_SPEND_SQL).fetchall()


ALL_KPI_SQL = {
    "kpi1_tool_inventory": KPI1_TOOL_INVENTORY_SQL,
    "kpi2_shadow_ai_rate": KPI2_SHADOW_AI_RATE_SQL,
    "kpi3_weekly_adoption": KPI3_WEEKLY_ADOPTION_SQL,
    "kpi3_volume_concentration": KPI3_VOLUME_CONCENTRATION_SQL,
    "kpi4_sensitive_exposure": KPI4_SENSITIVE_EXPOSURE_SQL,
    "kpi5_message_pattern_trend": KPI5_MESSAGE_PATTERN_TREND_SQL,
    "kpi5_aggregate_monthly_trend": KPI5_AGGREGATE_MONTHLY_TREND_SQL,
    "kpi6_monthly_spend_per_user": KPI6_MONTHLY_SPEND_SQL,
}
