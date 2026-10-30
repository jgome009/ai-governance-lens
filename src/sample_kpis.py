import sqlite3
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "synthetic" / "northwind.db"

# Must match MODELS pricing in generate_synthetic_data.py - illustrative,
# unverified placeholders (see LIMITATIONS.md).
PRICE = {
    "gpt-4o": 0.0060, "gpt-4o-mini": 0.00030,
    "claude-opus-4": 0.0300, "claude-sonnet-4": 0.0060,
    "gemini-1.5-pro": 0.0025, "gemini-1.5-flash": 0.00015,
}
THRESHOLD = 7.50  # Rule 3 threshold; see RULES.md for the derivation

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

print("=== ROW COUNTS ===")
for t in ["tools", "messages", "flags", "planted_violations"]:
    n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"{t:20s} {n}")

print("\n=== KPI 1: Tool inventory (distinct sources/models, active users per tool) ===")
for row in cur.execute("""
    SELECT m.source, t.sanctioned, COUNT(DISTINCT m.user_id) AS active_users,
           COUNT(DISTINCT m.model) AS models_used, COUNT(*) AS messages
    FROM messages m JOIN tools t ON t.source = m.source
    GROUP BY m.source ORDER BY messages DESC
"""):
    print(row)

print("\n=== KPI 2: Shadow AI rate (share of messages & users on unsanctioned tools) ===")
row = cur.execute("""
    SELECT
      ROUND(100.0 * SUM(CASE WHEN t.sanctioned=0 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_messages_unsanctioned,
      (SELECT COUNT(DISTINCT user_id) FROM messages m2 JOIN tools t2 ON t2.source=m2.source WHERE t2.sanctioned=0) AS unsanctioned_users,
      (SELECT COUNT(DISTINCT user_id) FROM messages) AS total_active_users
    FROM messages m JOIN tools t ON t.source = m.source
""").fetchone()
print(row)

print("\n=== KPI 3: Adoption - weekly active users, all 12 weeks + top-10% volume concentration ===")
wau_rows = cur.execute("""
    SELECT CAST((julianday(substr(ts,1,10)) - julianday('2026-08-03')) / 7 AS INT) AS week_number,
           COUNT(DISTINCT user_id) AS wau, COUNT(*) AS messages
    FROM messages GROUP BY week_number ORDER BY week_number
""").fetchall()
for row in wau_rows:
    print(row, f"{row[1]/400*100:.1f}% of 400 users")
wau_vals = [w for _, w, _ in wau_rows]
print(f"WAU range: {min(wau_vals)}-{max(wau_vals)} ({min(wau_vals)/400*100:.1f}%-{max(wau_vals)/400*100:.1f}% of all 400 users)")

vol_rows = cur.execute("SELECT user_id, COUNT(*) AS n FROM messages GROUP BY user_id ORDER BY n DESC").fetchall()
total_msgs = sum(n for _, n in vol_rows)
top10_n = max(1, round(len(vol_rows) * 0.10))
top10_msgs = sum(n for _, n in vol_rows[:top10_n])
print(f"active users: {len(vol_rows)}/400 ({len(vol_rows)/400*100:.1f}%); "
      f"top {top10_n} users ({top10_n/len(vol_rows)*100:.1f}% of active) = "
      f"{top10_msgs/total_msgs*100:.1f}% of message volume")

print("\n=== KPI 5: Policy violations - planted ground truth per rule (trigger_type, category separated) ===")
for row in cur.execute("""
    SELECT rule_id, trigger_type, category, COUNT(*) AS n
    FROM planted_violations GROUP BY rule_id, trigger_type, category ORDER BY rule_id
"""):
    print(row)

print("\n=== KPI 6: Estimated monthly spend per user - percentiles + Rule 3 threshold crossings ===")
msg_rows = cur.execute("SELECT user_id, substr(ts,1,7), model, token_count FROM messages").fetchall()
totals = defaultdict(float)
for u, m, model, tc in msg_rows:
    totals[(u, m)] += (tc or 0) / 1000.0 * PRICE.get(model, 0.006)
vals = sorted(totals.values())
n = len(vals)
pct = lambda p: vals[min(n - 1, int(n * p))]
print(f"user-months: {n}")
print(f"min=${vals[0]:.3f} p50=${pct(0.50):.3f} p75=${pct(0.75):.3f} p90=${pct(0.90):.3f} "
      f"p95=${pct(0.95):.3f} p99=${pct(0.99):.3f} max=${vals[-1]:.3f}")

planted_um = set(cur.execute(
    "SELECT user_id, month FROM planted_violations WHERE rule_id = 3"
).fetchall())
over = {k: v for k, v in totals.items() if v >= THRESHOLD}
organic_over = {k: v for k, v in over.items() if k not in planted_um}
print(f"user-months >= ${THRESHOLD} threshold: {len(over)} total "
      f"({len(over) - len(organic_over)} planted, {len(organic_over)} organic/legitimate heavy users)")

conn.close()
