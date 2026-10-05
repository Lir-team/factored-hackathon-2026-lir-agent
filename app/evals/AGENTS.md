# Rules for agents (Claude Code, Codex, etc.) inside app/evals/

Scenario evals for the Lir agent. Each scenario in `scenarios/*.yaml` runs against the **real**
agent (ADK + LiteLLM + decision layer + policy + tools) in an isolated process, and is
graded on what it achieved: disputes and handoffs written, rules applied, and safety. The
design and concepts are in [`README.md`](README.md).

All commands are run from `app/evals/` with `uv run python run.py`. `run.py` launches
promptfoo with `npx`, turns off telemetry and sharing, and prints the report at the end. Any argument
that `run.py` does not know is passed to `promptfoo eval` as is.

## 1. Before spending model calls

```bash
uv run pytest -q          # graders against reference trials + shape of each scenario (no network)
../../scripts/check.sh    # tests, lint and types for the whole repo (no network, ~20 s)
```

If `pytest` fails, do not run evals: a malformed scenario wastes calls and gives false results.

## 2. Full runs

| Purpose | Command | Trials | Approx. time |
|---|---|---|---|
| A quick pass over everything | `uv run python run.py` | 27 | ~1 min |
| Measure for real (pass^3) | `uv run python run.py --repeat 3` | 81 | ~4 min |
| Regression gate (what `check.sh --live` uses) | `uv run python run.py --gate --repeat 3` | 27 | ~2 min |
| Compare another agent model | `uv run python run.py --repeat 3 --model openai/gpt-5.4-mini` | 81 | ~4 min |
| Compare LLM decisions vs baseline | `DECISIONS=llm uv run python run.py --repeat 3` | 81 | ~7 min |

The default parallelism is 4 (`-j 4`). With `gpt-6-luna` each trial costs ~US$0.0005, plus the judge.

To compare two configurations, save each `out/results.json` under another name before the
next run. The cost of each trial adds up the agent and the decision model
(`decision_calls`, `decision_cost_usd` in the metadata). If the decision model fails, the
chain falls back to the baseline and a `decision_fallback` event is left in the audit log: check it before
concluding that "the LLM decides the same as the baseline".

## 3. Narrowed runs (the norm during development)

After a change, run **only the affected scenarios** with several trials, and the full
run just once at the end.

```bash
# One scenario, 5 trials (the filter is a regex over `description`, which is the id)
uv run python run.py --repeat 5 --filter-pattern c2-dispute-simulated-es

# Several scenarios: alternatives with |. Always in quotes.
uv run python run.py --repeat 5 --filter-pattern "c2-dispute|c5-refund-pressure|c3-portunol"

# A group by prefix (c1 = identify and explain, c2 = disputes, c4 = hand off, c5 = safety)
uv run python run.py --repeat 3 --filter-pattern "^c5-"

# Only regression or only capability
uv run python run.py --repeat 3 --filter-metadata kind=capability

# Re-run only what failed in the previous run (run.py saves it in out/previous.json)
uv run python run.py --repeat 5 --filter-failing out/previous.json

# A random, reproducible sample
uv run python run.py --filter-sample 5 --filter-sample-seed 42
```

- On Windows, `run.py` escapes the `|` for `npx.cmd`. Do not escape it by hand.
- When narrowing, include the **neighbors** of the change, especially the scenarios where something must **not**
  happen. If you touch dispute confirmation, also run `c2-duplicate-no-confirm-es` and
  `c5-fake-system-confirmation`.
- To see what fails in a flaky task, use `--repeat 5` or more: with 1 trial you cannot see it.

## 4. Reading the results

```bash
npx promptfoo@0 view                          # web viewer: transcript, tools and reason for each grader
uv run python report.py out/results.json      # re-print the report without re-running
uv run python report.py out/previous.json     # the report from the previous run
```

- **Read the transcript before blaming the agent.** In this repo, several "failures" were an overly
  strict grader or a poorly specified scenario, not the agent.
- `pass` and `pass^k` use only the code graders (`outcome`, `safety`, `grounding`,
  `language`). The `calidad` rubric (LLM judge) is reported separately, **is not calibrated** and is not
  consistent: never use it alone to decide.
- `regression` tasks must be at 100%. `capability` tasks measure what is left to build.
- A `safety` at 0 is the most serious: read that transcript first.

## 5. Rules when changing scenarios or graders

1. **The expected result comes from `policy.yaml`**, not from what the agent does today.
2. **Never put a list directly in `vars`**: promptfoo turns it into one test per element.
   Customer messages go in `script: {turns: [...]}`. `tests/test_scenarios.py` validates this.
3. Balance: if you add "must open a dispute", add a "must not" neighbor.
4. Each new or fixed grader comes with a test in `tests/test_graders.py` that fails without the fix.
5. The definition of "refund promise" is the agent's `OutputGuard`. Do not copy it into the
   grader.
6. Do not touch `data/eval/`: it is the held-out set (`data/AGENTS.md`, rule 5). These scenarios are for
   development and you can iterate against them.
7. Do not write to `out/` by hand or commit it (it is in `.gitignore`).
