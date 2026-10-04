"""Use case: a bank specialist approves or rejects a proposed dispute (human in the loop).

Approval opens the dispute in the case service and reads it back; rejection opens nothing.
Either way the decision, the reviewer and the time are kept in the handoff packet and the
audit log, and a customer who came through the web form hears the decision in their chat.
"""

import logging
import threading

from lir_agent.application.ports import (
    AuditSink,
    CaseRepository,
    CaseStore,
    MessageNotSentError,
    Messenger,
)
from lir_agent.application.use_cases.process_case import deliver_replies
from lir_agent.domain.models import HandoffPacket, ProposedDispute, ReviewStatus
from lir_agent.domain.session import utc_now
from lir_agent.domain.telegram import bot_language, bot_message

logger = logging.getLogger(__name__)


class ProposalNotFoundError(Exception):
    """The handoff does not exist or carries no proposed dispute."""


class ProposalAlreadyDecidedError(Exception):
    """The proposed dispute was already approved or rejected."""


class DisputeNotVerifiedError(Exception):
    """The case service did not return the dispute it was asked to open."""


class ReviewDispute:
    """Use case: record a specialist's decision on a proposed dispute."""

    def __init__(
        self,
        cases: CaseRepository,
        audit: AuditSink,
        store: CaseStore | None = None,
        messenger: Messenger | None = None,
    ) -> None:
        """Keep the case service, the audit sink and the customer's chat channel."""
        self._cases = cases
        self._audit = audit
        self._store = store
        self._messenger = messenger
        self._lock = threading.Lock()  # one decision per proposal, even with two reviewers

    async def execute(
        self, handoff_id: str, reviewer: str, approve: bool, note: str | None = None
    ) -> ProposedDispute:
        """Approve (open the dispute) or reject a pending proposal.

        Raises:
            ProposalNotFoundError: If there is no proposed dispute for this handoff.
            ProposalAlreadyDecidedError: If it was already decided.
            DisputeNotVerifiedError: If the opened dispute could not be read back.
        """
        with self._lock:
            packet = self._cases.get_handoff(handoff_id)
            if packet is None or packet.proposed_dispute is None:
                raise ProposalNotFoundError(handoff_id)
            proposal = packet.proposed_dispute
            if proposal.status is not ReviewStatus.PENDING:
                raise ProposalAlreadyDecidedError(handoff_id)
            decision: dict = {"reviewer": reviewer, "decided_at": utc_now(), "note": note}
            if approve:
                case = self._cases.open_dispute(
                    packet.customer_id, proposal.transaction_id, proposal.reason
                )
                if self._cases.get_dispute(case.case_id) != case:
                    raise DisputeNotVerifiedError(case.case_id)
                decision |= {
                    "status": ReviewStatus.APPROVED,
                    "dispute_case_id": case.case_id,
                }
            else:
                decision |= {"status": ReviewStatus.REJECTED}
            decided = proposal.model_copy(update=decision)
            updated = packet.model_copy(update={"proposed_dispute": decided})
            self._cases.submit_handoff(updated)
        self._audit.record(
            "dispute_reviewed",
            None,
            handoff_id=handoff_id,
            reviewer=reviewer,
            decision=decided.status.value,
            dispute_case_id=decided.dispute_case_id,
        )
        await self._tell_customer(updated)
        return decided

    async def _tell_customer(self, packet: HandoffPacket) -> None:
        """Queue the decision for the case's chat; a failed send stays queued, never undone."""
        report, proposal = packet.case_report, packet.proposed_dispute
        if self._store is None or report is None or not report.case_id or proposal is None:
            return
        conversation = self._store.get_conversation(report.case_id)
        if conversation is None:
            return
        key = (
            "dispute_approved"
            if proposal.status is ReviewStatus.APPROVED
            else "dispute_rejected"
        )
        text = bot_message(
            key,
            bot_language(conversation.language),
            folio=conversation.folio,
            case_id=proposal.dispute_case_id,
        )
        self._store.queue_reply(report.case_id, text)
        try:
            await deliver_replies(self._store, self._messenger, self._audit, report.case_id)
        except MessageNotSentError:
            logger.warning("Review of %s decided; the customer notice waits", packet.handoff_id)
