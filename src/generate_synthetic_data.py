"""
Synthetic data generator for AI Governance Lens (Northwind Medical GmbH demo).

Phases 1-3 (see CLAUDE.md phases 1-6): produces a reproducible SQLite
database matching the normalized schema in CLAUDE.md section 4 (schema +
insertion primitives live in loader.py, shared with future real-data
adapters), a populated `flags` table (phase 3's detector output), and a
test-only `planted_violations` ground-truth table used to measure
detection precision/recall.

Rule definitions, trigger types and the Rule-3 threshold derivation are
documented in RULES.md - this file implements them, it does not redefine
them. See RULES.md before changing any rule-related constant below.

Design note: raw message text is built in memory only to derive
char_count / token_count / text_hash (and, now, to run the detector -
see detector.py), then discarded - it is never written to the `messages`
table, per CLAUDE.md's "no raw text stored" constraint. The detector
itself has zero database access (see detector.py's docstring) and is
called here with a plain string, so it is architecturally incapable of
reading `planted_violations` - not just disciplined not to.

For Rule 1/2 planted violations, the detector is run on the exact
injected corpus snippet rather than a reconstructed full message. This is
equivalent to running it on the full message: the surrounding filler text
is drawn from textgen.VOCAB, which contains no digits and no
credential-shaped tokens, so it provably cannot match any rule in
detector.py - see LIMITATIONS.md for the full justification.
"""
import hashlib
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import corpus
import detector
import loader
from textgen import gen_text, gen_ordinary_message_text, text_stats

# ---------------------------------------------------------------------------
# Configuration (fixed for reproducibility)
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
NUM_USERS = 400
NUM_WEEKS = 12
ANCHOR_MONDAY = date(2026, 8, 3)  # fixed start date, independent of "today"
WINDOW_END_DATE = ANCHOR_MONDAY + timedelta(weeks=NUM_WEEKS)  # exclusive upper bound

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "synthetic" / "northwind.db"

# source -> (vendor, sanctioned, plan)
TOOLS = {
    "chatgpt":     ("OpenAI",    0, "Plus (consumer)"),
    "claude_web":  ("Anthropic", 1, "Team"),
    "claude_code": ("Anthropic", 1, "Team"),
    "gemini":      ("Google",    0, "Free (consumer)"),
}

# source -> [(model, usd_price_per_1k_tokens)] -- PLACEHOLDER blended prices,
# unverified against current public list prices (LIMITATIONS.md).
MODELS = {
    "chatgpt":     [("gpt-4o", 0.0060), ("gpt-4o-mini", 0.00030)],
    "claude_web":  [("claude-opus-4", 0.0300), ("claude-sonnet-4", 0.0060)],
    "claude_code": [("claude-sonnet-4", 0.0060)],
    "gemini":      [("gemini-1.5-pro", 0.0025), ("gemini-1.5-flash", 0.00015)],
}

# Rule 3 threshold - see RULES.md "Threshold derivation" for the full
# stated-budget-logic calculation (a heavy-use PERSONA, not the median
# user - see RULES.md clarification). This number is NOT fit to this
# generator's output.
MONTHLY_SPEND_THRESHOLD_USD = 7.50

# Adoption ramp (point 2): logistic curve, most of the org never reaches
# 100% adoption within the 12-week window - this models a realistic
# enterprise AI-tool rollout, not "everyone uses it from day one".
ADOPTION_CEILING = 0.80     # fraction of the 400 users who ever adopt at all
ADOPTION_MIDPOINT_WEEK = 4.0
ADOPTION_STEEPNESS = 0.7

# Once adopted, a user is still only "present" (sends >=1 message) in a
# given week with this probability - tiered by activity_weight so a chunk
# of adopters are occasional users, not weekly regulars.
PRESENCE_TIERS = [
    (1.5, "occasional", (0.15, 0.35)),
    (3.0, "regular",    (0.55, 0.80)),
    (float("inf"), "power", (0.85, 0.97)),
]

BASE_MESSAGES_PER_WEEK = 4.5

# A small cohort of legitimately heavy claude_code users ("dev power
# users") - realistic persona (engineer living in a coding agent all day),
# used so that some *organic* (non-planted) user-months cross the Rule 3
# threshold, not only deliberately injected ones (point 1).
DEV_POWERUSER_COUNT = 25
DEV_POWERUSER_ACTIVITY_RANGE = (30.0, 60.0)
DEV_POWERUSER_SOURCE_WEIGHTS = {"chatgpt": 5, "gemini": 5, "claude_web": 15, "claude_code": 75}

