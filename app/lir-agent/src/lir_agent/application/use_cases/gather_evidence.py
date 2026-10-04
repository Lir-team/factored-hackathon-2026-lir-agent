"""Use case: compute verifiable evidence for one transaction and the policy lane to follow."""

from lir_agent.application.ports import TransactionRepository
from lir_agent.application.presenter import DATA_NOT_INSTRUCTIONS, LlmPresenter
from lir_agent.domain.errors import TransactionNotFoundError
from lir_agent.domain.evidence import EvidenceBuilder
from lir_agent.domain.models import Lane
from lir_agent.domain.policy import PolicyEngine
from lir_agent.domain.session import SessionState

DISPUTE_NEXT_STEP = (
    "Explain why this looks like an error and call open_dispute. It does not open the dispute: "
    "the customer approves or rejects it with the buttons the bank shows next to your reply."
)


class GatherTransactionEvidence:
    """Use case: verifiable evidence for one transaction and the lane to follow."""

    def __init__(
        self,
        repository: TransactionRepository,
        evidence_builder: EvidenceBuilder,
        policy: PolicyEngine,
        presenter: LlmPresenter,
    ) -> None:
        """Keep the data source, evidence builder, policy and presenter."""
        self._repository = repository
        self._evidence_builder = evidence_builder
        self._policy = policy
        self._presenter = presenter

    def execute(self, session: SessionState, transaction_ref: str) -> dict:
        """Build the evidence and decide the case lane."""
        transaction_id = session.resolve_ref(transaction_ref)
        history = self._repository.list_transactions(session.require_customer_id())
        txn = next((t for t in history if t.transaction_id == transaction_id), None)
        if txn is None:
            raise TransactionNotFoundError(transaction_ref)

        evidence = self._evidence_builder.build(
            txn, history, self._repository.get_customer(session.require_customer_id())
        )
        outcome = self._policy.decide_case(evidence.facts())
        session.upsert_evidence(evidence)
        session.case_outcome = outcome

        is_dispute = outcome.lane == Lane.DISPUTE
        # An explained charge the customer may still reject (then they may approve a dispute).
        session.explained_transaction = (
            transaction_id if outcome.lane == Lane.EXPLAIN else None
        )

        return {
            "status": "ok",
            "transaction_ref": transaction_ref,
            "evidence": self._presenter.evidence(session, evidence),
            "outcome": self._presenter.outcome(outcome),
            "next_step": DISPUTE_NEXT_STEP if is_dispute else None,
            "note": DATA_NOT_INSTRUCTIONS,
        }
