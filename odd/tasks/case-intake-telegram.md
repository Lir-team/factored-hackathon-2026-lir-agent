# Case intake and Telegram follow-up

Locator: `odd/tasks/case-intake-telegram.md` · Engram mirror: `odd/case-intake-telegram/tasks`
Branches: one descriptive branch per task, stacked (T1: `refactor/shared-conversation-service`) · Status: in progress (T1)

## Objective

A customer files a complaint (fraud, unrecognized charge, wrong fee...) in the
`lir-web` form. The case reaches `lir-agent`, the agent works it, and when it
needs more information it asks the customer on Telegram and continues the
conversation there.

## Why

The target architecture (`materials/lir-Diagrama.drawio.xml`, root `README.md`
lines 24-41) has `/v1/cases`, `/pubsub/push` and `/channels`, but only
`/health` and the IAP operator routes `/v1/sessions*` exist today.

## Flow (follows the diagram, plus the Start link)

1. `lir-web` -> API Gateway (customer JWT, rate limit) -> `POST /v1/cases`.
2. `lir-agent` validates the case, stores it in `cases-inbox` (Cloud Storage),
   issues a single-use start token and returns `202` with
   `telegram_start_url = https://t.me/<bot>?start=<token>`.
3. GCS `OBJECT_FINALIZE` -> Pub/Sub -> push with OIDC -> `/pubsub/push`: the
   agent starts a case-bound session and runs the first turn. A reply for the
   customer is queued until a Telegram chat is linked.
4. Customer taps the link -> Telegram -> API Gateway -> `/channels/telegram`
   (secret token header). `/start <token>` links `chat_id` to the case, burns
   the token and flushes the queued reply. Later messages continue the session;
   replies go out with Bot API `sendMessage`.

Telegram bots cannot message a user first, so step 4 is required: the
`preferred_contact.value` handle from the form is informational only.

## Scope

In: the three routes, their ports and adapters, case-bound sessions, settings,
docs and tests in `app/lir-agent`.
Out: moving the HTTP API to its own package (after the hackathon), Terraform /
API Gateway config (`lir-infra`), `lir-web` changes (handed over as a contract
note), WhatsApp, email and phone channels, Slack escalation.

## Constraints and decisions

- Hexagonal: new ports in `application/ports.py`; GCS, Firestore and Telegram
  are infrastructure adapters with in-memory twins for tests and local runs.
- Customer identity comes from the gateway-verified JWT claims
  (`X-Apigateway-Api-Userinfo`); `customer.customer_id` must match it, and every
  `transaction_id` must belong to that customer (contract rule).
- `/pubsub/push` verifies the Pub/Sub OIDC token with `google-auth`;
  `/channels/telegram` verifies `X-Telegram-Bot-Api-Secret-Token`. Neither uses
  the IAP operator dependency.
- Pub/Sub is at-least-once: processing is idempotent by `case_id`.
- Case-bound sessions need a TTL longer than the 15-minute operator TTL
  (`before_agent` enforces it), configurable.
- The agent conversation logic moves from `interface/http/gateway.py` to
  `application/` so HTTP, Pub/Sub and Telegram share it; the session owner
  becomes generic (operator or case).
- Contract deviation to report to `lir-web`: the diagram ingests through GCS
  notifications, not a direct publish to `lir-cases`; case attributes travel as
  object metadata. The 202 body gains `telegram_start_url`.

### Decision: persistence (approved 2026-10-04)

State that must survive between the case arriving and the customer pressing
Start: case record, start token, `chat_id` link, queued reply, ADK session.
Cloud Run can recycle the instance in between, so memory alone loses it.
Approved: Firestore (already in the diagram) for case, token, link and
queued reply; ADK sessions stay in memory with `min-instances=max-instances=1`
for the demo, and a lost session is rebuilt from the case record.

## Tasks

- [x] **T1 Shared conversation service.** A `Conversations` port in
  `application/ports.py` (owner-agnostic start/send, TTL and auth method per
  call) implemented with ADK in `interface/adk/`; the HTTP routes depend on the
  port and `/v1/sessions` behaves as today. Application code stays free of ADK
  imports. Route: delegated (writer trigger: 2+ non-trivial files).
