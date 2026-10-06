"""Use case: transfer the case to a human with the verified facts gathered so far."""

import logging
import uuid

from lir_agent.application.ports import CaseRepository, HandoffNotifier
from lir_agent.domain.models import HandoffPacket
from lir_agent.domain.policy import PolicyConfig
from lir_agent.domain.session import SessionState, utc_now

HANDOFF_ID_PREFIX = "HND-"

logger = logging.getLogger(__name__)


class RequestHandoff:
    """Use case: hand the case to a human with the verified facts gathered so far."""

    def __init__(
        self,
        cases: CaseRepository,
        policy: PolicyConfig,
        notifier: HandoffNotifier | None = None,
    ) -> None:
        """Keep the case service, the policy's open questions and the team notifier."""
        self._cases = cases
        self._policy = policy
        self._notifier = notifier

    def execute(
        self,
        session: SessionState,
        summary: str | None = None,
        open_questions: list[str] | None = None,
    ) -> dict:
        """Submit the handoff once per session and read it back before reporting it."""
        if session.handoff_id:
            return {"status": "already_submitted", "handoff_id": session.handoff_id}

        questions = self._questions(session, open_questions or [])
        packet = self._build_packet(session, summary, questions)
        verified = self._submit(packet)
        session.handoff_id = packet.handoff_id
        session.record_action(
            {"action": "handoff", "handoff_id": packet.handoff_id, "verified": verified}
        )
        if verified and self._notifier:
            self._notify(packet)
        return {
            "status": "submitted" if verified else "unverified",
            "handoff_id": packet.handoff_id,
        }

    def _notify(self, packet: HandoffPacket) -> None:
        try:
            self._notifier.notify(packet)  # type: ignore[union-attr]
        except Exception as exc:
            # The type only: an HTTP error message carries the webhook URL, which is a secret.
            logger.warning(
                "Handoff %s stored but the team notice failed (%s)",
                packet.handoff_id,
                type(exc).__name__,
            )

    def attach_evidence(self, session: SessionState) -> None:
        """Add evidence gathered after the handoff (e.g. the charge a theft victim describes).

        A turn-level escalation (theft, wants a human) is created before any lookup; the
        specialist must still receive the charge the agent identifies afterwards.
        """
        stored = self._cases.get_handoff(session.handoff_id or "")
        if stored is None:
            return
        updated = stored.model_copy(
            update={
                "case_outcome": session.case_outcome,
                "verified_evidence": session.evidence,
                "actions_taken": session.actions,
                "open_questions": self._questions(session, stored.open_questions),
            }
        )
        self._submit(updated)

    def _questions(self, session: SessionState, extra: list[str]) -> list[str]:
        """Policy questions for the rules involved, then the model's, without repeats."""
        questions = self._policy.open_questions_for(
            session.turn_outcome, session.case_outcome
        )
        return list(dict.fromkeys([*questions, *extra]))

    def _submit(self, packet: HandoffPacket) -> bool:
        """Store the packet and read it back before reporting success."""
        handoff_id = self._cases.submit_handoff(packet)
        return self._cases.get_handoff(handoff_id) == packet

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
            case_report=session.case_report,
        )
