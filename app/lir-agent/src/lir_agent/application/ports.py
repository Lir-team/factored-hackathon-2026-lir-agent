"""Application ports (interfaces).

Infrastructure adapters implement them; use cases depend only on these contracts.
The decision model port is `decision_layer.DecisionModel`.
"""

from typing import Any, Protocol

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
