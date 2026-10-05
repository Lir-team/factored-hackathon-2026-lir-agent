"""Slack approval surface: asks a specialist to review, then posts the decision.

The specialist decides in the back office (behind IAP); the notice carries the approval id,
the action and a link, never customer data.
"""

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from lir_agent.domain.approvals import ApprovalRequest, Approver

logger = logging.getLogger(__name__)

REVIEW_PATH = "/v1/approvals"


class SlackApprovalSurface:
    """Posts specialist approval requests and their outcomes to the team channel."""

    name = "slack"

    def __init__(
        self,
        webhook_url: str,
        labels: Mapping[str, Any],
        public_base_url: str | None = None,
        mention: str = "<!here>",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Keep the webhook, labels, review link base and who is mentioned."""
        self._webhook_url = webhook_url
        self._labels = labels
        self._review_url = f"{(public_base_url or '').rstrip('/')}{REVIEW_PATH}"
        self._mention = mention
        self._client = client or httpx.AsyncClient(timeout=5.0)

    async def present(self, request: ApprovalRequest, link: str | None) -> bool:  # noqa: ARG002
        """Post the review request; False when the approver is not a specialist."""
        if request.approver is not Approver.SPECIALIST:
            return False
        text = self._labels["approval_pending"].format(
            approval_id=request.approval_id,
            action=request.action,
            mention=self._mention,
            review_url=self._review_url,
        )
        return await self._post(text, request.approval_id)

    async def report(self, request: ApprovalRequest) -> None:
        """Post how the request was decided and by whom (role and channel)."""
        text = self._labels["approval_decided"].format(
            approval_id=request.approval_id,
            action=request.action,
            status=request.status.value,
            role=request.decided_role.value if request.decided_role else "-",
            by=request.decided_by or "-",
        )
        await self._post(text, request.approval_id)

    async def _post(self, text: str, approval_id: str) -> bool:
        try:
            response = await self._client.post(self._webhook_url, json={"text": text})
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("Approval %s not posted to Slack", approval_id)
            return False
        return True
