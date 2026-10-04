"""Human in the loop: a customer rejects an explanation, a person approves the dispute."""

import asyncio

import pytest

from lir_agent.application.use_cases import (
    ProposalAlreadyDecidedError,
    ProposalNotFoundError,
    ReviewDispute,
)
from lir_agent.container import build_container
from lir_agent.domain.case_intake import CaseConversation, CaseReport
from lir_agent.domain.models import ReviewStatus
from lir_agent.domain.session import SessionState
from lir_agent.domain.telegram import ChatLink
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.infrastructure.case_store import InMemoryCaseStore
from tests.support import (
    IN_SCOPE,
    Harness,
    ScriptedDecisions,
    make_context,
    outcome_rule,
    tool,
    user_request,
)

HABITUAL = "TXN-D1-004"  # explained: the customer paid this merchant three times before
REVIEWER = "specialist@bank.example"
CASE_ID = "case-123"
CHAT = 7001


class RecordingMessenger:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture
def world(settings):
    decisions = ScriptedDecisions(
        {**IN_SCOPE, "rechaza_explicacion": (False, 0.0), "confirma": (False, 0.0)}
    )
    audit = InMemoryAuditSink()
    harness = Harness(build_container(settings, audit=audit, decisions=decisions), audit)
    return harness, decisions


def _explained(harness: Harness, context) -> str:
    """Investigate a habitual charge: the policy explains it."""
    session = SessionState(context.state)
    harness.callbacks.before_model(context, user_request("No reconozco un cargo"))
    ref = session.ref_for(HABITUAL)
    result = harness.toolkit.get_transaction_evidence(context, ref)
    assert result["outcome"]["lane"] == "explain"
    return ref


def _proposed(world, context) -> tuple[str, str]:
    """The customer rejects the explanation and confirms the dispute details."""
    harness, decisions = world
    ref = _explained(harness, context)
    decisions.answers["rechaza_explicacion"] = (True, 0.9)
    harness.callbacks.before_model(context, user_request("Igual no lo reconozco"))
    decisions.answers["confirma"] = (True, 0.95)
    harness.callbacks.before_model(context, user_request("Sí, confirmo"))
    args = {"transaction_ref": ref, "reason": "No reconoce el cargo"}
    assert harness.callbacks.before_tool(tool("propose_dispute"), args, context) is None
    result = harness.toolkit.propose_dispute(context, **args)
    assert result["status"] == "submitted_for_review"
    return ref, result["handoff_id"]


def test_rejecting_an_explanation_drafts_a_dispute_for_review(world):
    harness, decisions = world
    context = make_context()
    session = SessionState(context.state)
    ref = _explained(harness, context)

    decisions.answers["rechaza_explicacion"] = (True, 0.9)
    harness.callbacks.before_model(context, user_request("Igual no lo reconozco"))

    assert outcome_rule(session) == "T3c_explanation_rejected"
    assert session.turn_lane == "review"
    # Nothing goes to a person before the customer confirms the details.
    args = {"transaction_ref": ref, "reason": "No reconoce el cargo"}
    denied = harness.callbacks.before_tool(tool("propose_dispute"), args, context)
    assert denied is not None and denied["status"] == "confirmation_required"


def test_accepting_the_explanation_drafts_nothing(world):
    harness, _ = world
    context = make_context()
    _explained(harness, context)

    harness.callbacks.before_model(context, user_request("Ah, ya me acordé, gracias"))

    assert outcome_rule(SessionState(context.state)) != "T3c_explanation_rejected"


def test_a_confirmed_dispute_waits_for_a_person_and_is_not_opened(world):
    harness, _ = world
    context = make_context()
    ref, handoff_id = _proposed(world, context)

    assert outcome_rule(SessionState(context.state)) == "T2b_review_confirmed"
    packet = harness.container.cases.get_handoff(handoff_id)
    assert packet is not None and packet.proposed_dispute is not None
    assert packet.proposed_dispute.status is ReviewStatus.PENDING
    assert packet.proposed_dispute.transaction_id == HABITUAL
    # The agent itself still cannot open it.
    blocked = harness.callbacks.before_tool(
        tool("open_dispute"), {"transaction_ref": ref, "reason": "x"}, context
    )
    assert blocked is not None and blocked["status"] == "blocked"
    report = harness.container.handoff_report.markdown(packet, "es")
    assert f"POST /v1/handoffs/{handoff_id}/dispute-review" in report


def test_only_the_rejected_charge_can_go_to_review(world):
    harness, _ = world
    context = make_context()
    _explained(harness, context)
    other = SessionState(context.state).ref_for("TXN-D1-001")

    result = harness.toolkit.propose_dispute(context, other, "x")

    assert result == {"status": "blocked", "reason": "charge_not_under_review"}


def test_an_approval_opens_the_dispute_and_records_the_reviewer(world):
    harness, _ = world
    context = make_context()
    _, handoff_id = _proposed(world, context)
    review = ReviewDispute(harness.container.cases, harness.audit)

    decided = asyncio.run(review.execute(handoff_id, REVIEWER, approve=True, note="ok"))

    assert (decided.status, decided.reviewer) == (ReviewStatus.APPROVED, REVIEWER)
    dispute = harness.container.cases.get_dispute(decided.dispute_case_id or "")
    assert dispute is not None and dispute.transaction_id == HABITUAL
    [entry] = [e for e in harness.audit.entries if e["event"] == "dispute_reviewed"]
    assert (entry["reviewer"], entry["decision"]) == (REVIEWER, "approved")
    with pytest.raises(ProposalAlreadyDecidedError):
        asyncio.run(review.execute(handoff_id, REVIEWER, approve=False))


def test_a_rejection_opens_nothing(world):
    harness, _ = world
    context = make_context()
    _, handoff_id = _proposed(world, context)
    review = ReviewDispute(harness.container.cases, harness.audit)

    decided = asyncio.run(review.execute(handoff_id, REVIEWER, approve=False))

    assert (decided.status, decided.dispute_case_id) == (ReviewStatus.REJECTED, None)


def test_a_handoff_without_a_proposal_cannot_be_reviewed(world):
    harness, _ = world
    review = ReviewDispute(harness.container.cases, harness.audit)
    with pytest.raises(ProposalNotFoundError):
        asyncio.run(review.execute("HND-NOPE", REVIEWER, approve=True))


def test_the_customer_hears_the_decision_in_their_chat(world):
    harness, _ = world
    context = make_context()
    SessionState(context.state).case_report = CaseReport(
        category="unrecognized_charge",
        fraud_suspected=False,
        freeze_card_requested=False,
        case_id=CASE_ID,
    )
    _, handoff_id = _proposed(world, context)
    store, messenger = InMemoryCaseStore(), RecordingMessenger()
    store.add_conversation(
        CaseConversation(
            case_id=CASE_ID,
            folio="LB-2026-ABC123",
            language="es",
            owner=f"case:{CASE_ID}",
            session_id="s1",
        )
    )
    store.link_chat(CHAT, ChatLink(case_id=CASE_ID, folio="LB-2026-ABC123", language="es"))
    review = ReviewDispute(harness.container.cases, harness.audit, store, messenger)

    decided = asyncio.run(review.execute(handoff_id, REVIEWER, approve=True))

    [(chat, text)] = messenger.sent
    assert chat == CHAT
    assert "aprobó tu disputa del caso LB-2026-ABC123" in text
    assert decided.dispute_case_id and decided.dispute_case_id in text
