"""
Phase 4: builds dashboard.html - a single, self-contained, offline HTML
file - from northwind.db + EVALUATION.json. No CDN, no external fonts,
no network calls of any kind; everything (CSS, JS, chart rendering,
data) is inlined. tests/test_dashboard.py checks this by regex-scanning
the output for external URLs.

Reads the database ONLY through kpi_queries.py - the same production
query layer phase 2 built, which never reads planted_violations (see
tests/test_kpis.py). This script adds no new SQL of its own.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kpi_queries

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "synthetic" / "northwind.db"
EVALUATION_JSON_PATH = Path(__file__).resolve().parent.parent / "EVALUATION.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "dashboard.html"


def gather_data(conn: sqlite3.Connection) -> dict:
    tool_inventory = [
        {"source": src, "sanctioned": bool(sanctioned), "active_users": users,
         "models_used": models, "messages": messages}
        for src, sanctioned, users, models, messages in kpi_queries.kpi1_tool_inventory(conn)
    ]

    pct_unsanctioned, unsanctioned_users, total_users = kpi_queries.kpi2_shadow_ai_rate(conn)
    shadow_ai = {"pct_messages_unsanctioned": pct_unsanctioned,
                 "unsanctioned_users": unsanctioned_users, "total_active_users": total_users}

    weekly_adoption = [
        {"week": week, "wau": wau, "messages": messages, "messages_per_user": mpu}
        for week, wau, messages, mpu in kpi_queries.kpi3_weekly_adoption(conn)
    ]
    total_users_c, total_messages_c, top10_users, top10_messages = kpi_queries.kpi3_volume_concentration(conn)
    concentration = {
        "total_active_users": total_users_c, "total_messages": total_messages_c,
        "top10_user_count": top10_users, "top10_messages": top10_messages,
        "top10_share_pct": round(100.0 * top10_messages / total_messages_c, 1) if total_messages_c else None,
        "top10_user_share_pct": round(100.0 * top10_users / total_users_c, 1) if total_users_c else None,
    }

    sensitive_exposure = [
        {"sanctioned": bool(sanctioned), "total_messages": total, "flagged_messages": flagged,
         "flagged_per_1000": per_1000}
        for sanctioned, total, flagged, per_1000 in kpi_queries.kpi4_sensitive_exposure(conn)
    ]

    message_pattern_trend = [
        {"rule_id": rule_id, "week": week, "n": n}
        for rule_id, week, n in kpi_queries.kpi5_message_pattern_trend(conn)
    ]
    aggregate_monthly_trend = [
        {"month": month, "users_over_threshold": n}
        for month, n in kpi_queries.kpi5_aggregate_monthly_trend(conn)
    ]

    spend_rows = kpi_queries.kpi6_monthly_spend_per_user(conn)
    spend_values = sorted(s for _, _, s in spend_rows)
    n = len(spend_values)

    def pct(p):
        return round(spend_values[min(n - 1, int(n * p))], 4) if n else None

    cost = {
        "n_user_months": n,
        "percentiles": {"p50": pct(0.50), "p75": pct(0.75), "p90": pct(0.90),
                         "p95": pct(0.95), "p99": pct(0.99),
                         "max": round(spend_values[-1], 4) if n else None,
                         "min": round(spend_values[0], 4) if n else None},
        "top_spenders": [
            {"user_id": u, "month": m, "spend_usd": round(s, 2)}
            for u, m, s in spend_rows[:10]
        ],
        "threshold": kpi_queries.RULE3_MONTHLY_SPEND_THRESHOLD_USD,
        "price_by_model": kpi_queries.PRICE_BY_MODEL,
    }

    return {
        "tool_inventory": tool_inventory,
        "shadow_ai": shadow_ai,
        "weekly_adoption": weekly_adoption,
        "concentration": concentration,
        "sensitive_exposure": sensitive_exposure,
        "message_pattern_trend": message_pattern_trend,
        "aggregate_monthly_trend": aggregate_monthly_trend,
        "cost": cost,
    }


def load_evaluation() -> dict:
    return json.loads(EVALUATION_JSON_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Every tile's tooltip: definition, formula, data source, known bias.
# Four fixed fields per CLAUDE.md section 5 ("For each KPI the README
# states: definition, formula, data source, known bias") - inlined here
# since the README (phase 6) hasn't been written yet.
# ---------------------------------------------------------------------------
KPI_DOCS = {
    "shadow_ai_rate": {
        "title": "Shadow AI rate",
        "definition": "Share of messages (and of active users) sent to an unsanctioned AI tool.",
        "formula": "100 x unsanctioned_messages / total_messages. unsanctioned_users = distinct users with >=1 unsanctioned message.",
        "data_source": "messages JOIN tools (sanctioned flag)",
        "known_bias": "Counts messages, not conversations or intent - one off-policy message counts the same as a sustained pattern.",
    },
    "sensitive_exposure": {
        "title": "Sensitive-data exposure",
        "definition": "Flagged messages per 1,000, split sanctioned vs unsanctioned tool.",
        "formula": "1000 x flagged_messages / total_messages, grouped by tools.sanctioned",
        "data_source": "messages JOIN tools LEFT JOIN flags",
        "known_bias": "LOWER BOUND, HEURISTIC. flags come from a shape-based regex detector measured at 100% recall on same-distribution holdout but only 16.7-36.4% recall on out-of-template stress data (German phrasing, obfuscation, unfamiliar formats). Real exposure is very likely higher than shown here. See the Detector reliability panel.",
    },
    "violations_message_pattern": {
        "title": "Policy violations - Rule 1/2 (by week)",
        "definition": "Distinct messages flagged for Rule 1 (PII/health/financial identifiers in an unsanctioned tool) or Rule 2 (credentials, any tool), by week.",
        "formula": "COUNT(DISTINCT message_id) per rule_id, grouped by week derived from message timestamp",
        "data_source": "flags JOIN messages",
        "known_bias": "LOWER BOUND, HEURISTIC - same detector-recall caveat as sensitive-data exposure. A quiet week may mean low usage, not zero actual violations.",
    },
    "violations_rule3": {
        "title": "Policy violations - Rule 3 (spend reviews, by month)",
        "definition": "Count of user-months whose estimated AI spend crossed the monthly review threshold.",
        "formula": "COUNT(*) WHERE spend_usd >= threshold, grouped by month; spend_usd = SUM(token_count/1000 x model price) per user per month",
        "data_source": "messages (token_count, model) + a pricing table; threshold from RULES.md",
        "known_bias": "Exact arithmetic, not pattern-matching - no detector-recall caveat. But depends on placeholder/unverified model pricing (see cost tile) and an invented threshold ($7.50/month, a heavy-use-persona budget calculation, not negotiated policy). 'Crossed the threshold' does not mean misconduct - legitimately heavy users are included by design.",
    },
    "adoption_wau": {
        "title": "Weekly active users",
        "definition": "Distinct users sending at least one message in a given week.",
        "formula": "COUNT(DISTINCT user_id), grouped by week_number = floor((message_date - anchor_monday) / 7)",
        "data_source": "messages",
        "known_bias": "Message-sending is the only activity signal available - a user who reads without sending is invisible. Reflects this generator's adoption-ramp model, not real Northwind usage (synthetic data).",
    },
    "concentration": {
        "title": "Volume concentration",
        "definition": "Share of total message volume produced by the most active 10% of active users.",
        "formula": "SUM(messages) for the top decile by per-user message count / SUM(messages) for all active users",
        "data_source": "messages",
        "known_bias": "Ranks by message COUNT, not token volume or business value - a user sending many short messages outranks one sending few long, high-value ones.",
    },
    "tool_inventory": {
        "title": "Tool inventory",
        "definition": "Distinct sources/models in use and active users per tool.",
        "formula": "COUNT(DISTINCT user_id), COUNT(DISTINCT model), COUNT(*), grouped by source",
        "data_source": "messages JOIN tools",
        "known_bias": "Only sees usage that reaches this pipeline. Real shadow-AI tools with no export at all are invisible by definition - this inventory cannot prove completeness.",
    },
    "task_mix": {
        "title": "Task mix (KPI 7) - not implemented",
        "definition": "Not implemented.",
        "formula": "n/a",
        "data_source": "n/a",
        "known_bias": "Needs a keyword-based message-content classifier (coding/writing/analysis/other) that does not exist yet. Implementing it now would mean fabricating categorization logic ahead of any measured accuracy - see LIMITATIONS.md.",
    },
    "estimated_cost": {
        "title": "Estimated monthly spend",
        "definition": "Estimated USD spend per user per month: tokens x public list price per model.",
        "formula": "SUM(token_count)/1000 x price_per_1k, grouped by user + month",
        "data_source": "messages (token_count, model) + a hardcoded pricing table in kpi_queries.py",
        "known_bias": "PLACEHOLDER PRICING - illustrative, not verified against current public list prices (see LIMITATIONS.md). token_count is itself a heuristic estimate (chars/4), always flagged tokens_estimated. Treat as directional, not exact billing.",
    },
}

GENERAL_DOCS = {
    "detector_reliability": (
        "Detector reliability measured on three distinct samples: Holdout (same template distribution as "
        "dev, held out during tuning), Stress (deliberately out-of-template: German phrasing, obfuscated "
        "values, unfamiliar key formats, code/log context), and hard near-miss (benign strings structurally "
        "similar to a real match, from the holdout split). Database end-to-end numbers elsewhere on this "
        "dashboard are a demo on dev-derived data, not an unbiased measurement - see EVALUATION.md."
    ),
}


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Governance Lens - Northwind Medical GmbH</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  --page: #f9f9f7; --surface: #fcfcfb; --text: #0b0b0b; --text2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series1: #2a78d6; --series2: #eb6834;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  --good-text: #006300;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0d0d0d; --surface: #1a1a19; --text: #ffffff; --text2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series1: #3987e5; --series2: #d95926;
    --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #e66767;
    --good-text: #0ca30c;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--text);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 14px; line-height: 1.5;
}
header { padding: 20px 24px 0; }
h1 { font-size: 18px; margin: 0 0 4px; }
.subtitle { color: var(--text2); font-size: 13px; margin: 0 0 16px; }
nav.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); padding: 0 24px; }
nav.tabs button {
  background: none; border: none; padding: 10px 16px; font-size: 14px; font-weight: 600;
  color: var(--text2); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px;
}
nav.tabs button.active { color: var(--text); border-bottom-color: var(--series1); }
main { padding: 20px 24px 60px; max-width: 1200px; margin: 0 auto; }
.view { display: none; }
.view.active { display: block; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 14px; }
.grid.wide { grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }
.tile {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px; position: relative;
}
.tile-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
.tile-title { font-size: 12px; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: 0.02em; }
.info-btn {
  width: 18px; height: 18px; border-radius: 50%; border: 1px solid var(--axis); background: none;
  color: var(--muted); font-size: 11px; line-height: 1; cursor: pointer; flex-shrink: 0;
}
.stat-value { font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }
.stat-sub { font-size: 12px; color: var(--text2); margin-top: 2px; }
.popover {
  display: none; position: absolute; top: 28px; right: 0; z-index: 20; width: 280px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px 14px; box-shadow: 0 4px 16px rgba(0,0,0,0.18); font-size: 12px;
}
.popover.open { display: block; }
.popover dt { font-weight: 600; color: var(--text2); margin-top: 8px; font-size: 11px; text-transform: uppercase; }
.popover dt:first-child { margin-top: 0; }
.popover dd { margin: 2px 0 0; }
.badge {
  display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 700;
  padding: 2px 8px; border-radius: 999px; margin-bottom: 8px;
}
.badge.heuristic { background: rgba(250,178,25,0.18); color: #9a6a00; }
@media (prefers-color-scheme: dark) { .badge.heuristic { color: var(--warning); } }
.banner {
  background: rgba(250,178,25,0.14); border: 1px solid var(--warning); border-radius: 8px;
  padding: 8px 12px; font-size: 12px; font-weight: 600; margin-bottom: 14px; display: flex; gap: 6px; align-items: baseline;
}
table { width: 100%; border-collapse: collapse; font-size: 12px; }
table th, table td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }
table th { color: var(--text2); font-weight: 600; font-size: 11px; text-transform: uppercase; }
.chart-wrap { position: relative; }
.chart-table-toggle { font-size: 11px; color: var(--text2); background: none; border: none; cursor: pointer; text-decoration: underline; padding: 0; margin-top: 6px; }
.legend { display: flex; gap: 14px; font-size: 12px; margin-bottom: 6px; }
.legend-item { display: flex; align-items: center; gap: 5px; }
.legend-swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
#chart-tip {
  position: fixed; display: none; background: var(--text); color: var(--page); font-size: 11px;
  padding: 4px 8px; border-radius: 4px; pointer-events: none; z-index: 50; white-space: nowrap;
}
.panel-note { font-size: 12px; color: var(--text2); margin-top: 10px; }
.panel-note b { color: var(--text); }
footer { padding: 16px 24px 40px; color: var(--muted); font-size: 11px; max-width: 1200px; margin: 0 auto; }
.na-card { color: var(--muted); font-size: 13px; }
</style>
</head>
<body>
<header>
  <h1>AI Governance Lens - Northwind Medical GmbH</h1>
  <p class="subtitle">Synthetic demo data (400 users, 12 weeks). Generated __GENERATED_AT__ from northwind.db.</p>
</header>
<nav class="tabs">
  <button data-view="cio" class="active">CIO - Risk</button>
  <button data-view="coo">COO - Adoption &amp; value</button>
  <button data-view="cfo">CFO - Spend</button>
</nav>
<main>

<section class="view active" id="view-cio">
  <div class="grid">
    <div class="tile" data-doc="shadow_ai_rate">
      <div class="tile-head"><span class="tile-title">Shadow AI rate</span><button class="info-btn">i</button></div>
      <div class="stat-value" id="stat-shadow-pct"></div>
      <div class="stat-sub" id="stat-shadow-sub"></div>
      <div class="popover"></div>
    </div>
    <div class="tile" data-doc="sensitive_exposure">
      <div class="tile-head"><span class="tile-title">Sensitive-data exposure</span><button class="info-btn">i</button></div>
      <span class="badge heuristic">LOWER BOUND, HEURISTIC</span>
      <div class="stat-value" id="stat-exposure-unsanctioned"></div>
      <div class="stat-sub" id="stat-exposure-sub"></div>
      <div class="stat-sub" id="stat-exposure-recall"></div>
      <div class="popover"></div>
    </div>
  </div>

  <div class="grid wide">
    <div class="tile" data-doc="violations_message_pattern">
      <div class="tile-head"><span class="tile-title">Policy violations - Rule 1/2 by week</span><button class="info-btn">i</button></div>
      <span class="badge heuristic">LOWER BOUND, HEURISTIC</span>
      <div class="legend">
        <span class="legend-item"><span class="legend-swatch" style="background:var(--series1)"></span>Rule 1 (PII/health/financial)</span>
        <span class="legend-item"><span class="legend-swatch" style="background:var(--series2)"></span>Rule 2 (credential)</span>
      </div>
      <div class="chart-wrap" id="chart-violations"></div>
      <div class="popover"></div>
    </div>
    <div class="tile" data-doc="violations_rule3">
      <div class="tile-head"><span class="tile-title">Policy violations - Rule 3 reviews by month</span><button class="info-btn">i</button></div>
      <div class="chart-wrap" id="chart-rule3"></div>
      <div class="popover"></div>
    </div>
  </div>

  <div class="tile">
    <div class="tile-head"><span class="tile-title">Detector reliability</span></div>
    <p class="panel-note" id="reliability-intro"></p>
    <div class="grid" id="reliability-grid"></div>
    <p class="panel-note"><b id="reliability-warning"></b></p>
  </div>
</section>

<section class="view" id="view-coo">
  <div class="grid">
    <div class="tile" data-doc="concentration">
      <div class="tile-head"><span class="tile-title">Volume concentration</span><button class="info-btn">i</button></div>
      <div class="stat-value" id="stat-concentration"></div>
      <div class="stat-sub" id="stat-concentration-sub"></div>
      <div class="popover"></div>
    </div>
  </div>
  <div class="grid wide">
    <div class="tile" data-doc="adoption_wau">
      <div class="tile-head"><span class="tile-title">Weekly active users (ramp)</span><button class="info-btn">i</button></div>
      <div class="chart-wrap" id="chart-wau"></div>
      <div class="popover"></div>
    </div>
    <div class="tile" data-doc="tool_inventory">
      <div class="tile-head"><span class="tile-title">Tool inventory</span><button class="info-btn">i</button></div>
      <div id="table-tool-inventory"></div>
      <div class="popover"></div>
    </div>
  </div>
  <div class="tile" data-doc="task_mix">
    <div class="tile-head"><span class="tile-title">Task mix (KPI 7)</span><button class="info-btn">i</button></div>
    <p class="na-card">Not implemented - needs a message-content classifier that does not exist yet. See LIMITATIONS.md.</p>
    <div class="popover"></div>
  </div>
</section>

<section class="view" id="view-cfo">
  <div class="banner">&#9888; Placeholder pricing - estimate, unverified against current list prices (see LIMITATIONS.md)</div>
  <div class="grid">
    <div class="tile" data-doc="estimated_cost">
      <div class="tile-head"><span class="tile-title">Estimated monthly spend (percentiles, USD)</span><button class="info-btn">i</button></div>
      <div id="stat-cost-percentiles"></div>
      <div class="popover"></div>
    </div>
  </div>
  <div class="grid wide">
    <div class="tile">
      <div class="tile-head"><span class="tile-title">Top estimated spenders (user-month)</span></div>
      <div id="table-top-spenders"></div>
    </div>
    <div class="tile" data-doc="violations_rule3">
      <div class="tile-head"><span class="tile-title">Rule 3 reviews by month</span><button class="info-btn">i</button></div>
      <div class="stat-sub" id="stat-rule3-threshold"></div>
      <div class="chart-wrap" id="chart-rule3-cfo"></div>
      <div class="popover"></div>
    </div>
  </div>
</section>

</main>
<footer>
  AI Governance Lens is a portfolio demo on synthetic data. No real conversation content is stored or
  displayed anywhere in this file. See CLAUDE.md, RULES.md, LIMITATIONS.md and EVALUATION.md in the project
  root for full methodology and caveats.
</footer>
<div id="chart-tip"></div>

<script id="dashboard-data" type="application/json">__DASHBOARD_DATA_JSON__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("dashboard-data").textContent);
  var KPI = DATA.kpi, EVAL = DATA.evaluation, DOCS = DATA.docs, GENERAL = DATA.general_docs;

  function fmtPct(x, digits) { digits = digits === undefined ? 1 : digits; return (x === null || x === undefined || isNaN(x)) ? "n/a" : (x * 100).toFixed(digits) + "%"; }
  function fmtPctAlready(x, digits) { digits = digits === undefined ? 1 : digits; return (x === null || x === undefined) ? "n/a" : x.toFixed(digits) + "%"; }
  function fmtUsd(x) { return (x === null || x === undefined) ? "n/a" : "$" + x.toFixed(2); }

  // ---- tabs ----
  var tabs = document.querySelectorAll("nav.tabs button");
  tabs.forEach(function (btn) {
    btn.addEventListener("click", function () {
      tabs.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      document.querySelectorAll(".view").forEach(function (v) { v.classList.remove("active"); });
      document.getElementById("view-" + btn.dataset.view).classList.add("active");
    });
  });

  // ---- tooltip popovers ----
  function renderPopover(doc) {
    var html = "<dl>";
    html += "<dt>Definition</dt><dd>" + doc.definition + "</dd>";
    html += "<dt>Formula</dt><dd>" + doc.formula + "</dd>";
    html += "<dt>Data source</dt><dd>" + doc.data_source + "</dd>";
    html += "<dt>Known bias</dt><dd>" + doc.known_bias + "</dd>";
    html += "</dl>";
    return html;
  }
  document.querySelectorAll(".tile[data-doc]").forEach(function (tile) {
    var doc = DOCS[tile.dataset.doc];
    var pop = tile.querySelector(".popover");
    var btn = tile.querySelector(".info-btn");
    if (!doc || !pop || !btn) return;
    pop.innerHTML = renderPopover(doc);
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      document.querySelectorAll(".popover.open").forEach(function (p) { if (p !== pop) p.classList.remove("open"); });
      pop.classList.toggle("open");
    });
  });
  document.addEventListener("click", function () {
    document.querySelectorAll(".popover.open").forEach(function (p) { p.classList.remove("open"); });
  });

  // ---- shared chart tooltip ----
  var tip = document.getElementById("chart-tip");
  function showTip(evt, text) {
    tip.textContent = text; tip.style.display = "block";
    tip.style.left = (evt.clientX + 12) + "px"; tip.style.top = (evt.clientY + 12) + "px";
  }
  function hideTip() { tip.style.display = "none"; }

  // ---- CIO: shadow AI rate ----
  document.getElementById("stat-shadow-pct").textContent = fmtPctAlready(KPI.shadow_ai.pct_messages_unsanctioned);
  document.getElementById("stat-shadow-sub").textContent =
    KPI.shadow_ai.unsanctioned_users + " / " + KPI.shadow_ai.total_active_users + " active users touch an unsanctioned tool";

  // ---- CIO: sensitive exposure ----
  (function () {
    var rows = KPI.sensitive_exposure;
    var unsanctioned = rows.filter(function (r) { return !r.sanctioned; })[0];
    var sanctioned = rows.filter(function (r) { return r.sanctioned; })[0];
    document.getElementById("stat-exposure-unsanctioned").textContent =
      (unsanctioned ? unsanctioned.flagged_per_1000.toFixed(2) : "0.00") + " / 1,000 (unsanctioned)";
    document.getElementById("stat-exposure-sub").textContent =
      (sanctioned ? sanctioned.flagged_per_1000.toFixed(2) : "0.00") + " / 1,000 (sanctioned)";
    var stress = EVAL.stress;
    var cats = ["pii", "health", "financial", "credential"];
    var lo = Math.min.apply(null, cats.map(function (c) { return stress[c].recall.point; }));
    var hi = Math.max.apply(null, cats.map(function (c) { return stress[c].recall.point; }));
    document.getElementById("stat-exposure-recall").textContent =
      "measured detector recall on out-of-template stress data: " + fmtPct(lo, 1) + "-" + fmtPct(hi, 1) + " (not holdout's 100%)";
  })();

  // ---- generic bar/line chart renderer ----
  function svgEl(tag, attrs) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function renderGroupedBarChart(containerId, categories, series, opts) {
    // series: [{label, color, values: [n per category]}]
    var container = document.getElementById(containerId);
    var w = 560, h = 200, padL = 36, padB = 24, padT = 10, padR = 10;
    var innerW = w - padL - padR, innerH = h - padT - padB;
    var maxVal = Math.max(1, Math.max.apply(null, series.reduce(function (a, s) { return a.concat(s.values); }, [])));
    var svg = svgEl("svg", { viewBox: "0 0 " + w + " " + h, width: "100%", height: h, role: "img" });
    var groupW = innerW / categories.length;
    var barW = Math.max(2, (groupW - 6) / series.length);

    // gridlines
    for (var g = 0; g <= 4; g++) {
      var y = padT + innerH - (g / 4) * innerH;
      svg.appendChild(svgEl("line", { x1: padL, x2: w - padR, y1: y, y2: y, stroke: "var(--grid)", "stroke-width": 1 }));
      var lbl = svgEl("text", { x: padL - 6, y: y + 3, "text-anchor": "end", "font-size": 9, fill: "var(--muted)" });
      lbl.textContent = Math.round(maxVal * g / 4);
      svg.appendChild(lbl);
    }

    categories.forEach(function (cat, i) {
      var gx = padL + i * groupW;
      series.forEach(function (s, si) {
        var val = s.values[i];
        var barH = (val / maxVal) * innerH;
        var x = gx + si * barW + 3;
        var y = padT + innerH - barH;
        var rect = svgEl("rect", {
          x: x, y: y, width: Math.max(1, barW - 2), height: Math.max(0, barH),
          fill: s.color, rx: 2,
        });
        rect.addEventListener("mousemove", function (e) { showTip(e, s.label + " - " + opts.catLabel(cat) + ": " + val); });
        rect.addEventListener("mouseleave", hideTip);
        svg.appendChild(rect);
      });
      if (i % opts.labelEvery === 0 || i === categories.length - 1) {
        var xl = svgEl("text", { x: gx + groupW / 2, y: h - padB + 12, "text-anchor": "middle", "font-size": 9, fill: "var(--muted)" });
        xl.textContent = opts.catLabel(cat);
        svg.appendChild(xl);
      }
    });
    svg.appendChild(svgEl("line", { x1: padL, x2: w - padR, y1: padT + innerH, y2: padT + innerH, stroke: "var(--axis)", "stroke-width": 1 }));
    container.innerHTML = "";
    container.appendChild(svg);
    addTableToggle(container, categories, series, opts);
  }

  function addTableToggle(container, categories, series, opts) {
    var btn = document.createElement("button");
    btn.className = "chart-table-toggle";
    btn.textContent = "Show as table";
    var tableWrap = document.createElement("div");
    tableWrap.style.display = "none";
    var html = "<table><thead><tr><th></th>";
    series.forEach(function (s) { html += "<th>" + s.label + "</th>"; });
    html += "</tr></thead><tbody>";
    categories.forEach(function (cat, i) {
      html += "<tr><td>" + opts.catLabel(cat) + "</td>";
      series.forEach(function (s) { html += "<td>" + s.values[i] + "</td>"; });
      html += "</tr>";
    });
    html += "</tbody></table>";
    tableWrap.innerHTML = html;
    btn.addEventListener("click", function () {
      var showing = tableWrap.style.display !== "none";
      tableWrap.style.display = showing ? "none" : "block";
      btn.textContent = showing ? "Show as table" : "Hide table";
    });
    container.appendChild(btn);
    container.appendChild(tableWrap);
  }

  function renderLineChart(containerId, points, opts) {
    var container = document.getElementById(containerId);
    var w = 560, h = 200, padL = 36, padB = 24, padT = 10, padR = 10;
    var innerW = w - padL - padR, innerH = h - padT - padB;
    var maxVal = Math.max(1, Math.max.apply(null, points.map(function (p) { return p.y; })));
    var n = points.length;
    var stepX = n > 1 ? innerW / (n - 1) : 0;
    var svg = svgEl("svg", { viewBox: "0 0 " + w + " " + h, width: "100%", height: h, role: "img" });

    for (var g = 0; g <= 4; g++) {
      var gy = padT + innerH - (g / 4) * innerH;
      svg.appendChild(svgEl("line", { x1: padL, x2: w - padR, y1: gy, y2: gy, stroke: "var(--grid)", "stroke-width": 1 }));
      var lbl = svgEl("text", { x: padL - 6, y: gy + 3, "text-anchor": "end", "font-size": 9, fill: "var(--muted)" });
      lbl.textContent = Math.round(maxVal * g / 4);
      svg.appendChild(lbl);
    }

    var coords = points.map(function (p, i) { return [padL + i * stepX, padT + innerH - (p.y / maxVal) * innerH]; });
    var areaPath = "M" + coords[0][0] + "," + (padT + innerH);
    coords.forEach(function (c) { areaPath += " L" + c[0] + "," + c[1]; });
    areaPath += " L" + coords[coords.length - 1][0] + "," + (padT + innerH) + " Z";
    svg.appendChild(svgEl("path", { d: areaPath, fill: "var(--series1)", opacity: 0.12 }));

    var linePath = "M" + coords.map(function (c) { return c[0] + "," + c[1]; }).join(" L");
    svg.appendChild(svgEl("path", { d: linePath, fill: "none", stroke: "var(--series1)", "stroke-width": 2, "stroke-linecap": "round" }));

    coords.forEach(function (c, i) {
      var hit = svgEl("circle", { cx: c[0], cy: c[1], r: 8, fill: "transparent" });
      hit.addEventListener("mousemove", function (e) { showTip(e, opts.catLabel(points[i].x) + ": " + points[i].y); });
      hit.addEventListener("mouseleave", hideTip);
      svg.appendChild(hit);
      svg.appendChild(svgEl("circle", { cx: c[0], cy: c[1], r: 2.5, fill: "var(--series1)" }));
      if (i % opts.labelEvery === 0 || i === coords.length - 1) {
        var xl = svgEl("text", { x: c[0], y: h - padB + 12, "text-anchor": "middle", "font-size": 9, fill: "var(--muted)" });
        xl.textContent = opts.catLabel(points[i].x);
        svg.appendChild(xl);
      }
    });
    svg.appendChild(svgEl("line", { x1: padL, x2: w - padR, y1: padT + innerH, y2: padT + innerH, stroke: "var(--axis)", "stroke-width": 1 }));
    container.innerHTML = "";
    container.appendChild(svg);
    addTableToggle(container, points.map(function (p) { return p.x; }), [{ label: opts.seriesLabel, values: points.map(function (p) { return p.y; }) }], opts);
  }

  // ---- CIO: violations by rule/week ----
  (function () {
    var trend = KPI.message_pattern_trend;
    var weeks = [];
    for (var w = 0; w <= 11; w++) weeks.push(w);
    function valFor(rule, week) {
      var hit = trend.filter(function (r) { return r.rule_id === rule && r.week === week; })[0];
      return hit ? hit.n : 0;
    }
    renderGroupedBarChart("chart-violations", weeks, [
      { label: "Rule 1", color: "var(--series1)", values: weeks.map(function (wk) { return valFor(1, wk); }) },
      { label: "Rule 2", color: "var(--series2)", values: weeks.map(function (wk) { return valFor(2, wk); }) },
    ], { catLabel: function (wk) { return "W" + wk; }, labelEvery: 2 });
  })();

  // ---- CIO + CFO: Rule 3 reviews by month ----
  function renderRule3(containerId) {
    var trend = KPI.aggregate_monthly_trend;
    renderGroupedBarChart(containerId, trend.map(function (r) { return r.month; }), [
      { label: "User-months >= threshold", color: "var(--serious)", values: trend.map(function (r) { return r.users_over_threshold; }) },
    ], { catLabel: function (m) { return m; }, labelEvery: 1 });
  }
  renderRule3("chart-rule3");
  renderRule3("chart-rule3-cfo");
  document.getElementById("stat-rule3-threshold").textContent =
    "Threshold: " + fmtUsd(KPI.cost.threshold) + " / user / month (see RULES.md)";

  // ---- CIO: detector reliability panel ----
  (function () {
    document.getElementById("reliability-intro").innerHTML = GENERAL.detector_reliability;
    var grid = document.getElementById("reliability-grid");
    var cats = ["pii", "health", "financial", "credential"];
    function block(title, metricKey, source) {
      var rows = cats.map(function (c) {
        var m = source[c][metricKey];
        return "<tr><td>" + c + "</td><td>" + fmtPct(m.point) + "</td><td>[" + fmtPct(m.ci_low) + ", " + fmtPct(m.ci_high) + "]</td><td>" + m.n + "</td></tr>";
      }).join("");
      return "<div class='tile'><div class='tile-title' style='margin-bottom:6px'>" + title + "</div>" +
        "<table><thead><tr><th>Category</th><th>Point</th><th>95% CI</th><th>n</th></tr></thead><tbody>" + rows + "</tbody></table></div>";
    }
    grid.innerHTML =
      block("Holdout recall", "recall", EVAL.holdout) +
      block("Stress recall (out-of-template)", "recall", EVAL.stress) +
      block("Hard near-miss FPR (holdout)", "near_miss_fpr", EVAL.holdout);
    document.getElementById("reliability-warning").textContent =
      "Database end-to-end numbers on this dashboard are a demo on dev-derived data, not an unbiased measurement. See EVALUATION.md.";
  })();

  // ---- COO: concentration ----
  document.getElementById("stat-concentration").textContent = fmtPctAlready(KPI.concentration.top10_share_pct);
  document.getElementById("stat-concentration-sub").textContent =
    "from top " + KPI.concentration.top10_user_count + " of " + KPI.concentration.total_active_users +
    " active users (" + fmtPctAlready(KPI.concentration.top10_user_share_pct) + " of active users)";

  // ---- COO: WAU ramp ----
  renderLineChart("chart-wau", KPI.weekly_adoption.map(function (r) { return { x: r.week, y: r.wau }; }),
    { catLabel: function (wk) { return "W" + wk; }, labelEvery: 2, seriesLabel: "WAU" });

  // ---- COO: tool inventory table ----
  (function () {
    var rows = KPI.tool_inventory.map(function (t) {
      return "<tr><td>" + t.source + "</td><td>" + (t.sanctioned ? "Sanctioned" : "Unsanctioned") + "</td>" +
        "<td>" + t.active_users + "</td><td>" + t.models_used + "</td><td>" + t.messages + "</td></tr>";
    }).join("");
    document.getElementById("table-tool-inventory").innerHTML =
      "<table><thead><tr><th>Source</th><th>Status</th><th>Active users</th><th>Models</th><th>Messages</th></tr></thead><tbody>" + rows + "</tbody></table>";
  })();

  // ---- CFO: cost percentiles ----
  (function () {
    var p = KPI.cost.percentiles;
    var html = "<table><tbody>";
    ["min", "p50", "p75", "p90", "p95", "p99", "max"].forEach(function (k) {
      html += "<tr><td>" + k + "</td><td>" + fmtUsd(p[k]) + "</td></tr>";
    });
    html += "</tbody></table><p class='panel-note'>n=" + KPI.cost.n_user_months + " user-months</p>";
    document.getElementById("stat-cost-percentiles").innerHTML = html;
  })();

  // ---- CFO: top spenders ----
  (function () {
    var rows = KPI.cost.top_spenders.map(function (r) {
      return "<tr><td>" + r.user_id + "</td><td>" + r.month + "</td><td>" + fmtUsd(r.spend_usd) + "</td></tr>";
    }).join("");
    document.getElementById("table-top-spenders").innerHTML =
      "<table><thead><tr><th>User</th><th>Month</th><th>Spend</th></tr></thead><tbody>" + rows + "</tbody></table>";
  })();
})();
</script>
</body>
</html>
"""


def render_html(payload: dict) -> str:
    import datetime
    data_json = json.dumps(payload, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__DASHBOARD_DATA_JSON__", data_json)
    html = html.replace("__GENERATED_AT__", datetime.date.today().isoformat())
    return html


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        kpi_data = gather_data(conn)
    finally:
        conn.close()

    payload = {
        "kpi": kpi_data,
        "evaluation": load_evaluation(),
        "docs": KPI_DOCS,
        "general_docs": GENERAL_DOCS,
    }
    html = render_html(payload)
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(html):,} bytes)")


if __name__ == "__main__":
    sys.exit(main())

