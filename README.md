# Lir Agent

<p align="center"><img src="docs/assets/lir-rana.gif" alt="Lir" width="160"></p>

Factored AI & Data Hackathon 2026 entry: a banking customer-service agent for the
synthetic LATAM Bank dataset (v1.0.0).

The data analysis shows that complaints are the contact reason with the most
unresolved contacts, and that charge disputes are the largest block of formal
claims (PQR). The product proposal ("No reconozco este cargo") has the agent
handle that flow for a signed-in bank customer, in Spanish and Portuguese. It
finds the charge, explains it with verifiable evidence, puts a dispute to the
customer's and a specialist's approval only when it is eligible, or hands off to a human.
The LLM converses, a typed decision layer classifies (the ES/PT keyword baseline by default;
the LLM or Jev by configuration), and deterministic code authorizes. The agent app
(`app/lir-agent/`) implements this flow end to end and runs on Google Cloud over the
organizers' data (infrastructure as code in [`Lir-team/lir-infra`](https://github.com/Lir-team/lir-infra)).

**What runs today.** Decisions: the keyword baseline unless `DECISIONS=llm`;
Jev is integrated but off (no AI Gateway balance), and its probabilities are not measured.
The decision layer is evaluated against labels in
[`data/reports/decision_eval.md`](data/reports/decision_eval.md). Identity: the bank's sign-in
is mocked by a demo identity provider (a service account signs the customer's JWT); API
Gateway validates that JWT (`customer_sign_in`, on by default in `lir-infra`) and the service
takes the customer from it, never from the form. Telegram is the conversation channel; the
outcome email is sent only when SMTP is configured. Telegram voice notes are transcribed with Cloud
Speech-to-Text only when `SPEECH_TO_TEXT=google` (off by default; `lir-infra` must enable
`speech.googleapis.com` and grant `roles/speech.client` first).

## Architecture

![Lir serverless architecture on Google Cloud](docs/architecture/architecture-lir-agent.gif)

Deployed architecture: a serverless agent on Google Cloud, triggered by events (the case
form, Pub/Sub, the Telegram webhook). Services inside the grey box run on Google Cloud;
everything outside is an external system or provider. The diagram source is
[`docs/architecture/architecture-lir-agent.drawio`](docs/architecture/architecture-lir-agent.drawio).

| Zone | Services | Role |
|---|---|---|
| Customer channels | `lir-web` (Cloud Run), Telegram, Gmail (SMTP) | Statement and case form; the conversation; the outcome email |
| Security edge | API Gateway (identity provider mocked with a service account), IAP | Customer JWT and API key on the customer routes, Telegram webhook secret; IAP in front of the operator service |
| Ingestion | Pub/Sub (`lir-cases`), dead-letter topic (`lir-cases-dead-letter`), Cloud Storage (`<project>-cases`) | Carries each accepted case to the agent, buffers spikes and retries failures; the bucket archives every case |
| Agent runtime | One image (Google ADK, LiteLLM with pseudonymized data, ADK callbacks, versioned policy, DuckDB) on two Cloud Run services: `lir-agent` and `lir-agent-cases` | `lir-agent`, behind IAP: operator API (`/v1/sessions`, `/v1/handoffs`), specialist reviews (`GET /v1/approvals`, `/v1/approvals/{id}/review`) and `/backoffice`. `lir-agent-cases`, behind API Gateway and Pub/Sub: `/v1/cases`, `/v1/me/transactions`, the customer's approval card (`/v1/approvals/{id}`, `/decision`), `/pubsub/push`, `/channels/telegram` |
| Data | Data pipeline (Cloud Run job), Cloud Storage (`<project>-data`, mounted at `/mnt/data` with GCS FUSE), Firestore | Parquet read by the tools; cases, case files and approval requests shared by both services |
| Bank operations | Slack, back office (IAP) | Ticket with the reason and a rotating assignee; the specialist approves or rejects disputes |
| Audit, observability & analytics | Cloud Logging, BigQuery (`lir_analytics` views), Looker Studio | Audit receipt for every step, KPIs per day, session and turn ([setup](docs/analytics/looker-studio.md)) |
| Platform security & delivery | Secret Manager, Cloud IAM, Cloud Build, Artifact Registry, Workload Identity Federation | Keys, least-privilege service accounts, keyless GitHub deploys of the agent and lir-web |

How a case flows:

1. The signed-in customer opens `lir-web`, which reads their statement
   (`GET /v1/me/transactions`) and sends the case form (`POST /v1/cases`) through
   API Gateway with the customer's JWT.
2. `lir-agent-cases` archives the case, publishes it to Pub/Sub and returns `202`
   with a Telegram Start link; Pub/Sub pushes the case back to the agent.
   The wiring, routes and local run are in
   [docs/architecture/case-flow.md](docs/architecture/case-flow.md).
3. The agent talks to the customer over Telegram in Spanish or Portuguese. The decision
   layer returns typed decisions with probabilities (their accuracy and calibration are
   measured in `decision_eval.md`), and a versioned policy in code assigns the lane:
   explain the charge, propose a dispute, or escalate. Never a refund.
4. A dispute needs two approvals: the customer approves it in Telegram, then a specialist
   approves or rejects it in the back office. Only then is it opened, and the customer is
   told the outcome in Telegram (and by email when SMTP is configured).
5. Escalations reach the team's Slack channel with the reason, a rotating assignee and a
   link to the case file. Every step is written to the audit log, exported to BigQuery
   and charted in Looker Studio.

The LLM (OpenAI through LiteLLM, swappable by configuration) only receives
minimized data: opaque transaction references and an allowlist of fields. Bank records
(the customer's name, merchants, amounts and dates) leave only as placeholders such as
`[[COMERCIO_1]]`, resolved inside the service before the customer reads the reply, and
card, account, document and contact numbers are removed from the customer's messages
before any model reads them.

## Repository structure

```
.
├── app/
│   ├── lir-agent/       # Python agent (Google ADK + LiteLLM), managed with uv
│   ├── decision-layer/  # Typed decisions with probabilities (keyword baseline, Jev, fallback chain)
│   └── evals/           # Scenario evals from the jobs to be done (promptfoo runner, code graders)
├── data/         # Data lake, pipeline (raw -> staging -> curated), contracts, reports
├── docs/         # Product proposal and architecture diagram (docs/architecture/)
├── .github/      # deploy-agent.yml: build and roll out the agent image (see Deploy)
├── scripts/      # check.sh: does everything still work?
└── .githooks/    # Git hooks: secret scan on commit, no direct push to main
```

## Data pipeline (`data/`)

Layered flow where each layer is regenerated from the previous one with
versioned code. Stages, as wired in `data/pipelines/__main__.py`:

| Stage | Module | Output |
|---|---|---|
| Ingest (optional, `--ingest`) | `pipelines/ingest.py` | S3 to `raw/` + `manifests/ingest/` |
| Staging | `pipelines/staging.py` | `staging/` (typed Parquet) + `manifests/staging/` |
| Quality | `pipelines/quality.py` | `reports/data_quality.md` + `manifests/quality/` |
| Curated | `pipelines/curated.py` | `curated/` + `manifests/curated/` |
| Insights | `pipelines/insights.py` (calls `figures.py`) | `reports/insights.md` + `reports/figures/*.png` |

Install and run (from `data/README.md`; the venv activation shown there is for
Windows Git Bash):

```bash
cd data
python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env              # fill in the S3 credentials; never commit .env

python -m pipelines --ingest      # full run, downloads from S3 first (~4 min)
python -m pipelines               # skips the download (~1.5 min)
```

- `.env` variable names: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`.
- `raw/`, `staging/`, `curated/` and `samples/` are gitignored; `contracts/`,
  `manifests/`, `knowledge_base/`, `eval/`, `fixtures/` and `reports/` are versioned.
- Contracts live in `data/contracts/`: one `<table>.yaml` schema per table
  (customers, products, transactions, complaints, call_center_interactions,
  call_transcripts, digital_events, and others), plus `relationships.yaml` (FKs),
  `glossary.yaml` (value mappings and business definitions),
  `quality_rules.yaml` (rules, thresholds, owners) and `CHANGELOG.md`.
- Rules for agents working in `data/` (raw is read-only, no raw rows to external
  model APIs, reports are generated, never hand-edited) are in `data/AGENTS.md`.

## Agent app (`app/lir-agent/`)

- **Stack:** Python >= 3.13, [uv](https://docs.astral.sh/uv/), Google ADK, LiteLLM,
  DuckDB, pydantic-settings. Dev tools: pytest, pytest-cov, pyright, ruff.
- **Architecture:** hexagonal layers (`domain`, `application`, `infrastructure`,
  `interface/adk`, `interface/http`) wired in `container.py`. Typed decisions come from
  `app/decision-layer/` (the ES/PT keyword baseline by default, Jev by configuration) or, with
  `DECISIONS=llm`, from the agent's own LLM adapter (`infrastructure/decisions/llm.py`).
- **Flow:** find the charge, gather verifiable evidence, and follow the lane chosen by a
  versioned synthetic policy (`resources/policy.yaml`): explain, put a dispute to the customer's
  and then a specialist's approval, or hand off to a human with a case file.
- **Hardening:** the session guard refuses sessions without a valid signed-in customer
  (missing or expired) before the model runs; the customer ID lives in session state;
  tools take no customer ID; tools are allowed per turn lane; the model only sees
  allowlisted fields and opaque transaction references, with bank records as placeholders
  and identifiers redacted from the customer's words; replies promising refunds or
  asking for credentials are replaced; every step is written to an audit log.
- **Model backend:** any LiteLLM-supported provider (`LLM_MODEL`, `LLM_API_BASE`,
  `LLM_API_KEY`).
- **Data:** DuckDB over `data/staging` when the pipeline has run, otherwise the
  team-generated demo fixture (`STORE`, `DATA_DIR`).

```bash
cd app/lir-agent
cp .env.example .env
uv sync
uv run chat --customer-id CLI-DEMO-001   # terminal chat; type "chao pescao" to exit
uv run adk web apps                      # browser dev UI (set DEV_CUSTOMER_ID in .env)
uv run pytest && uv run ruff check . && uv run pyright
```

`--customer-id` and `DEV_CUSTOMER_ID` stand in for the bank's sign-in on local runs. See [`app/lir-agent/README.md`](app/lir-agent/README.md) for the tools,
layout, configuration and limitations.

## Does everything still work?

```bash
scripts/check.sh          # tests and lint of every app, types of lir-agent; no network, no cost (~20 s)
scripts/check.sh --live   # plus the regression evals against the real model, 3 trials each
```

`--live` needs Node.js and `app/lir-agent/.env` with `LLM_MODEL` and `LLM_API_KEY`. It fails
when any regression scenario fails the code graders in any of its trials. See
[`app/evals/README.md`](app/evals/README.md) for the scenarios, graders and report, and
[`app/evals/AGENTS.md`](app/evals/AGENTS.md) for full and scoped runs (one scenario, a group,
only what failed last time).

## Deploy

One container image (`app/lir-agent/Dockerfile`) runs as two Cloud Run services in the
`lir-agent` GCP project (`us-east1`):

- `lir-agent`: operator API and specialist back office, behind IAP.
- `lir-agent-cases`: the case flow (`/v1/cases`, `/pubsub/push`, `/channels/telegram`),
  reached only through API Gateway and Pub/Sub.

Everything around the services (accounts, secrets, buckets, Firestore, Pub/Sub, API
Gateway, IAM, the CI identity) is Terraform in the
[`lir-infra`](https://github.com/Lir-team/lir-infra) repository. Order:

1. **Infrastructure, first apply** in `lir-infra` (`agent_service_deployed = false`,
   `cases_service_url = ""`), then add the secret values with
   `gcloud secrets versions add`. A revision that mounts an empty secret fails to start.
2. **Create the services** once, with the settings listed in `lir-infra`'s README,
   "Service settings the workflow must carry": service account, env vars, secrets,
   IAP/audience, the data bucket volume at `/mnt/data`, scaling and probe. The env maps
   come from `terraform output -json agent_env cases_env agent_secret_env cases_secret_env`.
3. **Infrastructure, second apply** with `agent_service_deployed = true` and
   `cases_service_url` set: IAP users, `run.invoker`, API Gateway, Pub/Sub push.
4. **Every later release** is automatic: a push to `main` that touches `app/lir-agent/` or
   `app/decision-layer/` runs `.github/workflows/deploy-agent.yml`, which builds the
   image with Cloud Build and rolls out the new image only. The services keep their env
   vars and secrets, so a release needs no `terraform apply`.

### Deploy-time variables (GitHub)

Repository variables (*Settings > Secrets and variables > Actions > Variables*), read by
the workflow; values from `terraform output` in `lir-infra`. No keys: the workflow signs
in with Workload Identity Federation.

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | GCP project id |
| `GCP_REGION` | Region of Cloud Run and Artifact Registry |
| `WIF_PROVIDER` | Output `wif_provider` |
| `DEPLOY_SA` | Output `deploy_service_account` (`lir-deploy@...`) |
| `AR_REPO` | Artifact Registry repository (`lir`) |
| `BUILD_SA` | Output `build_service_account` (`lir-build@...`) |
| `BUILD_BUCKET` | Output `build_source_bucket` |
| `DEPLOY_CASES_SERVICE` | `true` once `lir-agent-cases` exists; `false` skips its rollout |

### Runtime variables (Cloud Run)

Set on the services at creation (step 2). The image already sets `ENVIRONMENT=production`,
`DATA_DIR=/mnt/data` and `AUDIT_SINK=stdout`; every variable is described in
[`app/lir-agent/.env.example`](app/lir-agent/.env.example).

| Variable | Service | Value |
|---|---|---|
| `LLM_MODEL`, `LLM_API_BASE` | both | LiteLLM model (e.g. `openai/gpt-4o`); `LLM_API_BASE` empty for a hosted provider |
| `STORE`, `DECISIONS`, `JEV_ENABLED` | both | Data source and decision model (`lir-infra` variables) |
| `GOOGLE_CLOUD_PROJECT` | both | Project id |
| `CASE_STORE`, `CASE_REPOSITORY`, `APPROVAL_REPOSITORY` | both | `firestore`, shared by both services |
| `FIRESTORE_DATABASE`, `FIRESTORE_COLLECTION_PREFIX` | both | `(default)`, `lir_` |
| `BACKOFFICE_ENABLED` | both | `true` on `lir-agent`, `false` on `lir-agent-cases` |
| `CASES_INBOX`, `CASES_BUCKET` | cases | `gcs`, `<project>-cases` |
| `CASES_PUBLISHER`, `CASES_TOPIC` | cases | `pubsub`, `lir-cases` |
| `PUBSUB_PUSH_AUDIENCE`, `PUBSUB_PUSH_SERVICE_ACCOUNT` | cases | `lir-agent-cases-pubsub-push`, the `lir-pubsub-push` account |
| `REQUIRE_IDENTITY`, `APPROVAL_REQUIRES_SIGN_IN` | cases | `true` only with `customer_sign_in` in `lir-infra` |
| `CORS_ORIGINS` | cases | Origins of lir-web |
| `TELEGRAM_BOT_USERNAME` | cases | Bot username, without `@`; empty means no start link |
| `SPEECH_TO_TEXT` | cases | Optional: `google` transcribes voice notes |
| `APPROVAL_LINK_TEMPLATE` | cases | Optional: https link to the approval card in lir-web |
| `REFERENCE_DATE`, `EXPOSE_TRACE`, `PUBLIC_BASE_URL`, `SLACK_ASSIGNEES` | optional | See `.env.example` |
| `SMTP_USER`, `SMTP_APP_PASSWORD`, `CUSTOMER_EMAIL_OVERRIDE` | optional | Approval-outcome email; `lir-infra` sets none of them, so it is off when deployed |

Secrets, mounted from Secret Manager (`NAME=<secret id>:latest`):

| Variable | Secret id | Needed when |
|---|---|---|
| `LLM_API_KEY`, `OPENAI_API_KEY` | `openai-api-key` | Always |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | `telegram-bot-token`, `telegram-webhook-secret` | Telegram is on; without them `/channels/telegram` is off |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | `cloudflare-account-id`, `cloudflare-api-token` | Jev is on |
| `OPENROUTER_API_KEY` | `openrouter-api-key` | The decision model is on OpenRouter |
| `SLACK_WEBHOOK_URL` | `slack-webhook-url` | Slack notifications are on |

## Git hooks

Enable once per clone:

```bash
git config core.hooksPath .githooks
```

- `pre-commit`: scans staged changes with [gitleaks](https://github.com/gitleaks/gitleaks)
  and blocks the commit if a secret is found or gitleaks is not installed.
- `pre-push`: blocks pushes to `main`; push a feature branch and open a PR.
- `.claude/hooks/guard-push.sh` is a Claude Code `PreToolUse` hook script that
  denies `git push` commands targeting `main` (or run from the `main` branch).

## Documentation

- [`data/README.md`](data/README.md): data layers, provenance, declared vs observed quality, leakage rules (Spanish)
- [`data/AGENTS.md`](data/AGENTS.md): rules for agents working in `data/` (Spanish)
- [`app/evals/AGENTS.md`](app/evals/AGENTS.md): how to run the evals, full or scoped, and rules for changing scenarios (Spanish)
- [`app/lir-agent/README.md`](app/lir-agent/README.md): agent flow, tools, layout, commands, limitations
- [`app/decision-layer/README.md`](app/decision-layer/README.md): typed decision layer (baseline, Jev, fallback chain)
- [`docs/architecture/case-flow.md`](docs/architecture/case-flow.md): case flow wiring (web form, Pub/Sub, agent, Telegram), routes and local run
- [`docs/analytics/looker-studio.md`](docs/analytics/looker-studio.md): audit trail and evaluation runs in BigQuery, Looker Studio dashboards
- [`docs/propuesta-opcion-1-disputas.md`](docs/propuesta-opcion-1-disputas.md): product proposal (Spanish)
- [`docs/privacy.md`](docs/privacy.md): what leaves the perimeter, to whom, and the residual risk
- [`docs/backlog-evaluacion.md`](docs/backlog-evaluacion.md): tickets from the critical review against the Bases (Spanish)
- [`data/reports/insights.md`](data/reports/insights.md): evidence for choosing the workflow (Spanish)
- [`data/reports/data_quality.md`](data/reports/data_quality.md): data-quality scorecard (Spanish)
