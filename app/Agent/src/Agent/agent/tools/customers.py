"""Customer tools.

Customer records are PII. Logs carry the customer ID for traceability and
never the record itself.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

from Agent.config import get_settings
from Agent.infrastructure.customers import CustomerRepository

logger = logging.getLogger(__name__)

# Internal columns the model has no use for.
_HIDDEN_COLUMNS = {"_source_file"}


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


def get_customer_by_id(customer_id: str) -> dict[str, Any]:
    """Look up a single bank customer by their unique customer ID.

    Use this when the user refers to a specific customer by ID. IDs are "CLI-"
    followed by 12 uppercase letters or digits. Do not guess an ID; ask the
    user for it.

    Args:
        customer_id: The customer's unique identifier.

    Returns:
        A dict with `found` set to True and the customer's fields under
        `customer`, or `found` set to False when no customer has that ID.
    """
    # The model may call the tool with a missing or empty argument. Answer
    # with a correction it can act on instead of raising.
    normalized = (customer_id or "").strip().upper()
    if not normalized:
        logger.warning("get_customer_by_id called without a customer_id")
        return {
            "found": False,
            "error": "customer_id is required; ask the user for the customer ID.",
        }
    row = _repository().get_by_id(normalized)
    if row is None:
        logger.info(
            "Customer %s not found", normalized, extra={"customer_id": normalized}
        )
        return {"found": False, "customer_id": normalized}
    customer = {
        key: _to_json_value(value)
        for key, value in row.items()
        if key not in _HIDDEN_COLUMNS
    }
    logger.debug("Customer %s found", normalized, extra={"customer_id": normalized})
    return {"found": True, "customer": customer}
