# Limitations and open items

Kept up to date as the project progresses (CLAUDE.md §7). Phases 1-3 are
now implemented (generator, loader + KPI queries + tests, rule-based
detector). See RULES.md for formal rule definitions and the
corpus-linkage design, and EVALUATION.md for the one-time final
holdout/stress numbers referenced below.

## Unverified / needs owner input

- **Model token prices** in `src/generate_synthetic_data.py` (`MODELS`
  dict) are illustrative placeholders, not checked against current public
  list prices. Proportioned relative to each other (opus ≫ sonnet >
  gemini-pro > gpt-4o > gemini-flash > gpt-4o-mini) but the absolute
  numbers are unverified.
- **Monthly spend threshold** for Rule 3 (`MONTHLY_SPEND_THRESHOLD_USD =
  7.50`) is derived from a stated budget calculation (RULES.md "Threshold
  derivation") anchored to an explicitly-named **heavy-use persona**, not
  to the organic median user (≈$0.11/month) or to any percentile of the
  generated data. It is still an invented number for the demo and needs
  CFO/owner sign-off before any non-demo use.
- **Adoption ramp shape** (logistic curve, 80% ceiling, midpoint week 4,
  steepness 0.7) is an assumption about rollout speed, not real adoption
  data for Northwind or any comparable company.
- **Export formats** for chatgpt / claude_web / claude_code / gemini have
  not been seen yet — this generator writes directly into the normalized
  schema (CLAUDE.md §4) via `loader.py`, not through a parser. Real-data
  adapters (phase 5) must be built against actual sample exports and
  should reuse `loader.py`'s `ensure_schema`/`load_tools`/`insert_message`
  rather than re-deriving the DDL.
- **EU AI Act / GDPR** framing is out of scope; no legal claims are made
  anywhere in this codebase.

## Known design tradeoffs (phase 1)

- `flags` is now populated by the generator, but only because the
  generator calls `detector.py` on text at the moment it exists in
  memory, before discarding it (see "Phase 3" below) — it is still
  exclusively the detector's output, not a shortcut using ground truth.
- **Rule trigger type vs. data category are separate fields**
  (`planted_violations.trigger_type`, `.category`). `category` is `NULL`
  for all `aggregate_monthly` (Rule 3) rows — a spend threshold is not a
  content-sensitivity classification. See RULES.md.
- **Rule 3 ground truth has three kinds of rows**: *clear* violations (8
  cases, multiplier drawn uniformly from **2×–6×** threshold per case, not
  a fixed ratio), *boundary-over* (`expected_trigger=1`, ~$0.25 above) and
  *boundary-under* (`expected_trigger=0`, ~$0.25 below), testing the `>=`
  operator at the edge. A separate, **non-planted** "dev power user"
  cohort (25 of 400 users) produces organic crossings alongside the 13
  planted ones.
- **Token volumes recalibrated per source**: `claude_code` assistant
  turns are generated much larger (400–4,000 words) than chat tools, to
  approximate real coding-agent context. Token counts remain a heuristic
  (`chars / 4`), always flagged `tokens_estimated = 1`.
- **Adoption + presence are two separate gates**: `adoption_week` (20% of
  users never adopt within the window) and, once adopted, a tiered
  `presence_prob` (occasional/regular/power), so even adopted users miss
  weeks. Current run: WAU ranges roughly 2.5%–48% of all 400 users across
  the 12 weeks (exact numbers drift slightly as the generator changes —
  see `src/sample_kpis.py` for the live figures, not a frozen snapshot
  here).
- No `users` table — `user_id` is a bare string key on `messages`,
  matching CLAUDE.md §4 exactly.

## Corpus for phase-3 detector testing (restructured this pass)

- `src/corpus.py` is the single source of truth for all test text; both
  `generate_synthetic_data.py` (for injecting Rule 1/2 violations) and
  `generate_text_corpus.py` (for writing fixture files) call it with the
  same fixed seeds, so they always agree.
- **Varied formats per category**: 4 structurally distinct templates each
  for `pii`/`health`/`financial`, 6 for `credential` (see RULES.md) — not
  one template with randomized digits.
- **Corpus linkage**: every message_pattern row in `planted_violations`
  now carries `corpus_id` + `corpus_text_hash`, pointing at the exact dev
  corpus entry whose text was injected. Verified 1:1 (100 rows → 100
  distinct corpus entries, text/hash/category/rule_id all matching) after
  this change — see RULES.md "Corpus linkage".
- **Benign background text**: 5,200 dev-split entries (tuning scale) and
  **15,500 holdout-split** entries (≥15,000, since the final false-positive
  rate is measured on holdout, not dev) — non-violating message-shaped
  strings, same size distribution as real messages (`textgen.py`, capped
  at `BACKGROUND_MAX_WORDS=600` so 15k+ entries still build in ~1s).
- **Dev/holdout split**: `corpus.py`'s `_assign_split()` (fixed
  `SPLIT_SEED`, independent of the generation RNG) assigns ~75% of each
  true-violation/near-miss pool to `dev`, ~25% to `holdout`.
  `generate_synthetic_data.py` only ever reads the **dev** split;
  `data/fixtures/corpus_holdout.jsonl` (80 true-violation + 75 near-miss +
  15,500 background entries) was never touched by the detector's
  development - it exists purely for the one-time final evaluation in
  EVALUATION.md.
