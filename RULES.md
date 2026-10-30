# Policy rules — precise definitions

Formal reference for the three invented policy rules (CLAUDE.md §2), used
consistently by the generator (phase 1), the loader/tests (phase 2), the
detector (phase 3), and the dashboard (phase 4). Each rule has a
**trigger_type** (how it's evaluated) that is independent of **data
category** (what kind of sensitive content, if any, it's about) — these two
properties were conflated in the first draft of the generator and are now
tracked as separate fields.

| field | meaning |
|---|---|
| `trigger_type` | `message_pattern` = evaluated against one message's content via a detector/regex. `aggregate_monthly` = evaluated by summing a metric across a user's messages in a calendar month; no single message is "the" violation. |
| `applies_to` | which tool(s) the rule is evaluated against. |
| `data_category` | for `message_pattern` rules only: which `flags.category` value(s) the match can carry. **Not applicable** (NULL) for `aggregate_monthly` rules — a spend threshold is not a content-sensitivity classification. |

## Rule 1 — customer/patient identifiers in unsanctioned tools

> Any message sent by a user to an **unsanctioned** tool (`chatgpt`,
> `gemini` under current policy — see `tools.sanctioned`) that contains a
> customer-identifying or patient-identifying pattern triggers a Rule 1
> violation.

- `trigger_type`: `message_pattern`
- `applies_to`: unsanctioned sources only (`tools.sanctioned = 0`)
- `data_category`: one of `pii` (customer identifiers — e.g. SSN-shaped
  numbers, customer account numbers), `financial` (customer financial
  identifiers — e.g. IBAN, card numbers — treated as a customer-identifier
  subtype for this rule), `health` (patient identifiers — e.g. MRN, patient
  ID + DOB combinations)
- Detection method (phase 3): regex/rule-based pattern match on message
  text at ingestion time, before text is discarded. Known limitation: regex
  cannot distinguish a real identifier from a structurally-similar benign
  string (order numbers, SKUs, version strings, hashes) — see the benign
  near-miss corpus (`src/corpus.py`) used to measure the resulting
  false-positive rate.
- Each planted Rule 1 violation has **4 structurally distinct formats per
  category** (e.g. `financial` cycles through IBAN / credit card / bank
  account+routing / SWIFT+IBAN — see `src/corpus.py`'s
  `_financial_formats`), so detection isn't validated against one template.

## Rule 2 — credentials/API keys in any tool

> Any message, sent to **any** tool regardless of sanctioned status, that
> contains a credential-shaped pattern (API key, access key, password)
> triggers a Rule 2 violation.

- `trigger_type`: `message_pattern`
- `applies_to`: all sources
- `data_category`: `credential` only
- Detection method (phase 3): same as Rule 1 — regex/rule-based, same
  false-positive caveat.
- 6 structurally distinct formats (`src/corpus.py`'s `_credential_formats`):
  API key, AWS secret, password, PEM private-key header, bearer/OAuth
  token, generic PAT-style token.

## Rule 3 — monthly AI spend per user above a threshold

> For each user and each calendar month, sum `token_count × model unit
> price` across **all** of that user's messages on **all** tools
> (sanctioned and unsanctioned). If the sum is **≥ the threshold**, the
> user-month triggers a review.

- `trigger_type`: `aggregate_monthly`
- `applies_to`: all sources (this rule is about spend, not tool sanction
  status — a user can trigger it entirely on sanctioned tools)
- `data_category`: not applicable (NULL) — this is a numeric threshold on
  an aggregate metric, not a content classification. Do not tag it
  `financial` as if it were a sensitive-data category; that was a
  conflation in the phase-1 draft (see git history / LIMITATIONS.md).
- Comparison operator: `>=` (a user-month exactly at the threshold
  triggers). Boundary-case ground truth is planted specifically to test
  that phase-2/3 SQL uses the correct operator (see below).
- "Violation" here does not imply misconduct — it only means "crossed the
  review threshold." A legitimately heavy, policy-compliant user (e.g. a
  developer running many claude_code sessions) can cross it; the dataset
  deliberately includes such organic (non-planted) cases alongside
  deliberately planted ones, so phase 3 testing isn't only validated
  against obvious injected anomalies.

### Threshold derivation (stated budget logic, not fit to the data)

Northwind's invented policy reasoning, calculated **before** looking at any
generated numbers:

**Important clarification:** the $3.02 figure below is a **heavy-use
persona** — a hypothetical, deliberately demanding user chosen to anchor
the threshold calculation — not an estimate of the typical or median
employee's spend. The actual median organic user-month in this generated
dataset is far below it (≈$0.11; see `src/sample_kpis.py` KPI 6 output).
That gap is expected and correct: if the "heavy-but-normal" persona and
the median user landed at the same number, the persona wouldn't be heavy.
The threshold is meant to sit above *this persona's* baseline, which is
already well above the org-wide median by construction.

1. Define the persona: a policy-compliant developer who relies on
   `claude_code` heavily — ~12 substantial assistant turns per workday,
   each carrying ~2,000 tokens of code/file context on average, over a
   21-day working month, on a Claude Sonnet-class model at an assumed
   blended rate of $0.0060 / 1K tokens (see pricing caveat in
   LIMITATIONS.md). This is a stated assumption about one kind of heavy
   user, not a population statistic.
   - tokens/month = 12 × 2,000 × 21 = 504,000
   - cost/month = 504,000 / 1,000 × $0.0060 = **$3.02** (the persona's
     baseline monthly spend)
2. Set the review threshold at **2.5×** the persona's baseline, so even
   this deliberately heavy, policy-compliant persona wouldn't normally
   trigger review — only usage clearly beyond it would:
   - $3.02 × 2.5 = $7.56 → rounded to a clean **$7.50/user/month**.

`MONTHLY_SPEND_THRESHOLD_USD = 7.50`. This is an invented number for the
demo, following an explicit (if simplistic) calculation anchored to a
named persona — not derived by looking at percentiles of the generated
dataset. It still needs real CFO/owner sign-off before any non-demo use.

### Ground truth planted for Rule 3

Three distinct kinds of `planted_violations` rows with `rule_id = 3`:

- **Clear violations** (`note` starts with "clear"): 8 cases, each
  injected to land at a per-case multiplier drawn uniformly from **2×–6×**
  threshold (not a fixed 3× for all 8) — unambiguous cases for the
  dashboard demo story, varied so the planted set doesn't look
  suspiciously uniform. The exact multiplier achieved is in each row's
  `note` (e.g. "2.02x threshold" up to "5.85x threshold" in the current
  run — see `src/sample_kpis.py` output for the live numbers).
- **Boundary — over** (`expected_trigger = 1`): injected to land ~$0.25
  **above** $7.50, to test that the SQL correctly includes a case barely
  past the line.
- **Boundary — under** (`expected_trigger = 0`): injected to land ~$0.25
  **below** $7.50, to test that the SQL correctly *excludes* a case
  barely short of the line.
- **Organic / legitimate heavy users**: NOT planted — a small cohort of
  users ("dev power users") is given deliberately higher, but still
  generator-plausible, `claude_code` activity so that some of them cross
  $7.50/month purely through normal generation, with no injection and no
  `planted_violations` row. Report them separately (see
  `src/sample_kpis.py`) to confirm the rule also catches real heavy use,
  not only plants.

## Corpus linkage (message_pattern rules)

Every Rule 1/2 (`message_pattern`) row in `planted_violations` is
traceable to exactly one entry in `data/fixtures/corpus_dev.jsonl`:

- `planted_violations.corpus_id` = the corpus entry's `id`
  (e.g. `corpus-000042`)
- `planted_violations.corpus_text_hash` = sha256 of that entry's `text`,
  matching the entry's own `text_hash` field — an integrity check that the
  snippet actually injected into the message is byte-identical to what's
  on record in the corpus
- `planted_violations.injected_snippet` = the same text, inline, so a
  reader doesn't have to cross-reference the corpus file just to see what
  was planted

The generator draws these snippets **without replacement** from the
**dev split only** (`corpus.dev_true_violation_pool()`); the holdout split
is never read by `generate_synthetic_data.py`. This means: every
message_pattern violation in the database came from a corpus entry you
can look up, and the 80 held-out true-violation entries (20 per category)
are guaranteed untouched, available for an unbiased final evaluation of
whatever regex rules phase 3 settles on. See LIMITATIONS.md for corpus
sizing (varied formats per category, benign near-misses, 5,000+ benign
background texts).
