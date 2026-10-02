"""Session identity contract: where the authenticated customer's ID lives.

Session creation stores the ID in session state under `CUSTOMER_ID_STATE_KEY`,
so it never passes through the model. The authentication guardrail and the
tools both read it through `require_customer_id`.
"""

from typing import Any, Protocol

# Session-state key holding the authenticated customer's ID. This is the
# session identity contract shared by session creation, the guardrail and tools.
CUSTOMER_ID_STATE_KEY = "customer_id"


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