- Both fixture files are **not committed** (`.gitignore`): synthetic, but
  still PII/credential-*shaped* text, kept out of version history as
  hygiene, separately from `data/real/` (excluded because it would be
  real data).
- Neither fixture file affects `northwind.db`'s reproducibility hash — the
  database only stores the already-injected snippet text inline in
  `messages`/`planted_violations`, not a reference to the corpus file.

## Phase 2 (loader, KPI queries, tests)

- `src/loader.py` holds the schema DDL and insert primitives;
  `generate_synthetic_data.py` no longer defines its own copy.
- `src/kpi_queries.py` holds one SQL query per KPI, for KPIs 1, 2, 3, 4, 5,
  6 (CLAUDE.md §5). **KPI 7 (task mix / value proxy) is deferred** — it
  needs a keyword-based message-content classifier that doesn't exist
  yet (not built in phase 1, and CLAUDE.md doesn't assign it to a
  specific phase); implementing it now would mean fabricating
  categorization logic ahead of the detection-rules phase. Flagging this
  rather than forcing a placeholder, per CLAUDE.md §7 ("say I'm not sure").
- KPI 4 (sensitive-data exposure) and KPI 5 (violations by rule/week) read
  `flags`, which phase 3 now populates. Tested two ways: against a small
  hand-built fixture database with known counts (isolates the SQL logic
  from the generator entirely), and against the real `northwind.db` with
  expected values computed independently from `messages`+`tools`+`flags`
  (and, for a recall cross-check only, `planted_violations` as the
  ground-truth *oracle* - never as a data source the KPI query itself
  reads; see `tests/test_kpis.py`).
- No KPI query reads `planted_violations` — enforced by an automated test
  that greps every SQL string in `kpi_queries.py` for the literal
  substring `planted_violations` and fails if found.
  `planted_violations` is read only by the *tests*, as the ground-truth
  oracle for exact-value assertions (its documented purpose since phase
  1) — never by the production query layer.

## Phase 3 (rule-based detector)

- `src/detector.py` implements the rule list from RULES.md as shape-based
  regexes (digit groupings, literal prefix + operator), not keyword
  matches — the background corpus's own vocabulary includes words like
  "token"/"patient"/"config"/"test", so a keyword-only rule would flood
  false positives. It has zero database access; it takes a string, returns
  matches. Tuned **only** against `data/fixtures/corpus_dev.jsonl`
  (`src/tune_on_dev.py`) — two real precision gaps were found and fixed
  this way (an unanchored SSN digit-pattern, and no placeholder/test-value
  exclusion for IBANs), both generalizable rules, not corpus-literal
  overfits.
