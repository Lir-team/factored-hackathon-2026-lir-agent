# Docs consistency

Status: in progress. Branch: `docs/deploy-readme`.

## Objective

Professional-grade English documentation for the hackathon: every fact in the docs matches
the code, the deploy workflow and `lir-infra`, and the docs agree with each other.

## Problem

A read-only audit (2026-10-05) found 22 inconsistencies: an incomplete `.env.example`, a
setting that does not exist, a wrong build command, a broken local-run command, wrong bucket
and dataset names, and "not built yet" claims made stale by PRs #58-#60 and by Cloud Run
leaving Terraform.

## Scope

In scope: `README.md`, `app/lir-agent/README.md`, `app/lir-agent/.env.example`,
`app/lir-agent/Dockerfile`, `app/decision-layer/README.md`, `docs/architecture/case-flow.md`,
`.github/workflows/deploy-agent.yml` (comments only), `scripts/check.sh` claims,
`data/contracts/quality_rules.yaml` (steward name), `odd/tasks/telegram-voice-notes.md`.

Out of scope: `lir-infra` (separate repo; its README says the workflow creates the services and
its secrets table omits `slack-webhook-url` and says Telegram secrets are cases-only), and
translating the Spanish docs (product decision pending).

## Constraints

- Documentation only; no behavior change. Facts come from code, not from other docs.
- English, neutral professional tone, match the surrounding style.

## Tasks

- [x] T0 Root README Deploy section (deploy order, GitHub variables, runtime variables and secrets). Route: inline.
- [x] T1 Fix high findings: `.env.example` completeness (22 missing settings, remove `CASE_SESSION_TTL_MINUTES`), agent README session TTL, Dockerfile build command, case-flow local command, bucket and dataset names, services and routes in the architecture table, decision-layer "LLM" claim, deploy workflow comments. Route: delegated (writer trigger: 2+ non-trivial files).
- [x] T2 Fix medium and low findings: stale limitations, stale "not wired yet", case-flow service names, HTTP API table rows, SMTP approval email, decision-layer next steps, documentation index (Spanish tags, missing links), voice-notes task status, check.sh wording, agent README layout, `Clir` steward. Route: delegated (same writer).

- [x] T3 Remaining inconsistencies in this repository: root README notes the approval email is off when deployed. Route: inline. The `lir-infra` README fixes stay out of scope (left uncommitted on its branch `docs/readme-consistency`).

## Acceptance criteria

- Every env var named in the docs exists in `settings.py`; every `settings.py` field appears in `.env.example`.
- Every command, path and relative link in the docs resolves.
- No doc contradicts another on service names, routes, buckets, defaults or what is mocked.

## Checks

- Structural: relative link check over the edited Markdown; `settings.py` fields vs `.env.example` names diff is empty.
- `scripts/check.sh` still passes (docs only, sanity).

## Progress

- T0 done (uncommitted on `docs/deploy-readme`).
- T1 done: findings 1-8 fixed. `.env.example` gained the 22 missing settings and dropped
  `CASE_SESSION_TTL_MINUTES`; the root architecture table now names both services, their routes,
  `<project>-cases`, `<project>-data` and `lir_analytics`; LLM decisions are attributed to
  `app/lir-agent` (`infrastructure/decisions/llm.py`); workflow comments only.
- T2 done: findings 9-18 fixed. Also corrected in passing: the security-edge row (API Gateway
  forwards the Telegram webhook; the agent checks its secret), the case-flow dead-letter gap
  (now describes `lir-cases-dead-letter`, 5 attempts, kept a week) and the Slack step ("a link to
  the case file").
- Checks (uncommitted):
  - relative link check over the 6 edited Markdown files: 21 links, 0 broken.
  - `settings.py` fields vs `.env.example` names: no field missing; extra only
    `PUBSUB_EMULATOR_HOST` and `FIRESTORE_EMULATOR_HOST`, read by the Google client libraries.
  - `scripts/check.sh`: all checks passed (offline). Its `uv sync` rewrote `app/evals/uv.lock`;
    reverted, out of scope.
- Not fixed (out of scope): generated `data/manifests/quality/*.json` still say
  `Equipo datos Clir` until the pipeline reruns; `lir-infra` sets no SMTP variables, so the
  approval email is off when deployed.
- T3 done (uncommitted). Generated `data/manifests/quality/*.json` keep `Equipo datos Clir` until
  the pipeline reruns: generated files are never hand-edited (`data/AGENTS.md`).
- Next step: PR for `docs/deploy-readme`.
