"""Use case: accept a case filed from the web form and hand it to the agent's inbox."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from lir_agent.application.ports import (
    AuditSink,
    CaseInbox,
    CaseStore,
    CustomerNotFoundError,
    StoredReceipt,
    TransactionRepository,
)
from lir_agent.domain.case_intake import (
    CaseReceipt,
    CaseStart,
    ForeignCaseError,
    IdempotencyKeyMismatchError,
    UnknownTransactionError,
    case_attributes,
    case_summary,
    folio_for,
    new_start_token,
    telegram_start_url,
)


class SubmitCase:
    """Use case: check a schema-valid case against the customer's records and accept it."""

    def __init__(
        self,
        customers: TransactionRepository,
        inbox: CaseInbox,
        store: CaseStore,
        audit: AuditSink,
        *,
        telegram_bot_username: str | None,
        start_token_ttl: timedelta,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Keep the adapters; without a bot username no Telegram start link is issued."""
        self._customers = customers
        self._inbox = inbox
        self._store = store
        self._audit = audit
        self._bot = telegram_bot_username
        self._token_ttl = start_token_ttl
        self._now = now or (lambda: datetime.now(UTC))

    def execute(
        self, customer_id: str, idempotency_key: str, payload: dict[str, Any]
    ) -> CaseReceipt:
        """Accept the case filed by `customer_id`; a repeated key replays the first answer.

        Raises:
            IdempotencyKeyMismatchError: If the key is not the payload's `case_id`.
            ForeignCaseError: If the case or its key belongs to another customer.
            CustomerNotFoundError: If the customer does not exist.
            UnknownTransactionError: If a transaction is not the customer's.
        """
        case_id = payload["case_id"]
        if idempotency_key != case_id:
            raise IdempotencyKeyMismatchError(case_id)
        stored = self._store.get_receipt(idempotency_key)
        if stored is not None:
            if stored.customer_id != customer_id:
                raise ForeignCaseError(case_id)
            return stored.receipt
        if payload["customer"]["customer_id"] != customer_id:
            raise ForeignCaseError(case_id)
        if self._customers.get_customer(customer_id) is None:
            raise CustomerNotFoundError(customer_id)
        owned = {
            t.transaction_id for t in self._customers.list_transactions(customer_id)
        }
        if any(t["transaction_id"] not in owned for t in payload["transactions"]):
            raise UnknownTransactionError(case_id)

        self._inbox.put(case_id, payload, case_attributes(payload))
        folio = folio_for(case_id, payload["submitted_at"])
        receipt = CaseReceipt(
            case_id=case_id,
            folio=folio,
            telegram_start_url=self._start_link(
                CaseStart(
                    case_id=case_id,
                    customer_id=customer_id,
                    folio=folio,
                    language=payload["language"],
                    summary=case_summary(payload),
                ),
                payload,
            ),
        )
        # Only successful answers are kept: a failed attempt is processed again.
        self._store.save_receipt(idempotency_key, StoredReceipt(customer_id, receipt))
        self._audit.record(
            "case_received",
            None,
            case_id=case_id,
            category=payload["category"],
            customer_id=customer_id,
        )
        return receipt

    def _start_link(self, start: CaseStart, payload: dict[str, Any]) -> str | None:
        """A Telegram start link when the customer chose Telegram and a bot is configured.

        The token keeps what `/start` needs to open the case's conversation.
        """
        channel = payload["customer"]["preferred_contact"]["channel"]
        if channel != "telegram" or not self._bot:
            return None
        token = new_start_token()
        self._store.add_start_token(token, start, self._now() + self._token_ttl)
        return telegram_start_url(self._bot, token)
