"""
Stress corpus: out-of-template cases the detector was NEVER tuned
against - kept entirely separate from corpus.py's dev/holdout split.
Not read by tune_on_dev.py, not read by generate_synthetic_data.py.
Read exactly once, by evaluate_detector.py, at final evaluation time.

Four scenario types, deliberately chosen because they're realistic ways
real sensitive data deviates from a template: German-language phrasing,
obfuscated/spaced values, key formats our rules don't name, and
sensitive values embedded in code/log syntax.

Each entry is either a true_violation (category + scenario) or a
benign_background-style distractor (German chat, benign code/log with no
secret) - the benign entries measure whether these same OOD conditions
also cause unexpected false positives, not just recall loss.
"""
import hashlib
import json
import sys
from pathlib import Path

STRESS_PATH = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "stress_corpus.jsonl"


def _entry(label, scenario, text, category=None, note=""):
    return {"label": label, "scenario": scenario, "category": category, "text": text, "note": note}


TRUE_VIOLATIONS = [
    # --- German-language phrasing ---
    _entry("true_violation", "german", "Kundennummer SSN 525-26-1466, bitte das Konto aktualisieren.",
           "pii", "keeps English 'SSN' label in a German sentence"),
    _entry("true_violation", "german", "Kundennummer: 525-26-1466, bitte Konto aktualisieren.",
           "pii", "no 'SSN' label at all - pure German phrasing"),
    _entry("true_violation", "german",
           "Patientin Maria Schmidt, Geburtsdatum 12.05.1978, bitte Laborergebnisse zusammenfassen.",
           "health", "German 'Patientin'/'Geburtsdatum', dot-separated date"),
    _entry("true_violation", "german",
           "Patient: Maria Schmidt, DOB: 12/05/1978 - bitte Laborergebnisse zusammenfassen.",
           "health", "English clinical shorthand kept inside German sentence"),
    _entry("true_violation", "german",
           "Kunde hat folgende IBAN angegeben: DE44500105517572649228 fuer die Rueckerstattung.",
           "financial", "IBAN digits are language-independent"),
    _entry("true_violation", "german",
           "Bitte das Passwort temporaer setzen: password: Northwind!2026 - danach bitte aendern.",
           "credential", "English 'password:' label kept inside German sentence"),
    _entry("true_violation", "german",
           "Bitte das Passwort temporaer setzen: Passwort: Northwind!2026 - danach bitte aendern.",
           "credential", "German 'Passwort' label only, no English keyword"),

    # --- obfuscated / spaced values ---
    _entry("true_violation", "obfuscated", "SSN 5 2 5 - 2 6 - 1 4 6 6, please update the account.",
           "pii", "digit-spaced SSN"),
    _entry("true_violation", "obfuscated", "MRN - 1 2 3 4 5 6, requesting a lab summary.",
           "health", "digit-spaced MRN"),
    _entry("true_violation", "obfuscated", "IBAN: DE44 5001 0551 7572 6492 28 for the transfer.",
           "financial", "IBAN in the common real-world 4-digit-grouped display format"),
    _entry("true_violation", "obfuscated", "a p i _ k e y = sk-live-abcdef1234567890abcdef1234567890",
           "credential", "letter-spaced keyword evades the literal 'api_key' match"),
    _entry("true_violation", "obfuscated", "AWS_SECRET\n_ACCESS_KEY=abcdef0123456789abcdef0123456789abcdef01",
           "credential", "keyword split across a line break"),

    # --- other key/identifier formats not in our rule set ---
    _entry("true_violation", "other_format", "Passport Number: AB1234567", "pii",
           "two-letter+digit passport format, not our 'P'+8digits shape"),
    _entry("true_violation", "other_format", "DL# 987654321, submitted for verification.", "pii",
           "DL# prefix instead of literal 'D'+8digits"),
    _entry("true_violation", "other_format", "Chart ID: CH-5521, follow-up needed.", "health",
           "different patient-chart ID scheme, not MRN/INS/PT"),
    _entry("true_violation", "other_format", "Routing/ABA 021000021, Acct 000123456789 for the wire.",
           "financial", "'Acct' abbreviation instead of literal 'Account'"),
    # The two literal example values below are deliberately NOT a contiguous
    # alnum run of plausible length: GitHub push-protection pattern-matches
    # on shape alone (it can't know these are deterministic stress-test
    # fixtures, not real keys) and blocked an earlier push over the
    # unbroken versions. The "FAKE-" break still demonstrates the same
    # stress scenario - a key format detector.py has no dedicated rule for -
    # without tripping a secret scanner. See LIMITATIONS.md.
    _entry("true_violation", "other_format", "sk_live_FAKE-51Hh2eKJ3doM-NOT-A-REAL-KEY-0123456789", "credential",
           "Stripe-style secret key prefix, no dedicated rule"),
    _entry("true_violation", "other_format", "xoxb-FAKE-1234567890-NOT-A-REAL-TOKEN-abcdefgh", "credential",
           "Slack bot token format, no dedicated rule"),
    _entry("true_violation", "other_format",
           "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
           "credential", "bare JWT, no dedicated rule"),
    _entry("true_violation", "other_format",
           "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXkAAAA\n-----END OPENSSH PRIVATE KEY-----",
           "credential", "OpenSSH key header, our rule only names the RSA PEM header"),

    # --- sensitive data inside code/log snippets ---
    _entry("true_violation", "code_log",
           '# config.py\nAPI_KEY = "sk-live-abcdef0123456789abcdef0123456789"', "credential",
           "credential inside a Python assignment"),
    _entry("true_violation", "code_log",
           "2026-09-14 10:22:01 ERROR auth failed for user, password=Northwind!2026 rejected", "credential",
           "credential inside a log line"),
    _entry("true_violation", "code_log",
           "export AWS_SECRET_ACCESS_KEY=abcdef0123456789abcdef0123456789abcdef01  # .env snippet",
           "credential", "credential inside a shell/.env snippet"),
    _entry("true_violation", "code_log",
           "2026-09-01,u0012,support_ticket,customer_ssn=525-26-1466,status=closed", "pii",
           "SSN inside a snake_case log field name (customer_ssn=...), not a standalone 'SSN' label"),
]

BENIGN = [
    _entry("benign_background", "german", "Koennten wir das Projektupdate auf naechste Woche verschieben?"),
    _entry("benign_background", "german", "Das Dashboard zeigt die Roadmap fuer das naechste Quartal."),
    _entry("benign_background", "german", "Bitte das Backlog vor dem Review nochmal durchgehen."),
    _entry("benign_background", "code_log", "api_endpoint = 'https://example.com/v1/status'  # no secret here"),
    _entry("benign_background", "code_log", "2026-09-14 10:22:01 INFO deployment pipeline finished successfully"),
    _entry("benign_background", "code_log", "commit_hash = '9e112e4af869d98e1234567890abcdef12345678'"),
    _entry("benign_background", "obfuscated", "The s e r v e r restarted after the config reload."),
    _entry("benign_background", "other_format", "Reference number REF-2026-00451 for the shipment."),
]


def build_stress_corpus():
    entries = TRUE_VIOLATIONS + BENIGN
    for i, e in enumerate(entries):
        e["id"] = f"stress-{i:04d}"
        e["text_hash"] = hashlib.sha256(e["text"].encode("utf-8")).hexdigest()
    return entries


def main():
    entries = build_stress_corpus()
    STRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STRESS_PATH.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"Wrote {len(entries)} entries to {STRESS_PATH} "
          f"({len(TRUE_VIOLATIONS)} true_violation, {len(BENIGN)} benign)")


if __name__ == "__main__":
    sys.exit(main())