N_RULE1_VIOLATIONS = 60   # PII / patient identifiers in unsanctioned tools
N_RULE2_VIOLATIONS = 40   # credentials / API keys, any tool
N_RULE3_CLEAR = 8         # clearly over threshold, multiplier varies 2x-6x per case
N_RULE3_CLEAR_MULTIPLIER_RANGE = (2.0, 6.0)
N_RULE3_BOUNDARY_OVER = 5   # ~$0.25 above threshold, expected_trigger=1
N_RULE3_BOUNDARY_UNDER = 5  # ~$0.25 below threshold, expected_trigger=0
BOUNDARY_MARGIN_USD = 0.25


def adoption_fraction(week: int) -> float:
    return ADOPTION_CEILING / (1.0 + math.exp(-ADOPTION_STEEPNESS * (week - ADOPTION_MIDPOINT_WEEK)))


def pick_adoption_week(rng: random.Random) -> int | None:
    u = rng.random()
    if u >= ADOPTION_CEILING:
        return None  # never adopts within the window
    for week in range(NUM_WEEKS):
        if adoption_fraction(week) >= u:
            return week
    return NUM_WEEKS - 1


def presence_tier(activity_weight: float):
    for cutoff, name, prob_range in PRESENCE_TIERS:
        if activity_weight < cutoff:
            return name, prob_range
    return PRESENCE_TIERS[-1][1], PRESENCE_TIERS[-1][2]


def build_user_profiles(rng: random.Random):
    """One profile per user: adoption week, weekly presence probability,
    activity weight (skew), and per-source affinity."""
    profiles = {}
    sources = list(TOOLS.keys())
    user_ids = [f"u{i:04d}" for i in range(1, NUM_USERS + 1)]
    dev_poweruser_ids = set(rng.sample(user_ids, DEV_POWERUSER_COUNT))

    for user_id in user_ids:
        is_dev_poweruser = user_id in dev_poweruser_ids

        if is_dev_poweruser:
            activity_weight = rng.uniform(*DEV_POWERUSER_ACTIVITY_RANGE)
            weights = DEV_POWERUSER_SOURCE_WEIGHTS
        else:
            activity_weight = min(rng.paretovariate(1.5), 15.0)
            shadow_tendency = rng.choices(
                ["none", "low", "mixed", "high"], weights=[35, 30, 25, 10], k=1
            )[0]
            weights = {
                "none":  {"chatgpt": 0,  "gemini": 0,  "claude_web": 60, "claude_code": 40},
                "low":   {"chatgpt": 10, "gemini": 5,  "claude_web": 55, "claude_code": 30},
                "mixed": {"chatgpt": 30, "gemini": 20, "claude_web": 30, "claude_code": 20},
                "high":  {"chatgpt": 45, "gemini": 35, "claude_web": 10, "claude_code": 10},
            }[shadow_tendency]

        source_weights = [weights[s] for s in sources]
        tier_name, prob_range = presence_tier(activity_weight)
        presence_prob = rng.uniform(*prob_range)
        adoption_week = pick_adoption_week(rng)

        profiles[user_id] = {
            "activity_weight": activity_weight,
            "source_weights": source_weights,
            "presence_tier": tier_name,
            "presence_prob": presence_prob,
            "adoption_week": adoption_week,
            "is_dev_poweruser": is_dev_poweruser,
        }
    return profiles


def generate_messages(conn, rng: random.Random, profiles: dict):
    sources = list(TOOLS.keys())
    conv_counter = 0

    for user_id, profile in profiles.items():
        adoption_week = profile["adoption_week"]
        if adoption_week is None:
            continue  # never adopted within the 12-week window

        for week in range(NUM_WEEKS):
            if week < adoption_week:
                continue
            if rng.random() > profile["presence_prob"]:
                continue  # adopted, but not active this particular week

            week_start = ANCHOR_MONDAY + timedelta(weeks=week)
            lam = profile["activity_weight"] * BASE_MESSAGES_PER_WEEK
            n_messages_this_week = max(1, round(rng.gauss(lam, lam * 0.4 + 0.5)))

            remaining = n_messages_this_week
            while remaining > 0:
                conv_len = min(remaining, rng.randint(2, 8))
                remaining -= conv_len
                conv_counter += 1
                conversation_id = f"c{conv_counter:07d}"

                source = rng.choices(sources, weights=profile["source_weights"], k=1)[0]
                model = rng.choice(MODELS[source])[0]
                day_offset = rng.randint(0, 6)
                base_dt = datetime.combine(week_start + timedelta(days=day_offset), datetime.min.time())
                base_dt += timedelta(hours=rng.randint(8, 19), minutes=rng.randint(0, 59))

                sanctioned = bool(TOOLS[source][1])
                for turn in range(conv_len):
                    role = "user" if turn % 2 == 0 else "assistant"
                    text = gen_ordinary_message_text(rng, source, role)
                    char_count, token_count, text_hash = text_stats(text)
                    ts = (base_dt + timedelta(minutes=turn * rng.randint(1, 4))).isoformat()
                    message_id = loader.insert_message(conn, source, user_id, conversation_id, ts, role,
                                                         model, char_count, token_count, 1, text_hash)
                    for rule_id, category, pattern_name in detector.detect_flags(text, sanctioned):
                        loader.insert_flag(conn, message_id, rule_id, category, pattern_name)
    conn.commit()


