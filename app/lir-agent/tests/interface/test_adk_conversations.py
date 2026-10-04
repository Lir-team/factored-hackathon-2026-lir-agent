import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import InMemoryRunner
from google.genai import types

from lir_agent.application.ports import (
    ConversationNotFoundError,
    CustomerNotFoundError,
)
from lir_agent.domain.session import SessionState, utc_now
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.infrastructure.persistence import FixtureTransactionRepository
from lir_agent.interface.adk import AdkConversations

TTL = timedelta(minutes=15)
AUTH = "iap_operator"


class CustomerEchoAgent(BaseAgent):
    """Replies with the customer bound to the session, so tests see the state the agent sees."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        customer = SessionState(ctx.session.state).customer_id
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            content=types.Content(
                role="model", parts=[types.Part(text=f"hi {customer}")]
            ),
        )


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def runner() -> InMemoryRunner:
    return InMemoryRunner(agent=CustomerEchoAgent(name="echo"), app_name="lir-test")


@pytest.fixture
def conversations(settings, runner, audit) -> AdkConversations:
    return AdkConversations(
        runner,
        customers=FixtureTransactionRepository(settings.fixture_path),
        audit=audit,
        llm_model="openai/test-model",
        cost=lambda model, tokens_in, tokens_out: 0.25,
    )


def test_session_binds_the_customer_and_the_agent_sees_it(conversations):
    async def scenario() -> str:
        started = await conversations.start(
            "tester@example.com", "CLI-DEMO-001", ttl=TTL, auth_method=AUTH
        )
        assert timedelta(minutes=14) < started.expires_at - utc_now() <= TTL
        return await conversations.send(
            "tester@example.com", started.session_id, "hola"
        )

    assert asyncio.run(scenario()) == "hi CLI-DEMO-001"


def test_ttl_and_auth_method_are_chosen_per_conversation(conversations, runner):
    async def state_of(owner: str, session_id: str) -> dict:
        session = await runner.session_service.get_session(
            app_name=runner.app_name, user_id=owner, session_id=session_id
        )
        assert session is not None
        return dict(session.state)

    async def scenario() -> None:
        short = await conversations.start(
            "tester@example.com", "CLI-DEMO-001", ttl=TTL, auth_method=AUTH
        )
        long = await conversations.start(
            "case:42", "CLI-DEMO-001", ttl=timedelta(days=7), auth_method="case_intake"
        )
        assert short.expires_at - utc_now() <= TTL
        assert long.expires_at - utc_now() > timedelta(days=6)
        short_state = await state_of("tester@example.com", short.session_id)
        long_state = await state_of("case:42", long.session_id)
        assert short_state["auth_method"] == AUTH
        assert long_state["auth_method"] == "case_intake"

    asyncio.run(scenario())


def test_owner_cannot_use_another_owners_conversation(conversations):
    async def scenario() -> None:
        started = await conversations.start(
            "tester@example.com", "CLI-DEMO-001", ttl=TTL, auth_method=AUTH
        )
        await conversations.send("intruder@example.com", started.session_id, "hola")

    with pytest.raises(ConversationNotFoundError):
        asyncio.run(scenario())


def test_unknown_conversation_is_not_found(conversations):
    with pytest.raises(ConversationNotFoundError):
        asyncio.run(conversations.send("tester@example.com", "missing", "hola"))


def test_unknown_customer_gets_no_conversation(conversations, audit):
    with pytest.raises(CustomerNotFoundError):
        asyncio.run(
            conversations.start(
                "tester@example.com", "CLI-UNKNOWN", ttl=TTL, auth_method=AUTH
            )
        )
    assert audit.entries == []


def test_conversation_start_is_audited_without_the_customer_id(conversations, audit):
    started = asyncio.run(
        conversations.start(
            "tester@example.com", "CLI-DEMO-001", ttl=TTL, auth_method=AUTH
        )
    )
    [entry] = audit.entries
    assert entry["event"] == "session_started"
    assert entry["session_id"] == started.session_id
    assert entry["owner"] == "tester@example.com"
    assert entry["auth_method"] == AUTH
    assert "CLI-DEMO-001" not in str(entry)


def test_every_turn_is_audited_with_its_trace(conversations, audit):
    async def scenario():
        started = await conversations.start(
            "tester@example.com",
            "CLI-DEMO-001",
            ttl=timedelta(minutes=15),
            auth_method="iap_operator",
        )
        return started, await conversations.converse(
            "tester@example.com", started.session_id, "hola"
        )

    started, turn = asyncio.run(scenario())
    assert turn.reply == "hi CLI-DEMO-001"
    assert turn.trace.llm_model == "openai/test-model"
    assert turn.trace.cost_usd == 0.25
    assert turn.trace.latency_ms >= 0
    completed = [e for e in audit.entries if e["event"] == "turn_completed"]
    assert len(completed) == 1
    assert completed[0]["session_id"] == started.session_id
    assert completed[0]["llm_model"] == "openai/test-model"
    assert "CLI-DEMO-001" not in str(completed[0])


def test_reported_charges_get_references_only_when_they_are_the_customers(
    conversations, runner
):
    async def scenario() -> tuple[tuple[str, ...], dict]:
        started = await conversations.start(
            "case:1",
            "CLI-DEMO-001",
            ttl=TTL,
            auth_method=AUTH,
            transaction_ids=["TXN-D1-006", "TXN-D2-001", "TXN-D1-001"],
        )
        session = await runner.session_service.get_session(
            app_name=conversations.app_name, user_id="case:1", session_id=started.session_id
        )
        assert session is not None
        return started.transaction_refs, dict(session.state)

    refs, state = asyncio.run(scenario())

    assert refs == ("T1", "T2")  # TXN-D2-001 belongs to another customer
    session = SessionState(state)
    assert session.resolve_ref("T1") == "TXN-D1-006"
    assert session.resolve_ref("T2") == "TXN-D1-001"
