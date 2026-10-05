"""Telegram approval surface: the request as a message with approve and reject buttons.

Presents requests that wait for the customer of a case whose chat is linked; a request
created before the customer linked their chat is presented when they link it. A button press
comes back as a callback (`AnswerApprovalButton`), which hands it to `DecideApproval`.
"""

import logging
from collections.abc import Mapping
from typing import Any

from lir_agent.application.ports import CaseStore, ChatButtons, MessageNotSentError
from lir_agent.domain.approvals import ApprovalRequest, ApprovalStatus, Approver
from lir_agent.domain.telegram import InlineButton, approval_callback

logger = logging.getLogger(__name__)

type Labels = Mapping[str, Mapping[str, Any]]


def outcome_text(request: ApprovalRequest, labels: Mapping[str, Any]) -> str:
    """How a decided request reads for the customer, whoever decided it."""
    prefix = "specialist_" if request.decided_role is Approver.SPECIALIST else ""
    if request.status is ApprovalStatus.REJECTED:
        return labels[f"{prefix}rejected"]
    if not request.is_last:
        return labels["awaiting_next_approval"]
    fallback = labels[f"{prefix}approved"]
    template = labels.get(f"{request.action}_{prefix}approved") or fallback
    try:
        return template.format(**(request.result or {}))
    except KeyError:
        return fallback


class TelegramApprovalSurface:
    """Shows a customer's approval requests in their case chat."""

    name = "telegram"

    def __init__(
        self,
        store: CaseStore,
        bot: ChatButtons,
        labels: Labels,
        require_sign_in: bool = False,
    ) -> None:
        """Keep the case store (case -> chat), the bot, the labels and the step-up setting.

        With `require_sign_in` the message carries no approve or reject buttons: only the
        link to the web card, which opens with the bank's sign-in.
        """
        self._store = store
        self._bot = bot
        self._labels = labels
        self._require_sign_in = require_sign_in

    def _chat(self, request: ApprovalRequest) -> int | None:
        """The chat linked to the request's case, if the customer linked one."""
        if not request.case_id:
            return None
        chat_id = self._store.get_case_chat(request.case_id)
        if chat_id is None:
            return None
        link = self._store.get_chat_link(chat_id)
        return chat_id if link and link.case_id == request.case_id else None

    def _labels_for(self, request: ApprovalRequest) -> Mapping[str, Any]:
        return self._labels.get(request.language) or self._labels["es"]

    async def present(self, request: ApprovalRequest, link: str | None) -> bool:
        """Send the card with its buttons; False when there is no linked chat (yet)."""
        chat_id = self._chat(request) if request.approver is Approver.CUSTOMER else None
        if chat_id is None:
            return False
        labels = self._labels_for(request)
        lines = [f"• {d.label}: {d.value}" for d in request.details]
        web = link if link and link.startswith("https://") else None  # Telegram rule
        hint = labels["hint_sign_in"] if self._require_sign_in else labels["hint"]
        text = "\n".join([request.title, "", *lines, "", hint])
        rows: list[list[InlineButton]] = []
        if self._require_sign_in:
            if web is None:
                return False  # nowhere to approve from this chat
            rows.append([InlineButton(labels["review_web"], url=web)])
        else:
            rows.append(
                [
                    InlineButton(labels["approve"], approval_callback(request.approval_id, True)),
                    InlineButton(labels["reject"], approval_callback(request.approval_id, False)),
                ]
            )
            if web:
                rows.append([InlineButton(labels["view_web"], url=web)])
        try:
            await self._bot.send_buttons(chat_id, text, rows)
        except MessageNotSentError:
            logger.warning("Approval %s not shown on Telegram", request.approval_id)
            return False
        return True

    async def report(self, request: ApprovalRequest) -> None:
        """Tell the customer, in their chat, how the request was decided (by whomever)."""
        chat_id = self._chat(request)
        if chat_id is None or request.decided_role is None:
            return
        try:
            await self._bot.send_buttons(chat_id, outcome_text(request, self._labels_for(request)), [])
        except MessageNotSentError:
            logger.warning("Outcome of approval %s not sent", request.approval_id)
