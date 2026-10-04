"""HTTP API of the human in the loop, shared by every surface that is not a chat bot.

- The customer decides through a single-use link (the web card): `GET` shows the request,
  `POST .../decision` decides it. The link token is the customer's credential.
- A specialist (when the policy names one) decides through the operator API: their identity
  is the one IAP verified, and it is required here even where other routes fall back to a
  local operator.

Chat surfaces (Telegram) authenticate the actor their own way and call the same
`DecideApproval` use case; the rules and the audit trail do not depend on the surface.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from lir_agent.application.ports import ApprovalRepository
from lir_agent.application.use_cases import DecideApproval, VerifyApprovalLink
from lir_agent.domain.approvals import (
    Actor,
    ApprovalError,
    ApprovalRequest,
    ApprovalStatus,
    Approver,
)

_IAP_PREFIX = "accounts.google.com:"
# The link token is a credential: sent in a header so proxies and gateways do not log it.
LINK_TOKEN_HEADER = "X-Approval-Token"

# Domain reasons to HTTP statuses. "not_found" also covers a wrong link token.
_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "wrong_approver": status.HTTP_403_FORBIDDEN,
    "not_your_request": status.HTTP_403_FORBIDDEN,
    "not_pending": status.HTTP_409_CONFLICT,
    "content_changed": status.HTTP_409_CONFLICT,
    "expired": status.HTTP_410_GONE,
    "action_not_verified": status.HTTP_503_SERVICE_UNAVAILABLE,
}


class ApprovalDetailView(BaseModel):
    """One line of the card."""

    label: str
    value: str


class ApprovalView(BaseModel):
    """What a surface shows: the request, in the approver's language, and its decision."""

    approval_id: str
    action: str
    approver: Approver
    status: ApprovalStatus
    language: str
    title: str
    details: list[ApprovalDetailView]
    content_hash: str = Field(description="Send it back with the decision.")
    expires_at: datetime
    decided_at: datetime | None = None
    result: dict[str, Any] | None = None
    link: str | None = Field(
        default=None, description="Single-use web link for the customer, when issued."
    )

    @classmethod
    def of(cls, request: ApprovalRequest, link: str | None = None) -> "ApprovalView":
        """The view of a request."""
        return cls(
            approval_id=request.approval_id,
            action=request.action,
            approver=request.approver,
            status=request.status,
            language=request.language,
            title=request.title,
            details=[ApprovalDetailView(**d.model_dump()) for d in request.details],
            content_hash=request.content_hash,
            expires_at=request.expires_at,
            decided_at=request.decided_at,
            result=request.result,
            link=link,
        )


class CustomerDecision(BaseModel):
    """Body of `POST /v1/approvals/{id}/decision` (the web card)."""

    decision: Literal["approve", "reject"]
    token: str = Field(min_length=1, description="The token of the link the customer opened.")
    content_hash: str = Field(description="The `content_hash` of the card that was shown.")


class SpecialistDecision(BaseModel):
    """Body of `POST /v1/approvals/{id}/review` (back office)."""

    decision: Literal["approve", "reject"]
    content_hash: str
    note: str | None = Field(default=None, max_length=500, description="Internal.")


def _http_error(error: ApprovalError) -> HTTPException:
    return HTTPException(_STATUS.get(error.code, status.HTTP_400_BAD_REQUEST), error.code)


def approvals_router(
    repository: ApprovalRepository,
    decide: DecideApproval,
    verify_link: VerifyApprovalLink,
    identity_header: str,
) -> APIRouter:
    """The approval routes."""
    router = APIRouter(prefix="/v1/approvals", tags=["approvals"])

    def specialist(request: Request) -> Actor:
        """The IAP-verified identity; required, never a local fallback."""
        identity = request.headers.get(identity_header, "").strip()
        if not identity:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing caller identity")
        return Actor(
            role=Approver.SPECIALIST,
            identity=identity.removeprefix(_IAP_PREFIX),
            channel="backoffice",
        )

    @router.get("/{approval_id}")
    async def show(
        approval_id: str,
        token: Annotated[str, Header(alias=LINK_TOKEN_HEADER, min_length=1)],
    ) -> ApprovalView:
        """The card behind a customer's link (the token travels in a header, not the URL)."""
        try:
            request, _ = verify_link.execute(approval_id, token)
        except ApprovalError as error:
            raise _http_error(error) from None
        return ApprovalView.of(request)

    @router.post("/{approval_id}/decision")
    async def customer_decision(approval_id: str, body: CustomerDecision) -> ApprovalView:
        """The customer approves or rejects from the web card."""
        try:
            _, actor = verify_link.execute(approval_id, body.token)
            decided = await decide.execute(
                approval_id, actor, body.decision == "approve", body.content_hash
            )
        except ApprovalError as error:
            raise _http_error(error) from None
        return ApprovalView.of(decided)

    @router.get("")
    async def pending_reviews(
        actor: Annotated[Actor, Depends(specialist)],  # noqa: ARG001 - identity required
    ) -> list[ApprovalView]:
        """Requests waiting for a specialist, newest first."""
        return [
            ApprovalView.of(r)
            for r in repository.list(ApprovalStatus.PENDING, Approver.SPECIALIST)
        ]

    @router.post("/{approval_id}/review")
    async def specialist_decision(
        approval_id: str,
        body: SpecialistDecision,
        actor: Annotated[Actor, Depends(specialist)],
    ) -> ApprovalView:
        """A specialist approves or rejects a request that waits for one."""
        try:
            decided = await decide.execute(
                approval_id, actor, body.decision == "approve", body.content_hash, body.note
            )
        except ApprovalError as error:
            raise _http_error(error) from None
        return ApprovalView.of(decided)

    return router
