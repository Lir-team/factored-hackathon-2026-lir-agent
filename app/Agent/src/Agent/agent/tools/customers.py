"""Customer tools.

Customer records are PII. The model only receives the fields in
`MODEL_VISIBLE_FIELDS`: what it never sees, it cannot leak. Logs carry the
customer ID for traceability and never the record itself.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from google.adk.tools import ToolContext

from Agent.agent.hardening.guardrails.authentication import require_customer_id
from Agent.config import get_settings
from Agent.infrastructure.customers import CustomerRepository

logger = logging.getLogger(__name__)

# The only customer fields sent to the model. An allowlist, not a denylist:
# a column added to the data later stays hidden until someone opts it in here.
# Identity documents, contact data, address, demographics, financials and the
# customer ID are deliberately absent.
MODEL_VISIBLE_FIELDS = (
    "first_name",
    "segment",
    "customer_status",
    "registration_date",
    "country",
    "accepts_marketing",
)


@lru_cache
def _repository() -> CustomerRepository:
    return CustomerRepository(get_settings().data_dir)


def _to_json_value(value: Any) -> Any:
    """Coerce DuckDB types into values the model (and JSON) understand."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def get_my_customer_profile(tool_context: ToolContext) -> dict[str, Any]:
    """Return the profile of the customer signed in to this session.

    Takes no arguments: the customer is already authenticated and identified
    by the session. Never ask the user for a customer ID, and never mention
    one.

    Returns:
        A dict with `found` set to True and a minimal, non-sensitive profile
        under `customer` (first name, segment, account status, registration
        date, country and marketing opt-in), or `found` set to False when no
        such customer exists. Contact details, identity documents and
        financial data are never available.

    Raises:
        UnauthenticatedSessionError: If the session has no authenticated
            customer (a wiring bug: the guardrail should have refused it).
    """
    # The authentication guardrail guarantees the ID, so a missing one here is
    # a wiring bug and raises instead of returning a friendly error.
    customer_id = require_customer_id(tool_context.state)
    row = _repository().get_by_id(customer_id)
    if row is None:
        logger.info(
            "Customer %s not found", customer_id, extra={"customer_id": customer_id}
        )
        return {"found": False}
    customer = {
        field: _to_json_value(row[field])
        for field in MODEL_VISIBLE_FIELDS
        if field in row
    }
    logger.debug("Customer %s found", customer_id, extra={"customer_id": customer_id})
    return {"found": True, "customer": customer}
