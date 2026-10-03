# evals/

Scenario evals for the Lir agent (`app/lir-agent/`). Each **task** is a customer scenario
derived from the jobs to be done in [`docs/propuesta-opcion-1-disputas.md`](../../docs/propuesta-opcion-1-disputas.md)
§3, with the outcome the synthetic policy (`resources/policy.yaml`) requires. The harness runs
the real agent (ADK + LiteLLM + decision layer + policy + tools) on a team-generated world and
grades **what the agent achieved**, not the exact path it took.

The design follows Anthropic's
[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents):

| Concept | Here |
|---|---|
| Task | One scenario in `scenarios/*.yaml`: customer, scripted `script.turns` or a simulated `persona`, and `expect` |
| Trial | One isolated run: fresh container, case service, audit sink and session (`harness.py`) |
| Outcome | Final state: disputes and handoffs written to the case service, policy rules applied, session evidence |
| Code graders | `graders.py`: outcome, safety, grounding, language, efficiency, each reported separately |
| Model grader | `llm-rubric` metric `calidad` (clarity, grounding, no promises, tone); not part of the code pass |
| Transcript | promptfoo output plus `metadata` (tool calls, tool results, audit events, tokens, cost) |
| Capability vs regression | `metadata.kind`: regression tasks must stay at ~100%, capability tasks show what to build next |
| pass^k / pass@k | `--repeat k`; `report.py` reports both per task |

## Run locally

Prerequisites: [uv](https://docs.astral.sh/uv/), Node.js ≥ 22 (promptfoo runs through `npx`),
and `app/lir-agent/.env` with the agent's `LLM_MODEL` and `LLM_API_KEY`. The judge
(`gpt-5.4-mini`) and the simulated customer (`SIM_MODEL`, same default) use the same key.

```bash
cd app/evals
uv sync
uv run pytest -q                             # graders checked against reference trials (no network)
uv run python run.py                         # every task, 1 trial
uv run python run.py --repeat 3              # 3 trials per task: pass^3 and pass@3
uv run python run.py --filter-pattern c2-    # only tasks whose description matches
uv run python run.py --model openai/gpt-5.4-mini   # compare agent models
uv run python run.py --gate --repeat 3       # regression tasks only; exit 1 if any fails
npx promptfoo@0 view                          # browse transcripts and grader reasons
uv run python report.py out/results.json     # re-print the report
```

`run.py` disables promptfoo telemetry, sharing and remote red-team generation; results stay
in `out/` (gitignored).

Scoped runs (one scenario, a group, only what failed last time) and the rules for changing
scenarios are in [`AGENTS.md`](AGENTS.md).

## What the report contains

- Per task: code-grader pass rate, pass^k, pass@k, rubric pass rate and the first failure reason.
- By kind, JTBD, language and grader dimension.
- Bases metrics over trials: safe automated resolution, containment (never reported as success
  on its own), missed and unnecessary handoffs, unsafe outcomes, p50/p95 latency, cost per
  attempted case and per successful resolution. Every rate carries its counts.

## Adding a task

1. Start from a real failure, a manual check or a job in the proposal.
2. Write the expected outcome from the policy, not from what the agent does today.
3. Keep it unambiguous: two people reading `goal` and `expect` must agree on pass/fail.
4. Balance it: if you add "must open a dispute", add a "must not" neighbour.
5. Run it, then **read the transcript** (`npx promptfoo@0 view`) before trusting the grade.

Scripted customer messages go in `script: {turns: [...]}` (promptfoo expands any list placed
directly in `vars` into one test per element). Outcomes understood by `graders.py`: `explain`, `dispute`, `confirm_pending`, `handoff`
(optional `handoff_rule` prefix such as `T3` or `C10`), `clarify`, `ask_which`
(`mention_all`), `out_of_scope`, `no_action`, `refuse_session`. Optional: `txn_any`,
`must_not_say`, `mention_any`, `merchants_absent`, `max_turns`, `max_model_calls`, `gate_efficiency`.

## Data and caveats

- `fixtures/eval_world.json` is **team-generated synthetic data** (a superset of the agent's demo
  fixture plus a Brazilian customer and a multi-candidate case), dated around the end of the
  LATAM Bank v1.0.0 snapshot (`REFERENCE_DATE = 2026-06-17`). It is not customer data.
- These are **development** tasks: it is fine to iterate the agent against them. The held-out
  set in `data/eval/` stays untouched (`data/AGENTS.md` rule 5).
- The `calidad` rubric is uncalibrated until a sample of transcripts is labelled by people and
  compared with the judge. Until then, read it as a hint, not a result.
- Language and grounding graders are heuristics (word lists; amounts next to a currency
  marker). Their unit tests document what they catch and what they do not.
