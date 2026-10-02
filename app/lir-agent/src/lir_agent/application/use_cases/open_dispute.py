"""Use case: open a dispute once the policy allows it and the customer confirmed it."""

from lir_agent.application.ports import CaseRepository
from lir_agent.domain.dispute_guard import DisputeGuard
from lir_agent.domain.errors import DisputeBlock
from lir_agent.domain.session import SessionState


class OpenDispute:
    """Use case: open a dispute once the policy allows it and the customer confirmed."""

    def __init__(self, cases: CaseRepository, guard: DisputeGuard) -> None:
        """Keep the case service and the dispute guard."""
        self._cases = cases
        self._guard = guard

    def execute(self, session: SessionState, transaction_ref: str, reason: str) -> dict:
        """Open the dispute and read it back before reporting success."""
        transaction_id = session.resolve_ref(transaction_ref)
        # Also enforced in the tool guard (defense in depth).
        blocked = self._guard.check(session, transaction_id)
        if blocked or transaction_id is None:
            reason_code = blocked or DisputeBlock.POLICY_DOES_NOT_ALLOW
            return {"status": "blocked", "reason": reason_code.value}

        case = self._cases.open_dispute(
            session.require_customer_id(), transaction_id, reason
        )
        verified = (
            self._cases.get_dispute(case.case_id) == case
        )  # read back before reporting success
        session.record_action(
            {
                "action": "open_dispute",
                "case_id": case.case_id,
                "transaction_id": transaction_id,
                "verified": verified,
            }
        )
        session.confirmed_transaction = None
        session.pending_confirmation = None
        return {
            "status": "verified" if verified else "unverified",
            "case_id": case.case_id,
            "case_status": case.status,
        }
