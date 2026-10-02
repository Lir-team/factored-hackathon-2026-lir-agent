# lir-agent

Google ADK agent for the "I don't recognize this charge" flow. The LLM converses, typed
decisions (Jev, or the keyword baseline) classify, and **deterministic code authorizes**:
policy, permissions and confirmations are enforced outside the prompt, and every step is
written to an audit log.

## How a turn works

1. The channel creates the session with the authenticated customer in its state
   (`customer_id`, `auth_expires_at`, `auth_method`). The ID never passes through the model.
2. `before_agent` refuses a session without a valid signed-in customer (missing or expired)
   before the agent runs, so the model is never called for it.
3. `before_model` classifies the new message with the decision layer and routes the turn
   with `resources/policy.yaml` (`proceed`, `clarify`, `confirm`, `out_of_scope`,
   `escalate`). Escalations create the human handoff deterministically.
4. `before_tool` denies tools without a valid session, tools not allowed in the turn lane,
   and `open_dispute` without the `dispute` case lane and an explicit customer confirmation.
5. Tools read only the session customer's records. Results sent to the LLM contain only
   allowlisted fields and opaque transaction references (`T1`, `T2`).
6. `after_model` replaces replies that promise refunds or ask for credentials.

## Tools

| Tool | What it does |
|---|---|
| `get_my_customer_profile` | Minimal profile of the signed-in customer (no arguments, allowlisted fields) |
| `find_candidate_transactions` | Matches the customer's description (amount, dates, merchant) |
| `get_transaction_evidence` | Verifiable facts and the policy lane: explain, dispute, propose or escalate |
| `open_dispute` | Only with the `dispute` lane and a prior explicit confirmation; read back before reporting |
| `request_human_handoff` | Case file built from session state, not from model prose |

## Layout

Hexagonal layers: dependencies point inward (interface → application → domain), and
infrastructure implements the application ports.

```
src/lir_agent/
├── domain/            # policy engine, evidence, session state, dispute guard, models
├── application/       # ports, presenter (data minimization), one use case per action
├── infrastructure/    # DuckDB and fixture repositories, audit sinks, mock cases, resources
├── interface/adk/     # toolkit (tools as methods), callbacks, guidance, agent factory
├── config/settings.py # typed settings
├── container.py       # composition root
├── chat.py            # terminal chat
├── main.py            # prints the resolved configuration
└── resources/         # policy.yaml, reference.yaml, prompts/, fixtures/demo.json
apps/lir/agent.py      # ADK entry point for `adk web` / `adk run`
```

The decision layer lives next to this app in `../decision-layer` and is installed as an
editable path dependency.

## Setup

```bash
cd app/lir-agent
cp .env.example .env      # pick Ollama or OpenAI
uv sync
```

On Windows inside OneDrive, set `UV_LINK_MODE=copy` before `uv sync`.

For real data, run the pipeline first (`cd data && python -m pipelines --ingest`);
without `data/staging`, the agent uses the team-generated demo fixture.

## Commands

```bash
uv run lir-agent                          # print the resolved configuration
uv run chat --customer-id CLI-DEMO-001    # terminal chat; type "chao pescao" to exit
uv run adk web apps                       # browser dev UI (needs DEV_CUSTOMER_ID in .env)
uv run adk run apps/lir                   # ADK terminal chat (needs DEV_CUSTOMER_ID)
uv run pytest                             # tests (offline: scripted decisions, fixture data)
uv run ruff check . && uv run ruff format --check .
uv run pyright                            # type check
```

`--customer-id` and `DEV_CUSTOMER_ID` stand in for the bank's identity check (biometric
KYC, mocked).

## Try it (demo fixture)

| Message | Expected behavior |
|---|---|
| "No reconozco un cargo de 245.50 en OXXO", then "Confirmo, abre la disputa" | detects the duplicate, asks for confirmation, opens a verified dispute |
| "¿Qué es un cargo de 179 de Spotify?" | explains it: recurring payment, previous charges |
| "No reconozco un cobro de PAYPAL STEAMGAMES" | explains it as a pending authorization |
| "Veo una compra de 38900 en Argentina" | hands off to a specialist without revealing why |
| "Quiero hablar con una persona" / "Me clonaron la tarjeta" | immediate handoff |

## Configuration

Settings come from the environment, then `.env` (see `.env.example`). Thresholds, rules,
tool permissions per lane, LLM-facing fields and the output guard live in
`src/lir_agent/resources/policy.yaml`, a synthetic, versioned team policy.

## Dependencies

```bash
uv add <package>              # runtime dependency
uv add --dev <package>        # dev-only dependency
uv lock --upgrade-package <package>
uv sync                       # reconcile .venv with uv.lock
```

Commit `uv.lock`: it makes installs reproducible across machines and CI.

## Known limitations

- `open_dispute` and the handoff queue are in-memory mocks with documented contracts; no money moves.
- Session state and the audit log are local (in-memory sessions, JSONL file); production
  needs a persistent session service and BigQuery or Cloud Logging.
- The keyword baseline misses a bare "sí" as a confirmation; Jev is expected to handle it.
- `CLI-DEMO-001` exists only in the demo fixture; with DuckDB use a real customer id.
