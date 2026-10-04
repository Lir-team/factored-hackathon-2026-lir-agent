"""Application ports (interfaces).

Infrastructure adapters implement them; use cases depend only on these contracts.
The decision model port is `decision_layer.DecisionModel`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from lir_agent.domain.case_intake import CaseReceipt
from lir_agent.domain.models import Customer, DisputeCase, HandoffPacket, Transaction


class TransactionRepository(Protocol):
    """Read access to a customer's records.

    Contract for `list_transactions`: results belong to `customer_id` only and are ordered by
    transaction_date descending.
    """

    name: str

    def list_transactions(self, customer_id: str) -> list[Transaction]:
        """Return the customer's transactions, most recent first."""
        ...

    def get_customer(self, customer_id: str) -> Customer | None:
        """Return the customer, or None when the id is unknown."""
        ...


class CaseRepository(Protocol):
    """Case service (mock in this PoC). Writes are read back by use cases before reporting success."""

    def open_dispute(
        self, customer_id: str, transaction_id: str, reason: str
    ) -> DisputeCase:
        """Open a dispute case and return it as stored."""
        ...

    def get_dispute(self, case_id: str) -> DisputeCase | None:
        """Return a stored dispute, or None."""
        ...

    def submit_handoff(self, packet: HandoffPacket) -> str:
        """Store a handoff packet for human review and return its id."""
        ...

    def get_handoff(self, handoff_id: str) -> HandoffPacket | None:
        """Return a stored handoff packet, or None."""
        ...


class AuditSink(Protocol):
    """Execution record (Bases §6): one structured entry per decision, rule, tool call or reply."""

    def record(self, event: str, session_id: str | None, **fields: Any) -> dict:
        """Write one audit entry and return it."""
        ...


class ConversationNotFoundError(Exception):
    """The conversation does not exist, expired from memory, or belongs to another owner."""


class CustomerNotFoundError(Exception):
    """No customer with this id exists in the data source."""


@dataclass(frozen=True)
class StartedConversation:
    """A new conversation bound to a customer."""

    session_id: str
    expires_at: datetime


class Conversations(Protocol):
    """Customer conversations with the agent, shared by every entry point.

    Each conversation belongs to an `owner` (the IAP operator over HTTP, a case for intake
    channels): only that owner can continue it. The customer is bound on start and never
    passes through the conversation with the model.
    """

    async def start(
        self, owner: str, customer_id: str, *, ttl: timedelta, auth_method: str
    ) -> StartedConversation:
        """Start a conversation for `customer_id`, valid for `ttl`.

        Raises:
            CustomerNotFoundError: If the customer does not exist.
        """
        ...

    async def send(self, owner: str, session_id: str, text: str) -> str:
        """Send one customer message and return the agent's reply.

        Raises:
            ConversationNotFoundError: If `owner` has no conversation with this id.
        """
        ...


class CaseInbox(Protocol):
    """Where accepted cases land for the agent (the `cases-inbox` bucket in production)."""

    def put(
        self, case_id: str, payload: dict[str, Any], attributes: dict[str, str]
    ) -> None:
        """Store the case payload unchanged, with its routing attributes as metadata."""
        ...


@dataclass(frozen=True)
class StoredReceipt:
    """An accepted case's answer and the customer who filed it."""

    customer_id: str
    receipt: CaseReceipt


class CaseStore(Protocol):
    """Intake state that must outlive the request: idempotent answers and start tokens."""

    def get_receipt(self, idempotency_key: str) -> StoredReceipt | None:
        """Return the answer stored for this key, or None."""
        ...

    def save_receipt(self, idempotency_key: str, stored: StoredReceipt) -> None:
        """Keep a successful answer for replay."""
        ...

    def add_start_token(self, token: str, case_id: str, expires_at: datetime) -> None:
        """Bind a single-use Telegram start token to a case until `expires_at`."""
        ...

    def consume_start_token(self, token: str, now: datetime) -> str | None:
        """Burn the token and return its case id; None when unknown, used or expired."""
        ...