def _build_dev_corpus_pools():
    """Dev-split true_violation entries, grouped by category. Holdout is
    never touched here - see RULES.md 'Corpus linkage'."""
    entries = corpus.build_full_corpus()
    pools = {cat: corpus.dev_true_violation_pool(entries, cat) for cat in corpus.CATEGORIES}
    return pools


def plant_rule1_rule2(conn, rng: random.Random):
    """Rule 1: PII/patient identifiers in unsanctioned tools (message_pattern).
       Rule 2: credentials/API keys in any tool (message_pattern).
    Injected text is drawn (without replacement) from the dev-split corpus
    pool per category, so every row here links to a traceable corpus_id."""
    cur = conn.cursor()
    pools = _build_dev_corpus_pools()
    for cat in pools:
        rng.shuffle(pools[cat])

    unsanctioned_ids = [
        r[0] for r in cur.execute(
            "SELECT m.id FROM messages m JOIN tools t ON t.source = m.source "
            "WHERE t.sanctioned = 0 AND m.role = 'user'"
        ).fetchall()
    ]
    any_ids = [
        r[0] for r in cur.execute("SELECT id FROM messages WHERE role = 'user'").fetchall()
    ]

    rule1_targets = rng.sample(unsanctioned_ids, min(N_RULE1_VIOLATIONS, len(unsanctioned_ids)))
    remaining_pool = [i for i in any_ids if i not in set(rule1_targets)]
    rule2_targets = rng.sample(remaining_pool, min(N_RULE2_VIOLATIONS, len(remaining_pool)))

    def inject(message_id: int, corpus_entry: dict, rule_id: int):
        row = cur.execute(
            "SELECT user_id, ts, char_count, source FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        user_id, ts, old_char_count, source = row
        sanctioned = bool(TOOLS[source][1])
        snippet = corpus_entry["text"]
        week_number = (date.fromisoformat(ts[:10]) - ANCHOR_MONDAY).days // 7
        month = ts[:7]
        new_char_count = old_char_count + len(snippet) + 1
        new_token_count = max(1, round(new_char_count / 4))
        new_hash = hashlib.sha256(f"{snippet}-{message_id}".encode("utf-8")).hexdigest()
        cur.execute(
            "UPDATE messages SET char_count = ?, token_count = ?, text_hash = ? WHERE id = ?",
            (new_char_count, new_token_count, new_hash, message_id),
        )
        cur.execute(
            """INSERT INTO planted_violations
               (rule_id, trigger_type, category, user_id, message_id, week_number, month,
                corpus_id, corpus_text_hash, injected_snippet, note)
               VALUES (?, 'message_pattern', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rule_id, corpus_entry["category"], user_id, message_id, week_number, month,
             corpus_entry["id"], corpus_entry["text_hash"], snippet,
             f"planted by generate_synthetic_data.py, from corpus dev entry {corpus_entry['id']} "
             f"(format_index={corpus_entry['format_index']})"),
        )
        # Detector runs on the injected snippet itself - equivalent to running
        # on the full message, since the surrounding filler text is provably
        # pattern-free (see module docstring).
        for detected_rule_id, category, pattern_name in detector.detect_flags(snippet, sanctioned):
            loader.insert_flag(conn, message_id, detected_rule_id, category, pattern_name)

    for mid in rule1_targets:
        category = rng.choice(["pii", "health", "financial"])
        pool = pools[category]
        if not pool:
            category = next(c for c in ["pii", "health", "financial"] if pools[c])
            pool = pools[category]
        entry = pool.pop()
        inject(mid, entry, rule_id=1)

    for mid in rule2_targets:
        entry = pools["credential"].pop()
        inject(mid, entry, rule_id=2)

    conn.commit()
    return len(rule1_targets), len(rule2_targets)


def _inject_rule3_message(conn, rng, user_id, month, source, model, tokens, day):
    text = gen_text(rng, 150, 150)
    char_count, _, text_hash = text_stats(text)
    ts = f"{month}-{day:02d}T{rng.randint(8,19):02d}:00:00"
    conv_id = f"c-rule3-{user_id}-{month}-{day}"
    return loader.insert_message(conn, source, user_id, conv_id, ts, "assistant",
                                  model, char_count, tokens, 1, text_hash)


def _monthly_cost(cur, price_by_model, user_id, month) -> float:
    total = 0.0
    for model, tc in cur.execute(
        "SELECT model, token_count FROM messages WHERE user_id = ? AND substr(ts,1,7) = ?",
        (user_id, month),
    ).fetchall():
        total += (tc or 0) / 1000.0 * price_by_model.get(model, 0.006)
    return total


def plant_rule3(conn, rng: random.Random):
    """Rule 3: monthly AI spend per user >= MONTHLY_SPEND_THRESHOLD_USD
    (aggregate_monthly). Plants three kinds of ground truth: clear
    violations (multiplier varies 2x-6x per case), and boundary
    over/under cases - see RULES.md."""
    cur = conn.cursor()
    price_by_model = {m: p for models in MODELS.values() for m, p in models}

    user_months = cur.execute(
        "SELECT user_id, substr(ts, 1, 7) AS month FROM messages GROUP BY user_id, month"
    ).fetchall()
    rng.shuffle(user_months)

    def inject_to_target(user_id, month, target_usd, max_msgs, max_tokens_per_msg):
        current_cost = _monthly_cost(cur, price_by_model, user_id, month)
        source_row = cur.execute(
            "SELECT source, model FROM messages WHERE user_id = ? AND substr(ts,1,7) = ? LIMIT 1",
            (user_id, month),
        ).fetchone()
        if not source_row:
            return None
        source, _ = source_row
        source_models = [m for m, _ in MODELS[source]]
        inject_model = max(source_models, key=lambda m: price_by_model.get(m, 0))
        inject_price = price_by_model[inject_model]

        deficit = target_usd - current_cost
        if deficit <= 0:
            return None  # already past target without injection; skip, pick another candidate
        tokens_needed = deficit / inject_price * 1000.0
        n_msgs = max(1, min(max_msgs, -(-int(tokens_needed) // max_tokens_per_msg)))
        tokens_per_msg = max(1, -(-int(tokens_needed) // n_msgs))

        # Never schedule an injected message before the window start or
        # on/after the window end. The anchor month (2026-08) starts mid-way
        # through on ANCHOR_MONDAY (day 3) - day 1 there would predate the
        # window and produce a negative week number; a trailing partial
        # month at the other end would get phantom activity past it.
        year, month_num = (int(p) for p in month.split("-"))
        first_of_month = date(year, month_num, 1)
        min_valid_date = max(first_of_month, ANCHOR_MONDAY)
        min_day = (min_valid_date - first_of_month).days + 1
        days_in_window = (WINDOW_END_DATE - first_of_month).days
        max_day = max(min_day, min(28, days_in_window))

        injected_ids = []
        added_cost = 0.0
        for i in range(n_msgs):
            day = (min_day + (i * (max_day - min_day) // max(1, n_msgs - 1))
                   if n_msgs > 1 else (min_day + max_day) // 2)
            tokens_this_msg = round(tokens_per_msg * rng.uniform(0.97, 1.03))
            mid = _inject_rule3_message(conn, rng, user_id, month, source, inject_model, tokens_this_msg, day)
            injected_ids.append(mid)
            added_cost += tokens_this_msg / 1000.0 * inject_price
        return injected_ids, current_cost + added_cost

    planted = {"clear": 0, "boundary_over": 0, "boundary_under": 0}
    used = set()

    def claim(ceiling_usd):
        for user_id, month in user_months:
            if (user_id, month) in used:
                continue
            if _monthly_cost(cur, price_by_model, user_id, month) < ceiling_usd:
                used.add((user_id, month))
                return user_id, month
        return None

    # Clear violations: multiplier varies 2x-6x threshold per case (not a
    # fixed 3x), so the demo dataset isn't suspiciously uniform.
    for _ in range(N_RULE3_CLEAR):
        cand = claim(MONTHLY_SPEND_THRESHOLD_USD)
        if not cand:
            break
        user_id, month = cand
        multiplier = rng.uniform(*N_RULE3_CLEAR_MULTIPLIER_RANGE)
        target = MONTHLY_SPEND_THRESHOLD_USD * multiplier
        result = inject_to_target(user_id, month, target, max_msgs=80, max_tokens_per_msg=6000)
        if not result:
            continue
        injected_ids, final_cost = result
        cur.execute(
            """INSERT INTO planted_violations
               (rule_id, trigger_type, category, user_id, message_id, month,
                metric_name, metric_value, threshold_value, expected_trigger, note)
               VALUES (3, 'aggregate_monthly', NULL, ?, ?, ?, 'monthly_spend_usd', ?, ?, 1, ?)""",
            (user_id, injected_ids[0], month, round(final_cost, 4), MONTHLY_SPEND_THRESHOLD_USD,
             f"clear violation: {len(injected_ids)} injected messages, final=${final_cost:.2f} "
             f"({multiplier:.2f}x threshold ${MONTHLY_SPEND_THRESHOLD_USD:.2f})"),
        )
        planted["clear"] += 1

    # Boundary - over: target threshold + margin, expected_trigger=1.
    for _ in range(N_RULE3_BOUNDARY_OVER):
        cand = claim(MONTHLY_SPEND_THRESHOLD_USD - BOUNDARY_MARGIN_USD)
        if not cand:
            break
        user_id, month = cand
        target = MONTHLY_SPEND_THRESHOLD_USD + BOUNDARY_MARGIN_USD
        result = inject_to_target(user_id, month, target, max_msgs=10, max_tokens_per_msg=6000)
        if not result:
            continue
        injected_ids, final_cost = result
        cur.execute(
            """INSERT INTO planted_violations
               (rule_id, trigger_type, category, user_id, message_id, month,
                metric_name, metric_value, threshold_value, expected_trigger, note)
               VALUES (3, 'aggregate_monthly', NULL, ?, ?, ?, 'monthly_spend_usd', ?, ?, 1, ?)""",
            (user_id, injected_ids[0], month, round(final_cost, 4), MONTHLY_SPEND_THRESHOLD_USD,
             f"boundary-over: final=${final_cost:.2f}, threshold=${MONTHLY_SPEND_THRESHOLD_USD:.2f} "
             f"(must be included by >= threshold)"),
        )
        planted["boundary_over"] += 1

    # Boundary - under: target threshold - margin, expected_trigger=0.
    for _ in range(N_RULE3_BOUNDARY_UNDER):
        cand = claim(MONTHLY_SPEND_THRESHOLD_USD - BOUNDARY_MARGIN_USD - 0.05)
        if not cand:
            break
        user_id, month = cand
        target = MONTHLY_SPEND_THRESHOLD_USD - BOUNDARY_MARGIN_USD
        result = inject_to_target(user_id, month, target, max_msgs=10, max_tokens_per_msg=6000)
        if not result:
            continue
        injected_ids, final_cost = result
        if final_cost >= MONTHLY_SPEND_THRESHOLD_USD:
            continue  # overshot past threshold; skip rather than mislabel
        cur.execute(
            """INSERT INTO planted_violations
               (rule_id, trigger_type, category, user_id, message_id, month,
                metric_name, metric_value, threshold_value, expected_trigger, note)
               VALUES (3, 'aggregate_monthly', NULL, ?, ?, ?, 'monthly_spend_usd', ?, ?, 0, ?)""",
            (user_id, injected_ids[0], month, round(final_cost, 4), MONTHLY_SPEND_THRESHOLD_USD,
             f"boundary-under: final=${final_cost:.2f}, threshold=${MONTHLY_SPEND_THRESHOLD_USD:.2f} "
             f"(must be excluded by >= threshold)"),
        )
        planted["boundary_under"] += 1

    conn.commit()
    return planted


def main():
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    rng = random.Random(RANDOM_SEED)
    conn = sqlite3.connect(DB_PATH)
    try:
        loader.ensure_schema(conn)
        loader.load_tools(conn, TOOLS)
        profiles = build_user_profiles(rng)
        generate_messages(conn, rng, profiles)
        n1, n2 = plant_rule1_rule2(conn, rng)
        n3 = plant_rule3(conn, rng)
        conn.commit()
    finally:
        conn.close()

    print(f"Database written to {DB_PATH}")
    print(f"Planted: rule1={n1} rule2={n2} rule3={n3}")


if __name__ == "__main__":
    sys.exit(main())
