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
uv run lir-agent-api                      # HTTP API on PORT (default 8080)
uv run pytest                             # tests (offline: scripted decisions, fixture data)
uv run ruff check . && uv run ruff format --check .
uv run pyright                            # type check
```

`--customer-id` and `DEV_CUSTOMER_ID` stand in for the bank's identity check (biometric
KYC, mocked).

## HTTP API

The container entry point on Cloud Run. IAP verifies the caller's Google identity and sends
it in `X-Goog-Authenticated-User-Email`; that caller (a tester or the bank channel) owns the
sessions it creates, and requests without it get `401`. The customer is chosen when the
session is created, standing in for the bank's identity check (biometric KYC, mocked).

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/health` | - | `{"status": "ok"}` |
| `POST` | `/v1/sessions` | `{"customer_id": "CLI-DEMO-001"}` | `201 {"session_id", "expires_at"}`; `404` if the customer does not exist |
| `POST` | `/v1/sessions/{session_id}/messages` | `{"text": "No reconozco un cargo de 245.50"}` | `{"reply": "..."}` |
| `POST` | `/v1/cases` | a `lir-web` case (schema 1.1), header `Idempotency-Key: <case_id>` | `202 {"case_id", "folio", "status", "telegram_start_url"}` |
| `POST` | `/channels/telegram` | a Telegram update, header `X-Telegram-Bot-Api-Secret-Token` | `200` (`401` wrong secret, `404` channel not configured) |

`POST /v1/cases` is the web form's entry point and does not use IAP: API Gateway verifies the
customer's JWT and forwards its claims in `X-Apigateway-Api-Userinfo` (missing or unreadable:
`401`). The payload is validated against `src/lir_agent/resources/schemas/case.schema.json`,
copied from `lir-web`: field errors return `422 {"errors": {"<form field>": "<code>"}}`, and
errors without a form field return `400`. `customer.customer_id` must be the JWT's customer
(`403`) and exist (`404`); every transaction must be theirs (`422 transaction_ids: unknown`).
The `Idempotency-Key` must equal `case_id` (`400`), and repeating it replays the first `202`.
Accepted cases go to the inbox chosen by `CASES_INBOX` (`cases/<case_id>.json`, attributes as
object metadata). `telegram_start_url` is a single-use `https://t.me/<bot>?start=<token>` link
when the customer chose Telegram and `TELEGRAM_BOT_USERNAME` is set, otherwise `null`.

### Telegram channel

The customer taps the start link and Telegram sends `/start <token>` to the webhook. The
token is single-use: it opens a conversation for the case (owner `case:<case_id>`, valid for
`CASE_SESSION_TTL_MINUTES`, 7 days by default), links the chat to it (a later link replaces
it), confirms the case folio and runs the agent's first turn from the case description and
reported charges. Later messages from that chat go to the same conversation and the reply is
sent back (split at Telegram's 4096 characters). Messages longer than `MAX_MESSAGE_CHARS`
are refused with a short note; unlinked chats are asked to use the link from the form; used
or expired links and lost conversations get a short message in Spanish or Portuguese. Only
text from private chats is read, and repeated updates are handled once.

Set `TELEGRAM_BOT_TOKEN` (BotFather) and `TELEGRAM_WEBHOOK_SECRET` (any 1-256 characters of
`A-Z a-z 0-9 _ -`), then register the webhook once; Telegram sends the secret back in
`X-Telegram-Bot-Api-Secret-Token`, compared in constant time:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://<public base URL>/channels/telegram" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
  -d 'allowed_updates=["message"]'
```

Telegram only calls HTTPS URLs: locally, expose the API with a tunnel (e.g.
`ngrok http 8080`) and register its URL. The bot token is part of every Bot API URL, so
keep `LOG_LEVEL` above `DEBUG` outside local runs: at `DEBUG`, `httpx` logs request URLs.

Locally, without IAP:

```bash
REQUIRE_IDENTITY=false uv run lir-agent-api
curl -X POST localhost:8080/v1/sessions -H 'content-type: application/json' -d '{"customer_id":"CLI-DEMO-001"}'
curl -X POST localhost:8080/v1/sessions/<session_id>/messages -H 'content-type: application/json' -d '{"text":"No reconozco un cargo de 245.50 en OXXO"}'
```

Container (build context is `app/`, for the decision-layer path dependency):

```bash
docker build -f app/lir-agent/Dockerfile -t lir-agent app/
```

The image runs as a non-root user, reads data from `DATA_DIR=/mnt/data` (the Cloud Storage
bucket mounted by Cloud Run) and gets its keys from Secret Manager as environment variables. With `AUDIT_SINK=stdout`
(set in the image) every audit entry is a structured JSON line in Cloud Logging.

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
- Sessions, start tokens and Telegram chat links are in memory; production needs a
  persistent store (Firestore). The
  audit log is a local JSONL file, or Cloud Logging on Cloud Run (`AUDIT_SINK=stdout`). On Cloud Run this means
  one instance (`max-instances=1`) so a session's messages reach the instance that holds it.
- The HTTP API trusts the identity header set by IAP; it must only be reachable through IAP
  (Cloud Run ingress and IAP settings, managed in `lir-infra`).
- The keyword baseline misses a bare "sí" as a confirmation; Jev is expected to handle it.
- `CLI-DEMO-001` exists only in the demo fixture; with DuckDB use a real customer id.
