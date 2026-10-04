"""Use case: send a dispute the customer confirmed to a bank specialist for approval (HITL).

Used when the customer rejects the explanation of a charge. The agent never opens this
dispute itself: a person approves or rejects it (`ReviewDispute`).
"""

from lir_agent.application.use_cases.request_handoff import RequestHandoff
from lir_agent.domain.dispute_guard import DisputeGuard
from lir_agent.domain.errors import DisputeBlock
from lir_agent.domain.models import ProposedDispute
from lir_agent.domain.session import SessionState, utc_now

NEXT_STEP = (
    "Tell the customer a bank specialist will review the dispute and give them the handoff id. "
    "The dispute is NOT open yet: never say it is, and do not promise the outcome."
)


class ProposeDispute:
    """Use case: hand a confirmed dispute to a person for approval."""

    def __init__(self, handoff: RequestHandoff, guard: DisputeGuard) -> None:
        """Keep the handoff use case and the dispute guard."""
        self._handoff = handoff
        self._guard = guard

    def execute(self, session: SessionState, transaction_ref: str, reason: str) -> dict:
        """Submit the proposal and read it back before reporting it."""
        transaction_id = session.resolve_ref(transaction_ref)
        # Also enforced in the tool guard (defense in depth).
        blocked = self._guard.check_proposal(session, transaction_id)
        if blocked or transaction_id is None:
            reason_code = blocked or DisputeBlock.NOT_UNDER_REVIEW
            return {"status": "blocked", "reason": reason_code.value}

        proposal = ProposedDispute(
            transaction_id=transaction_id, reason=reason, proposed_at=utc_now()
        )
        result = self._handoff.propose_dispute(session, proposal)
        submitted = result["status"] == "submitted"
        session.record_action(
            {
                "action": "propose_dispute",
                "transaction_id": transaction_id,
                "handoff_id": result["handoff_id"],
                "verified": submitted,
            }
        )
        session.review_transaction = None
        session.explained_transaction = None
        session.confirmed_transaction = None
        session.pending_confirmation = None
        return {
            "status": "submitted_for_review" if submitted else "unverified",
            "handoff_id": result["handoff_id"],
            "next_step": NEXT_STEP,
        }
