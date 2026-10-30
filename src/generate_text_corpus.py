"""
Writes the phase-3 test corpus (see corpus.py for what it contains and
why) to two files, split by corpus.py's deterministic dev/holdout
assignment:

  data/fixtures/corpus_dev.jsonl      - used during phase-3 development
  data/fixtures/corpus_holdout.jsonl  - UNTOUCHED until final evaluation

generate_synthetic_data.py also calls corpus.build_full_corpus() directly
(not by reading these files) to source the exact text it injects for
Rule 1/2 planted violations - both call sites use the same fixed seeds,
so the dev file written here and the entries the generator drew from are
identical. This script exists so the corpus is inspectable/usable on disk
for phase-3 work, independent of running the main generator.
"""
import json
import sys
from pathlib import Path

import corpus

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def write_jsonl(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    entries = corpus.build_full_corpus()

    dev = [e for e in entries if e["split"] == "dev"]
    holdout = [e for e in entries if e["split"] == "holdout"]

    write_jsonl(FIXTURES_DIR / "corpus_dev.jsonl", dev)
    write_jsonl(FIXTURES_DIR / "corpus_holdout.jsonl", holdout)

    def summarize(label, pool):
        from collections import Counter
        c = Counter(e["label"] for e in pool)
        print(f"{label}: {len(pool)} entries -> {dict(c)}")

    summarize("dev", dev)
    summarize("holdout", holdout)
    print(f"Wrote {FIXTURES_DIR / 'corpus_dev.jsonl'} and {FIXTURES_DIR / 'corpus_holdout.jsonl'}")


if __name__ == "__main__":
    sys.exit(main())
