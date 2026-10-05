# Data sent to third parties

What leaves the bank's perimeter when the agent works a case, and what does not. The code is
the source of truth: `llm_exposure` in `app/lir-agent/src/lir_agent/resources/policy.yaml`,
`domain/pseudonyms.py` and the ADK callbacks in `interface/adk/callbacks.py`.

## Never sent to a model

- Customer id, transaction ids, identity documents, contact details, the customer's name,
  balances.
- Risk signals: `fraud_score`, the transaction's country and the policy rule ids stay in
  code.
- The `is_fraud` evaluation label (never read by the agent).
- Transactions are shown to the model through opaque session references (`T1`, `T2`). Cases
  from the web form start with their charges as references too, never as merchant, amount or
  date.

## How bank records reach the model: placeholders

The fields in `llm_exposure.pseudonymized_fields` (merchant, amount, transaction dates,
registration date) leave only as placeholders such as `[[COMERCIO_1]]`, `[[MONTO_1]]` or
`[[FECHA_1]]`. The table that maps each placeholder to its value lives in the session state,
inside our infrastructure; a placeholder means nothing outside the session.

| Step | Callback | What happens |
|---|---|---|
| Tool result | presenter | Allowlisted fields only; pseudonymized fields as placeholders |
| Every model request | `before_model` | The request is rebuilt from copies: identifiers are redacted from the customer's words, and values the customer already read (in earlier replies, in their own messages) go back as placeholders. The session history itself is not changed |
| Tool call | `before_tool` | Placeholders in the arguments are resolved, so tools and the handoff packet work on real values |
| Model reply | `after_model` | Placeholders are resolved, **then** the output guard checks the text the customer will read. A placeholder the session never issued is shown as `—` and audited |

Re-concealing the history on every request is what keeps the protection after the first
turn: replies hold real values once resolved, and ADK sends them back to the model as
conversation history.

### Identifiers in the customer's words

Card, account (CLABE, CBU), phone and CPF numbers (ten or more digits, a number with exactly
two decimals counts as an amount), e-mails, CURP and RFC are replaced by `[[DATO_PROTEGIDO]]`
before any model reads the message. The replacement is irreversible: the value is not stored
and never comes back, because the agent never needs it. The audit log records only how many
were removed.

## Sent today

| Data | Recipient | Purpose |
|---|---|---|
| Customer's message, identifiers redacted, known merchants as placeholders | Conversation model (`LLM_MODEL`), decision model (Jev on Cloudflare Workers AI, or `DECISIONS=llm`) | Understand the request; typed decisions |
| Candidate charges: placeholders for date, amount and merchant; currency, category, status, type and channel in clear | Conversation model | Identify and explain the charge |
| Customer profile: segment, status, country, marketing opt-in; registration date as a placeholder | Conversation model | Tailor the reply |
| Candidate merchant names and categories (no amounts, dates or ids), next to the customer's description | Decision model (D4, "which merchant") | Match the description to a charge; this decision needs the names |
| The whole conversation, in clear: the customer's messages and the resolved replies | Telegram (when `telegram_enabled`) | The customer's chat channel |
| Handoff id, policy rule, lane and the case file link (behind IAP), no customer data | Slack (when `SLACK_WEBHOOK_URL` is set) | Tell the team a case was handed off |

## Residual risk

- What the customer types still reaches the models (without identifiers): the model has to
  understand it.
- Redaction is pattern based. An 8-digit DNI or cédula cannot be told from an amount and is
  kept; names the customer types are kept.
- D4 reads candidate merchant names in clear.
- Telegram receives every message in clear. A bank channel needs a provider under contract
  (e.g. WhatsApp Business through a BSP); Telegram is a demo channel.

A model inside the perimeter closes the first three: the conversation and decision models are
LiteLLM strings, so a model hosted in-region (e.g. on Vertex AI) or locally replaces the
hosted API without code changes.

## Jurisdiction

| Country | Law |
|---|---|
| Mexico | LFPDPPP |
| Argentina | Ley 25.326 |
| Colombia | Ley 1581 de 2012 |
| Brazil | LGPD (Lei 13.709/2018) |

- Banking secrecy and cloud outsourcing rules apply on top of data protection (e.g. Mexico's
  Ley de Instituciones de Crédito art. 142, Argentina's Ley 21.526 art. 39). Legal must
  validate every article listed here.
- Infrastructure runs in `us-east1`; the region is a Terraform variable (`region` in
  Lir-team/lir-infra). Regions exist in Querétaro (`northamerica-south1`), São Paulo
  (`southamerica-east1`) and Santiago (`southamerica-west1`).
- Country-specific claim deadlines are not modeled yet; they belong in the versioned policy
  as rules on `customer_country`.
