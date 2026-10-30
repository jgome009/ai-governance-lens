"""
Dev-only tuning check for the detector. Reads ONLY corpus_dev.jsonl -
never holdout, never stress. Run this as many times as needed while
iterating on detector.py's regexes; evaluate_detector.py (holdout+stress)
is meant to run exactly once, at the end, after this loop is done.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detector
import eval_metrics

DEV_PATH = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "corpus_dev.jsonl"


def load_entries(path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def detect_fn(text):
    return [category for category, _ in detector.detect_categories(text)]


def main():
    entries = load_entries(DEV_PATH)
    results = eval_metrics.evaluate(entries, detect_fn)
    print(eval_metrics.format_report(results, "DEV (tuning only)"))


if __name__ == "__main__":
    sys.exit(main())
