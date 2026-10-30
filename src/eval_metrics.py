"""
Shared evaluation metrics for the detector - used both by the dev-only
tuning loop (tune_on_dev.py) and the one-time final evaluation
(evaluate_detector.py). Keeping the metric math in one place means "dev
numbers" and "final numbers" are computed identically and are
comparable.
"""
import math

CATEGORIES = ["pii", "health", "financial", "credential"]


def wilson_ci(successes: int, n: int, z: float = 1.96):
    """Wilson score interval for a binomial proportion - better-behaved
    than the normal approximation at small n or p near 0/1, both of which
    happen here (small holdout per-category counts, near-miss counts)."""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half_width = (z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
    return (p, max(0.0, center - half_width), min(1.0, center + half_width))


def evaluate(entries, detect_fn):
    """entries: corpus entries (dicts with 'label' and either 'category'
    (true_violation) or 'near_miss_for' (benign_near_miss) or nothing
    (benign_background)). detect_fn: text -> list of category strings
    detected (category-level, not rule-level - see detector.py's
    detect_categories).

    Returns a dict per category with recall / precision / background_fpr
    / near_miss_fpr, each as (point_estimate, ci_low, ci_high, n)."""
    results = {}
    for category in CATEGORIES:
        true_violations = [e for e in entries if e["label"] == "true_violation" and e["category"] == category]
        near_misses = [e for e in entries if e["label"] == "benign_near_miss" and e["near_miss_for"] == category]
        background = [e for e in entries if e["label"] == "benign_background"]

        tp = sum(1 for e in true_violations if category in detect_fn(e["text"]))
        fp_from_near_miss = sum(1 for e in near_misses if category in detect_fn(e["text"]))
        fp_from_background = sum(1 for e in background if category in detect_fn(e["text"]))

        recall = wilson_ci(tp, len(true_violations)) if true_violations else (float("nan"),) * 3
        total_flagged_as_category = tp + fp_from_near_miss + fp_from_background
        precision = wilson_ci(tp, total_flagged_as_category) if total_flagged_as_category else (float("nan"),) * 3
        near_miss_fpr = wilson_ci(fp_from_near_miss, len(near_misses)) if near_misses else (float("nan"),) * 3
        background_fpr = wilson_ci(fp_from_background, len(background)) if background else (float("nan"),) * 3

        results[category] = {
            "recall": (*recall, len(true_violations)),
            "precision": (*precision, total_flagged_as_category),
            "near_miss_fpr": (*near_miss_fpr, len(near_misses)),
            "background_fpr": (*background_fpr, len(background)),
            "tp": tp, "fp_near_miss": fp_from_near_miss, "fp_background": fp_from_background,
        }
    return results


def results_to_json(results):
    """Convert evaluate()'s per-category metric tuples (point, lo, hi, n)
    into plain JSON-serializable dicts - used by build_dashboard.py."""
    out = {}
    for category, m in results.items():
        out[category] = {}
        for metric_name in ("recall", "precision", "near_miss_fpr", "background_fpr"):
            p, lo, hi, n = m[metric_name]
            out[category][metric_name] = {
                "point": None if p != p else round(p, 4),  # p != p checks NaN
                "ci_low": None if lo != lo else round(lo, 4),
                "ci_high": None if hi != hi else round(hi, 4),
                "n": n,
            }
    return out


def format_report(results, title):
    lines = [f"=== {title} ==="]
    for category, m in results.items():
        r_p, r_lo, r_hi, r_n = m["recall"]
        p_p, p_lo, p_hi, p_n = m["precision"]
        nm_p, nm_lo, nm_hi, nm_n = m["near_miss_fpr"]
        bg_p, bg_lo, bg_hi, bg_n = m["background_fpr"]
        lines.append(
            f"{category:10s} "
            f"recall={r_p:.1%} [{r_lo:.1%},{r_hi:.1%}] n={r_n}  "
            f"precision={p_p:.1%} [{p_lo:.1%},{p_hi:.1%}] n={p_n}  "
            f"near_miss_fpr={nm_p:.1%} [{nm_lo:.1%},{nm_hi:.1%}] n={nm_n}  "
            f"background_fpr={bg_p:.2%} [{bg_lo:.2%},{bg_hi:.2%}] n={bg_n}"
        )
    return "\n".join(lines)
