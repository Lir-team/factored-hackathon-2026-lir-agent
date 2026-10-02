"""Authentication guardrail: no model run without a signed-in customer.

Identity is enforced in three layers, never by the prompt:

1. Session creation stores the authenticated customer's ID in session state
   under `CUSTOMER_ID_STATE_KEY` (see `Agent.agent.auth.session`). The ID never
   passes through the model.
2. `require_authenticated_customer` runs before the agent and refuses a
   session without that ID, so the model is never called for it.
3. Tools call `require_customer_id`, which raises: by then the guardrail has
   guaranteed the ID, so a missing one is a wiring bug, not a user error.
"""

import logging

from google.adk.agents.context import Context
from google.genai import types

from Agent.agent.auth.session import UnauthenticatedSessionError, require_customer_id

logger = logging.getLogger(__name__)

UNAUTHENTICATED_REPLY = "You need to sign in before I can help you."


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
