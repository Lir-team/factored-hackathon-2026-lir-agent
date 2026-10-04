"""Wire format of `POST /v1/cases`: the gateway identity header and the `lir-web` schema.

Schema errors are reported as `{"errors": {<form field>: <code>}}` with the field names and
codes the form already translates (see `lir-web` `docs/case-contract.md`).
"""

import base64
import json
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from lir_agent.config.settings import RESOURCES_DIR

SCHEMA_PATH = RESOURCES_DIR / "schemas" / "case.schema.json"

# Payload path (array indexes dropped) -> form field. The longest matching prefix wins.
_FORM_FIELDS: dict[tuple[str, ...], str] = {
    ("category",): "category",
    ("transactions",): "transaction_ids",
    ("cards",): "card_last4",
    ("incident", "occurred_at"): "incident_occurred_at",
    ("incident", "card_in_possession"): "card_in_possession",
    ("incident", "shared_credentials"): "shared_credentials",
    ("description",): "description",
    ("customer", "preferred_contact", "channel"): "contact_channel",
    ("customer", "preferred_contact", "value"): "contact_value",
    ("consent",): "declaration",
}
# JSON Schema keyword -> error code; any other keyword is "invalid".
_CODES = {
    "required": "required",
    "minLength": "too_short",
    "maxLength": "too_long",
    "minItems": "required",
    "maxItems": "too_many",
}


def customer_from_userinfo(header: str, claim: str) -> str | None:
    """The customer id in API Gateway's userinfo header, or None when it cannot be read.

    The header is the JWT payload as base64url JSON, usually without padding.
    """
    try:
        claims = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
    except ValueError:  # bad base64, bad UTF-8 or bad JSON
        return None
    customer = claims.get(claim) if isinstance(claims, dict) else None
    return customer if isinstance(customer, str) and customer else None


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def schema_errors(payload: Any) -> dict[str, str] | None:
    """Validate against the case schema.

    Returns:
        None when the payload is valid; otherwise the form-field errors, empty when no
        error maps to a form field (the caller then rejects the case as a whole).
    """
    errors = list(_validator().iter_errors(payload))
    if not errors:
        return None
    fields: dict[str, str] = {}
    for error in errors:
        code = _CODES.get(str(error.validator), "invalid")
        for path in _paths(error):
            field = _form_field(path)
            if field is not None:
                fields.setdefault(field, code)
    return fields


def _paths(error: ValidationError) -> list[tuple[str, ...]]:
    """Paths the error is about; a missing property is reported at its own path."""
    path = tuple(part for part in error.absolute_path if isinstance(part, str))
    required, instance = error.validator_value, error.instance
    if error.validator == "required" and isinstance(required, list):
        return [(*path, name) for name in required if name not in instance]
    return [path]


def _form_field(path: tuple[str, ...]) -> str | None:
    for size in range(len(path), 0, -1):
        field = _FORM_FIELDS.get(path[:size])
        if field is not None:
            return field
    return None