- **Flags are populated at generation time, not by reading the DB back**:
  `generate_messages()` runs the detector on each message's text in the
  instant before that text is discarded; `plant_rule1_rule2()` runs it on
  the exact injected corpus snippet rather than a reconstructed full
  message. This is provably equivalent to scanning the full message,
  because the surrounding filler text is built from `textgen.VOCAB`,
  which contains no digits and no credential-shaped tokens — it cannot
  match any rule in `detector.py`. Verified empirically after generation:
  zero flags on any message with no corresponding `planted_violations`
  row (see the generation session's verification step).
- **Never reads `planted_violations`**: enforced architecturally
  (`detector.py` has no DB access at all) and by an automated test
  (`tests/test_detector.py`) that also checks `loader.py` has no
  SELECT/INSERT/UPDATE/DELETE against that table — it only owns the
  table's schema (`CREATE TABLE`).
- **Stress corpus** (`src/stress_corpus.py`, `data/fixtures/stress_corpus.jsonl`,
  32 entries: 24 true-violation + 8 benign) covers German-language
  phrasing, obfuscated/spaced values, key formats with no dedicated rule
  (Stripe/Slack/JWT/OpenSSH), and sensitive data inside code/log syntax.
  Built once, never read by `tune_on_dev.py` or the generator — only by
  `evaluate_detector.py`, at the very end.

### Final evaluation (one-time) — see EVALUATION.md for full numbers

Run once via `src/evaluate_detector.py` after tuning was frozen. Holdout
(same distribution as dev, just unseen rows) matched dev almost exactly:
100% recall/precision across all 4 categories, 0% background FPR
(n=15,500/category), 0% near-miss FPR — consistent with, not surprising
given, the dev numbers.

**Stress recall collapsed to 16.7%-36.4% per category** (vs. 100% on
holdout) — this is the actual, honest limitation of a shape-based regex
detector, not a bug to fix quietly:
- Obfuscated (letter/digit-spaced) and unfamiliar key-format cases: ~0%
  recall — expected, since the regexes require contiguous structural
  shapes.
- German-language cases: ~50% recall — survives when an English keyword
  ("SSN", "DOB", "password") happens to be kept inline, fails on fully
  native phrasing ("Patientin", "Geburtsdatum", "Passwort").
- Code/log-embedded credentials: recall held up well (100% on the 3
  stress cases) — assignment syntax (`KEY = "value"`) structurally
  matches the keyword+operator pattern regardless of surrounding code
  fencing.
- All stress sample sizes are small (n=1-11 per category/scenario cell) —
  the 95% CIs in EVALUATION.md are correspondingly wide; treat the point
  estimates as directional, not precise, exactly because of that n.

**This detector is production-naive by construction** - it is a
first-pass rule set meant to establish an honest, measured baseline (per
CLAUDE.md §3 "heuristics are labelled... no fake precision"), not a
claim that shape-based regex is sufficient for a real deployment.

### Evaluation integrity - read before touching anything in phase 4+

- **Database end-to-end results are a demo on dev-derived data.** Every
  flag in `northwind.db` comes from running the frozen detector against
  either (a) ordinary filler text that is provably pattern-free, or (b)
  injected snippets drawn from the **dev** corpus split — the same split
  the detector was tuned against. The ~100% recall visible in the live
  database (`tests/test_kpis.py`'s
  `test_kpi5_message_pattern_trend_real_db_matches_planted_recall`) is
  therefore an expected consequence of tuning on dev-sourced violations,
  **not** an unbiased measurement of real-world detector performance.
  The unbiased numbers are in EVALUATION.md (holdout + stress), not the
  database.
- **Any further tuning of `detector.py` burns the holdout and stress
  sets.** If a future session edits a regex in `detector.py` after
  reading EVALUATION.md's numbers — even "just to fix the obvious German
  gap" — and then re-runs `evaluate_detector.py`, the new numbers are no
  longer a held-out measurement; they are dev-tuning that happened to use
  the holdout/stress files as the dev set. If detection needs to improve,
  the correct process is: generate a *new* stress/holdout batch (new
  seeds) for the next evaluation, or explicitly relabel the current
  holdout/stress as "spent" and accept that the next real holdout
  evaluation requires fresh fixtures.
- KPI 7 (task mix) is still deferred — it needs a message-content
  classifier that doesn't exist, separate from Rule 1/2/3 detection.
