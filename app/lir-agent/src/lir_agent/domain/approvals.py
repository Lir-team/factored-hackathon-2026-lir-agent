"""Human in the loop: important actions run only after a person approves them.

The agent never runs an important action (opening a dispute) from the conversation. It
creates an `ApprovalRequest` with the exact action, its parameters and what the approver
will read. A person approves or rejects it on a surface (Telegram, web, a back-office
page); every surface hands the decision to the same core, which checks it here:

- the actor plays the role the request waits for, and a customer can only decide their own;
- the decision is about the content the actor saw (`content_hash`);
- the request is still pending and not expired (one decision per request).

Requests may form a chain (the customer approves, then a specialist): only the last
approval runs the action.
"""

import hashlib
import json
import secrets
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lir_agent.domain.errors import DomainError
from lir_agent.domain.language import Language
from lir_agent.domain.models import Outcome

APPROVAL_ID_PREFIX = "APR-"
_LINK_TOKEN_BYTES = 24


class Approver(StrEnum):
    """Who must decide a request."""

    CUSTOMER = "customer"
    SPECIALIST = "specialist"


class ApprovalStatus(StrEnum):
    """Where a request stands."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalDetail(BaseModel):
    """One line the approver reads (label and value, already in their language)."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: str


class ApprovalDraft(BaseModel):
    """What an action adapter says the approver must read, and what will run."""

    model_config = ConfigDict(frozen=True)

    params: dict[str, Any]
    title: str
    details: list[ApprovalDetail]
    outcome: Outcome = Field(description="The policy decision that led to the action.")


class Actor(BaseModel):
    """Who decides, as authenticated by the surface the decision came from."""

    model_config = ConfigDict(frozen=True)

    role: Approver
    identity: str = Field(description="Customer id, or the specialist's verified identity.")
    channel: str = Field(description="The surface: telegram, web, backoffice, ...")


class ApprovalRequest(BaseModel):
    """An important action waiting for a person, and its decision once made."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    action: str
    params: dict[str, Any]
    customer_id: str
    approver: Approver
    # Approvers still needed after this one; only the last approval runs the action.
    then: list[Approver] = Field(default_factory=list)
    previous_approval_id: str | None = None
    language: Language
    title: str
    details: list[ApprovalDetail]
    content_hash: str
    policy_version: str
    rule_id: str
    session_id: str | None = None
    case_id: str | None = None
    created_at: datetime
    expires_at: datetime
    # Single-use link credential for surfaces without their own identity (e.g. web).
    link_token_hash: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    decided_role: Approver | None = None
    decided_at: datetime | None = None
    channel: str | None = None
    note: str | None = Field(default=None, description="The approver's note; internal.")
    result: dict[str, Any] | None = None

    @property
    def is_last(self) -> bool:
        """Whether approving this request runs the action."""
        return not self.then


class ApprovalError(DomainError):
    """Why a decision cannot be applied (the code is the API's error reason)."""

    def __init__(self, code: str) -> None:
        """Keep the machine-readable reason."""
        super().__init__(code)
        self.code = code


def content_hash(action: str, params: dict[str, Any], details: list[ApprovalDetail]) -> str:
    """Fingerprint of what the approver reads and what will run."""
    canonical = json.dumps(
        {
            "action": action,
            "params": params,
            "details": [d.model_dump() for d in details],
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_link_token() -> tuple[str, str]:
    """A link credential and the hash that is stored (the token itself never is)."""
    token = secrets.token_urlsafe(_LINK_TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    """The stored form of a link credential."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_decision(
    request: ApprovalRequest, actor: Actor, seen_hash: str | None, now: datetime
) -> None:
    """Raise when this actor may not decide this request now.

    Raises:
        ApprovalError: `not_pending`, `expired`, `wrong_approver`, `not_your_request`
            or `content_changed`.
    """
    if request.status is not ApprovalStatus.PENDING:
        raise ApprovalError("not_pending")
    if now >= request.expires_at:
        raise ApprovalError("expired")
    if actor.role is not request.approver:
        raise ApprovalError("wrong_approver")
    if actor.role is Approver.CUSTOMER and actor.identity != request.customer_id:
        raise ApprovalError("not_your_request")
    if seen_hash is not None and seen_hash != request.content_hash:
        raise ApprovalError("content_changed")
