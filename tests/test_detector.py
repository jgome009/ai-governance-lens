"""
Phase 3 integrity guards: the detector and the loader must never read
`planted_violations` - that table is a test-only ground-truth oracle
(RULES.md / LIMITATIONS.md), and a real deployment would not have it.

detector.py is checked for zero references at all (it has no DB access
by design - this is a regression guard on an architectural guarantee,
not the only thing enforcing it). loader.py legitimately CREATEs the
table (schema ownership) but must never SELECT/INSERT/UPDATE/DELETE it.
"""
import re
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

def test_detector_never_mentions_planted_violations():
    source = (SRC_DIR / "detector.py").read_text(encoding="utf-8")
    assert "planted_violations" not in source, (
        "detector.py references planted_violations - it has no DB access at all, "
        "so it should never need to name that table"
    )


def test_loader_only_creates_planted_violations_schema_never_reads_it():
    source = (SRC_DIR / "loader.py").read_text(encoding="utf-8")
    bad_statement = re.compile(
        r"\b(SELECT|INSERT|UPDATE|DELETE)\b[^;\"]{0,300}planted_violations", re.IGNORECASE | re.DOTALL
    )
    match = bad_statement.search(source)
    assert match is None, f"loader.py has a read/write statement against planted_violations: {match.group(0)[:120]!r}"
    # Sanity: the table name should still appear exactly where expected -
    # its own CREATE TABLE line - so this test isn't vacuously passing
    # because the table was renamed out from under it.
    assert "CREATE TABLE planted_violations" in source