- [x] **T2 `POST /v1/cases`.** Schema validation against the `lir-web` schema
  (v1.1), 422 error shape from the contract, identity and ownership checks,
  `Idempotency-Key` replay of 2xx, `CaseInbox` port (GCS + local adapters),
  start token issued, 202 with `case_id`, `folio`, `status`,
  `telegram_start_url`. Route: delegated.
- [x] **T3 Case store.** Port for case record, start token, chat link and
  queued replies; Firestore + in-memory adapters. Route: delegated.
- [x] **T4 `/pubsub/push`.** OIDC check, GCS notification parsing, load case,
  dedupe by `case_id`, case-bound session with long TTL, first agent turn,
  reply queued or sent. Route: delegated.
- [x] **T5 Telegram channel.** `Messenger` port + Bot API adapter (httpx);
  `/channels/telegram` webhook: secret check, `/start <token>` linking and
  flush, message relay to the session, reply via `sendMessage`. Route:
  delegated.
- [ ] **T6 Config and docs.** Settings, `.env.example`, `pyproject` deps,
  `app/lir-agent/README.md`, contract note for `lir-web`. Route: inline.

## Acceptance criteria

- A valid case POSTed with gateway claims returns 202 and a start link; an
  invalid one returns 422 with field codes; a foreign `customer_id` is rejected.
- A Pub/Sub push for that case runs the agent once, even if delivered twice.
- `/start <token>` links the chat once; a reused or unknown token is refused.
- A customer message on Telegram reaches the same session and the reply is sent
  back to that chat.
- Requests without a valid OIDC token or Telegram secret are rejected.

## Checks

`uv run pytest`, `uv run ruff check`, `uv run pyright` from `app/lir-agent`.
Test-first per task with in-memory adapters and FastAPI `TestClient`; no real
GCP or Telegram calls in tests.

## Delivery

Forecast: well over 400 authored lines across T1-T6, so the `ask-on-risk`
strategy applies: chain strategy to be chosen before the first PR.

## Progress

- 2026-10-04: plan created from the diagram, `lir-web` contract and a mapping
  of `lir-agent`. Awaiting the open decision and go-ahead.
- 2026-10-04: `lir-web` confirmed as the entry point. Frontend brief written to
  `lir-web/docs/goal-telegram-start-link.md`. Contract committed there:
  `202` adds `telegram_start_url` (only `https://t.me/` URLs, present when
  `preferred_contact.channel` is `telegram`); requests carry
  `Authorization: Bearer <customer JWT>`; cross-origin through API Gateway, so
  the `OPTIONS` preflight must be answered.
- 2026-10-04: T1 done (delegated writer). `Conversations` port + errors in
  `application/ports.py`, `AdkConversations` in `interface/adk/conversations.py`,
  `interface/http/gateway.py` removed. RED: new per-call TTL test failed with
  ImportError before the change. Checks: pytest 179 passed, pyright 0 errors,
  ruff check clean, ruff format clean on touched files (6 untouched base files
  were already unformatted). Audit field `operator` renamed to `owner` on
  `session_started`.
- 2026-10-04: T1 review granted and approved (one reliability lens, receipt
  acknowledged). Non-blocking follow-ups: audit field rename `operator` ->
  `owner` (warning), removed public exports from `interface/http`, TTL passed
  to `start` is not validated. Separate change: `.env.example` completed on
  branch `chore/complete-env-template` (`f2975c2`).
- 2026-10-04: branch re-reviewed after the doc note (granted, approved,
  acknowledged; same two non-blocking notes). T2 started on
  `feat/case-intake-endpoint`, stacked on `refactor/shared-conversation-service`.
  Route: delegated (writer trigger). T2 adds a minimal `CaseStore` port with an
  in-memory adapter (idempotency replay, start tokens); T3 adds Firestore.
