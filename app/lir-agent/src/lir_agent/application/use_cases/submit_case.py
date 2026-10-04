"""Use case: accept a case filed from the web form, archive it and hand it to the agent."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from lir_agent.application.ports import (
    AuditSink,
    CaseInbox,
    CaseInProgressError,
    CasePublisher,
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
    folio_for,
    new_start_token,
    telegram_start_url,
)

# How long a request may take to accept a case before another one may take its key over.
CLAIM_TTL = timedelta(minutes=5)


class SubmitCase:
    """Use case: check a schema-valid case against the customer's records and accept it."""

    def __init__(
        self,
        customers: TransactionRepository,
        inbox: CaseInbox,
        publisher: CasePublisher,
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
        self._publisher = publisher
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
            CaseInProgressError: If another request with the key is still accepting it.
            CustomerNotFoundError: If the customer does not exist.
            UnknownTransactionError: If a transaction is not the customer's.
            CasePublishError: If the case could not be handed to the agent (nothing is kept
                for replay, so a retry with the same key publishes again).
        """
        case_id = payload["case_id"]
        if idempotency_key != case_id:
            raise IdempotencyKeyMismatchError(case_id)
        replay = self._replay(customer_id, idempotency_key)
        if replay is not None:
            return replay
        if payload["customer"]["customer_id"] != customer_id:
            raise ForeignCaseError(case_id)
        if self._customers.get_customer(customer_id) is None:
            raise CustomerNotFoundError(customer_id)
        owned = {
            t.transaction_id for t in self._customers.list_transactions(customer_id)
        }
        if any(t["transaction_id"] not in owned for t in payload["transactions"]):
            raise UnknownTransactionError(case_id)

        # Claimed before any side effect: a concurrent request with the key archives and
        # publishes nothing. Released on failure, so a retry can accept the case.
        now = self._now()
        if not self._store.claim_key(idempotency_key, now, now + CLAIM_TTL):
            replay = self._replay(customer_id, idempotency_key)  # it just finished
            if replay is not None:
                return replay
            raise CaseInProgressError(case_id)
        try:
            receipt = self._accept(customer_id, idempotency_key, payload)
        except BaseException:
            self._store.release_key(idempotency_key)
            raise
        self._audit.record(
            "case_received",
            None,
            case_id=case_id,
            category=payload["category"],
            customer_id=customer_id,
        )
        return receipt

    def _replay(self, customer_id: str, idempotency_key: str) -> CaseReceipt | None:
        """The answer stored for the key, or None when the key was never accepted."""
        stored = self._store.get_receipt(idempotency_key)
        if stored is None:
            return None
        if stored.customer_id != customer_id:
            raise ForeignCaseError(idempotency_key)
        return stored.receipt

    def _accept(
        self, customer_id: str, idempotency_key: str, payload: dict[str, Any]
    ) -> CaseReceipt:
        """Archive and publish the case, then keep its answer for replay."""
        case_id = payload["case_id"]
        attributes = case_attributes(payload)
        self._inbox.put(case_id, payload, attributes)
        # Ordered per customer: one customer's cases reach the agent in filing order.
        self._publisher.publish(payload, attributes, ordering_key=customer_id)
        folio = folio_for(case_id, payload["submitted_at"])
        receipt = CaseReceipt(
            case_id=case_id,
            folio=folio,
            telegram_start_url=self._start_link(
                CaseStart(case_id=case_id, folio=folio, language=payload["language"]),
                payload,
            ),
        )
        # Only successful answers are kept: a failed attempt is processed again.
        self._store.save_receipt(idempotency_key, StoredReceipt(customer_id, receipt))
        return receipt

    def _start_link(self, start: CaseStart, payload: dict[str, Any]) -> str | None:
        """A Telegram start link when the customer chose Telegram and a bot is configured.

        The token keeps what `/start` needs to link the chat to the case.
        """
        channel = payload["customer"]["preferred_contact"]["channel"]
        if channel != "telegram" or not self._bot:
            return None
        token = new_start_token()
        self._store.add_start_token(token, start, self._now() + self._token_ttl)
        return telegram_start_url(self._bot, token)
