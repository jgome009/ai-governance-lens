"""
Deterministic text corpus for phase-3 detector testing.

Separate from northwind.db by design: the normalized schema (CLAUDE.md
section 4) never stores raw message text, so a standalone fixture is
needed to (a) supply the exact text injected for each Rule 1/2 planted
violation in the database, so that link is traceable, and (b) give
phase 3 a realistic-scale false-positive test bed (benign background
text + near-misses), not just a handful of hand-picked examples.

Three entry kinds:
  - true_violation   - a genuine Rule 1 (pii/health/financial) or
                        Rule 2 (credential) match, several structurally
                        distinct FORMATS per category so detection isn't
                        tuned to one template.
  - benign_near_miss  - structurally similar to a category but not a
                        real match (tests false-positive rate).
  - benign_background - ordinary, non-sensitive message text, same size
                        distribution as real messages (tests false-positive
                        rate at realistic scale: >=15,000 HOLDOUT entries,
                        since the holdout background is what the final
                        FPR numbers are computed from).

Every entry gets a deterministic id and a sha256 hash of its text. The
whole corpus is split into 'dev' and 'holdout': generate_synthetic_data.py
draws ONLY from the dev-split true_violation pool when injecting Rule 1/2
violations into messages (so planted_violations can record which exact
corpus entry produced each injected snippet). The holdout split is never
touched by the generator - it exists purely for a final, unbiased
detector evaluation once phase 3's regex rules are frozen.
"""
import hashlib
import random

from textgen import gen_text

CORPUS_SEED = 4242
SPLIT_SEED = 777
DEV_FRACTION = 0.75  # applies to true_violation and benign_near_miss pools

N_TRUE_VIOLATIONS_PER_CATEGORY = 80   # -> ~60 dev / ~20 holdout per category
N_NEAR_MISS_PER_TYPE = 60             # -> ~45 dev / ~15 holdout per type
BACKGROUND_DEV_COUNT = 5200           # dev-tuning scale, not the final FPR measurement
BACKGROUND_HOLDOUT_COUNT = 15500      # ">= 15,000 ... for false-positive measurement" + margin
BACKGROUND_MAX_WORDS = 600            # cap per-entry length so 15.5k entries generate quickly

CATEGORIES = ["pii", "health", "financial", "credential"]


# ---------------------------------------------------------------------------
# True violations - varied formats per category. Each category has several
# structurally distinct template functions; entries cycle through them so
# detection can't be tuned to a single string shape.
# ---------------------------------------------------------------------------

def _pii_formats(rng):
    fmts = [
        lambda: f"Customer SSN {rng.randint(100,899)}-{rng.randint(10,99)}-{rng.randint(1000,9999)}, please update the account.",
        lambda: f"Customer Account# ACC-{rng.randint(1000000,9999999)} needs a profile correction.",
        lambda: f"Driver's license number D{rng.randint(10000000,99999999)} was submitted for verification.",
        lambda: f"Passport No. P{rng.randint(10000000,99999999)} attached for the travel reimbursement.",
    ]
    return fmts


def _health_formats(rng):
    fmts = [
        lambda: f"Patient MRN-{rng.randint(100000,999999)}, DOB {rng.randint(1,28):02d}/{rng.randint(1,12):02d}/19{rng.randint(40,99)}, requesting a lab summary.",
        lambda: f"Patient {rng.choice(['Jane Doe','John Smith','Maria Alves','Ahmed Khan'])}, DOB {rng.randint(1,28):02d}/{rng.randint(1,12):02d}/19{rng.randint(40,99)}, follow-up needed.",
        lambda: f"Member ID: INS-{rng.randint(1000000,9999999)}, please confirm coverage for the procedure.",
        lambda: f"Diagnosis code J45.{rng.randint(100,999)} for patient ref PT-{rng.randint(10000,99999)}.",
    ]
    return fmts


def _financial_formats(rng):
    fmts = [
        lambda: f"Customer IBAN DE{rng.randint(10,99)}370400440{rng.randint(100000000,999999999)}, can you draft a refund confirmation?",
        lambda: f"Card number 4{rng.randint(100,999)} {rng.randint(1000,9999)} {rng.randint(1000,9999)} {rng.randint(1000,9999)} on file for the dispute.",
        lambda: f"Account {rng.randint(100000000,999999999)}, Routing {rng.randint(100000000,999999999)} - wire the deposit here.",
        lambda: f"SWIFT COBADEFF{rng.randint(100,999)}, IBAN DE{rng.randint(10,99)}370400440{rng.randint(100000000,999999999)} for the international transfer.",
    ]
    return fmts


def _credential_formats(rng):
    fmts = [
        lambda: f"api_key=sk-live-{rng.getrandbits(128):032x}",
        lambda: f"export AWS_SECRET_ACCESS_KEY={rng.getrandbits(160):040x}",
        lambda: f"temp password: Northwind!{rng.randint(1000,9999)} - please rotate it",
        lambda: "-----BEGIN RSA PRIVATE KEY-----\nMIIBOg" + f"{rng.getrandbits(64):016x}" + "\n-----END RSA PRIVATE KEY-----",
        lambda: f"Authorization: Bearer ya29.{rng.getrandbits(96):024x}",
        lambda: f"token=ghp_{rng.getrandbits(144):036x}",
    ]
    return fmts


