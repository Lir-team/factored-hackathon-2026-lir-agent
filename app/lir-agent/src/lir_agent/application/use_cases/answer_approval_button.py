"""Use case: a customer pressed approve or reject under an approval message in their chat.

The chat surface authenticates the actor by the chat itself: the chat must be linked to the
request's case (a link only `/start` with the case's single-use token can create). The
decision then goes through `DecideApproval`, like any other surface.
"""

from collections.abc import Mapping
from typing import Any

from lir_agent.application.ports import (
    ApprovalRepository,
    AuditSink,
    CaseStore,
    ChatButtons,
)
from lir_agent.application.use_cases.approvals import DecideApproval
from lir_agent.domain.approvals import Actor, ApprovalError, Approver
from lir_agent.domain.telegram import parse_approval_callback

CHANNEL = "telegram"
_ERRORS = {"not_pending": "error_not_pending", "expired": "error_expired"}


class AnswerApprovalButton:
    """Use case: apply a button press and acknowledge it."""

    def __init__(
        self,
        repository: ApprovalRepository,
        decide: DecideApproval,
        store: CaseStore,
        bot: ChatButtons,
        audit: AuditSink,
        labels: Mapping[str, Mapping[str, Any]],
        require_sign_in: bool = False,
    ) -> None:
        """Keep the approval core, the case store (chat -> case) and the bot."""
        self._repository = repository
        self._decide = decide
        self._store = store
        self._bot = bot
        self._audit = audit
        self._labels = labels
        self._require_sign_in = require_sign_in

    async def execute(self, chat_id: int, callback_id: str, data: str | None) -> None:
        """Decide the request the button belongs to; any other button is ignored."""
        parsed = parse_approval_callback(data)
        if parsed is None:
            await self._bot.answer_callback(callback_id, "")
            return
        approval_id, approve = parsed
        request = self._repository.get(approval_id)
        link = self._store.get_chat_link(chat_id)
        labels = self._labels.get(request.language if request else "es") or self._labels["es"]
        if request is None or link is None or link.case_id != request.case_id:
            # Not this chat's request: say nothing about it.
            self._audit.record(
                "approval_refused", None, approval_id=approval_id, reason="chat_not_linked",
                channel=CHANNEL,
            )
            await self._bot.answer_callback(callback_id, labels["error_other"], alert=True)
            return
        if self._require_sign_in:
            # Step-up: holding the chat is not enough; the card opens with the bank's sign-in.
            await self._bot.answer_callback(callback_id, labels["sign_in_needed"], alert=True)
            return
        actor = Actor(
            role=Approver.CUSTOMER,
            identity=request.customer_id,
            channel=CHANNEL,
            proof="linked_chat",
        )
        try:
            # The message showed this request's content, and requests never change.
            decided = await self._decide.execute(
                approval_id, actor, approve, request.content_hash
            )
        except ApprovalError as error:
            text = labels[_ERRORS.get(error.code, "error_other")]
            await self._bot.answer_callback(callback_id, text, alert=True)
            return
        key = "approved" if decided.status == "approved" else "rejected"
        await self._bot.answer_callback(callback_id, labels[key])
