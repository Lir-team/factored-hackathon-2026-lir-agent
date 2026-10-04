"""Case intake: what the bank answers when a customer files a case from the web form.

The payload follows the `lir-web` contract (`resources/schemas/case.schema.json`); these
rules assume it already passed that schema.
"""

import secrets
from dataclasses import dataclass
from typing import Any

from lir_agent.domain.errors import DomainError

# 24 random bytes -> 32 URL-safe characters, within Telegram's 64-character start payload
# (A-Z, a-z, 0-9, _ and -).
_START_TOKEN_BYTES = 24


@dataclass(frozen=True)
class CaseReceipt:
    """The answer to an accepted case, replayed unchanged for a repeated idempotency key."""

    case_id: str
    folio: str
    telegram_start_url: str | None
    status: str = "received"


@dataclass(frozen=True)
class CaseStart:
    """What a Telegram start token links a chat to: the case, its folio and language."""

    case_id: str
    folio: str
    language: str


@dataclass(frozen=True)
class CaseConversation:
    """The agent's conversation about a case, started when the case is delivered to it."""

    case_id: str
    folio: str
    language: str
    owner: str
    session_id: str


class IdempotencyKeyMismatchError(DomainError):
    """The `Idempotency-Key` is not the payload's `case_id`."""


class ForeignCaseError(DomainError):
    """The case, or its idempotency key, belongs to another customer."""


class UnknownTransactionError(DomainError):
    """A reported transaction does not belong to the customer."""


def folio_for(case_id: str, submitted_at: str) -> str:
    """Human-readable reference, e.g. `LB-2026-3F2A9C` (same rule as `lir-web`'s `folioFor`)."""
    return f"LB-{submitted_at[:4]}-{case_id.replace('-', '')[:6].upper()}"


def case_attributes(payload: dict[str, Any]) -> dict[str, str]:
    """String attributes that route the case before its payload is read (contract)."""
    return {
        "category": payload["category"],
        "intent_hint": payload["intent_hint"],
        "fraud_suspected": str(payload["fraud_suspected"]).lower(),
        "priority_hint": payload["priority_hint"],
        "country": payload["customer"]["country"],
        "language": payload["language"],
        "schema_version": payload["schema_version"],
    }


# Written as the customer, in the case language (`en` reads as Spanish, like the agent).
_REPORTED_CHARGES = {"pt": "Cobranças que reporto"}
_DEFAULT_REPORTED_CHARGES = "Cargos que reporto"


def case_summary(payload: dict[str, Any]) -> str:
    """The case as the customer's first message to the agent: description and charges.

    Charges are described as the customer saw them (merchant, amount, date), never by
    internal transaction id: the agent only sees opaque references to records.
    """
    charges = "; ".join(
        f"{t['merchant']}, {t['amount']} {t['currency']}, {t['occurred_at'][:10]}"
        for t in payload["transactions"]
    )
    if not charges:
        return payload["description"]
    label = _REPORTED_CHARGES.get(payload["language"], _DEFAULT_REPORTED_CHARGES)
    return f"{payload['description']}\n{label}: {charges}."


def new_start_token() -> str:
    """An opaque, unguessable Telegram start token. Never log it."""
    return secrets.token_urlsafe(_START_TOKEN_BYTES)


def telegram_start_url(bot_username: str, token: str) -> str:
    """Deep link that opens the bot and sends `/start <token>` when the customer taps Start."""
    return f"https://t.me/{bot_username}?start={token}"
