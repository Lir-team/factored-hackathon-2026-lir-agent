# decision-layer

The **typed decision layer** of the customer-service agent, installed in `app/lir-agent` as a
path dependency. The agent asks closed questions about the customer's message (intent, wants a
human, theft suspected, which merchant) and gets back answers with a **probability**, so every
routing decision has an explicit threshold, a measurable accuracy and a trace. See the
repository root README for the shared conventions.

## Purpose and context

`decision-layer` answers one question: can we separate *deciding* from *conversing*, so the
agent's routing decisions are measurable, cheap and auditable instead of buried in LLM prose?

It targets the "unrecognized charge → explain, dispute or hand off" workflow. The LLM talks to
the customer, this layer decides, and deterministic code authorizes. It provides:

- A `DecisionModel` contract (`Noul` yes/no and `Choice` questions → `Answer` with probability),
  so Jev, Laya, an LLM or keyword rules are interchangeable without touching the agent. The LLM
  implementation lives in the agent (`app/lir-agent/src/lir_agent/infrastructure/decisions/llm.py`),
  not in this package.
- Decisions D1–D4 (`questions.py`): intent, wants a human, theft suspected, which merchant.
- A **Jev** client ([TypeSafe AI](https://developers.cloudflare.com/ai/models/typesafe/jev/)
  through Cloudflare Workers AI) with bounded retries on 429/5xx. 402 (billing) and 401/403
  (auth) are not retried, and an unknown option in the response is rejected.
- A deterministic **ES/PT keyword baseline**, the baseline every learned component is compared
  against.
- A **fallback chain**: Jev → baseline → `DecisionError`, in which case the agent hands off to a human.

Facts and caveats to keep in mind:

- **Jev is currently off.** The credentials work, but Cloudflare answers
  `402 Insufficient balance` because Jev is billed from the AI Gateway balance (it is not covered
  by the Workers AI free allocation). Until then, the chain uses the baseline.
- Jev's own docs say it is not good with numbers, dates or adversarial content. Amounts and dates
  are filtered by code, and prompt-injection defense lives in the tool layer, never in a classifier.
- Only customer text leaves the machine (plus merchant names for D4). Never IDs, documents,
  balances, amounts or dates (`data/AGENTS.md` rule 3).
- The keyword baseline is intentionally naive. On a message with no signal (for example
  "Quero falar com um atendente" for intent), it returns a uniform distribution (p = 0.20), and
  the agent must ask for clarification.

## Layout

```
src/decision_layer/
  base.py        contract: Noul / Choice -> Answer, DecisionModel, DecisionError
  questions.py   decisions D1-D4 (question texts in Spanish: they are model input)
  jev.py         Jev client via Cloudflare Workers AI
  keywords.py    deterministic ES/PT baseline
  chain.py       fallback chain
  config.py      env vars, then decision-layer/.env, then data/.env (values never printed)
  __main__.py    smoke test
tests/           pytest, no network (HTTP transport is faked)
```

No runtime dependencies (standard library only). `pytest` and `ruff` are dev dependencies.

## Run locally

Prerequisite: [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python -m decision_layer --model keywords   # 5 ES/PT sentences with the baseline
uv run python -m decision_layer                    # default chain (Jev if enabled, else baseline)
uv run python -m decision_layer --model jev --one  # one call to Jev: checks credentials and balance
```

```python
from decision_layer import build_default
from decision_layer.questions import TURN_QUESTIONS

result = build_default().decide("No reconozco este cargo", TURN_QUESTIONS)
result.answers["intencion"].value, result.answers["intencion"].probability
```

## Test

```bash
uv run pytest -q && uv run ruff check .
```

## Configuration

Set these in `decision-layer/.env` or `data/.env` (both gitignored), or as environment variables.
See [`.env.example`](.env.example).

| Variable                | Default | Purpose                                                        |
|-------------------------|---------|----------------------------------------------------------------|
| `CLOUDFLARE_ACCOUNT_ID` | —       | Cloudflare account that runs Jev                               |
| `CLOUDFLARE_API_TOKEN`  | —       | Token with the Account → Workers AI permission                 |
| `JEV_ENABLED`           | `0`     | `1` puts Jev first in the default chain; requires AI Gateway balance |

## Connecting Jev

1. Top up the balance: Cloudflare dashboard → **AI → AI Gateway → Credits Available → Manage →
   Top-up credits** (US$10 minimum plus a 5% fee). Leave automatic top-up **off**.
   Alternatively, use BYOK with a TypeSafe key.
2. Set `JEV_ENABLED=1`.
3. Run `uv run python -m decision_layer --model jev --one`.

## Next steps

- Jev and Laya against the labelled ES/PT set. The baseline and the LLM are already compared
  there (`data/reports/decision_eval.md`): macro-F1, calibration (ECE), coverage/accuracy
  curve, thresholds chosen on validation, p50/p95 latency and cost.
