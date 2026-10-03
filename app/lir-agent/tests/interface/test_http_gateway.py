import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import InMemoryRunner
from google.genai import types

from lir_agent.domain.session import SessionState, utc_now
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.infrastructure.persistence import FixtureTransactionRepository
from lir_agent.interface.http import (
    AgentGateway,
    CustomerNotFoundError,
    SessionNotFoundError,
)


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
def gateway(settings, audit) -> AgentGateway:
    runner = InMemoryRunner(agent=CustomerEchoAgent(name="echo"), app_name="lir-test")
    return AgentGateway(
        runner,
        customers=FixtureTransactionRepository(settings.fixture_path),
        audit=audit,
        session_ttl=timedelta(minutes=15),
        auth_method="iap_operator",
    )


def test_session_binds_the_customer_and_the_agent_sees_it(gateway):
    async def scenario() -> str:
        started = await gateway.start_session("tester@example.com", "CLI-DEMO-001")
        assert (
            timedelta(minutes=14)
            < started.expires_at - utc_now()
            <= timedelta(minutes=15)
        )
        return await gateway.send("tester@example.com", started.session_id, "hola")

    assert asyncio.run(scenario()) == "hi CLI-DEMO-001"


def test_operator_cannot_use_another_operators_session(gateway):
    async def scenario() -> None:
        started = await gateway.start_session("tester@example.com", "CLI-DEMO-001")
        await gateway.send("intruder@example.com", started.session_id, "hola")

    with pytest.raises(SessionNotFoundError):
        asyncio.run(scenario())


def test_unknown_session_is_not_found(gateway):
    with pytest.raises(SessionNotFoundError):
        asyncio.run(gateway.send("tester@example.com", "missing", "hola"))


def test_unknown_customer_gets_no_session(gateway, audit):
    with pytest.raises(CustomerNotFoundError):
        asyncio.run(gateway.start_session("tester@example.com", "CLI-UNKNOWN"))
    assert audit.entries == []


def test_session_start_is_audited_without_the_customer_id(gateway, audit):
    started = asyncio.run(gateway.start_session("tester@example.com", "CLI-DEMO-001"))
    [entry] = audit.entries
    assert entry["event"] == "session_started"
    assert entry["session_id"] == started.session_id
    assert entry["operator"] == "tester@example.com"
    assert "CLI-DEMO-001" not in str(entry)
