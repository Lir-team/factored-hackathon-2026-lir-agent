"""Shared fakes and helpers for the tests. No test touches the network or an LLM."""

from datetime import timedelta
from types import SimpleNamespace

from decision_layer import DecisionError
from decision_layer.base import Answer, DecisionResult
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from lir_agent.container import Container
from lir_agent.domain.session import SessionState
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.infrastructure.resources import ResourceLoader
from lir_agent.interface.adk.callbacks import AgentCallbacks
from lir_agent.interface.adk.guidance import CustomerMessages, TurnGuidance
from lir_agent.interface.adk.toolkit import ChargeInvestigationToolkit

CUSTOMER = "CLI-DEMO-001"
OTHER_CUSTOMER_TXN = "TXN-D2-001"
IN_SCOPE = {
    "intencion": ("cargo_no_reconocido", 0.9),
    "pide_humano": (False, 0.05),
    "sospecha_robo": (False, 0.05),
}


class ScriptedDecisions:
    """Decision model stub: fixed answers per question key."""

    name = "scripted"

    def __init__(
        self, answers: dict[str, tuple] | None = None, fail: bool = False
    ) -> None:
        self.answers = answers or {}
        self.fail = fail
        self.calls: list[tuple] = []  # (state, questions) of every decide() call

    def decide(self, state, questions):
        self.calls.append((state, questions))
        if self.fail:
            raise DecisionError("unavailable")
        missing = set(questions) - set(self.answers)
        if missing:
            raise DecisionError(f"no scripted answer for {sorted(missing)}")
        return DecisionResult(
            answers={
                k: Answer(value=v, probability=p)
                for k, (v, p) in self.answers.items()
                if k in questions
            },
            model=self.name,
            latency_ms=1.0,
        )


class Harness:
    """Everything a test needs, wired through the real container."""

    def __init__(self, container: Container, audit: InMemoryAuditSink) -> None:
        self.container = container
        self.audit = audit
        self.toolkit = ChargeInvestigationToolkit(
            container.get_profile,
            container.find_candidates,
            container.gather_evidence,
            container.request_handoff,
        )
        loader, settings = ResourceLoader(), container.settings
        self.callbacks = AgentCallbacks(
            settings=settings,
            policy=container.policy,
            route_turn=container.route_turn,
            approval_gate=container.request_action_approval,
            guidance=TurnGuidance(loader.load_mapping(settings.turn_guidance_path)),
            messages=CustomerMessages(
                loader.load_localized_mapping(settings.customer_messages_path)
            ),
            audit=audit,
        )


def make_context(
    authenticated: bool = True, ttl_minutes: int = 15, user_text: str | None = None
) -> SimpleNamespace:
    """Stand-in for ADK's CallbackContext / ToolContext: state, session id, user message."""
    state: dict = {}
    if authenticated:
        SessionState(state).start(CUSTOMER, timedelta(minutes=ttl_minutes), "unit_test")
    content = (
        types.Content(role="user", parts=[types.Part(text=user_text)])
        if user_text
        else None
    )
    return SimpleNamespace(
        state=state, session=SimpleNamespace(id="test-session"), user_content=content
    )


def user_request(text: str) -> LlmRequest:
    return LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text=text)])]
    )


def tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def outcome_rule(session: SessionState) -> str:
    """Rule id of the current turn outcome (fails the test when there is none)."""
    outcome = session.turn_outcome
    assert outcome is not None
    return outcome.rule_id
