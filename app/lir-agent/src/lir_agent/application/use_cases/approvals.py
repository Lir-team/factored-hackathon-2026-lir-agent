"""Use cases of the human in the loop: request, present and decide approvals.

Surfaces (Telegram, web, back office) plug in through the `ApprovalSurface` port: they show
requests and report outcomes, and hand every decision to `DecideApproval` with the actor
they authenticated. Rules and audit live here, whatever the surface.
"""

import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from lir_agent.application.ports import (
    ApprovalAction,
    ApprovalRepository,
    ApprovalSurface,
    AuditSink,
)
from lir_agent.domain.approvals import (
    APPROVAL_ID_PREFIX,
    Actor,
    ApprovalDraft,
    ApprovalError,
    ApprovalRequest,
    ApprovalStatus,
    Approver,
    check_decision,
    content_hash,
    hash_token,
    new_link_token,
)
from lir_agent.domain.language import DEFAULT_LANGUAGE, Language, detect_language
from lir_agent.domain.session import SessionState, utc_now

type Clock = Callable[[], datetime]


class RequestApproval:
    """Create an approval request for an action the agent may not run itself."""

    def __init__(
        self,
        repository: ApprovalRepository,
        audit: AuditSink,
        ttl: timedelta,
        clock: Clock = utc_now,
    ) -> None:
        """Keep the request store, the audit sink and how long a request stays open."""
        self._repository = repository
        self._audit = audit
        self._ttl = ttl
        self._clock = clock

    def execute(
        self,
        session: SessionState,
        *,
        action: str,
        draft: ApprovalDraft,
        approvers: list[Approver],
        language: Language,
        session_id: str | None = None,
    ) -> ApprovalRequest:
        """Store the request (or return the pending one for the same action) and queue it.

        The conversation layer presents the queued requests after the agent's reply.
        """
        for known in session.approval_ids:
            existing = self._repository.get(known)
            if (
                existing
                and existing.status is ApprovalStatus.PENDING
                and (existing.action, existing.params) == (action, draft.params)
            ):
                return existing
        now = self._clock()
        request = ApprovalRequest(
            approval_id=f"{APPROVAL_ID_PREFIX}{uuid.uuid4().hex[:12].upper()}",
            action=action,
            params=draft.params,
            customer_id=session.require_customer_id(),
            approver=approvers[0],
            then=approvers[1:],
            language=language,
            title=draft.title,
            details=draft.details,
            content_hash=content_hash(action, draft.params, draft.details),
            policy_version=draft.outcome.policy_version,
            rule_id=draft.outcome.rule_id,
            session_id=session_id,
            case_id=session.case_report.case_id if session.case_report else None,
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._repository.save(request)
        session.approval_ids = [*session.approval_ids, request.approval_id]
        self._audit.record(
            "approval_requested",
            session_id,
            approval_id=request.approval_id,
            action=action,
            approver=request.approver.value,
            then=[a.value for a in request.then],
            content_hash=request.content_hash,
            rule_id=request.rule_id,
        )
        return request


NEXT_STEP = (
    "The customer sees this with approve and reject buttons next to your reply: tell them to "
    "decide there. Nothing has been done yet: never say it was, and do not promise the outcome."
)


class RequestActionApproval:
    """The tool guard's gate: a call to an important action becomes an approval request."""

    def __init__(
        self,
        request_approval: RequestApproval,
        actions: Mapping[str, ApprovalAction],
        approvers: Mapping[str, list[Approver]],
        labels: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Keep the core, the action adapters, the policy's approvers and the card labels."""
        missing = sorted(set(approvers) - set(actions))
        if missing:
            raise ValueError(f"Approval required for actions without an adapter: {missing}")
        self._request_approval = request_approval
        self._actions = dict(actions)
        self._approvers = dict(approvers)
        self._labels = labels

    def requires_approval(self, tool_name: str) -> bool:
        """Whether the policy makes this tool an important action."""
        return tool_name in self._approvers

    def execute(
        self,
        session: SessionState,
        tool_name: str,
        args: Mapping[str, Any],
        session_id: str | None = None,
    ) -> dict:
        """The tool result the model gets: the request created, or why it was not."""
        language = detect_language(session.last_user_text)
        labels = self._labels.get(language) or self._labels[DEFAULT_LANGUAGE]
        draft = self._actions[tool_name].describe(session, args, labels)
        if draft is None:
            return {"status": "blocked", "reason": "action_not_allowed"}
        request = self._request_approval.execute(
            session,
            action=tool_name,
            draft=draft,
            approvers=self._approvers[tool_name],
            language=language,
            session_id=session_id,
        )
        return {
            "status": "approval_requested",
            "approval_id": request.approval_id,
            "next_step": NEXT_STEP,
        }


class PresentApprovals:
    """Show requests on every surface that serves their approver."""

    def __init__(
        self,
        repository: ApprovalRepository,
        surfaces: Iterable[ApprovalSurface],
        audit: AuditSink,
        link_template: str | None = None,
    ) -> None:
        """Keep the surfaces and the web link template (`{approval_id}`, `{token}`)."""
        self._repository = repository
        self._surfaces = list(surfaces)
        self._audit = audit
        self._link_template = link_template

    async def execute(self, approval_ids: Iterable[str]) -> dict[str, str | None]:
        """Present each pending request; returns its web link (None without a template)."""
        links: dict[str, str | None] = {}
        for approval_id in approval_ids:
            request = self._repository.get(approval_id)
            if request is None or request.status is not ApprovalStatus.PENDING:
                continue
            link = self._issue_link(request)
            links[approval_id] = link
            shown_on = [
                surface.name
                for surface in self._surfaces
                if await surface.present(request, link)
            ]
            self._audit.record(
                "approval_presented",
                request.session_id,
                approval_id=approval_id,
                surfaces=shown_on,
                link=link is not None,
            )
        return links

    async def execute_for_case(self, case_id: str) -> dict[str, str | None]:
        """Present a case's pending customer requests (e.g. once its chat is linked)."""
        pending = [
            r.approval_id
            for r in reversed(self._repository.list(ApprovalStatus.PENDING, Approver.CUSTOMER))
            if r.case_id == case_id
        ]
        return await self.execute(pending)

    def _issue_link(self, request: ApprovalRequest) -> str | None:
        """A fresh single-use web link; the stored hash replaces any earlier one."""
        if not self._link_template or request.approver is not Approver.CUSTOMER:
            return None
        token, token_hash = new_link_token()
        self._repository.save(request.model_copy(update={"link_token_hash": token_hash}))
        return self._link_template.format(approval_id=request.approval_id, token=token)


class VerifyApprovalLink:
    """Who holds a web link: the customer the request belongs to, while it is pending."""

    def __init__(self, repository: ApprovalRepository) -> None:
        """Keep the request store."""
        self._repository = repository

    def execute(self, approval_id: str, token: str, channel: str = "web") -> tuple[
        ApprovalRequest, Actor
    ]:
        """The request and its customer as the actor.

        Raises:
            ApprovalError: `not_found` for an unknown request or a wrong token.
        """
        request = self._repository.get(approval_id)
        if (
            request is None
            or not request.link_token_hash
            or hash_token(token) != request.link_token_hash
        ):
            raise ApprovalError("not_found")  # never tell which part was wrong
        return request, Actor(
            role=Approver.CUSTOMER, identity=request.customer_id, channel=channel
        )


class DecideApproval:
    """Apply a person's decision: the single entry point of every surface."""

    def __init__(
        self,
        repository: ApprovalRepository,
        actions: Mapping[str, ApprovalAction],
        presenter: PresentApprovals,
        surfaces: Iterable[ApprovalSurface],
        audit: AuditSink,
        clock: Clock = utc_now,
    ) -> None:
        """Keep the store, the actions that may run, the surfaces and the audit sink."""
        self._repository = repository
        self._actions = dict(actions)
        self._presenter = presenter
        self._surfaces = list(surfaces)
        self._audit = audit
        self._clock = clock
        self._lock = threading.Lock()  # one decision per request, whatever the surface

    async def execute(
        self,
        approval_id: str,
        actor: Actor,
        approve: bool,
        seen_hash: str | None = None,
        note: str | None = None,
    ) -> ApprovalRequest:
        """Approve (the next approver, or the action runs) or reject a pending request.

        Raises:
            ApprovalError: `not_found`, or why this actor may not decide it now.
        """
        decided, follow_up = self._decide(approval_id, actor, approve, seen_hash, note)
        if follow_up is not None:
            await self._presenter.execute([follow_up.approval_id])
        for surface in self._surfaces:
            await surface.report(decided)
        return decided

    def _decide(
        self,
        approval_id: str,
        actor: Actor,
        approve: bool,
        seen_hash: str | None,
        note: str | None,
    ) -> tuple[ApprovalRequest, ApprovalRequest | None]:
        with self._lock:
            request = self._repository.get(approval_id)
            if request is None:
                raise ApprovalError("not_found")
            now = self._clock()
            try:
                check_decision(request, actor, seen_hash, now)
            except ApprovalError as error:
                if error.code == "expired" and request.status is ApprovalStatus.PENDING:
                    self._repository.save(
                        request.model_copy(update={"status": ApprovalStatus.EXPIRED})
                    )
                self._audit.record(
                    "approval_refused",
                    request.session_id,
                    approval_id=approval_id,
                    reason=error.code,
                    actor=actor.identity,
                    role=actor.role.value,
                    channel=actor.channel,
                )
                raise
            decision = {
                "decided_by": actor.identity,
                "decided_role": actor.role,
                "decided_at": now,
                "channel": actor.channel,
                "note": note,
                "link_token_hash": None,  # a link works for one decision only
            }
            follow_up = None
            if not approve:
                decided = request.model_copy(
                    update={**decision, "status": ApprovalStatus.REJECTED}
                )
            elif request.is_last:
                result = self._actions[request.action].execute(request)
                decided = request.model_copy(
                    update={**decision, "status": ApprovalStatus.APPROVED, "result": result}
                )
            else:
                decided = request.model_copy(
                    update={**decision, "status": ApprovalStatus.APPROVED}
                )
                follow_up = request.model_copy(
                    update={
                        "approval_id": f"{APPROVAL_ID_PREFIX}{uuid.uuid4().hex[:12].upper()}",
                        "approver": request.then[0],
                        "then": request.then[1:],
                        "previous_approval_id": request.approval_id,
                        "created_at": now,
                        "expires_at": now + (request.expires_at - request.created_at),
                        "link_token_hash": None,
                    }
                )
                self._repository.save(follow_up)
            self._repository.save(decided)
        self._audit.record(
            "approval_decided",
            decided.session_id,
            approval_id=approval_id,
            action=decided.action,
            decision=decided.status.value,
            actor=actor.identity,
            role=actor.role.value,
            channel=actor.channel,
            content_hash=decided.content_hash,
            seen_hash=seen_hash,
            result=decided.result,
            next_approval_id=follow_up.approval_id if follow_up else None,
        )
        return decided, follow_up
