"""Use case: transfer the case to a human with the verified facts gathered so far."""

import uuid

from lir_agent.application.ports import CaseRepository
from lir_agent.domain.models import HandoffPacket
from lir_agent.domain.session import SessionState, utc_now

HANDOFF_ID_PREFIX = "HND-"


class RequestHandoff:
    """Use case: hand the case to a human with the verified facts gathered so far."""

    def __init__(self, cases: CaseRepository) -> None:
        """Keep the case service that stores handoffs."""
        self._cases = cases

    def execute(
        self,
        session: SessionState,
        summary: str | None = None,
        open_questions: list[str] | None = None,
    ) -> dict:
        """Submit the handoff once per session and read it back before reporting it."""
        if session.handoff_id:
            return {"status": "already_submitted", "handoff_id": session.handoff_id}

        packet = self._build_packet(session, summary, open_questions or [])
        handoff_id = self._cases.submit_handoff(packet)
        verified = (
            self._cases.get_handoff(handoff_id) == packet
        )  # read back before reporting success
        session.handoff_id = handoff_id
        session.record_action(
            {"action": "handoff", "handoff_id": handoff_id, "verified": verified}
        )
        return {
            "status": "submitted" if verified else "unverified",
            "handoff_id": handoff_id,
        }

    @staticmethod
    def _build_packet(
        session: SessionState, summary: str | None, open_questions: list[str]
    ) -> HandoffPacket:
        """Facts come from session state; only `model_summary` is model-generated, and labeled so."""
        return HandoffPacket(
            handoff_id=f"{HANDOFF_ID_PREFIX}{uuid.uuid4().hex[:10].upper()}",
            created_at=utc_now(),
            customer_id=session.require_customer_id(),
            customer_request=session.last_user_text,
            turn_outcome=session.turn_outcome,
            case_outcome=session.case_outcome,
            verified_evidence=session.evidence,
            actions_taken=session.actions,
            decisions=session.decisions or {},
            open_questions=open_questions,
            model_summary=summary,
        )
