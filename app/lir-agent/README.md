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
   allowlisted fields and opaque transaction references (`T1`, `T2`). The fields in
   `llm_exposure.pseudonymized_fields` (name, merchant, amount, dates) leave as placeholders
   (`[[COMERCIO_1]]`, `[[MONTO_1]]`); the placeholder table lives in the session state.
6. Every model request is rebuilt by `before_model`: card, account, document and contact
   numbers are removed from the customer's words (`[[DATO_PROTEGIDO]]`, only the count is
   audited), and values the customer already read go back as placeholders. The decision
   model receives the same protected text. Cases from the web form start with their charges
   as references (`T1`), never as merchant, amount or date.
7. `after_model` resolves placeholders for the customer, then replaces replies that promise
   refunds or ask for credentials. `before_tool` resolves the placeholders the model passes
   to a tool, so tools and the handoff packet work on real values.

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
| `POST` | `/v1/sessions/{session_id}/messages` | `{"text": "No reconozco un cargo de 245.50"}` | `{"reply": "...", "trace": {...}}` (`trace` only with `EXPOSE_TRACE=true`) |
| `GET` | `/v1/handoffs/{handoff_id}/report.md?language=es` | - | Markdown case file for the bank specialist; each read is audited |
| `POST` | `/v1/cases` | a `lir-web` case (schema 1.1), header `Idempotency-Key: <case_id>` | `202 {"case_id", "folio", "status", "telegram_start_url"}` (`409` same key in flight, `503` retry) |
| `POST` | `/channels/telegram` | a Telegram update, header `X-Telegram-Bot-Api-Secret-Token` | `200` (`401` wrong secret, `404` channel not configured) |
| `POST` | `/pubsub/push` | a Pub/Sub push message carrying a case, header `Authorization: Bearer <OIDC token>` | `204` (`401` bad token, `404` not configured, `500` retried) |

`POST /v1/cases` is the web form's entry point and does not use IAP: API Gateway verifies the
customer's JWT and forwards its claims in `X-Apigateway-Api-Userinfo` (missing or unreadable:
`401`). The payload is validated against `src/lir_agent/resources/schemas/case.schema.json`,
copied from `lir-web`: field errors return `422 {"errors": {"<form field>": "<code>"}}`, and
errors without a form field return `400`. `customer.customer_id` must be the JWT's customer
(`403`) and exist (`404`); every transaction must be theirs (`422 transaction_ids: unknown`).
The `Idempotency-Key` must equal `case_id` (`400`), and repeating it replays the first `202`.
The key is claimed before anything is archived or published, so a concurrent request with
the same key gets `409` and changes nothing; the client retries it and gets the replay.
Accepted cases are archived in the inbox chosen by `CASES_INBOX` (`cases/<case_id>.json`,
attributes as object metadata) and published to the agent (see below) before the `202`; if
publishing fails the answer is `503` and the claim is released, so a retry with the same key
publishes again. `telegram_start_url` is a single-use `https://t.me/<bot>?start=<token>` link
when the customer chose Telegram and `TELEGRAM_BOT_USERNAME` is set, otherwise `null`.

### Telegram channel

The customer taps the start link and Telegram sends `/start <token>` to the webhook. The
token is single-use: it links the chat to the case (a later link replaces it), confirms the
case folio and sends the agent's replies already waiting for the case. It never starts a
conversation: the agent works the case as soon as it is delivered (next section), and if
that is still in flight its reply arrives on its own. Later messages from that chat go to
the case's conversation and the reply is sent back (split at Telegram's 4096 characters);
before the case is worked, the chat is asked to wait. Messages longer than `MAX_MESSAGE_CHARS`
are refused with a short note; unlinked chats are asked to use the link from the form; used
or expired links and lost conversations get a short message in Spanish or Portuguese. These
notices are best effort; an agent reply Telegram did not accept stays queued and is sent
before the chat's next answer. Only
text from private chats is read, and repeated updates are handled once; an update whose
handling failed answers `500`, so Telegram's retry is handled again.

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

### Case processing (Pub/Sub)

With `CASES_PUBLISHER=pubsub`, `POST /v1/cases` publishes each accepted case to `CASES_TOPIC`
(`lir-cases`) in `GOOGLE_CLOUD_PROJECT`: the payload JSON unchanged as data, the case
attributes as message attributes, the customer id as ordering key. A push subscription
delivers it to `POST /pubsub/push`, which starts the case's conversation (owner
`case:<case_id>`, auth method `case_intake`, valid for `CASE_SESSION_TTL_MINUTES`, 7 days by
default) and runs the agent's first turn from the case description and reported charges.
The reply goes to the linked Telegram chat, or waits until `/start` links one. Pub/Sub
delivers at least once: a case already worked only sends its replies still waiting.
Messages that are not a valid case are acknowledged and audited as `case_rejected`; agent
failures, and replies Telegram did not accept (kept queued), answer `500` so Pub/Sub retries. Audit events: `case_processed`, `case_reply_queued`,
`case_reply_sent` (never the message text).

The push subscription must sign its calls (OIDC) with audience `PUBSUB_PUSH_AUDIENCE`; set
`PUBSUB_PUSH_SERVICE_ACCOUNT` to accept only its service account. Without an audience the
route is absent. With the default `CASES_PUBLISHER=none` cases are accepted but never worked.

Locally, with the Pub/Sub emulator (`scripts/pubsub-emulator.sh` at the repository root
starts it, creates topic `lir-cases` and a push subscription to
`http://<host>:8080/pubsub/push`). The emulator sends no token, so turn verification off
for this run only:

