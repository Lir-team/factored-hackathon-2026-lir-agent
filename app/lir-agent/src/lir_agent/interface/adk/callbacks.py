"""ADK callbacks: where permissions and policy are enforced outside model-generated prose.

- before_agent: seeds a labeled TEST session in local development only, then refuses any
  session without a valid signed-in customer before the agent (and model) run.
- before_model: routes each new customer message (RouteTurn), appends lane guidance and
  protects the request: identifiers redacted, bank records as placeholders.
- before_tool: authentication, session expiry, tools allowed per turn lane, dispute confirmation,
  and placeholders in the arguments resolved before the tool runs.
- after_tool / after_model: audit records, placeholders resolved for the customer and the
  deterministic output guard.
"""

import logging
from datetime import timedelta
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import BaseTool, ToolContext
from google.genai import types

from lir_agent.application.ports import AuditSink
from lir_agent.application.use_cases import RouteTurn
from lir_agent.config.settings import Settings
from lir_agent.domain.dispute_guard import DisputeGuard
from lir_agent.domain.errors import DisputeBlock
from lir_agent.domain.language import detect_language
from lir_agent.domain.policy import PolicyEngine
from lir_agent.domain.pseudonyms import Pseudonyms, redact_identifiers
from lir_agent.domain.session import SessionState
from lir_agent.interface.adk.guidance import CustomerMessages, TurnGuidance

logger = logging.getLogger(__name__)

DEV_AUTH_METHOD = "dev_test_session"
OPEN_DISPUTE_TOOL = "open_dispute"


def _session_id(context: Any) -> str | None:
    return getattr(getattr(context, "session", None), "id", None)


def _latest_user_text(llm_request: LlmRequest) -> str | None:
    """Text of the newest customer message, or None when the last content is a tool round."""
    if not llm_request.contents:
        return None
    last = llm_request.contents[-1]
    if last.role != "user":
        return None
    text = " ".join(part.text for part in (last.parts or []) if part.text).strip()
    return text or None


def _response_text(llm_response: LlmResponse) -> str:
    return _content_text(llm_response.content)


def _content_text(content: types.Content | None) -> str:
    if not content:
        return ""
    return " ".join(part.text for part in (content.parts or []) if part.text)


def _protected_part(part: types.Part, role: str | None, pseudonyms: Pseudonyms) -> types.Part:
    """A copy of a request part as the external model may read it.

    Copies, never edits: the request's contents may share objects with the session history.
    """
    if part.text:
        if role == "user":
            return part.model_copy(update={"text": pseudonyms.protect_customer_text(part.text)})
        # Replies the customer read hold resolved values; they go back as placeholders.
        return part.model_copy(update={"text": pseudonyms.conceal(part.text)})
    # Tool arguments and results echo the customer's words (e.g. `merchant_hint`).
    call, result = part.function_call, part.function_response
    if call and call.args:
        args = pseudonyms.conceal_value(call.args)
        return part.model_copy(update={"function_call": call.model_copy(update={"args": args})})
    if result and result.response:
        response = pseudonyms.conceal_value(result.response)
        return part.model_copy(
            update={"function_response": result.model_copy(update={"response": response})}
        )
    return part


