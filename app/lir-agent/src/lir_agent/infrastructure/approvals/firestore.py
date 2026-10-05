"""Approval requests on Firestore, shared by every service instance.

The case flow service creates requests (the customer approves in Telegram) and the operator
service decides the ones that wait for a specialist (back office behind IAP).
"""

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from lir_agent.domain.approvals import ApprovalRequest, ApprovalStatus, Approver

DEFAULT_PREFIX = "lir_"


class FirestoreApprovalRepository:
    """Approval requests as Firestore documents keyed by their ids."""

    def __init__(self, client: firestore.Client, prefix: str = DEFAULT_PREFIX) -> None:
        """Bind the client; `prefix` keeps environments or test runs apart."""
        self._requests = client.collection(f"{prefix}approvals")

    def save(self, request: ApprovalRequest) -> None:
        """Store a request (insert or replace by id)."""
        self._requests.document(request.approval_id).set(request.model_dump(mode="json"))

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Return a request, or None."""
        snapshot = self._requests.document(approval_id).get()
        return ApprovalRequest.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def list(
        self, status: ApprovalStatus | None = None, approver: Approver | None = None
    ) -> list[ApprovalRequest]:
        """Requests, newest first, optionally filtered (sorted here: no composite index)."""
        query = self._requests
        if status is not None:
            query = query.where(filter=FieldFilter("status", "==", status.value))
        if approver is not None:
            query = query.where(filter=FieldFilter("approver", "==", approver.value))
        requests = [ApprovalRequest.model_validate(s.to_dict()) for s in query.stream()]
        return sorted(requests, key=lambda r: r.created_at, reverse=True)
