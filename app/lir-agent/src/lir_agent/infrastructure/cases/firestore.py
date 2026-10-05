"""Case service on Firestore: disputes and handoffs shared by every service instance.

The operator service (case files behind IAP) and the case flow service (web form, Telegram)
read and write the same handoffs, and they survive restarts. Same mock contract as the
in-memory repository: no real dispute is filed and no money moves.
"""

import uuid

from google.cloud import firestore

from lir_agent.domain.models import DisputeCase, HandoffPacket
from lir_agent.domain.session import utc_now
from lir_agent.infrastructure.cases.in_memory import DISPUTE_ID_PREFIX, OPEN_STATUS

DEFAULT_PREFIX = "lir_"


class FirestoreCaseRepository:
    """Disputes and handoffs as Firestore documents keyed by their ids."""

    def __init__(self, client: firestore.Client, prefix: str = DEFAULT_PREFIX) -> None:
        """Bind the client; `prefix` keeps environments or test runs apart."""
        self._disputes = client.collection(f"{prefix}disputes")
        self._handoffs = client.collection(f"{prefix}handoffs")

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
        self._disputes.document(case.case_id).set(case.model_dump(mode="json"))
        return case

    def get_dispute(self, case_id: str) -> DisputeCase | None:
        """Return a stored dispute, or None."""
        snapshot = self._disputes.document(case_id).get()
        return DisputeCase.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def submit_handoff(self, packet: HandoffPacket) -> str:
        """Store (or replace) a handoff packet and return its id."""
        self._handoffs.document(packet.handoff_id).set(packet.model_dump(mode="json"))
        return packet.handoff_id

    def get_handoff(self, handoff_id: str) -> HandoffPacket | None:
        """Return a stored handoff packet, or None."""
        snapshot = self._handoffs.document(handoff_id).get()
        return HandoffPacket.model_validate(snapshot.to_dict()) if snapshot.exists else None
