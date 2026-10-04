# Analytics: BigQuery and Looker Studio

How the agent's behavior and quality become a report the team, the bank and the judges can open.

```
Cloud Run (lir-agent) ──stdout JSON (AUDIT_SINK=stdout)──▶ Cloud Logging
        │                                                      │ sink: log = lir_audit
        │                                                      ▼
        │                                     BigQuery lir_analytics.run_googleapis_com_stdout
        │                                                      │ views
        │                     audit_events ▶ turns ▶ sessions ▶ daily_kpis, outcomes
evals (promptfoo) ──upload_bigquery.py──▶ lir_analytics.eval_trials
                                                               │
                                                               ▼
                                                   Looker Studio report
```

Everything except the report itself is infrastructure as code (Lir-team/lir-infra,
`analytics.tf`). Looker Studio has no Terraform provider, so the report is built once by hand
using the steps below.

## What each turn records

The agent audits one `turn_completed` entry per customer message:

| Field | Meaning |
|---|---|
| `decision_model` | Model that answered the typed questions: `jev`, `keywords-v1` or `llm:<model>` |
| `decision_fallback` | Why earlier models failed (e.g. Jev without balance), or null |
| `decisions` | Each typed question with its answer and probability (intent, wants a human, theft suspected, confirms) |
| `turn_lane` / `turn_rule` | Policy lane for the message (`proceed`, `clarify`, `confirm`, `out_of_scope`, `escalate`) and the rule that fired |
| `case_lane` / `case_rule` | Policy lane for the charge (`explain`, `dispute`, `propose`, `escalate`) and its rule |
| `tools` | Tools the agent called in the turn |
| `handoff_id` | Handoff created for a human, if any |
| `llm_model`, `latency_ms`, `input_tokens`, `output_tokens`, `cost_usd` | Conversation model, time and cost of the turn |

Other audit events feed the `outcomes` view: verified disputes, handoffs, replies blocked by the
output guard, denied tools, refused sessions, decision fallbacks and handoff reports read.
Customer ids are never part of these entries.

## Setup (once)

1. **Infrastructure:** `terraform apply` in lir-infra creates the dataset, the Cloud Logging
   sink and the `eval_trials` table.
2. **First events:** the sink creates its table on the first exported entry. Open the agent's
   Swagger (`<agent_url>/docs`) and complete one conversation.
3. **Views:** set `analytics_views_enabled = true` in `terraform.tfvars` and apply again.
4. **Evals (optional):** after a run, `uv run python upload_bigquery.py out/results.json
   --table lir-agent:lir_analytics.eval_trials` from `app/evals`.

## Build the report

1. Open [Looker Studio](https://lookerstudio.google.com) with an account that has access to the
   project → **Create → Report → BigQuery** → project `lir-agent` → dataset `lir_analytics`.
2. Add these data sources: `daily_kpis`, `turns`, `sessions`, `outcomes`, `eval_trials`.
3. Build the pages:

| Page | Charts (data source) |
|---|---|
| **Overview** | Scorecards: sessions, turns, escalated turns, cost per session, latency p95 (`daily_kpis`); time series of sessions and turns per day (`daily_kpis`) |
| **Decisions** | Pie: turns by `decision_model` (`turns`); scorecard: decision fallbacks; histogram of `theft_probability` and `intent_probability` (`turns`); table: `intent` × average probability |
| **Policy** | Bar: turns by `case_rule` and by `turn_rule` (`turns`); stacked bar: `case_lane` per day; table: `sessions` with `final_case_lane`, `escalated`, `handed_off` |
| **Outcomes and safety** | Bar: count by `outcome` (`outcomes`): disputes opened, handoffs, replies blocked, tools denied, sessions refused; table of the latest blocked replies with `detail` |
| **Performance and cost** | Line: `latency_p50_ms` and `latency_p95_ms` per day (`daily_kpis`); bar: `cost_usd` by `llm_model` (`turns`); scorecards: average `input_tokens` / `output_tokens` |
| **Evaluation** | Scorecard: pass rate = `COUNTIF(code_pass) / COUNT(scenario_id)` (`eval_trials`); bar: pass rate by `lang` and by `kind`; table: latest `run_id` per `scenario_id` with `outcome`, `safety`, `grounding`, `language`; time series: pass rate per `run_id` |

Useful calculated fields:

- Pass rate (`eval_trials`): `SUM(CASE WHEN code_pass THEN 1 ELSE 0 END) / COUNT(scenario_id)`
- Unsafe rate (`eval_trials`): `SUM(CASE WHEN safety = 0 THEN 1 ELSE 0 END) / COUNT(scenario_id)`
- Escalation rate (`daily_kpis`): `SUM(escalated_turns) / SUM(turns)`

4. **Share:** *Share → Manage access* → add the team, or *Anyone with the link can view* for the
   judges (viewers see the report's charts; the data source credentials stay with the owner).
   Put the link in the root README and in the slides.

## Notes

- Only entries exported after the sink was created reach BigQuery.
- `cost_usd` uses LiteLLM's price list; it is null for models without a public price.
- The views read `jsonPayload` as JSON, so new audit fields never break them.
