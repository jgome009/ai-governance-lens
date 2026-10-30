"""
Phase 3: rule-based detector for Rule 1 (pii/health/financial in
unsanctioned tools) and Rule 2 (credential, any tool). See RULES.md for
the rule definitions and the "list of rules" given to the user before
this module was written.

Deliberately has ZERO database or file access: every function here takes
a plain string and returns matches. It cannot read the test-only
ground-truth table (see RULES.md "Corpus linkage" / LIMITATIONS.md)
because it has no way to read anything - that's the strongest possible
form of that constraint (tests/test_detector.py also checks this by
source inspection, as a regression guard, but the real guarantee is
architectural).

Patterns are shape-based (digit groupings, literal prefixes + an
assignment operator), not keyword-based: the synthetic background text
corpus freely uses words like "token", "patient", "config", "test" as
ordinary vocabulary (see textgen.py's VOCAB), so a pattern that fired on
a bare keyword would flood false positives. Every pattern here requires
a structural marker (digits in a specific shape, or a keyword FOLLOWED BY
an operator/specific format) that ordinary word-salad text cannot
produce.

Developed and tuned ONLY against data/fixtures/corpus_dev.jsonl - see
evaluate_detector.py for the one-time holdout/stress evaluation this
tuning is NOT allowed to see.
"""
import re

# Placeholder/test values a real DLP rule would routinely exclude (not
# overfit to this corpus's wording - "dummy"/"sample"/"example" etc. are
# standard test-data markers in any real codebase or support ticket).
_PLACEHOLDER_CONTEXT = re.compile(r"\b(test|sandbox|example|dummy|placeholder|sample|fake)\b", re.IGNORECASE)


def _not_placeholder_context(match: re.Match, text: str) -> bool:
    window_start = max(0, match.start() - 40)
    if _PLACEHOLDER_CONTEXT.search(text[window_start:match.start()]):
        return False
    digits = re.sub(r"\D", "", match.group(0))
    if len(set(digits)) <= 1:  # all-same-digit, e.g. a zeroed-out placeholder
        return False
    return True


# (category, pattern_name, compiled_regex, optional validator(match, text) -> bool)
RULES = [
    # --- pii (Rule 1 category) ---
    # Requires the literal "SSN" label nearby - a bare 3-2-4 digit group
    # alone is indistinguishable from an order number, phone number, etc.
    # (see the pii benign-near-miss in corpus.py); real SSN detectors
    # routinely gate on this kind of context label for the same reason.
    ("pii", "ssn_like", re.compile(r"\bSSN\b\s*\d{3}-\d{2}-\d{4}\b", re.IGNORECASE), None),
    ("pii", "customer_account_number", re.compile(r"\bACC-\d{6,8}\b", re.IGNORECASE), None),
    ("pii", "drivers_license_number", re.compile(r"\bD\d{8}\b"), None),
    ("pii", "passport_number", re.compile(r"\bP\d{8}\b"), None),

    # --- health (Rule 1 category) ---
    ("health", "mrn", re.compile(r"\bMRN-\d{6}\b", re.IGNORECASE), None),
    ("health", "patient_name_with_dob",
     re.compile(r"\bPatient\b[^.\n]{0,60}?\bDOB\b[^.\n]{0,20}?\d{2}/\d{2}/\d{4}", re.IGNORECASE), None),
    ("health", "insurance_member_id", re.compile(r"\bINS-\d{7}\b", re.IGNORECASE), None),
    ("health", "patient_reference_code", re.compile(r"\bPT-\d{5}\b", re.IGNORECASE), None),

    # --- financial (Rule 1 category) ---
    # IBAN: reject placeholder/test context and all-zero (or any
    # all-same-digit) account numbers - a provably fake value, not a real
    # account, regardless of what label surrounds it.
    ("financial", "iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), _not_placeholder_context),
    ("financial", "credit_card_number",
     re.compile(r"\b(?:4\d{3}|5[1-5]\d{2})[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"), None),
    ("financial", "bank_account_and_routing",
     re.compile(r"\bAccount\s+\d{6,12}\b[^.\n]{0,30}?\bRouting\s+\d{9}\b", re.IGNORECASE), None),

    # --- credential (Rule 2 category) ---
    ("credential", "api_key_generic", re.compile(r"\bapi[_-]?key\s*[=:]\s*\S+", re.IGNORECASE), None),
    ("credential", "aws_secret_key", re.compile(r"\bAWS_SECRET_ACCESS_KEY\s*=\s*\S+", re.IGNORECASE), None),
    ("credential", "password_assignment", re.compile(r"\bpassword\s*[:=]\s*\S+", re.IGNORECASE), None),
    ("credential", "pem_private_key_header", re.compile(r"-----BEGIN (RSA )?PRIVATE KEY-----"), None),
    ("credential", "bearer_oauth_token", re.compile(r"\bBearer\s+\S+|\bya29\.\S+"), None),
    ("credential", "generic_pat_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), None),
]

# Rule 1 applies only on unsanctioned tools; Rule 2 applies on any tool.
# See RULES.md.
RULE1_CATEGORIES = {"pii", "health", "financial"}
RULE2_CATEGORIES = {"credential"}


def detect_categories(text: str):
    """All (category, pattern_name) matches found in text, deduped per
    distinct pattern (a pattern matching 3 times in one message still
    yields one entry - we flag 'this message contains X', not a count)."""
    found = []
    seen = set()
    for category, pattern_name, regex, validator in RULES:
        if (category, pattern_name) in seen:
            continue
        for match in regex.finditer(text):
            if validator is None or validator(match, text):
                found.append((category, pattern_name))
                seen.add((category, pattern_name))
                break
    return found


def detect_flags(text: str, sanctioned: bool):
    """(rule_id, category, pattern_name) tuples to write to `flags`,
    applying the Rule 1 (unsanctioned-only) / Rule 2 (any tool) gate."""
    flags = []
    for category, pattern_name in detect_categories(text):
        if category in RULE1_CATEGORIES and not sanctioned:
            flags.append((1, category, pattern_name))
        elif category in RULE2_CATEGORIES:
            flags.append((2, category, pattern_name))
    return flags
