"""Decides whether a dispute may be opened.

Shared by the tool guard and the use case, so the rule is enforced twice (defense in
depth) from a single definition.
"""

from lir_agent.domain.errors import DisputeBlock
from lir_agent.domain.models import Lane
from lir_agent.domain.session import SessionState


class DisputeGuard:
    """Decides whether a dispute may be opened for a transaction."""

    def check(
        self, session: SessionState, transaction_id: str | None
    ) -> DisputeBlock | None:
        """None when allowed; otherwise the reason it is blocked."""
        case_outcome = session.case_outcome
        if not session.has_evidence_for(transaction_id) or case_outcome is None:
            return DisputeBlock.POLICY_DOES_NOT_ALLOW
        if case_outcome.lane != Lane.DISPUTE:
            return DisputeBlock.POLICY_DOES_NOT_ALLOW
        if transaction_id not in self._confirmed_charge(session):
            return DisputeBlock.CONFIRMATION_REQUIRED
        return None

    @staticmethod
    def _confirmed_charge(session: SessionState) -> set[str]:
        """The confirmed transaction plus its duplicates: one charge, billed twice.

        The customer confirms disputing "the duplicate charge"; which of the twin records
        the agent looked at last must not decide whether the dispute can be opened.
        """
        confirmed = session.confirmed_transaction
        if confirmed is None:
            return set()
        twins = {
            twin
            for evidence in session.evidence
            if evidence.transaction_id == confirmed
            for twin in evidence.duplicate_of
        }
        return {confirmed, *twins}