```bash
PUBSUB_EMULATOR_HOST=localhost:8085 GOOGLE_CLOUD_PROJECT=lir-local \
CASES_PUBLISHER=pubsub PUBSUB_VERIFY_TOKEN=false REQUIRE_IDENTITY=false \
uv run lir-agent-api
```

### Case store (Firestore)

Idempotent receipts, Telegram start tokens, chat links, case conversations and replies
waiting for a chat live in the case store. The default `CASE_STORE=memory` loses them on
restart and does not share them between instances; `CASE_STORE=firestore` keeps them in
Firestore (`GOOGLE_CLOUD_PROJECT`, database `FIRESTORE_DATABASE`, collections named
`FIRESTORE_COLLECTION_PREFIX` + `receipts`, `claims`, `start_tokens`, `chats`, `case_chats`,
`conversations`, `replies`). Start tokens are stored as their SHA-256 hash only.

Locally, with the Firestore emulator (`scripts/firestore-emulator.sh up` at the repository
root; it listens on `localhost:8086`, project `lir-local`):

```bash
FIRESTORE_EMULATOR_HOST=localhost:8086 GOOGLE_CLOUD_PROJECT=lir-local \
CASE_STORE=firestore REQUIRE_IDENTITY=false uv run lir-agent-api
```

The case store tests run against both adapters; the Firestore ones only when
`FIRESTORE_EMULATOR_HOST` is set (otherwise skipped):

```bash
FIRESTORE_EMULATOR_HOST=localhost:8086 GOOGLE_CLOUD_PROJECT=lir-local uv run pytest -q
```

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

Every turn is also audited as `turn_completed`, one entry per turn with the decision model
that answered (Jev, keyword baseline or LLM) and any fallback, the typed decisions with their
probabilities, the turn and case lanes and rules, the tools called, latency, tokens and USD
cost (LiteLLM price list). With `EXPOSE_TRACE=true` the same trace is returned with each reply,
for demos and operators; it is never sent to customer channels.

## Try it (demo fixture)

| Message | Expected behavior |
|---|---|
| "No reconozco un cargo de 245.50 en OXXO", then "Confirmo, abre la disputa" | detects the duplicate, asks for confirmation, opens a verified dispute |
| "¿Qué es un cargo de 179 de Spotify?" | explains it: recurring payment, previous charges |
| "No reconozco un cobro de PAYPAL STEAMGAMES" | explains it as a pending authorization |
| "Veo una compra de 38900 en Argentina" | hands off to a specialist without revealing why |
| "Quiero hablar con una persona" / "Me clonaron la tarjeta" | immediate handoff |

## Deploy

`.github/workflows/deploy-agent.yml` runs on every push to `main` that touches
`app/lir-agent/` or `app/decision-layer/` (or by hand, *Run workflow*). It signs in to
GCP with Workload Identity Federation (no keys), builds the image with Cloud Build
(`cloudbuild.yaml`, tagged with the commit SHA) and rolls it out to both Cloud Run
services: `lir-agent` (operator API, behind IAP) and `lir-agent-cases` (case flow).
It only changes the image: Terraform in `lir-infra` owns every other setting (env vars,
secrets, IAM, scaling) and ignores the image, so a deploy never needs `terraform apply`.

Set these GitHub repository variables (*Settings > Secrets and variables > Actions >
Variables*); the values come from `terraform output` in `lir-infra`:

| Variable         | Value                                                                      |
| ---------------- | -------------------------------------------------------------------------- |
| `GCP_PROJECT_ID` | GCP project ID                                                             |
| `GCP_REGION`     | Region of Cloud Run and Artifact Registry                                  |
| `WIF_PROVIDER`   | `projects/<number>/locations/global/workloadIdentityPools/github/providers/lir-team` |
| `DEPLOY_SA`      | `lir-deploy@<project>.iam.gserviceaccount.com`                             |
| `AR_REPO`        | Artifact Registry repository (`lir`)                                       |
| `BUILD_SA`       | Cloud Build service account email (`lir-build@<project>.iam.gserviceaccount.com`) |
| `BUILD_BUCKET`   | Build source bucket (`<project>-build-source`)                             |
| `DEPLOY_CASES_SERVICE` | `true` once `lir-agent-cases` exists; `false` skips its rollout |

To build by hand, see the command at the top of `cloudbuild.yaml`.

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
- ADK sessions are in memory: on Cloud Run this means one instance (`max-instances=1`) so a
  session's messages reach the instance that holds it, and a restart ends open
  conversations. Case state (start tokens, chat links, case conversations, waiting replies)
  survives restarts only with `CASE_STORE=firestore`. The audit log is a local JSONL file,
  or Cloud Logging on Cloud Run (`AUDIT_SINK=stdout`).
- The HTTP API trusts the identity header set by IAP; it must only be reachable through IAP
  (Cloud Run ingress and IAP settings, managed in `lir-infra`).
- The keyword baseline misses a bare "sí" as a confirmation; Jev is expected to handle it.
- What the customer types still reaches the external models, without identifiers: the model
  must understand it. Identifier redaction is pattern based (ten or more digits, e-mails,
  CURP/RFC); an 8-digit DNI or cédula is indistinguishable from an amount and is kept. The
  merchant decision (D4) reads candidate merchant names in clear (no amounts, dates or ids).
  Closing these needs a model inside the perimeter (e.g. Vertex AI in-region or a local one).
- `CLI-DEMO-001` exists only in the demo fixture; with DuckDB use a real customer id.