- 2026-10-04: T2 done (delegated writer). `SubmitCase` use case, `CaseInbox`
  (GCS + local) and `CaseStore` (in-memory) ports, schema copied from lir-web
  v1.1. RED: both new test modules failed at import before the change. Checks:
  pytest 213 passed (34 new), ruff check clean, ruff format clean on touched
  files, pyright 0 errors. Notes for later: `date-time` formats are not checked
  (needs `rfc3339-validator`); `create_app` builds a second container for case
  intake besides the agent's one; Cloud Run must set `CASES_INBOX=gcs`;
  Firestore `consume_start_token` must be transactional (T3).
- 2026-10-04: receipt-driven review turned off by the user (global). CORS made
  opt-in (`CORS_ORIGINS`, `03ea3d6`) for local runs with lir-web; lir-web mock
  aligned with the fixture on its branch `fix/mock-data-matches-agent-fixture`.
  Local end-to-end submit verified (202, case stored). Reordered: T5 before T3
  and T4, because the bot answering is the visible part of the demo; the
  in-memory store with `max-instances=1` is enough until T3. T5 on
  `feat/telegram-channel`, stacked on `feat/case-intake-endpoint`. Until T4,
  `/start` triggers the agent's first turn from a case summary kept with the
  start token.
- 2026-10-04: T5 done (delegated writer). Webhook `/channels/telegram`
  (secret header, update_id dedupe), `AnswerTelegramMessage` use case,
  `Messenger` port + Bot API adapter (httpx), `CaseStart` carried with the start
  token, `ChatLink` per chat; owner `case:<case_id>`, auth method
  `telegram_case_link`. RED: 4 collection errors before the change. Checks:
  pytest 259 passed (44 new), ruff clean, pyright 0 errors. Open risks: httpx
  logs the bot token in URLs at DEBUG; a failing `conversations.start` burns the
  token and returns 500; once T4 runs the agent from Pub/Sub, `/start` must stop
  running the first turn.
- 2026-10-04: user asked to finish T3 and T4 and prove the flow end to end with
  the Pub/Sub emulator (Docker). Decision (user): `POST /v1/cases` publishes the
  case directly to topic `lir-cases` (as the lir-web contract already says);
  the Cloud Storage inbox stays as the case archive, and the GCS notification
  path is dropped. Order: T4 on `feat/pubsub-case-processing`, then T3
  (Firestore adapter for the final store interface, tested on the Firestore
  emulator) on `feat/firestore-case-store`, then the end-to-end run.
- 2026-10-04: T4 done (delegated writer). `/v1/cases` archives, publishes to
  `lir-cases` (ordering key = customer_id; publish failure -> 503, no receipt),
  then issues the token. `/pubsub/push` verifies the OIDC token (or
  `PUBSUB_VERIFY_TOKEN=false` for the emulator), dedupes with a first-writer-wins
  `add_conversation`, runs the first turn and queues the reply; `/start` only
  links and flushes queued replies. Emulator scripts: `scripts/pubsub-emulator.sh`,
  `scripts/firestore-emulator.sh` (host network: ufw blocks docker0). RED: 4
  collection errors. Checks: pytest 284 passed, ruff clean, pyright 0 errors.
  Pending: lir-web `docs/case-contract.md` must go back to direct publishing.
- 2026-10-04: T3 done (delegated writer). `FirestoreCaseStore` behind
  `CASE_STORE=firestore`: tokens stored by sha256, transactional consume and
  reply pop, first-writer-wins conversations. Same contract tests run against
  both stores. RED: ImportError on `build_case_store`. Checks: pytest 293 passed
  + 12 skipped without the emulator, 305 passed with it; ruff clean, pyright 0.
- 2026-10-04: local end-to-end run passed (Pub/Sub + Firestore emulators, real
  model `openai/gpt-4o`, fake bot token): POST /v1/cases 202 with start link ->
  published -> pushed -> agent first turn (escalate, handoff) -> reply queued in
  Firestore -> simulated `/start <token>` linked the chat and popped the queue ->
  duplicate update ignored -> reused token refused -> follow-up text answered by
  the agent. Tokens never appeared in logs. Found: when sendMessage fails the
  popped reply is lost and `case_reply_sent` is still audited (follow-up).
  Pending: real Telegram delivery (needs a public URL or a polling bridge) and
  lir-web `docs/case-contract.md` back to direct publishing.
