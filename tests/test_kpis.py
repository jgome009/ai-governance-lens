"""
Phase 2 tests: each KPI query returns exact expected values.

Two different oracles are used, matching what's actually knowable:

  - KPI 1/2/3/6 run against the real generated database. Expected values
    are computed independently in plain Python directly from `messages`/
    `tools` (never by re-running the KPI SQL itself), so a match is a
    real correctness check, not a tautology.
  - KPI 4/5 (message_pattern side) depend on `flags`, which is correctly
    EMPTY in the real database until phase 3 ships a detector. They are
    tested against a small hand-built fixture database with manually
    inserted flags rows of known count, independent of the generator.
  - KPI 5's aggregate_monthly side and the Rule 3 boundary cases ARE
    tested using `planted_violations` as the ground-truth oracle - that
    is its documented purpose (RULES.md, LIMITATIONS.md). This is
    different from the KPI SQL itself reading that table, which
    test_no_kpi_reads_planted_violations below checks never happens.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import kpi_queries  # noqa: E402


# ---------------------------------------------------------------------------
# Guardrail: no KPI query may reference planted_violations.
# ---------------------------------------------------------------------------
def test_no_kpi_reads_planted_violations():
    for name, sql in kpi_queries.ALL_KPI_SQL.items():
        assert "planted_violations" not in sql.lower(), (
            f"{name} references planted_violations, which is test-only ground "
            f"truth and must never be read by a production KPI query"
        )


# ---------------------------------------------------------------------------
# KPI 1: Tool inventory
# ---------------------------------------------------------------------------
def test_kpi1_tool_inventory_exact(conn):
    rows = cursor_rows(conn, "SELECT source, user_id, model FROM messages")
    by_source = defaultdict(lambda: {"users": set(), "models": set(), "n": 0})
    for source, user_id, model in rows:
        by_source[source]["users"].add(user_id)
        by_source[source]["models"].add(model)
        by_source[source]["n"] += 1

    result = {r[0]: r for r in kpi_queries.kpi1_tool_inventory(conn)}
    assert set(result) == set(by_source)
    for source, stats in by_source.items():
        _, sanctioned, active_users, models_used, messages = result[source]
        assert active_users == len(stats["users"])
        assert models_used == len(stats["models"])
        assert messages == stats["n"]


# ---------------------------------------------------------------------------
# KPI 2: Shadow AI rate
# ---------------------------------------------------------------------------
def test_kpi2_shadow_ai_rate_exact(conn):
    rows = cursor_rows(conn, """
        SELECT m.user_id, t.sanctioned FROM messages m JOIN tools t ON t.source = m.source
    """)
    total_messages = len(rows)
    unsanctioned_messages = sum(1 for _, s in rows if s == 0)
    expected_pct = round(100.0 * unsanctioned_messages / total_messages, 2)
    expected_unsanctioned_users = len({u for u, s in rows if s == 0})
    expected_total_users = len({u for u, _ in rows})

    pct, unsanctioned_users, total_users = kpi_queries.kpi2_shadow_ai_rate(conn)
    assert pct == expected_pct
    assert unsanctioned_users == expected_unsanctioned_users
    assert total_users == expected_total_users


# ---------------------------------------------------------------------------
# KPI 3: Adoption (weekly) + volume concentration
# ---------------------------------------------------------------------------
def test_kpi3_weekly_adoption_exact(conn):
    rows = cursor_rows(conn, "SELECT user_id, ts FROM messages")
    by_week_users = defaultdict(set)
    by_week_count = Counter()
    import datetime
    anchor = datetime.date(2026, 8, 3)
    for user_id, ts in rows:
        d = datetime.date.fromisoformat(ts[:10])
        week = (d - anchor).days // 7
        by_week_users[week].add(user_id)
        by_week_count[week] += 1

    result = {r[0]: r for r in kpi_queries.kpi3_weekly_adoption(conn)}
    assert set(result) == set(by_week_users)
    for week, users in by_week_users.items():
        _, wau, messages, _ = result[week]
        assert wau == len(users)
        assert messages == by_week_count[week]

    # Regression guard for the adoption-ramp fix: WAU must stay well below
    # 100% of all 400 users throughout the window (point 2 of the follow-up).
    assert max(wau for _, wau, _, _ in result.values()) < 400 * 0.9


def test_kpi3_volume_concentration_exact(conn):
    rows = cursor_rows(conn, "SELECT user_id, COUNT(*) AS n FROM messages GROUP BY user_id")
    counts = sorted((n for _, n in rows), reverse=True)
    total_users = len(counts)
    total_messages = sum(counts)
    top10_n = max(1, round(total_users * 0.10))
    expected_top10_messages = sum(counts[:top10_n])

    total_active_users, total_msgs, top10_user_count, top10_messages = \
        kpi_queries.kpi3_volume_concentration(conn)
    assert total_active_users == total_users
    assert total_msgs == total_messages
    # NTILE(10) bucket sizing can differ by 1 from round() at the boundary;
    # assert the SQL's own top decile share is within rounding of the
    # independently-counted top10_n share rather than requiring bucket
    # sizes to match exactly.
    assert abs(top10_user_count - top10_n) <= 1
    assert top10_messages / total_messages == pytest.approx(
        expected_top10_messages / total_messages, abs=0.02
    )


# ---------------------------------------------------------------------------
# KPI 4 / KPI 5 (message_pattern side): fixture database, known exact counts
# ---------------------------------------------------------------------------
def _seed_fixture_flags(fixture_db):
    import loader
    mid1 = loader.insert_message(fixture_db, "chatgpt", "u1", "c1", "2026-08-03T10:00:00",
                                  "user", "gpt-4o", 100, 25, 1, "h1")
    loader.insert_message(fixture_db, "chatgpt", "u1", "c1", "2026-08-03T10:01:00",
                           "assistant", "gpt-4o", 100, 25, 1, "h2")
    mid3 = loader.insert_message(fixture_db, "claude_web", "u2", "c2", "2026-08-10T10:00:00",
                                  "user", "claude-sonnet-4", 100, 25, 1, "h3")
    loader.insert_message(fixture_db, "claude_web", "u2", "c2", "2026-08-10T10:01:00",
                           "assistant", "claude-sonnet-4", 100, 25, 1, "h4")
    fixture_db.execute(
        "INSERT INTO flags (message_id, rule_id, category, matched_pattern_name) VALUES (?, 1, 'pii', 'ssn_regex')",
        (mid1,),
    )
    fixture_db.execute(
        "INSERT INTO flags (message_id, rule_id, category, matched_pattern_name) VALUES (?, 2, 'credential', 'api_key_regex')",
        (mid3,),
    )
    fixture_db.commit()
    return mid1, mid3


def test_kpi4_sensitive_exposure_fixture_exact(fixture_db):
    _seed_fixture_flags(fixture_db)
    rows = {r[0]: r for r in kpi_queries.kpi4_sensitive_exposure(fixture_db)}
    assert rows[0] == (0, 2, 1, 500.0)  # chatgpt: 2 messages, 1 flagged
    assert rows[1] == (1, 2, 1, 500.0)  # claude_web: 2 messages, 1 flagged


def test_kpi5_message_pattern_trend_fixture_exact(fixture_db):
    _seed_fixture_flags(fixture_db)
    rows = kpi_queries.kpi5_message_pattern_trend(fixture_db, anchor_monday="2026-08-03")
    assert rows == [(1, 0, 1), (2, 1, 1)]


# ---------------------------------------------------------------------------
# KPI 4 / KPI 5 (message_pattern side), now against the REAL database: the
# phase-3 detector has populated `flags` (previously always empty). Expected
# values are computed independently from messages+tools+flags directly -
# never from planted_violations, which is a different, test-only table.
# ---------------------------------------------------------------------------
def test_kpi4_sensitive_exposure_real_db_exact(conn):
    rows = cursor_rows(conn, """
        SELECT m.id, t.sanctioned, f.message_id
        FROM messages m JOIN tools t ON t.source = m.source
        LEFT JOIN flags f ON f.message_id = m.id
    """)
    by_sanctioned_total = Counter()
    by_sanctioned_flagged = defaultdict(set)
    seen_messages = defaultdict(set)
    for message_id, sanctioned, flagged_message_id in rows:
        seen_messages[sanctioned].add(message_id)
        if flagged_message_id is not None:
            by_sanctioned_flagged[sanctioned].add(flagged_message_id)
    for sanctioned in seen_messages:
        by_sanctioned_total[sanctioned] = len(seen_messages[sanctioned])

    result = {r[0]: r for r in kpi_queries.kpi4_sensitive_exposure(conn)}
    assert scalar(conn, "SELECT COUNT(*) FROM flags") > 0  # sanity: phase-3 detector has actually run
    for sanctioned, total in by_sanctioned_total.items():
        _, total_messages, flagged_messages, flagged_per_1000 = result[sanctioned]
        assert total_messages == total
        assert flagged_messages == len(by_sanctioned_flagged[sanctioned])
        assert flagged_per_1000 == round(1000.0 * flagged_messages / total_messages, 3)


def test_kpi5_message_pattern_trend_real_db_matches_planted_recall(conn):
    """Cross-check using planted_violations as the ground-truth ORACLE for
    what SHOULD have been detected (its documented purpose) - not as a
    data source the KPI query itself reads."""
    planted = cursor_rows(conn, """
        SELECT rule_id, message_id FROM planted_violations WHERE trigger_type = 'message_pattern'
    """)
    flagged_pairs = set(cursor_rows(conn, "SELECT rule_id, message_id FROM flags"))
    detected = sum(1 for rule_id, message_id in planted if (rule_id, message_id) in flagged_pairs)
    recall = detected / len(planted)
    assert recall == pytest.approx(1.0, abs=0.01)  # matches the ~100% measured on dev

    trend = kpi_queries.kpi5_message_pattern_trend(conn)
    assert sum(n for _, _, n in trend) == len(flagged_pairs)


# ---------------------------------------------------------------------------
# KPI 5 (aggregate_monthly side) / KPI 6: cross-checked against
# planted_violations as the ground-truth oracle (its documented purpose).
# ---------------------------------------------------------------------------
def test_kpi5_aggregate_monthly_trend_includes_all_planted_and_organic(conn):
    # Only rows that are SUPPOSED to trigger (expected_trigger=1: clear
    # violations + boundary-over) must appear in over_threshold.
    # Boundary-under rows are deliberately planted BELOW threshold - their
    # whole purpose is to confirm the query correctly excludes them.
    should_trigger = set(cursor_rows(conn, """
        SELECT user_id, month FROM planted_violations
        WHERE rule_id = 3 AND expected_trigger = 1
    """))
    should_not_trigger = set(cursor_rows(conn, """
        SELECT user_id, month FROM planted_violations
        WHERE rule_id = 3 AND expected_trigger = 0
    """))

    spend_rows = kpi_queries.kpi6_monthly_spend_per_user(conn)
    over_threshold = {(u, m) for u, m, s in spend_rows if s >= kpi_queries.RULE3_MONTHLY_SPEND_THRESHOLD_USD}

    assert should_trigger <= over_threshold, "every rule-3 case planted to trigger must be included"
    assert should_not_trigger.isdisjoint(over_threshold), "boundary-under cases must stay excluded"

    trend = kpi_queries.kpi5_aggregate_monthly_trend(conn)
    assert sum(n for _, n in trend) == len(over_threshold)


def test_kpi6_rule3_boundary_cases_exact(conn):
    boundary_rows = cursor_rows(conn, """
        SELECT user_id, month, metric_value, expected_trigger
        FROM planted_violations WHERE rule_id = 3 AND note LIKE 'boundary%'
    """)
    assert len(boundary_rows) == 10  # 5 over + 5 under

    spend_by_user_month = {(u, m): s for u, m, s in kpi_queries.kpi6_monthly_spend_per_user(conn)}
    threshold = kpi_queries.RULE3_MONTHLY_SPEND_THRESHOLD_USD

    for user_id, month, metric_value, expected_trigger in boundary_rows:
        kpi_spend = spend_by_user_month[(user_id, month)]
        assert kpi_spend == pytest.approx(metric_value, abs=0.01)
        actually_triggers = kpi_spend >= threshold
        assert actually_triggers == bool(expected_trigger), (
            f"{user_id}/{month}: kpi6 spend={kpi_spend:.2f} vs threshold={threshold} "
            f"disagrees with planted expected_trigger={expected_trigger}"
        )


def test_kpi6_clear_violations_vary_2x_to_6x(conn):
    clear_rows = cursor_rows(conn, """
        SELECT metric_value, threshold_value FROM planted_violations
        WHERE rule_id = 3 AND note LIKE 'clear%'
    """)
    assert len(clear_rows) == 8
    multipliers = [value / thresh for value, thresh in clear_rows]
    assert all(2.0 <= m <= 6.0 for m in multipliers)
    assert max(multipliers) - min(multipliers) > 1.0  # genuinely varied, not all ~equal


# ---------------------------------------------------------------------------
# Row counts (exact, deterministic)
# ---------------------------------------------------------------------------
def test_row_counts_exact(conn):
    assert scalar(conn, "SELECT COUNT(*) FROM tools") == 4
    assert scalar(conn, "SELECT COUNT(*) FROM flags") > 0  # phase-3 detector has run
    assert scalar(conn, "SELECT COUNT(*) FROM planted_violations WHERE rule_id = 1") == 60
    assert scalar(conn, "SELECT COUNT(*) FROM planted_violations WHERE rule_id = 2") == 40
    assert scalar(conn, "SELECT COUNT(*) FROM planted_violations WHERE rule_id = 3") == 18


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def cursor_rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]
