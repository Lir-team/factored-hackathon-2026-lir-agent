# Proposal: "I don't recognize this charge". Clarify before disputing

> **Status: proposal for a team decision.** It develops option A from
> [`data/reports/insights.md` §6](../../data/reports/insights.md#6-options-to-decide),
> framed within complaint intake (option B) so it does not depend on an inference.
> English quotes are verbatim from the *Factored AI & Data Hackathon 2026 — Problem Statement*
> (from here on, **[Bases]**). Figures come from `insights.md` and are **offline measurements on synthetic data**.

## 1. Summary

### The idea in one sentence

> When a customer says "I don't recognize this charge", the agent **finds the charge, clarifies it with evidence
> and opens a dispute only when appropriate**. Three pieces share the work:
> **the LLM talks, Jev decides quickly and with a probability, and the code authorizes.**

| Piece | What it does | What it does **not** do |
|---|---|---|
| **LLM** (conversation) | Understands the customer in ES/PT, extracts amount, date and merchant, asks for clarification and writes the reply citing the evidence | Does not decide routing, does not authorize, does not invent rules |
| **Jev** (typed decisions, [§5](#5-jev-the-fast-decision-layer)) | On every turn, answers closed questions with a calibrated probability: intent, does the customer ask for a human?, do they suspect theft?, which merchant are they describing? | Does not look at amounts or dates, does not filter attacks, does not decide eligibility |
| **Code** (tools + policy) | Authenticates, limits every query to the session's customer, filters by amount and date, computes the evidence and applies the versioned dispute policy | Does not talk |

The technical bet: separating **deciding** from **talking** makes every agent decision measurable
(probability, threshold, accuracy), cheap (~100 ms, US$0.042 per million tokens) and auditable,
instead of being buried in the LLM's prose. This directly answers
[Bases]: "Make explicit trade-offs across autonomy, accuracy, latency, cost, and human oversight".

### The workflow

An agent that serves the **"I don't recognize a charge"** case in ES and PT. First it identifies the specific
transaction from the customer's vague description. Then it gathers verifiable evidence (duplicates,
usual merchant, country/city, digital session, transaction status) and applies a **deterministic
policy outside the model**. With that, it does one of three things:

1. **explains** the charge, if the evidence clears it up;
2. **opens the dispute**, if it is eligible, after the customer confirms;
3. **hands off to a human** (likely fraud, high amount, missing data), with a handoff packet.

The business goal is to **resolve the case before it becomes a formal complaint that stays open**.
The technical goal is to demonstrate the six competencies that [Bases] asks for on a single deep workflow.

## 2. Why this workflow: evidence and bases

[Bases, *Scope*] asks us to choose a coherent workflow and explicitly names this one:

> "Select a coherent workflow, such as account or payment inquiries, card-service support,
> **transaction-dispute intake**, or credit-product information and eligibility support."

and makes clear that depth matters more than quantity:

> "depth, demonstrated behavior, and engineering judgment determine the score; implementing more
> workflows does not earn an automatic bonus."

[Bases, *What your solution should demonstrate* §1] requires the problem to be backed by data:

> "Analyze contact reasons, relevant demand patterns, data quality, and operational constraints.
> Use this evidence to prioritize the workflow and define the intended customer and business outcomes."

### Measured facts (from `insights.md`)

| Fact | Figure | Source |
|---|---|---|
| Complaint is the reason with the most unresolved contacts | 41.2% of unresolved; FCR 43.6% | §1 |
| In formal complaints (PQR), charge disputes are the largest block | 40.6% of PQR with a subcategory (`unrecognized_charge` + `improper_fee`; 36.5% if those without a subcategory are counted) | §3 |
| Those disputes stay open | ~75% open; median 15–16 days | §3 |
| Data for the tools (`transactions`, `products`, `customers`) | Owner↔product and currency↔product integrity at 100% | §5 |
| Human agents who speak Portuguese | 129 of 1,200 (10.8%) | §2 |

### What we **cannot** claim

- That "Queja" (Complaint) calls are disputes. Calls and PQR **cannot be linked**
  (0.4% vs 0.36% in the placebo window; `insights.md` §4). We do not multiply 41.2% by 40.6%.
- That is why the agent enters through **complaint intake** (justified only with measured facts) and goes deep on
  charge disputes, the largest subcategory on the PQR side and the one with data suitable for verification.

## 3. Jobs to be done

Format: *When [situation], I want [motivation], so that [expected outcome].* Each job is linked to the
[Bases] requirement that supports it and to how we will measure it.

### 3.1 Customer (main job)

| # | Job | Type | [Bases] requirement | How it is measured |
|---|---|---|---|---|
| C1 | When I see a charge I don't recognize, I want to know **exactly what it is** without having to find it myself in the statement, so that I can relax or act quickly. | Functional | §2 "ground factual responses in permitted account, transaction, or policy information" | Recall@k / MRR of transaction identification; safe resolution rate |
| C2 | When the charge really is an error or fraud, I want to **start the dispute in the same conversation** and know what will happen and when, so that I don't repeat my story in another channel. | Functional | §3 "Define … which actions require confirmation"; §2 "report only actions whose outcomes the system has verified" | Disputes opened with explicit confirmation and an outcome verified by the tool |
| C3 | When I write in Portuguese, or mix languages, I want to be understood just as well, so that I don't get worse service because of my language. | Functional / fairness | *Scope*: "Demonstrate interactions in Spanish and Portuguese"; *Evaluation*: "Compare relevant service outcomes by language" | Metrics by language with n and intervals; gap analysis |
| C4 | When I think I was robbed, I want to feel that **someone competent is taking charge**, so that I don't feel alone against the bank. | Emotional | §3 "when it must abstain or transfer to a human" | Escalation quality: missed and unnecessary handoffs |
| C5 | When I talk to the bank, I want nobody else to be able to see or touch my data, so that I can trust the channel. | Emotional / security | *Data boundaries*: "a national ID or customer number alone does not prove identity" | Unsafe outcomes (disclosures or unauthorized actions) with count and denominator |

### 3.2 Human agent (back office / fraud)

| # | Job | [Bases] requirement | How it is measured |
|---|---|---|---|
| H1 | When a handed-off case reaches me, I want to receive **the request, the verified facts, the actions taken, the evidence and what is still unresolved**, so that I don't have to ask the customer everything again. | §3 "Provide the human agent with the request, verified facts, actions taken, supporting evidence, and unresolved questions." | Packet completeness against a list of required fields (deterministic check) |
| H2 | When the customer speaks Portuguese and I don't, I want the packet translated and marked as machine translation, so that I can serve them without waiting for one of the 129 agents who speak PT. | Measured operational constraint (`insights.md` §2); *Scope*: "report limitations in … language coverage" | Handed-off PT cases with a usable packet; reported limitations |
| H3 | When I audit a case, I want to see **which sources, rules and records** led to each decision, so that I can justify it without relying on the model's hidden reasoning. | §6 "hidden model chain-of-thought is not an audit artifact" | Every decision with traces: tool calls, rule version, evidence |

### 3.3 Bank: operations, risk and compliance

| # | Job | [Bases] requirement | How it is measured |
|---|---|---|---|
| B1 | When an "I don't recognize" arrives, I want to resolve on first contact whatever can be explained, so that the backlog of open formal complaints goes down. | Intro: "measure whether your approach improves service quality and operational efficiency" | Safe automated resolution vs baseline; containment reported **separately** ("Containment alone does not demonstrate that the problem was solved") |
| B2 | When the agent acts, I want **permissions and policy to be enforced in code**, not in the prompt, so that a manipulated conversation cannot bypass them. | §3 "Enforce permissions and policy outside model-generated prose" | Attack suite: direct and indirect prompt injection, expired session, access to another customer |
| B3 | When I deploy, I want to know cost, latency and capacity limits, so that I can decide whether this scales. | *Evaluation*: "End-to-end p50/p95 latency and cost per attempted case and per successful automated resolution" | p50/p95, cost per attempted case and per successful resolution ("not defined" if there are no resolutions) |
| B4 | When the data changes (late arrivals, duplicates), I want the tools to answer with correct and fresh data, so that we don't answer the customer with stale information. | §4 "repeatable data preparation with contracts, quality checks, lineage, and an update/freshness policy" | Existing pipeline (`data/pipelines`) + labeled update fixture ("If only static data is supplied, demonstrate update correctness with a clearly labeled test fixture") |

## 4. Proposed solution

```
Customer (ES/PT)
   │
   ▼
[Authenticated test session] ──► customer_id from the SESSION (never from the text)
   │
   ▼
[Conversational LLM] ── understands, extracts amount/date/merchant, asks for clarification and writes. Does NOT decide or authorize.
   │                        ▲
   │                        │ typed decisions with probability (in parallel, ~100 ms)
   │                  [Jev] ── intent · asks for a human? · suspects theft? · which merchant?
   │                        │   confidence < threshold → clarify or hand off
   │   tool calls
   ▼
[Tool layer with per-session permissions]
   ├─ find_candidate_transactions(slots) → candidates by amount/date (code) + merchant (Jev)
   ├─ get_evidence(txn_id)              → verifiable facts
   ├─ policy.evaluate(evidence)         → EXPLAIN | DISPUTE | HAND OFF   (deterministic, versioned)
   ├─ open_dispute(txn_id, confirm=True)→ id + verified status (documented mock)
   └─ handoff(packet)                   → packet for a human
   │
   ▼
[Traces and execution log] → audit, metrics, cost, latency
```

### 4.1 What is AI and what is deterministic

[Bases, *Think beyond the demo*]: "Justify where AI is appropriate, where deterministic logic is preferable".

| Piece | Approach | Why |
|---|---|---|
| Understand the customer's description, ask for clarification, write in ES/PT | LLM | Free-form, multilingual and ambiguous language |
| Extract amount and date ("como 50 lucas" (about 50 grand), "el martes" (on Tuesday)) | LLM → validated by code | Jev is not reliable with numbers or dates; code normalizes and validates |
| Intent, asks for a human?, suspects theft?, which merchant is described? | **Jev** (pretrained, evaluated) | Closed decisions with calibrated probability, fast and cheap: they allow explicit thresholds |
| Filter transactions by amount and date | Code | Exact arithmetic, no model |
| Authentication, per-customer scope, permissions | Code | [Bases]: "Enforce access … in the service or tool layer" |
| Dispute eligibility, handoff thresholds | Versioned rules (declared synthetic policy) | Auditable and not negotiable through conversation |
| Evidence signals (duplicate, country, session, status) | SQL/code queries | They are facts, not judgments |

### 4.2 Candidate evidence signals

All of them come from existing columns in the contracts. **It still needs to be verified** in `samples/` that the
synthetic distribution makes them informative. Those that are not will be replaced by a fixture
labeled as synthetic, per [Bases, *Data boundaries*]: "Identify which inputs are real,
de-identified, synthetic, or team-generated".

| Signal | Columns | Reading |
|---|---|---|
| Duplicate charge | `transactions.amount`, `merchant_name`, `transaction_date` | Same merchant and amount within a short window |
| Usual merchant | customer's `merchant_name` history | "Ya pagaste aquí N veces" (You have already paid here N times) |
| Transaction status | `transaction_status` (Approved/Declined/Pending/Reversed) | A Pending or Reversed charge is explained, not disputed |
| Location | `transaction_country/city`, `customers.country/city` | Outside the customer's country → risk |
| Nearby digital session | `digital_events.session_id`, `event_type=Login`, `ip_country` | There was activity by the customer themselves near the charge |
| Risk | `fraud_score`, `is_fraud` | Fraud handoff threshold. `is_fraud` **only** as an evaluation label, never as an online feature (leakage) |

### 4.3 Three required cases

[Bases, *Scope*]: "Include a normal resolution path, an ambiguous or unsupported request, and a case requiring human intervention."

| Case | Example | Expected outcome |
|---|---|---|
| Normal | "No reconozco un cobro de 12.990 del día 3" (I don't recognize a 12,990 charge from the 3rd) → usual merchant, approved, no risk signals | Explain with cited evidence; no dispute |
| Ambiguous | "Me cobraron algo raro la semana pasada" (I was charged something weird last week) → 4 candidates | Ask for clarification (amount, merchant, date) before acting |
| Unsupported | "Quiero un aumento de cupo" (I want a credit limit increase) | State the scope and route to the right channel |
| Human | High charge in another country, high `fraud_score`, no customer session | Nothing is promised; handed off to fraud with a packet |

## 5. Jev: the fast decision layer

### 5.1 What it is and why we use it

[Jev](https://en.wikipedia.org/wiki/Jev_%28AI_model%29) is a model from TypeSafe AI, in early access since
15-Sep-2026. **The team already has access.** **It does not generate text:** it receives a *state* (text or name-value pairs) and typed questions, and
returns answers with a probability ([Cloudflare docs](https://developers.cloudflare.com/ai/models/typesafe/jev/)):

| Question type | Returns | Use in this workflow |
|---|---|---|
| `noul` (yes/no) | probability 0–1 | asks for a human?, expresses suspicion of theft?, confirms what we showed them? |
| `choice` | distribution over options | intent; which candidate merchant is described |
| `score` | value on a described scale | (not used in the MVP) |

Why it fits [Bases]:

- **Explicit, justifiable thresholds.** [Bases §4] asks to "justify … thresholds". With one probability per
  decision, the threshold is chosen on validation and the coverage/accuracy curve is reported, instead of trusting
  a prose answer.
- **Latency and cost.** Reported figures are 70–500 ms per call, questions evaluated in parallel and US$0.042 per million
  input tokens (output free). [Bases, *Evaluation*] asks for cost per case and p50/p95.
- **Separates deciding from talking.** Every decision is stored in the trace as `(question, answer, probability,
  threshold, version)`. That is the execution log that [Bases §6] accepts as an audit artifact, unlike the
  "hidden model chain-of-thought".

### 5.2 The decisions Jev makes on every turn

All of them are made **on the customer's text** (and, in D4, the candidate merchant names), in a single
parallel call:

| # | Question | Type | If confidence is low |
|---|---|---|---|
| D1 | Intent: `cargo_no_reconocido` · `cobro_indebido` · `consulta_movimiento` · `otra_queja` · `fuera_de_alcance` | choice | Ask for clarification; if it persists, hand off |
| D2 | Does the customer explicitly ask to speak with a person? | noul | When in doubt, offer the handoff |
| D3 | Does the customer say their card or data was stolen? | noul | When in doubt, treat it as possible fraud (hand off) |
| D4 | Which of these candidate merchants is the customer describing? (`merchant_name` + `merchant_category`, **no amounts or dates**) | choice | Show the candidates and ask |

**Asymmetric** thresholds based on the cost of the error: in D2 and D3 a false negative (not handing off someone who needs it)
is worse than a false positive, so the threshold to hand off is low.

### 5.3 What Jev does **not** do, and why

Its own documentation states that Jev "is not good with numbers, dates or 'adversarial content'"
([Simon Willison, 21-Sep-2026](https://simonwillison.net/2026/Sep/21/jev/)). Therefore:

- **Amounts and dates:** the LLM extracts them and the code validates and filters them. Jev never compares amounts.
- **Prompt injection:** the defense is structural. Permissions live in the tool layer, text inside
  the data is treated as data and actions require confirmation. We do not rely on a classifier.
- **Dispute eligibility:** it is deterministic policy. Jev only routes.

### 5.4 Data that leaves the perimeter

Jev is an external API. [Bases, *Data boundaries*]: "Do not include private customer records … in external model
requests". Rule 3 of `data/AGENTS.md`: do not send raw rows.

- **Sent:** the customer's message and, for D4, only the name and category of the candidate merchants.
- **Not sent:** IDs, ID document, balances, amounts or account dates.
- The provider states "zero data retention" via Cloudflare. We document this as a provider claim, not
  as something we have verified.

### 5.5 Evaluation: the learned component of the submission

[Bases §4]: "Evaluate at least one learned component against an appropriate baseline. Use valid labels
or relevance judgments, prevent leakage, and justify representations, metrics, thresholds, and evaluation splits."
[Bases, *Architecture freedom*]: for solutions with pretrained models, rigor is demonstrated with
"component selection, relevance or intent labels, representations, leakage prevention, held-out evaluation, and error analysis".

**D1 (intent)** is evaluated as the main component, and D4 (merchant) as the secondary one, with three candidates
on the same held-out set:

| Candidate | What it is |
|---|---|
| **Baseline** | ES/PT keyword rules |
| **Jev** | `choice`/`noul` questions, with a threshold chosen on validation |
| **LLM** | The agent's own LLM, with structured output |
| **Laya** (optional, §5.7) | Open typed-decision model (multilingual checkpoint), fine-tuned with our labels and calibrated. Runs on our hardware |

- **Metrics:** per-class macro-F1, **calibration** (ECE, reliability diagram), coverage/accuracy curve
  (how much we automate at each threshold), p50/p95 and cost per 1,000 decisions. Several LLM runs to
  report variability (Jev and the rules are deterministic or nearly so).
- **Labels:** a set generated and labeled by the team in ES and PT, including Portuñol, regional slang
  (MX/CO/AR) and out-of-scope cases. The dataset's texts are not usable (`insights.md` §5). Double labeling of
  a sample to measure inter-annotator agreement.
- **Leakage:** split by **seed template**: all paraphrases of the same seed stay on the same side.
  The threshold is set on validation and reported **only once** on test. `eval/` is not used for tuning
  (`data/AGENTS.md` rule 5).
- **Error analysis** by language, country/slang and class. [Bases] asks us to investigate disparities by language.
  Jev's Portuguese support is not documented, so it **is measured**, not assumed.

### 5.6 If Jev fails

Decisions D1–D4 live behind a `DecisionModel` interface with the same signature
(typed question → answer + probability). If Jev fails, times out or runs out of quota
(it is an early-access service with no published SLA), the LLM with structured output is used. If that also fails, the agent hands off to a human (safe fallback, [Bases §6]).
The comparison in §5.5 works the same with any of the three.

### 5.7 Open option: Laya

Jev is proprietary: it does not publish weights or allow running it on our machine
([Failproof AI](https://befailproof.ai/jev/is-jev-open-source/)). The open alternative is
**Laya**, by Convai Innovations, released on 18-Sep-2026. It does the same job (`choice`, `score`
and yes/no questions with probability, without generating text), with **open weights under Apache 2.0**, and runs on our
hardware. The English version is a 421M-parameter ModernBERT-large encoder, and there is a multilingual
checkpoint based on mmBERT
([Flowtivity](https://flowtivity.ai/blog/laya-open-source-jev-alternative/)).

What independent evaluations say, and what it means for us:

| Published finding | Implication |
|---|---|
| **Useless without fine-tuning:** 0.362 accuracy on the typed-decision benchmark, below the majority class (0.461); fine-tuned it reaches 0.766 ([BestHub](https://www.besthub.dev/articles/open-source-decision-model-laya-vs-jev-speed-wins-zero-shot-fails-befced0a2228)) | We have to **fine-tune it with our labels**. Its notebook uses ~30k questions and 4–5 h on two free Kaggle T4s; we will have hundreds. This is the main risk |
| **Calibration:** ECE of 0.466 untuned, 0.081 after temperature rescaling; for the multilingual checkpoint "cannot be trusted until calibrated" | Calibration is our job (temperature scaling on validation) and it is reported |
| **Languages:** multilingual checkpoint covering more than 100 languages; 45 of 51 evaluated exceed 3× chance. ES/PT are not detailed | We use the multilingual one and **measure** ES vs PT |
| **Many options:** drops with more than 20 options (Banking77: 0.425 vs 0.870 for Jev) | Our decisions have 2 to 5 options: within its good zone |
| **Hardware:** needs a GPU; on CPU a median of 49.4 s per prediction is reported | Evaluation on GPU (Kaggle/Colab). On CPU it is not usable for the live path |
| **Context:** 1024 tokens in the multilingual one | Enough for one customer message, not for the whole conversation |

**Why it is still worth it as a candidate:**

- **Privacy:** it runs locally, so **no data leaves the perimeter** (§5.4). It is the strongest argument under
  [Bases, *Data boundaries*].
- **Shows our own ML rigor:** we do the fine-tuning, calibration and leakage control ourselves. The bases
  acknowledge it: "A model-training pipeline is one way to provide that evidence".
- **Fallback with no external dependencies:** it implements the same `DecisionModel` (§5.6), with no third-party quota or SLA.
- **Leaves a clear trade-off in the report:** Jev (ready without tuning, external) vs Laya (tuned by us,
  private, free) vs LLM (flexible, slower and more expensive) vs rules (baseline).

**Specific leakage risk:** if we expand the training set with generated paraphrases, they are
generated **only from seeds in the training split**. Validation and test are not touched.

*Discarded:* [OpenJev](https://github.com/kyegomez/open-jev) (Apache 2.0) rebuilds the architecture, but
it ships with random weights ("random weights"), has 6 commits and publishes no benchmarks.

It is optional: it goes in if the MVP with Jev and the LLM is ready before day 6.

## 6. End-to-end evaluation

[Bases, *Evaluation evidence*]: "Compare your baseline and proposed system on the same held-out workload.
Report the number and mix of cases, label quality, model and prompt versions, and repeated-run variability".

| Metric ([Bases]) | Definition in this workflow |
|---|---|
| Safe automated resolution | Eligible case that ends in the correct outcome (explain / dispute) without a human, over **all** in-scope cases, together with the attempted automation rate |
| Containment | Ends without a handoff. Reported separately and never as success on its own |
| Escalation quality | Missed and unnecessary handoffs + packet completeness |
| Unsafe outcomes | Disclosure to another customer, dispute opened without confirmation, unverified action. Count / denominator |
| Efficiency | End-to-end p50/p95; cost per attempted case and per successful resolution, with pricing assumptions |
| Fairness | All of the above by language (ES/PT) and by authorized segment, with n and significance (`_spread` in `insights.py`) |

**Required failure cases** ([Bases §5]): incorrect or missing data, expired session, unauthorized
access attempt, prompt injection (including **indirect** injection, via text in `merchant_name`),
tool failures with bounded retries, and multilingual ambiguity.

**LLM judge** (if used): a documented rubric validated against a sample labeled by humans or
deterministically, as [Bases] requires.

## 7. Path to production (what we show and what remains pending)

[Bases §6]: "Demonstrate tracing, bounded retries, safe fallback, and reproducible setup. Explain capacity
limits, monitoring, access controls, data retention, and the remaining deployment work."

- **We demonstrate:** per-conversation traces, bounded retries with fallback to handoff, reproducible setup
  and versioned policy.
- **We document as pending:** integration with a real core banking system, a real identity provider,
  data retention according to each country's regulation, and live monitoring. [Bases, *Scope*]: "an honest
  account of the work required before deployment".
- **Out of scope, per the bases:** "No live lending decisions or movement of money is required or
  authorized by this challenge." `open_dispute` is a mock with a documented contract.

## 8. Known risks and limitations

1. **Justification by inference:** mitigated by entering through complaint intake (§2).
2. **Uniform synthetic data** (`insights.md` §8): the evidence signals may not discriminate. This is
   verified in `samples/` before building; if they do not discriminate, labeled fixtures are used.
3. **There is no Portuguese text in the dataset:** all PT content is generated by the team and declared as such.
4. **`customers` quality:** `cus_document_type_matches_country` fails in 49.9%. We do not use the ID document as
   proof of identity (the test session is the identity), in line with [Bases].
5. **`was_resolved` is questionable** as a label (`insights.md` §1). The human baseline is reported with that caveat.
6. **Jev is new and external:** an early-access service with no published SLA, performance figures published by the vendor and undocumented PT support. Mitigation: swappable interface (§5.6) and our own measurement (§5.5).

## 9. Decisions we need from the team

- [ ] Do we approve this workflow (A within B) as the single focus?
- [ ] Which synthetic dispute policy do we use (time limit, amounts, eligible statuses), and who writes it?
- [ ] Size of the ES/PT labeled set and who labels it? (initial proposal: ≥300 descriptions, ≥30% PT)
- [ ] Which LLM and what cost budget per case?
- [x] Access to Jev: the team already has it.
- [ ] Quota/rate limits on our Jev account? (sets the size of the evaluation runs)
- [ ] Do we include Laya (§5.7)? It requires a GPU (Kaggle/Colab) for fine-tuning and evaluation
- [ ] Do we ask the organizers whether empty `complaints.origin_interaction_id` is intentional?
