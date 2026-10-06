"""Use case: a specialist resolves a handed-off case, and the customer and the team are told.

Accepting a claim records the decision and tells the customer the bank will follow up; it
moves no money and promises no refund (the case service is a mock).
"""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from lir_agent.application.ports import (
    AuditSink,
    CaseRepository,
    CaseStore,
    HandoffNotifier,
    MessageNotSentError,
    Messenger,
)
from lir_agent.domain.language import DEFAULT_LANGUAGE
from lir_agent.domain.models import HandoffPacket, HandoffResolution
from lir_agent.domain.session import utc_now

logger = logging.getLogger(__name__)


class HandoffResolutionError(Exception):
    """Why a case cannot be resolved: `not_found` or `already_resolved`."""

    def __init__(self, code: str) -> None:
        """Keep the machine-readable reason."""
        super().__init__(code)
        self.code = code


class ResolveHandoff:
    """Record a specialist's decision on a case once, then tell the customer and the team."""

    def __init__(
        self,
        cases: CaseRepository,
        case_store: CaseStore,
        audit: AuditSink,
        labels: Mapping[str, Mapping[str, Any]],
        messenger: Messenger | None = None,
        notifier: HandoffNotifier | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """Keep the case service, the chat links, the customer texts and the notifiers."""
        self._cases = cases
        self._case_store = case_store
        self._audit = audit
        self._labels = labels
        self._messenger = messenger
        self._notifier = notifier
        self._clock = clock

    async def execute(
        self, handoff_id: str, resolved_by: str, accepted: bool, note: str | None = None
    ) -> HandoffPacket:
        """Resolve the case.

        Raises:
            HandoffResolutionError: `not_found`, or `already_resolved`.
        """
        packet = self._cases.get_handoff(handoff_id)
        if packet is None:
            raise HandoffResolutionError("not_found")
        if packet.resolution is not None:
            raise HandoffResolutionError("already_resolved")
        resolved = packet.model_copy(
            update={
                "resolution": HandoffResolution(
                    accepted=accepted,
                    resolved_by=resolved_by,
                    resolved_at=self._clock(),
                    note=note,
                )
            }
        )
        self._cases.submit_handoff(resolved)
        told = await self._tell_customer(resolved, accepted)
        if told:
            resolved = self._mark_notified(resolved)
        self._audit.record(
            "handoff_resolved",
            None,
            handoff_id=handoff_id,
            decision="accepted" if accepted else "rejected",
            operator=resolved_by,
            customer_told=told,
        )
        self._tell_team(resolved)
        return resolved

    async def deliver_pending(self, case_id: str) -> None:
        """Send outcomes decided before the customer linked their chat (called on link)."""
        for packet in self._cases.list_handoffs():
            resolution = packet.resolution
            if (
                resolution is None
                or resolution.customer_notified
                or not packet.case_report
                or packet.case_report.case_id != case_id
            ):
                continue
            if await self._tell_customer(packet, resolution.accepted):
                self._mark_notified(packet)
                self._audit.record(
                    "handoff_resolution_delivered", None, handoff_id=packet.handoff_id
                )

    def _mark_notified(self, packet: HandoffPacket) -> HandoffPacket:
        resolution = packet.resolution
        assert resolution is not None
        notified = packet.model_copy(
            update={"resolution": resolution.model_copy(update={"customer_notified": True})}
        )
        self._cases.submit_handoff(notified)
        return notified

    async def _tell_customer(self, packet: HandoffPacket, accepted: bool) -> bool:
        """Send the outcome to the case's chat; False when there is none or it failed."""
        case_id = packet.case_report.case_id if packet.case_report else None
        chat_id = self._case_store.get_case_chat(case_id) if case_id else None
        link = self._case_store.get_chat_link(chat_id) if chat_id is not None else None
        if self._messenger is None or chat_id is None or not link or link.case_id != case_id:
            return False
        labels = self._labels.get(link.language) or self._labels[DEFAULT_LANGUAGE]
        text = labels["handoff_accepted" if accepted else "handoff_rejected"]
        try:
            await self._messenger.send(chat_id, text)
        except MessageNotSentError:
            logger.warning("Resolution of %s not sent to the customer", packet.handoff_id)
            return False
        return True

    def _tell_team(self, packet: HandoffPacket) -> None:
        if self._notifier is None:
            return
        try:
            self._notifier.notify_resolution(packet)
        except Exception as exc:
            # The type only: an HTTP error message carries the webhook URL, which is a secret.
            logger.warning(
                "Resolution of %s stored but the team notice failed (%s)",
                packet.handoff_id,
                type(exc).__name__,
            )
