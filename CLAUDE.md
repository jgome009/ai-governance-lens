# AI Governance Lens — project kickoff

Save this file as `CLAUDE.md` at the root of the new project. The last section is the first prompt to paste into Claude Code.

## 1. Business problem (start here, not with the tool)

A CIO asks: "Which AI tools are my people using, how much, with what kind of data, at what cost, and does it comply with our policy?"
Today the answer is scattered across ChatGPT, Claude, Gemini and coding agents. Consumer-tier tools expose no admin API, so nobody has the full picture (shadow AI).

Goal: a prototype that turns raw usage exports into **governance KPIs** for a fictional company, and is honest about what the data can and cannot prove.

This is a portfolio piece, not a product. Existing tools already cover usage counting and quota tracking (ccusage, Claude Tuner, Convoviz, Kubera). Do not rebuild them. The differentiator is the governance layer: inventory, sanctioned vs unsanctioned use, data-sensitivity flags, policy violations.

## 2. Scenario

Fictional company: **Northwind Medical GmbH**, 400 employees, Germany. Policy (invented for the demo):
- Sanctioned tools: Claude (Team plan), Claude Code. Everything else is unsanctioned.
- Rule 1: no customer or patient identifiers in any unsanctioned tool.
- Rule 2: no credentials or API keys in any tool.
- Rule 3: monthly AI spend per user above a threshold triggers review.

Persona to present to: CIO (wants risk and cost), COO (wants adoption and value), CFO (wants spend).

## 3. Hard constraints

- **Privacy:** never commit real conversations. The repo contains only synthetic data. Real exports live outside the repo, and `.gitignore` excludes `data/real/`.
- **Local only:** no network call may send conversation content anywhere. No LLM API calls on content.
- **Formats:** do not invent export formats from memory. Build parsers against real sample files supplied by the owner. Until then, generate synthetic data from a minimal normalized schema and mark each parser as "unverified against real export".
- **Licenses:** read the license of any existing tool before reusing code. Prefer reading their approach and writing our own.
- **Heuristics are labelled:** every detection (sensitive data, task category) is rule-based, shows its rule, and states its false-positive/negative limits in the dashboard. No fake precision.
- **EU AI Act / GDPR references:** only as framing in the README, flagged "verify current requirements". Do not state legal obligations as fact.

## 4. Normalized data model (SQLite)

`messages`: id, source (chatgpt | claude_web | claude_code | gemini), user_id, conversation_id, ts, role, model, char_count, token_count (nullable, estimated if missing, flag `tokens_estimated`), text_hash.
Raw text is processed in memory for detection and is not stored in the demo database. Store only flags.

`flags`: message_id, rule_id, category (pii | credential | financial | health), matched_pattern_name (never the matched text).

`tools`: source, vendor, sanctioned (bool), plan.

## 5. KPIs (define each one precisely in README)

1. **Tool inventory:** distinct sources and models in use, active users per tool.
2. **Shadow AI rate:** share of messages (and of active users) on unsanctioned tools.
3. **Adoption:** weekly active users, messages per user per week, concentration (share of volume from the top 10% of users).
4. **Sensitive-data exposure:** flagged messages per 1,000, split by sanctioned vs unsanctioned tool. Heuristic, label it.
5. **Policy violations:** count per rule (Rules 1 to 3), trend by week.
6. **Estimated cost:** tokens x public list price per model. Always labelled "estimate".
7. **Task mix (value proxy):** keyword-rule categories (coding, writing, analysis, other). Labelled heuristic.

For each KPI the README states: definition, formula, data source, known bias, what decision it supports.

## 6. Phases and acceptance criteria

1. **Synthetic data generator.** 400 users, 12 weeks, 4 sources, realistic skew (few heavy users, some shadow AI, some planted violations). Acceptance: seeded and reproducible; planted violations known so detection can be tested.
2. **Schema + loader + tests.** Acceptance: SQL KPIs return the planted values exactly.
3. **Detection rules.** Regex/rule-based for PII, credentials, financial identifiers. Acceptance: precision/recall measured against the planted set and reported honestly.
4. **Dashboard.** Single self-contained HTML (client-side, no server), CIO / COO / CFO views. Acceptance: opens offline, every KPI has a definition tooltip, heuristic KPIs are visibly labelled.
5. **Real-data adapters.** One parser per provider, written against real exports supplied by the owner, run locally only. Acceptance: output never leaves the machine; nothing real is committed.
6. **README as the pitch.** Problem, scenario, KPI definitions, assumptions, limits, screenshots from synthetic data, what I would need to run it in a real company (admin APIs, compliance APIs on enterprise plans: verify current availability).

## 7. Working rules for Claude Code

- Start every task from the business question it answers. State assumptions before building.
- Challenge scope creep. If a request does not serve a governance KPI, say so.
- Say "I'm not sure" when unsure, and list what must be verified (export formats, pricing, regulation).
- Small commits, tests for every KPI query.
- Keep a `LIMITATIONS.md` updated as you go.

## 8. First prompt to paste into Claude Code

> Read CLAUDE.md. Do phase 1 only: build the synthetic data generator for Northwind Medical GmbH (400 users, 12 weeks, sources: chatgpt, claude_web, claude_code, gemini) writing into the SQLite schema in section 4. Include a seeded set of planted violations (Rules 1 to 3) and write them to a separate `planted_violations` table used only for testing. Before coding, give me a 10-line plan and list your assumptions. After coding, show me the row counts and 5 sample KPI queries with results.
