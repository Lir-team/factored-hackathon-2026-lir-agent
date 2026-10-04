"""Case intake: what the bank answers when a customer files a case from the web form.

The payload follows the `lir-web` contract (`resources/schemas/case.schema.json`); these
rules assume it already passed that schema.
"""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

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


def case_summary(payload: dict[str, Any], refs: Sequence[str] = ()) -> str:
    """The case as the customer's first message to the agent: description and charges.

    Charges are named by their session references (T1, T2, ...), never by internal id nor
    by merchant, amount or date: this message goes to the external model, and the bank's
    records reach it only through the tools, as placeholders.
    """
    if not refs:
        return payload["description"]
    label = _REPORTED_CHARGES.get(payload["language"], _DEFAULT_REPORTED_CHARGES)
    return f"{payload['description']}\n{label}: {', '.join(refs)}."


# Form category for a lost or stolen card: always a fraud case.
LOST_OR_STOLEN_CARD = "card_lost_stolen"


class CaseReport(BaseModel):
    """What the customer reported in the form, as structured facts (not model inferences).

    The form asks directly whether the card was lost or stolen and whether to freeze it; the
    policy acts on those answers instead of re-inferring them from the description.
    """

    model_config = ConfigDict(frozen=True)

    category: str
    fraud_suspected: bool
    freeze_card_requested: bool
    card_in_possession: str | None = None
    shared_credentials: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CaseReport":
        """The report of a schema-valid case payload."""
        incident = payload.get("incident") or {}
        return cls(
            category=payload["category"],
            fraud_suspected=payload["fraud_suspected"],
            freeze_card_requested=payload["freeze_card_requested"],
            card_in_possession=incident.get("card_in_possession"),
            shared_credentials=incident.get("shared_credentials"),
        )

    def facts(self) -> dict[str, bool]:
        """Policy facts (turn rules T0b, T0c)."""
        return {
            "case_fraud_reported": self.fraud_suspected
            or self.category == LOST_OR_STOLEN_CARD,
            "case_freeze_requested": self.freeze_card_requested,
        }


def new_start_token() -> str:
    """An opaque, unguessable Telegram start token. Never log it."""
    return secrets.token_urlsafe(_START_TOKEN_BYTES)


def telegram_start_url(bot_username: str, token: str) -> str:
    """Deep link that opens the bot and sends `/start <token>` when the customer taps Start."""
    return f"https://t.me/{bot_username}?start={token}"
