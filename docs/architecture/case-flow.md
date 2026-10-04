# How a case flows: web form → Pub/Sub → agent → Telegram

A customer reports a problem in the `lir-web` form. `lir-agent` accepts the case,
publishes it to Pub/Sub, works it as soon as Pub/Sub pushes it back, and talks to the
customer on Telegram once they tap the Start link. This page shows how those pieces are
wired, what each route trusts, and how to run the whole loop on a laptop.

## The flow in one picture

```text
lir-web (browser)                         Telegram
   │ POST /v1/cases (JWT)                     ▲  │ webhook: /start <token>, messages
   ▼                                          │  ▼
API Gateway ── verified claims ──► lir-agent (Cloud Run) ◄── /channels/telegram
                                    │   ▲          │
              archive case ◄────────┤   │ push     │ sendMessage
          (Cloud Storage inbox)     │   │ (OIDC)   ▼
                                    ▼   │       customer's chat
                            Pub/Sub topic lir-cases
                                    │
                       case state ──┴──► Firestore (tokens, links, conversations, replies)
```

1. **Submit.** The form posts the case to `POST /v1/cases` through API Gateway, which
   verifies the customer JWT and forwards its claims.
2. **Accept.** `lir-agent` validates the case, archives it in the cases inbox, publishes
   it to `lir-cases`, stores a single-use start token, and answers `202` with
   `telegram_start_url = https://t.me/<bot>?start=<token>`.
3. **Work.** Pub/Sub pushes the case to `/pubsub/push`. The agent starts a conversation
   for the case, runs its first turn and queues the reply.
4. **Link.** The customer taps the link and presses Start. Telegram calls
   `/channels/telegram` with `/start <token>`; the agent links that chat to the case and
   sends the queued reply.
5. **Talk.** Every later message in that chat continues the same conversation.

Telegram bots cannot message someone first, which is why step 4 exists: the chat id
only becomes known when the customer presses Start.

## Routes

| Route | Called by | Trusts | Does |
|---|---|---|---|
| `POST /v1/cases` | the browser, via API Gateway | `X-Apigateway-Api-Userinfo` (gateway-verified JWT claims) | validate, check ownership, archive, publish, issue the start link |
| `POST /pubsub/push` | Pub/Sub push subscription | OIDC bearer token for `PUBSUB_PUSH_AUDIENCE` | process each case once, run the first turn, queue or send the reply |
| `POST /channels/telegram` | Telegram, via API Gateway | `X-Telegram-Bot-Api-Secret-Token` = `TELEGRAM_WEBHOOK_SECRET` | link the chat on `/start`, relay messages to the agent |
| `/v1/sessions*` | bank operators behind IAP | IAP identity header | the operator chat (unchanged) |

Routes that are not configured are not registered: no audience means no `/pubsub/push`,
no bot token or webhook secret means no `/channels/telegram`.

## Where each piece of state lives

| State | Store | Notes |
|---|---|---|
| Accepted case (full payload) | Cases inbox: Cloud Storage `cases/<case_id>.json` (`CASES_INBOX=gcs`) or a local folder | Archive only; the agent reads the case from the Pub/Sub message |
| Idempotent `202` replies | Case store | Keyed by `Idempotency-Key` (= `case_id`); bound to the customer |
| Start tokens | Case store | Stored as SHA-256, single use, expire after `START_TOKEN_TTL_MINUTES` |
| Chat ↔ case links | Case store | Latest `/start` wins for a chat |
| Case conversation (owner, session) | Case store | Created once per case: duplicates from Pub/Sub are dropped |
| Replies waiting for a chat | Case store | Popped atomically, so each is sent once |
| ADK session (the dialogue itself) | Memory of the instance | Keep `max-instances=1` until sessions move to a persistent service |

The case store is in memory by default (`CASE_STORE=memory`) and Firestore with
`CASE_STORE=firestore`, one collection per record kind under `FIRESTORE_COLLECTION_PREFIX`.

## Configuration

Every variable is documented in `app/lir-agent/.env.example`. The ones this flow needs:

| Concern | Cloud Run | Local run |
|---|---|---|
| Customer identity | `REQUIRE_IDENTITY=true` | `REQUIRE_IDENTITY=false` (trusts the form's `customer_id`) |
| Cases inbox | `CASES_INBOX=gcs`, `CASES_BUCKET=<bucket>` | `CASES_INBOX=local` |
| Publishing | `CASES_PUBLISHER=pubsub`, `GOOGLE_CLOUD_PROJECT`, `CASES_TOPIC=lir-cases` | same, plus `PUBSUB_EMULATOR_HOST=localhost:8085` |
| Push auth | `PUBSUB_PUSH_AUDIENCE`, `PUBSUB_PUSH_SERVICE_ACCOUNT` | `PUBSUB_VERIFY_TOKEN=false` (the emulator signs nothing) |
| Case store | `CASE_STORE=firestore` | same, plus `FIRESTORE_EMULATOR_HOST=localhost:8086` |
| Telegram | `TELEGRAM_BOT_USERNAME` (no `@`), `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` from Secret Manager | same values in `.env` |
| Browser calls | empty `CORS_ORIGINS` (the gateway answers CORS) | `CORS_ORIGINS=http://localhost:<web port>` |

## Run the whole loop locally

1. Start the emulators (Docker, host network so the push reaches the agent past a host
   firewall):

   ```sh
   scripts/pubsub-emulator.sh up      # topic lir-cases + push to localhost:8080/pubsub/push
   scripts/firestore-emulator.sh up   # Firestore on localhost:8086
   ```

2. Start the agent with the local values from the table above:

   ```sh
   cd app/lir-agent
   CASES_PUBLISHER=pubsub PUBSUB_EMULATOR_HOST=localhost:8085 GOOGLE_CLOUD_PROJECT=lir-local \
   PUBSUB_VERIFY_TOKEN=false CASE_STORE=firestore FIRESTORE_EMULATOR_HOST=localhost:8086 \
   uv run lir-agent-api
   ```

3. Expose it to Telegram and register the webhook (once per tunnel URL):

   ```sh
   ngrok http 8080
   curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -d url=https://<tunnel>/channels/telegram -d secret_token=$TELEGRAM_WEBHOOK_SECRET
   ```

4. Serve `lir-web` (`python3 -m http.server <port>`), set `casesEndpoint` to
   `http://localhost:8080/v1/cases`, submit a case and tap **Continue on Telegram**.

Expected: the form shows the folio; the agent logs `case_received`, `case_processed` and
`case_reply_queued`; after Start, `telegram_linked` and `case_reply_sent`, and the reply
arrives in the chat.

## Known gaps

- If `sendMessage` fails, the popped reply is lost and `case_reply_sent` is still audited.
- ADK sessions live in memory, so a restart ends ongoing conversations.
- Pub/Sub dead-lettering and the Slack hand-off are not wired yet.
