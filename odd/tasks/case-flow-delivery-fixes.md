# Case flow delivery fixes

Locator: `odd/tasks/case-flow-delivery-fixes.md` · Engram mirror: `odd/case-flow-delivery-fixes/tasks`
Branch: `fix/case-flow-delivery` · Status: in progress (T1) · Delivery: `single-pr` (target `v0.1.0-beta.2`)

## Objective

Close the three delivery bugs listed as known issues in `v0.1.0-beta.1`, so a
customer never silently loses a reply and a case is never submitted twice.

## Why

Found in the review of PRs #23-#29 (2026-10-04). None was fixed later in the chain.

## Scope

- T1-T3 below, each with a test that fails before the fix.
- Out of scope: Pub/Sub dead-letter topic (infra), Firestore calls blocking the
  event loop, Telegram 429 retry, message splitting.

## Tasks

- [ ] **T1 Reply lost when Telegram send fails** — `application/use_cases/process_case.py`
  `deliver_replies` pops queued replies before sending; on a send error Pub/Sub
  redelivers but the case is skipped as already worked. Fix: never lose a reply
  that was not sent (send then remove, or re-queue the unsent ones on failure).
  Same on the `/start` path. Check: test where `messenger.send` raises, then a
  retry delivers the reply exactly once.
- [x] **T2 Telegram update dropped when the agent fails** — `interface/http/telegram.py`
  marks `update_id` seen before `execute()`; on an unexpected error the 500 makes
  Telegram retry, and the dedupe drops it. Fix: mark seen only after success (or
  answer the customer with a short "try again" and return 200). Check: test where
  the agent raises once, then the redelivered update is answered.
- [x] **T3 Idempotency race on `POST /v1/cases`** — `application/use_cases/submit_case.py`
  reads the receipt, writes inbox, publishes, then saves the receipt, so two
  concurrent requests with the same key both go through. Fix: claim the key
  atomically (create-if-absent on `CaseStore`, memory + Firestore) before side
  effects, release on failure. Check: contract test for both adapters (Firestore
  under emulator) + use-case test with two concurrent submits → one publish.

## Acceptance

- `scripts/check.sh` green (offline), new tests fail before and pass after.

## Route

- T1-T3 touch several non-trivial files → delegated direct (one writer).

## Progress

- T2 done (delegated writer). RED: `tests/interface/test_telegram_webhook.py::test_an_update_that_failed_is_answered_when_telegram_retries_it`
  (retry got 200 but nothing was sent). Fix: `_RecentUpdates.remember` runs only after
  `execute()` succeeds. GREEN: 294 passed, 12 skipped; ruff and pyright clean.
  Commit: `fix(agent): retry a Telegram update whose handling failed`.
- T3 done (delegated writer). RED: `tests/application/test_submit_case.py::test_a_concurrent_duplicate_is_refused_and_the_case_published_once`
  (DID NOT RAISE `CaseInProgressError`), plus the new `claim_key` contract tests and
  `tests/interface/test_cases_api.py::test_a_key_still_being_accepted_is_a_conflict`.
  Fix: `CaseStore.claim_key`/`release_key` (memory: lock; Firestore: `.create()`, expired
  claims taken over in a transaction), claimed in `SubmitCase` before any side effect and
  released on failure; `409` for a key still in flight. GREEN: 300 passed, 16 skipped;
  `test_case_store.py` 34 passed against the Firestore emulator; ruff and pyright clean.
  Commit: `fix(agent): claim the idempotency key before accepting a case`.
