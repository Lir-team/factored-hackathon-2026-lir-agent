# Data sent to third parties

What leaves the bank's perimeter when the agent works a case, and what does not.

## Never sent to a model

- Customer id, transaction ids, identity documents, contact details, names, balances.
- The `is_fraud` evaluation label (never read by the agent).
- Transactions are shown to the model through opaque session references (`T1`, `T2`).

Enforced in code: `llm_exposure` in `app/lir-agent/src/lir_agent/resources/policy.yaml` is
the allowlist of fields a tool result may carry, applied by the presenter before any result
reaches the model.

## Sent today

| Data | Recipient | Purpose |
|---|---|---|
| Customer's message text | Conversation model (`LLM_MODEL`), decision model (Jev or `DECISIONS=llm`) | Understand the request; typed decisions |
| Candidate charges: date, amount, currency, merchant, category, status, type, channel | Conversation model | Identify and explain the charge |
| Customer profile: segment, status, registration date, country, marketing opt-in | Conversation model | Tailor the reply |

The message text is sent as written; it may contain personal data the customer chose to type
(card numbers, document numbers, names).

## Planned: pseudonymization layer

Personal and banking values are replaced by placeholders before any model call and restored
inside the bank before the reply reaches the customer:

- Inbound: card, document, email and phone patterns in the customer's text become `[CARD_1]`,
  `[DOC_1]`... (`before_model` callback).
- Tool results: merchants and amounts become `{{MERCHANT_1}}`, `{{AMOUNT_1}}`; placeholders in
  tool arguments are resolved back before the tool runs.
- Outbound: placeholders in the reply are restored after the output guard (`after_model`).

## Jurisdiction

| Country | Law |
|---|---|
| Mexico | LFPDPPP |
| Argentina | Ley 25.326 |
| Colombia | Ley 1581 de 2012 |
| Brazil | LGPD (Lei 13.709/2018) |

- Infrastructure runs in `us-east1`; the region is a Terraform variable (`region` in
  Lir-team/lir-infra). Regions exist in Querétaro (`northamerica-south1`), São Paulo
  (`southamerica-east1`) and Santiago (`southamerica-west1`).
- The conversation model is a LiteLLM string; a model hosted in-region (e.g. on Vertex AI)
  replaces the hosted API without code changes.
- Country-specific claim deadlines are not modeled yet; they belong in the versioned policy
  as rules on `customer_country`.
