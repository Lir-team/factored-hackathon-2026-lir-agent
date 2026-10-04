"""Human in the loop: important actions run only after a person approves them."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from lir_agent.application.use_cases import (
    DecideApproval,
    PresentApprovals,
    RequestApproval,
    VerifyApprovalLink,
)
from lir_agent.domain.approvals import (
    Actor,
    ApprovalDetail,
    ApprovalDraft,
    ApprovalError,
    ApprovalRequest,
    ApprovalStatus,
    Approver,
)
from lir_agent.domain.models import Lane, Outcome
from lir_agent.domain.session import SessionState
from lir_agent.infrastructure.approvals import InMemoryApprovalRepository
from lir_agent.infrastructure.audit import InMemoryAuditSink
from tests.support import IN_SCOPE, make_context, outcome_rule, tool, user_request

CUSTOMER = "CLI-DEMO-001"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
LINK = "https://web.example/aprobar?id={approval_id}&t={token}"
DRAFT = ApprovalDraft(
    params={"transaction_id": "TXN-1", "reason": "duplicado"},
    title="Abrir una disputa",
    details=[ApprovalDetail(label="Monto", value="245.50 MXN")],
    outcome=Outcome(lane=Lane.DISPUTE, rule_id="C7", reason="r", policy_version="v"),
)


class RecordingSurface:
    name = "recording"

    def __init__(self) -> None:
        self.presented: list[tuple[str, Approver, str | None]] = []
        self.reported: list[tuple[str, ApprovalStatus]] = []

    async def present(self, request: ApprovalRequest, link: str | None) -> bool:
        self.presented.append((request.approval_id, request.approver, link))
        return True

    async def report(self, request: ApprovalRequest) -> None:
        self.reported.append((request.approval_id, request.status))


class CountingAction:
    name = "open_dispute"

    def __init__(self) -> None:
        self.runs: list[str] = []

    def describe(self, session, args, labels):  # pragma: no cover - not used here
        return None

    def execute(self, request: ApprovalRequest) -> dict:
        self.runs.append(request.approval_id)
        return {"dispute_case_id": "DSP-1"}


class World:
    def __init__(self, approvers: list[Approver], clock=lambda: NOW) -> None:
        self.repository = InMemoryApprovalRepository()
        self.audit = InMemoryAuditSink()
        self.surface = RecordingSurface()
        self.action = CountingAction()
        self.present = PresentApprovals(self.repository, [self.surface], self.audit, LINK)
        self.decide = DecideApproval(
            self.repository,
            {"open_dispute": self.action},
            self.present,
            [self.surface],
            self.audit,
            clock,
        )
        self.verify = VerifyApprovalLink(self.repository, self.audit)
        state: dict = {}
        self.session = SessionState(state)
        self.session.start(CUSTOMER, timedelta(minutes=15), "test")
        self.request = RequestApproval(
            self.repository, self.audit, timedelta(minutes=60), lambda: NOW
        ).execute(
            self.session,
            action="open_dispute",
            draft=DRAFT,
            approvers=approvers,
            language="es",
            session_id="s1",
        )

    def link_token(self) -> str:
        links = asyncio.run(self.present.execute([self.request.approval_id]))
        link = links[self.request.approval_id]
        assert link is not None
        return link.rsplit("t=", 1)[1]

    def decide_as(self, actor: Actor, approve: bool = True, seen: str | None = None):
        return asyncio.run(
            self.decide.execute(self.request.approval_id, actor, approve, seen)
        )


CUSTOMER_ACTOR = Actor(role=Approver.CUSTOMER, identity=CUSTOMER, channel="web")


def test_an_approval_runs_the_action_once_and_keeps_who_decided():
    world = World([Approver.CUSTOMER])

    decided = world.decide_as(CUSTOMER_ACTOR, seen=world.request.content_hash)

    assert decided.status is ApprovalStatus.APPROVED
    assert (decided.decided_by, decided.channel) == (CUSTOMER, "web")
    assert decided.result == {"dispute_case_id": "DSP-1"}
    assert world.action.runs == [world.request.approval_id]
    [entry] = [e for e in world.audit.entries if e["event"] == "approval_decided"]
    assert (entry["decision"], entry["actor"], entry["seen_hash"]) == (
        "approved",
        CUSTOMER,
        world.request.content_hash,
    )
    assert world.surface.reported == [(world.request.approval_id, ApprovalStatus.APPROVED)]
    with pytest.raises(ApprovalError, match="not_pending"):
        world.decide_as(CUSTOMER_ACTOR)
    assert len(world.action.runs) == 1


def test_a_rejection_runs_nothing():
    world = World([Approver.CUSTOMER])

    decided = world.decide_as(CUSTOMER_ACTOR, approve=False)

    assert decided.status is ApprovalStatus.REJECTED
    assert world.action.runs == []


@pytest.mark.parametrize(
    ("actor", "seen", "code"),
    [
        (Actor(role=Approver.CUSTOMER, identity="CLI-OTHER", channel="web"), None, "not_your_request"),
        (Actor(role=Approver.SPECIALIST, identity="ana@bank", channel="backoffice"), None, "wrong_approver"),
        (CUSTOMER_ACTOR, "a-different-hash", "content_changed"),
    ],
)
def test_who_may_decide_and_on_what(actor, seen, code):
    world = World([Approver.CUSTOMER])
    with pytest.raises(ApprovalError, match=code):
        world.decide_as(actor, seen=seen)
    assert world.action.runs == []
    assert any(e["event"] == "approval_refused" for e in world.audit.entries)


def test_an_expired_request_is_closed_and_runs_nothing():
    world = World([Approver.CUSTOMER], clock=lambda: NOW + timedelta(hours=2))

    with pytest.raises(ApprovalError, match="expired"):
        world.decide_as(CUSTOMER_ACTOR)

    stored = world.repository.get(world.request.approval_id)
    assert stored is not None and stored.status is ApprovalStatus.EXPIRED
    assert world.action.runs == []


def test_in_a_chain_only_the_last_approval_runs_the_action():
    world = World([Approver.CUSTOMER, Approver.SPECIALIST])

    world.decide_as(CUSTOMER_ACTOR)

    assert world.action.runs == []
    [(next_id, approver, link)] = world.surface.presented
    assert (approver, link) == (Approver.SPECIALIST, None)  # web links are for customers
    specialist = Actor(role=Approver.SPECIALIST, identity="ana@bank", channel="backoffice")
    decided = asyncio.run(world.decide.execute(next_id, specialist, True))
    assert decided.previous_approval_id == world.request.approval_id
    assert world.action.runs == [next_id]


def test_a_web_link_identifies_the_customer_and_works_for_one_decision():
    world = World([Approver.CUSTOMER])
    token = world.link_token()

    request, actor = world.verify.execute(world.request.approval_id, token)
    assert actor == Actor(role=Approver.CUSTOMER, identity=CUSTOMER, channel="web", proof="link")
    with pytest.raises(ApprovalError, match="not_found"):
        world.verify.execute(world.request.approval_id, "a-wrong-token")
    refused = [e for e in world.audit.entries if e["event"] == "approval_link_refused"]
    assert [e["approval_id"] for e in refused] == [world.request.approval_id]

    world.decide_as(actor, seen=request.content_hash)
    with pytest.raises(ApprovalError, match="not_found"):
        world.verify.execute(world.request.approval_id, token)


def test_the_same_action_twice_is_one_request():
    world = World([Approver.CUSTOMER])
    again = RequestApproval(world.repository, world.audit, timedelta(minutes=60)).execute(
        world.session,
        action="open_dispute",
        draft=DRAFT,
        approvers=[Approver.CUSTOMER],
        language="es",
    )
    assert again.approval_id == world.request.approval_id


def test_a_customer_who_rejects_an_explanation_may_approve_a_dispute(make_harness):
    h = make_harness({**IN_SCOPE, "rechaza_explicacion": (True, 0.9)})
    context = make_context()
    session = SessionState(context.state)
    h.callbacks.before_model(context, user_request("No reconozco el cargo de Spotify"))
    ref = session.ref_for("TXN-D1-004")  # the customer paid this merchant before: explain
    assert h.toolkit.get_transaction_evidence(context, ref)["outcome"]["lane"] == "explain"

    h.callbacks.before_model(context, user_request("Igual no lo reconozco"))

    assert outcome_rule(session) == "T3c_explanation_rejected"
    result = h.callbacks.before_tool(
        tool("open_dispute"), {"transaction_ref": ref, "reason": "No lo reconoce"}, context
    )
    assert result is not None and result["status"] == "approval_requested"
    request = h.container.approvals.get(result["approval_id"])
    assert request is not None and (request.approver, request.then) == ("customer", [])


def test_accepting_an_explanation_puts_nothing_to_approval(make_harness):
    h = make_harness({**IN_SCOPE, "rechaza_explicacion": (False, 0.1)})
    context = make_context()
    session = SessionState(context.state)
    h.callbacks.before_model(context, user_request("No reconozco el cargo de Spotify"))
    ref = session.ref_for("TXN-D1-004")
    h.toolkit.get_transaction_evidence(context, ref)
    h.callbacks.before_model(context, user_request("Ah, ya me acordé, gracias"))

    result = h.callbacks.before_tool(
        tool("open_dispute"), {"transaction_ref": ref, "reason": "x"}, context
    )
    assert result is not None and result["reason"] == "action_not_allowed"
