"""Authentication guardrail: no model run without a signed-in customer.

Identity is enforced in three layers, never by the prompt:

1. Session creation stores the authenticated customer's ID in session state
   under `CUSTOMER_ID_STATE_KEY`. The ID never passes through the model.
2. `require_authenticated_customer` runs before the agent and refuses a
   session without that ID, so the model is never called for it.
3. Tools call `require_customer_id`, which raises: by then the guardrail has
   guaranteed the ID, so a missing one is a wiring bug, not a user error.
"""

import logging
from typing import Any, Protocol

from google.adk.agents.context import Context
from google.genai import types

logger = logging.getLogger(__name__)

# Session-state key holding the authenticated customer's ID. This is the
# session identity contract shared by session creation, the guardrail and tools.
CUSTOMER_ID_STATE_KEY = "customer_id"

UNAUTHENTICATED_REPLY = "You need to sign in before I can help you."


class UnauthenticatedSessionError(RuntimeError):
    """Raised when a session has no authenticated customer."""


class _StateReader(Protocol):
    """Read access shared by ADK's `State` and a plain dict."""

    def get(self, key: str, default: Any = None, /) -> Any: ...


def require_customer_id(state: _StateReader) -> str:
    """Return the authenticated customer's ID from session state.

    Args:
        state: Session state holding the ID under `CUSTOMER_ID_STATE_KEY`.

    Raises:
        UnauthenticatedSessionError: If the ID is missing, blank or not a str.
    """
    customer_id = state.get(CUSTOMER_ID_STATE_KEY)
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise UnauthenticatedSessionError("No authenticated customer in this session.")
    return customer_id


def require_authenticated_customer(callback_context: Context) -> types.Content | None:
    """Refuse unauthenticated sessions before the agent (and model) run.

    Registered as the agent's `before_agent_callback`: returning content makes
    ADK skip the run and send that content to the user.
    """
    try:
        require_customer_id(callback_context.state)
    except UnauthenticatedSessionError:
        logger.warning("Refused a request from an unauthenticated session")
        return types.Content(
            role="model", parts=[types.Part(text=UNAUTHENTICATED_REPLY)]
        )
    return None
