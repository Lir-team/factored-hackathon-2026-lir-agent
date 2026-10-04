"""ADK implementation of the `Conversations` port.

Each conversation is an ADK session whose `user_id` is the owner, so an owner can never read
or continue another owner's conversation. The customer is bound to the session state on
creation, exactly like the terminal chat; it never passes through the conversation with the
model.
"""

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import TYPE_CHECKING

from google.adk.runners import InMemoryRunner, Runner
from google.genai import types

from lir_agent.application.ports import (
    AuditSink,
    ConversationNotFoundError,
    CustomerNotFoundError,
    StartedConversation,
    TransactionRepository,
    Turn,
    TurnTrace,
)
from lir_agent.config.settings import Settings
from lir_agent.domain.case_intake import CaseReport
from lir_agent.domain.session import SessionState

if TYPE_CHECKING:
    from lir_agent.container import Container

# (model, input tokens, output tokens) -> USD, or None when the price is unknown.
type CostFunction = Callable[[str, int, int], float | None]

logger = logging.getLogger(__name__)

APP_NAME = "lir"


class AdkConversations:
    """Creates customer sessions on an ADK runner and relays messages to the agent."""

    def __init__(
        self,
        runner: Runner,
        *,
        customers: TransactionRepository,
        audit: AuditSink,
        llm_model: str,
        cost: CostFunction | None = None,
    ) -> None:
        """Keep the runner, the customer lookup, the audit sink and how turns are priced."""
        self._runner = runner
        self._customers = customers
        self._audit = audit
        self._llm_model = llm_model
        self._cost = cost

    @property
    def app_name(self) -> str:
        """ADK application name the sessions belong to."""
        return self._runner.app_name

    async def start(
        self,
        owner: str,
        customer_id: str,
        *,
        ttl: timedelta,
        auth_method: str,
        transaction_ids: Sequence[str] = (),
        case_report: CaseReport | None = None,
        max_ttl: timedelta | None = None,
    ) -> StartedConversation:
        """Create a session for `customer_id`, owned by `owner` and valid for `ttl`.

        Only `transaction_ids` of this customer get a reference; others are ignored.

        Raises:
            CustomerNotFoundError: If the customer does not exist.
        """
        if self._customers.get_customer(customer_id) is None:
            raise CustomerNotFoundError(customer_id)
        state: dict = {}
        session = SessionState(state)
        expires_at = session.start(customer_id, ttl, auth_method, max_ttl=max_ttl)
        if case_report is not None:
            session.case_report = case_report
        refs: tuple[str, ...] = ()
        if transaction_ids:
            owned = {
                txn.transaction_id
                for txn in self._customers.list_transactions(customer_id)
            }
            refs = tuple(
                session.ref_for(txn_id) for txn_id in transaction_ids if txn_id in owned
            )
        session_id = uuid.uuid4().hex
        await self._runner.session_service.create_session(
            app_name=self.app_name, user_id=owner, session_id=session_id, state=state
        )
        self._audit.record(
            "session_started", session_id, auth_method=auth_method, owner=owner
        )
        return StartedConversation(
            session_id=session_id, expires_at=expires_at, transaction_refs=refs
        )

    async def send(self, owner: str, session_id: str, text: str) -> str:
        """Send one customer message and return the agent's reply.

        Raises:
            ConversationNotFoundError: If `owner` has no session with this id.
        """
        return (await self.converse(owner, session_id, text)).reply

    async def converse(self, owner: str, session_id: str, text: str) -> Turn:
        """Send one customer message; return the reply and how the turn was decided.

        Every turn is audited as `turn_completed`, so analytics (BigQuery) get one row per
        turn with its lanes, decision model, latency and cost.

        Raises:
            ConversationNotFoundError: If `owner` has no session with this id.
        """
        session = await self._runner.session_service.get_session(
            app_name=self.app_name, user_id=owner, session_id=session_id
        )
        if session is None:
            raise ConversationNotFoundError(session_id)
        approvals_before = len(SessionState(session.state).approval_ids)
        content = types.Content(role="user", parts=[types.Part(text=text)])
        reply: list[str] = []
        tools: list[str] = []
        input_tokens = output_tokens = 0
        started = time.perf_counter()
        async for event in self._runner.run_async(
            user_id=owner, session_id=session_id, new_message=content
        ):
            tools.extend(call.name for call in event.get_function_calls() if call.name)
            usage = event.usage_metadata
            if usage:
                input_tokens += usage.prompt_token_count or 0
                output_tokens += usage.candidates_token_count or 0
            if event.is_final_response() and event.content and event.content.parts:
                reply.extend(part.text for part in event.content.parts if part.text)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if not reply:
            logger.warning("Agent returned no text for session %s", session_id)
        state = await self._state(owner, session_id)
        trace = self._trace(state, tools, latency_ms, input_tokens, output_tokens)
        self._audit.record("turn_completed", session_id, **vars(trace))
        return Turn(
            reply="".join(reply),
            trace=trace,
            approvals=tuple(state.approval_ids[approvals_before:]),
        )

    async def _state(self, owner: str, session_id: str) -> SessionState:
        """The session state after a turn."""
        session = await self._runner.session_service.get_session(
            app_name=self.app_name, user_id=owner, session_id=session_id
        )
        return SessionState(session.state if session else {})

    def _trace(
        self,
        state: SessionState,
        tools: list[str],
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
    ) -> TurnTrace:
        """Read the turn's decisions and lanes back from the session state."""
        meta = state.decision_meta or {}
        turn, case = state.turn_outcome, state.case_outcome
        outcome = turn or case
        cost = (
            self._cost(self._llm_model, input_tokens, output_tokens)
            if self._cost
            else None
        )
        return TurnTrace(
            decision_model=meta.get("model"),
            decision_fallback=meta.get("fallback"),
            decisions=state.decisions,
            turn_lane=turn.lane.value if turn else None,
            turn_rule=turn.rule_id if turn else None,
            case_lane=case.lane.value if case else None,
            case_rule=case.rule_id if case else None,
            policy_version=outcome.policy_version if outcome else None,
            tools=tools,
            handoff_id=state.handoff_id,
            llm_model=self._llm_model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )


def build_conversations(
    settings: Settings, container: "Container | None" = None
) -> AdkConversations:
    """Wire the real agent behind an in-memory ADK runner.

    Pass the app container so the agent and the HTTP routes share one case store (the
    handoff report reads the handoffs the agent creates).
    """
    from lir_agent.container import build_container  # heavy imports, only when serving
    from lir_agent.infrastructure.llm import litellm_cost
    from lir_agent.interface.adk.factory import build_agent

    container = container or build_container(settings)
    runner = InMemoryRunner(agent=build_agent(container=container), app_name=APP_NAME)
    return AdkConversations(
        runner,
        customers=container.repository,
        audit=container.audit,
        llm_model=settings.llm_model,
        cost=litellm_cost,
    )
