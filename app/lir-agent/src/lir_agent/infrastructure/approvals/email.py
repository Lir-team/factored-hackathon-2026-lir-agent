"""Email approval surface: tells the customer how an approval was decided.

It does not present requests (deciding needs the signed-in web card); it reports outcomes,
including a specialist's decision, so the customer learns it without a chat channel.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Protocol

from lir_agent.domain.approvals import ApprovalRequest
from lir_agent.infrastructure.approvals.telegram import outcome_text

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    """Sends one plain-text message."""

    def send(self, to: str, subject: str, body: str) -> None:
        """Send it; raises on failure."""
        ...


class EmailApprovalSurface:
    """Reports decided approvals to the customer by email."""

    name = "email"

    def __init__(
        self,
        sender: EmailSender,
        labels: Mapping[str, Mapping[str, Any]],
        recipient: str,
    ) -> None:
        """Keep the sender, the approval labels and the customer's address."""
        self._sender = sender
        self._labels = labels
        self._recipient = recipient

    async def present(self, request: ApprovalRequest, link: str | None) -> bool:  # noqa: ARG002
        """Never presents: the customer decides on the web card."""
        return False

    async def report(self, request: ApprovalRequest) -> None:
        """Email the outcome; a failed message is logged and never undoes the decision."""
        labels = self._labels.get(request.language) or self._labels["es"]
        outcome = outcome_text(request, labels)
        subject = labels["email_subject"].format(approval_id=request.approval_id)
        body = "\n\n".join([request.title, outcome, labels["email_footer"]])
        try:
            await asyncio.to_thread(self._sender.send, self._recipient, subject, body)
        except Exception:
            logger.warning("Outcome of approval %s not emailed", request.approval_id)
