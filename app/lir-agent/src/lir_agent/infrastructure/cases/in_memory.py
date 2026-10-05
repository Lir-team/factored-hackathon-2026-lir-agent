"""Mock case service with a documented contract.

No real dispute is filed and no money moves: the bases neither require nor authorize it.
"""

import threading
import uuid

from lir_agent.domain.models import DisputeCase, HandoffPacket
from lir_agent.domain.session import utc_now

DISPUTE_ID_PREFIX = "DSP-"
OPEN_STATUS = "open"


class InMemoryCaseRepository:
    """In-memory case service: disputes and handoffs live for the process lifetime."""

    def __init__(self) -> None:
        """Start with no cases."""
        self._disputes: dict[str, DisputeCase] = {}
        self._handoffs: dict[str, HandoffPacket] = {}
        self._lock = threading.Lock()

    def open_dispute(
        self, customer_id: str, transaction_id: str, reason: str
    ) -> DisputeCase:
        """Store a new open dispute and return it."""
        case = DisputeCase(
            case_id=f"{DISPUTE_ID_PREFIX}{uuid.uuid4().hex[:10].upper()}",
            customer_id=customer_id,
            transaction_id=transaction_id,
            reason=reason,
            status=OPEN_STATUS,
            created_at=utc_now(),
        )
        with self._lock:
            self._disputes[case.case_id] = case
        return case

    def get_dispute(self, case_id: str) -> DisputeCase | None:
        """Return a stored dispute, or None."""
        return self._disputes.get(case_id)

    def submit_handoff(self, packet: HandoffPacket) -> str:
        """Store a handoff packet and return its id."""
        with self._lock:
            self._handoffs[packet.handoff_id] = packet
        return packet.handoff_id

    def get_handoff(self, handoff_id: str) -> HandoffPacket | None:
        """Return a stored handoff packet, or None."""
        return self._handoffs.get(handoff_id)

    def list_handoffs(self) -> list[HandoffPacket]:
        """Every stored handoff packet, newest first."""
        with self._lock:
            packets = list(self._handoffs.values())
        return sorted(packets, key=lambda p: p.created_at, reverse=True)