class AgentCallbacks:
    """The ADK callbacks of the agent, sharing one set of injected dependencies."""

    def __init__(
        self,
        settings: Settings,
        policy: PolicyEngine,
        route_turn: RouteTurn,
        dispute_guard: DisputeGuard,
        guidance: TurnGuidance,
        messages: CustomerMessages,
        audit: AuditSink,
    ) -> None:
        """Keep the dependencies the callbacks need."""
        self._settings = settings
        self._policy = policy
        self._route_turn = route_turn
        self._dispute_guard = dispute_guard
        self._guidance = guidance
        self._messages = messages
        self._audit = audit

    # ---- agent ---------------------------------------------------------------------------
    def before_agent(self, callback_context: CallbackContext) -> types.Content | None:
        """Refuse sessions without a valid signed-in customer before the agent runs.

        Returning content makes ADK skip the run and send that content to the customer,
        so the model is never called for an unauthenticated or expired session.
        """
        session, session_id = (
            SessionState(callback_context.state),
            _session_id(callback_context),
        )
        dev_customer = self._settings.dev_customer_id
        if dev_customer and not session.customer_id:
            session.start(
                dev_customer,
                timedelta(minutes=self._settings.session_ttl_minutes),
                DEV_AUTH_METHOD,
            )
            self._audit.record(
                "dev_session_seeded", session_id, customer_id=dev_customer
            )

        auth_error = session.auth_error()
        if auth_error is None:
            return None
        logger.warning("Refused a request from a session: %s", auth_error.value)
        self._audit.record("session_refused", session_id, reason=auth_error.value)
        user_content = getattr(callback_context, "user_content", None)
        language = detect_language(_content_text(user_content))
        return types.Content(
            role="model",
            parts=[
                types.Part(text=self._messages.for_auth_error(auth_error, language))
            ],
        )

    # ---- model ---------------------------------------------------------------------------
    def before_model(
        self, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        """Route each new customer message and append the lane guidance to the request."""
        session = SessionState(callback_context.state)
        auth_error = session.auth_error()
        if auth_error:
            llm_request.append_instructions([self._guidance.for_auth_error(auth_error)])
            return None
        text = _latest_user_text(llm_request)
        if text is not None:
            self._route_turn.execute(session, text, _session_id(callback_context))
            removed = redact_identifiers(text)[1]
            if removed:  # the count only: the identifiers themselves are never logged
                self._audit.record(
                    "identifiers_redacted", _session_id(callback_context), count=removed
                )
        self._protect(session, llm_request)
        llm_request.append_instructions(
            [
                self._guidance.date_context(self._settings.today()),
                self._guidance.for_turn(session),
            ]
        )
        return None

    @staticmethod
    def _protect(session: SessionState, llm_request: LlmRequest) -> None:
        """Rebuild the request contents with identifiers redacted and records as placeholders."""
        pseudonyms = Pseudonyms(session)
        llm_request.contents = [
            content.model_copy(
                update={
                    "parts": [
                        _protected_part(part, content.role, pseudonyms)
                        for part in content.parts or []
                    ]
                }
            )
            for content in llm_request.contents
        ]

    def after_model(
        self, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        """Resolve placeholders, then replace replies that promise refunds or ask for credentials."""
        revealed = self._reveal(callback_context, llm_response)
        llm_response = revealed or llm_response
        text = _response_text(llm_response)
        guard = self._policy.config.output_guard
        violations = guard.violations(text) if text else []
        if not violations:
            return revealed
        self._audit.record(
            "output_blocked", _session_id(callback_context), patterns=violations
        )
        language = detect_language(SessionState(callback_context.state).last_user_text)
        return LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text=guard.fallback(language))]
            )
        )

    def _reveal(
        self, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        """The reply with its placeholders resolved for the customer, or None if it has none."""
        content = llm_response.content
        if not content or not any(part.text for part in content.parts or []):
            return None
        pseudonyms = Pseudonyms(SessionState(callback_context.state))
        parts: list[types.Part] = []
        unknown: list[str] = []
        changed = False
        for part in content.parts or []:
            if part.text:
                text, missing = pseudonyms.reveal(part.text)
                unknown += missing
                if text != part.text:
                    changed = True
                    part = part.model_copy(update={"text": text})
            parts.append(part)
        if unknown:  # the model wrote a placeholder it was never given
            self._audit.record(
                "unknown_placeholder", _session_id(callback_context), placeholders=unknown
            )
        if not changed:
            return None
        return llm_response.model_copy(
            update={"content": content.model_copy(update={"parts": parts})}
        )

    # ---- tools ---------------------------------------------------------------------------
    def before_tool(
        self, tool: BaseTool, args: dict, tool_context: ToolContext
    ) -> dict | None:
        """Deny tools without a valid session, outside the turn lane or without confirmation."""
        session, session_id = (
            SessionState(tool_context.state),
            _session_id(tool_context),
        )
        auth_error = session.auth_error()
        if auth_error:
            self._audit.record(
                "tool_denied", session_id, tool=tool.name, reason=auth_error.value
            )
            return {"status": "error", "error": auth_error.value}

        lane = session.turn_lane
        if tool.name not in self._policy.allowed_tools(lane):
            self._audit.record(
                "tool_denied",
                session_id,
                tool=tool.name,
                reason="not_allowed_for_lane",
                lane=lane.value,
            )
            return {
                "status": "blocked",
                "reason": "not_allowed_for_turn_lane",
                "lane": lane.value,
                # What to do instead (e.g. ask for the confirmation again), so the model
                # does not improvise "contact support" after a block.
                "instruction": self._guidance.for_turn(session),
            }

        # Tools work on real values: resolve the placeholders the model passed. ADK hands the
        # tool this same dict; the conversation history keeps the placeholders.
        pseudonyms = Pseudonyms(session)
        for key, value in args.items():
            args[key] = pseudonyms.reveal_value(value)

        if tool.name == OPEN_DISPUTE_TOOL:
            denial = self._check_dispute(session, session_id, args)
            if denial:
                return denial

        self._audit.record("tool_call", session_id, tool=tool.name, args=args)
        return None

    def after_tool(
        self,
        tool: BaseTool,
        args: dict,  # noqa: ARG002 - ADK passes callback arguments by these exact names
        tool_context: ToolContext,
        tool_response: dict,
    ) -> dict | None:
        """Record the outcome status of every tool call."""
        self._audit.record(
            "tool_result",
            _session_id(tool_context),
            tool=tool.name,
            status=(tool_response or {}).get("status"),
        )
        return None

    def _check_dispute(
        self, session: SessionState, session_id: str | None, args: dict
    ) -> dict | None:
        transaction_id = session.resolve_ref(args.get("transaction_ref"))
        block = self._dispute_guard.check(session, transaction_id)
        if block is DisputeBlock.CONFIRMATION_REQUIRED:
            session.pending_confirmation = transaction_id
            self._audit.record(
                "confirmation_requested", session_id, transaction_id=transaction_id
            )
            return {
                "status": "confirmation_required",
                "instruction": "Ask the customer to confirm explicitly in their next message.",
            }
        if block:
            self._audit.record(
                "tool_denied", session_id, tool=OPEN_DISPUTE_TOOL, reason=block.value
            )
            return {"status": "blocked", "reason": block.value}
        return None
