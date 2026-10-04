"""ADK implementation of the `Conversations` port.

Each conversation is an ADK session whose `user_id` is the owner, so an owner can never read
or continue another owner's conversation. The customer is bound to the session state on
creation, exactly like the terminal chat; it never passes through the conversation with the
model.
"""

import logging
import uuid
from datetime import timedelta

from google.adk.runners import InMemoryRunner, Runner
from google.genai import types

from lir_agent.application.ports import (
    AuditSink,
    ConversationNotFoundError,
    CustomerNotFoundError,
    StartedConversation,
    TransactionRepository,
)
from lir_agent.config.settings import Settings
from lir_agent.domain.session import SessionState

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
    ) -> None:
        """Keep the runner, the customer lookup and the audit sink."""
        self._runner = runner
        self._customers = customers
        self._audit = audit

    @property
    def app_name(self) -> str:
        """ADK application name the sessions belong to."""
        return self._runner.app_name

    async def start(
        self, owner: str, customer_id: str, *, ttl: timedelta, auth_method: str
    ) -> StartedConversation:
        """Create a session for `customer_id`, owned by `owner` and valid for `ttl`.

        Raises:
            CustomerNotFoundError: If the customer does not exist.
        """
        if self._customers.get_customer(customer_id) is None:
            raise CustomerNotFoundError(customer_id)
        state: dict = {}
        expires_at = SessionState(state).start(customer_id, ttl, auth_method)
        session_id = uuid.uuid4().hex
        await self._runner.session_service.create_session(
            app_name=self.app_name, user_id=owner, session_id=session_id, state=state
        )
        self._audit.record(
            "session_started", session_id, auth_method=auth_method, owner=owner
        )
        return StartedConversation(session_id=session_id, expires_at=expires_at)

    async def send(self, owner: str, session_id: str, text: str) -> str:
        """Send one customer message and return the agent's reply.

        Raises:
            ConversationNotFoundError: If `owner` has no session with this id.
        """
        session = await self._runner.session_service.get_session(
            app_name=self.app_name, user_id=owner, session_id=session_id
        )
        if session is None:
            raise ConversationNotFoundError(session_id)
        content = types.Content(role="user", parts=[types.Part(text=text)])
        reply: list[str] = []
        async for event in self._runner.run_async(
            user_id=owner, session_id=session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                reply.extend(part.text for part in event.content.parts if part.text)
        if not reply:
            logger.warning("Agent returned no text for session %s", session_id)
        return "".join(reply)


def build_conversations(settings: Settings) -> AdkConversations:
    """Wire the real agent behind an in-memory ADK runner, sharing one container."""
    from lir_agent.container import build_container  # heavy imports, only when serving
    from lir_agent.interface.adk.factory import build_agent

    container = build_container(settings)
    runner = InMemoryRunner(agent=build_agent(container=container), app_name=APP_NAME)
    return AdkConversations(
        runner, customers=container.repository, audit=container.audit
    )