_CATEGORY_FORMAT_BUILDERS = {
    "pii": _pii_formats,
    "health": _health_formats,
    "financial": _financial_formats,
    "credential": _credential_formats,
}
_CATEGORY_RULE_ID = {"pii": 1, "health": 1, "financial": 1, "credential": 2}


def build_true_violations(rng):
    entries = []
    for category in CATEGORIES:
        fmts = _CATEGORY_FORMAT_BUILDERS[category](rng)
        for i in range(N_TRUE_VIOLATIONS_PER_CATEGORY):
            fmt = fmts[i % len(fmts)]
            text = fmt()
            entries.append({
                "label": "true_violation",
                "rule_id": _CATEGORY_RULE_ID[category],
                "category": category,
                "format_index": i % len(fmts),
                "text": text,
            })
    return entries


# ---------------------------------------------------------------------------
# Benign near-misses - structurally similar to a category, not a real match.
# ---------------------------------------------------------------------------

def build_benign_near_misses(rng):
    entries = []

    def add(n, near_miss_for, note, fmt):
        for _ in range(n):
            entries.append({
                "label": "benign_near_miss",
                "near_miss_for": near_miss_for,
                "note": note,
                "text": fmt(),
            })

    add(N_NEAR_MISS_PER_TYPE, "pii",
        "same digit grouping as an SSN but is an order number",
        lambda: f"Order number {rng.randint(100,899)}-{rng.randint(10,99)}-{rng.randint(1000,9999)} shipped today.")

    add(N_NEAR_MISS_PER_TYPE, "health",
        "ID+date shape resembles MRN+DOB but is an appointment slot, explicitly no patient data",
        lambda: (f"Appointment slot APT-{rng.randint(100000,999999)} confirmed for "
                 f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/2026 - no patient data included."))

    add(N_NEAR_MISS_PER_TYPE, "financial",
        "IBAN-shaped but explicitly a labelled sandbox/test value",
        lambda: "Test IBAN (sandbox, non-real): DE00000000000000000000, do not use for live transfers.")

    add(N_NEAR_MISS_PER_TYPE, "credential",
        "hex string the same length/shape as an API key, but a commit hash",
        lambda: f"Build artifact hash: {rng.getrandbits(128):032x} (commit SHA, not a credential)")

    add(N_NEAR_MISS_PER_TYPE, "credential",
        "mentions 'password' but contains no actual secret value",
        lambda: "Please remember to update your password after reading the new security policy document.")

    return entries


# ---------------------------------------------------------------------------
# Benign background - ordinary non-sensitive text, same size distribution as
# real messages. This is the bulk of the corpus, for a realistic
# false-positive-rate denominator (point 1/5 of the follow-up request).
# ---------------------------------------------------------------------------

def build_benign_background(rng, n):
    from textgen import TEXT_LENGTH_WORDS
    entries = []
    sources = list(["chatgpt", "claude_web", "claude_code", "gemini"])
    for _ in range(n):
        source = rng.choice(sources)
        role = rng.choice(["user", "assistant"])
        min_w, max_w = TEXT_LENGTH_WORDS[source][role]
        max_w = min(max_w, BACKGROUND_MAX_WORDS)  # cap so 15k+ entries build quickly
        min_w = min(min_w, max_w)
        text = gen_text(rng, min_w, max_w)
        entries.append({
            "label": "benign_background",
            "source": source,
            "role": role,
            "text": text,
        })
    return entries


# ---------------------------------------------------------------------------
# Assembly, hashing, split
# ---------------------------------------------------------------------------

def _finalize(entries):
    for i, e in enumerate(entries):
        e["id"] = f"corpus-{i:06d}"
        e["text_hash"] = hashlib.sha256(e["text"].encode("utf-8")).hexdigest()
    return entries


def _assign_split(entries, dev_fraction, split_seed):
    """Deterministic dev/holdout assignment, independent of generation RNG
    draw order (its own shuffle with a fixed seed), stable across reruns."""
    split_rng = random.Random(split_seed)
    order = list(range(len(entries)))
    split_rng.shuffle(order)
    n_dev = round(len(entries) * dev_fraction)
    dev_indices = set(order[:n_dev])
    for i, e in enumerate(entries):
        e["split"] = "dev" if i in dev_indices else "holdout"
    return entries


def build_full_corpus():
    """Returns a flat list of all entries (true_violation, benign_near_miss,
    benign_background), each tagged with split='dev'|'holdout'."""
    rng = random.Random(CORPUS_SEED)

    true_violations = build_true_violations(rng)
    near_misses = build_benign_near_misses(rng)
    # Split each pool independently so the dev fraction holds exactly within
    # true_violations and within near_misses (not just on the combined set).
    _assign_split(true_violations, DEV_FRACTION, SPLIT_SEED)
    _assign_split(near_misses, DEV_FRACTION, SPLIT_SEED + 1)

    background_dev = build_benign_background(rng, BACKGROUND_DEV_COUNT)
    for e in background_dev:
        e["split"] = "dev"
    background_holdout = build_benign_background(rng, BACKGROUND_HOLDOUT_COUNT)
    for e in background_holdout:
        e["split"] = "holdout"

    all_entries = true_violations + near_misses + background_dev + background_holdout
    return _finalize(all_entries)


def dev_true_violation_pool(entries, category):
    return [e for e in entries if e["split"] == "dev"
            and e["label"] == "true_violation" and e["category"] == category]
