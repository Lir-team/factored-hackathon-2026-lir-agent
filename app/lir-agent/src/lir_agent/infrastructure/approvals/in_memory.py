"""In-memory approval requests: they live for the process lifetime, like the case mock."""

import threading

from lir_agent.domain.approvals import ApprovalRequest, ApprovalStatus, Approver


class InMemoryApprovalRepository:
    """Approval requests keyed by id."""

    def __init__(self) -> None:
        """Start with no requests."""
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()

    def save(self, request: ApprovalRequest) -> None:
        """Store a request (insert or replace by id)."""
        with self._lock:
            self._requests[request.approval_id] = request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Return a request, or None."""
        return self._requests.get(approval_id)

    def list(
        self, status: ApprovalStatus | None = None, approver: Approver | None = None
    ) -> list[ApprovalRequest]:
        """Requests, newest first, optionally filtered."""
        with self._lock:
            requests = list(self._requests.values())
        return sorted(
            (
                r
                for r in requests
                if (status is None or r.status is status)
                and (approver is None or r.approver is approver)
            ),
            key=lambda r: r.created_at,
            reverse=True,
        )
