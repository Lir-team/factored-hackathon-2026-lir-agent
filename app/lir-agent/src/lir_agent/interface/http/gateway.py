"""Bridge between HTTP requests and the ADK runner.

Sessions are owned by the operator that created them (the identity verified by IAP), so an
operator can never read or continue another operator's session. The customer is bound to
the session state on creation, exactly like the terminal chat; it never passes through the
conversation with the model.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from google.adk.runners import Runner
from google.genai import types

from lir_agent.domain.session import SessionState

logger = logging.getLogger(__name__)


class SessionNotFoundError(Exception):
    """The session does not exist, expired from memory, or belongs to another operator."""


@dataclass(frozen=True)
class StartedSession:
    """A new session bound to a customer."""

    session_id: str
    expires_at: datetime


class AgentGateway:
    """Creates customer sessions and relays messages to the agent."""

    def __init__(
        self,
        runner: Runner,
        *,
        session_ttl: timedelta,
        auth_method: str,
    ) -> None:
        """Keep the runner and how sessions are authenticated."""
        self._runner = runner
        self._session_ttl = session_ttl
        self._auth_method = auth_method

    @property
    def app_name(self) -> str:
        """ADK application name the sessions belong to."""
        return self._runner.app_name

    async def start_session(self, operator: str, customer_id: str) -> StartedSession:
        """Create a session for `customer_id`, owned by `operator`."""
        state: dict = {}
        expires_at = SessionState(state).start(
            customer_id, self._session_ttl, self._auth_method
        )
        session_id = uuid.uuid4().hex
        await self._runner.session_service.create_session(
            app_name=self.app_name, user_id=operator, session_id=session_id, state=state
        )
        logger.info("HTTP session %s started", session_id)
        return StartedSession(session_id=session_id, expires_at=expires_at)

    async def send(self, operator: str, session_id: str, text: str) -> str:
        """Send one customer message and return the agent's reply.

        Raises:
            SessionNotFoundError: If `operator` has no session with this id.
        """
        session = await self._runner.session_service.get_session(
            app_name=self.app_name, user_id=operator, session_id=session_id
        )
        if session is None:
            raise SessionNotFoundError(session_id)
        content = types.Content(role="user", parts=[types.Part(text=text)])
        reply: list[str] = []
        async for event in self._runner.run_async(
            user_id=operator, session_id=session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                reply.extend(part.text for part in event.content.parts if part.text)
        if not reply:
            logger.warning("Agent returned no text for session %s", session_id)
        return "".join(reply)
