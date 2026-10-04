"""Opening a dispute: an important action that runs only after the customer approves it.

`OpenDisputeAction` is the approval adapter of the `open_dispute` tool: `describe` builds
what the customer reads (merchant, date, amount, reason) when the agent calls the tool, and
`execute` opens the dispute after the approval, reading it back.
"""

from collections.abc import Mapping
from typing import Any

from lir_agent.application.ports import CaseRepository
from lir_agent.domain.approvals import (
    ApprovalDetail,
    ApprovalDraft,
    ApprovalError,
    ApprovalRequest,
)
from lir_agent.domain.dispute_guard import DisputeGround, DisputeGuard
from lir_agent.domain.models import Evidence
from lir_agent.domain.pseudonyms import Kind, format_value
from lir_agent.domain.session import SessionState

OPEN_DISPUTE = "open_dispute"


class OpenDisputeAction:
    """Approval adapter of `open_dispute`."""

    name = OPEN_DISPUTE

    def __init__(self, cases: CaseRepository, guard: DisputeGuard) -> None:
        """Keep the case service and the guard that says whether there is a ground."""
        self._cases = cases
        self._guard = guard

    def describe(
        self, session: SessionState, args: Mapping[str, Any], labels: Mapping[str, Any]
    ) -> ApprovalDraft | None:
        """The approval card for a dispute on this charge; None without a ground for it."""
        transaction_id = session.resolve_ref(args.get("transaction_ref"))
        ground = self._guard.ground(session, transaction_id)
        evidence = next(
            (e for e in session.evidence if e.transaction_id == transaction_id), None
        )
        outcome = (
            session.case_outcome
            if ground is DisputeGround.POLICY
            else session.turn_outcome
        )
        if ground is None or evidence is None or outcome is None:
            return None
        reason = str(args.get("reason") or "")
        return ApprovalDraft(
            params={"transaction_id": evidence.transaction_id, "reason": reason},
            title=labels["open_dispute"],
            details=_details(evidence, reason, labels),
            outcome=outcome,
        )

    def execute(self, request: ApprovalRequest) -> dict:
        """Open the dispute; its id and status are kept with the request.

        Raises:
            ApprovalError: `action_not_verified` when it cannot be read back.
        """
        case = self._cases.open_dispute(
            request.customer_id,
            request.params["transaction_id"],
            request.params["reason"],
        )
        if self._cases.get_dispute(case.case_id) != case:
            raise ApprovalError("action_not_verified")
        return {"dispute_case_id": case.case_id, "dispute_status": case.status}


def _details(
    evidence: Evidence, reason: str, labels: Mapping[str, Any]
) -> list[ApprovalDetail]:
    """What the customer reads before deciding, in their language."""
    amount = format_value(Kind.AMOUNT, evidence.amount)
    return [
        ApprovalDetail(label=labels["merchant"], value=evidence.merchant_name or "—"),
        ApprovalDetail(label=labels["date"], value=format_value(Kind.DATE, evidence.date)),
        ApprovalDetail(
            label=labels["amount"], value=f"{amount} {evidence.currency or ''}".strip()
        ),
        ApprovalDetail(label=labels["reason"], value=reason),
    ]
