"""Decides whether a dispute may be put to the customer for approval, and on what grounds.

Shared by the tool guard and the use case, so the rule is enforced twice (defense in
depth) from a single definition. The agent never opens a dispute: it only asks the
customer to approve one (`domain/approvals.py`).
"""

from enum import StrEnum

from lir_agent.domain.models import Lane
from lir_agent.domain.session import SessionState


class DisputeGround(StrEnum):
    """Why a dispute may be put to the customer; names the approval chain in the policy."""

    # The policy found an error in the charge (case lane `dispute`, e.g. a duplicate).
    POLICY = "dispute"
    # The customer still rejects a charge the agent explained (turn lane `review`).
    REJECTED_EXPLANATION = "review"


class DisputeGuard:
    """Decides whether a dispute on a transaction may go to approval."""

    def ground(
        self, session: SessionState, transaction_id: str | None
    ) -> DisputeGround | None:
        """The ground for a dispute on this transaction, or None when there is none."""
        if transaction_id is None or not session.has_evidence_for(transaction_id):
            return None
        case_outcome = session.case_outcome
        last_evidence = session.evidence[-1] if session.evidence else None
        if (
            case_outcome is not None
            and case_outcome.lane == Lane.DISPUTE
            and last_evidence is not None
            and transaction_id in self._same_charge(session, last_evidence.transaction_id)
        ):
            return DisputeGround.POLICY
        if transaction_id == session.review_transaction:
            return DisputeGround.REJECTED_EXPLANATION
        return None

    @staticmethod
    def _same_charge(session: SessionState, transaction_id: str) -> set[str]:
        """The transaction plus its duplicates: one charge, billed twice.

        The customer disputes "the duplicate charge"; which of the twin records the agent
        looked at last must not decide whether the dispute can go to approval.
        """
        twins = {
            twin
            for evidence in session.evidence
            if evidence.transaction_id == transaction_id
            for twin in evidence.duplicate_of
        }
        return {transaction_id, *twins}
