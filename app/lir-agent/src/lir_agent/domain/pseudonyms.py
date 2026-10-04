"""Pseudonyms for bank records sent to external models.

Bank records (the customer's name, merchants, amounts and dates of their transactions)
reach the external LLM only as placeholders such as `[[COMERCIO_1]]`. The table that maps
each placeholder back to its value lives in the session state, inside our infrastructure,
and the replies are resolved before they reach the customer. The provider never receives
a value from the bank's systems, and a placeholder means nothing outside the session.

What the customer types still reaches the model (it has to understand it), after
`redact_identifiers` removes card, account, document and contact numbers.
"""

import re
from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from lir_agent.domain.session import SessionState


class Kind(StrEnum):
    """What a placeholder stands for; its value names it (`[[MONTO_2]]`)."""

    NAME = "NOMBRE"
    MERCHANT = "COMERCIO"
    AMOUNT = "MONTO"
    DATE = "FECHA"


# Kinds concealed in the customer's own words too. Amounts and dates the customer types stay
# readable: the model passes them to the search tool as numbers and ISO dates.
TEXT_KINDS = frozenset({Kind.NAME, Kind.MERCHANT})

# Irreversible: the agent never needs a card, account, document or contact number.
REDACTED = "[[DATO_PROTEGIDO]]"
# Shown to the customer when the model writes a placeholder this session never issued.
UNKNOWN_PLACEHOLDER = "—"

_PLACEHOLDER = re.compile(r"\[\[([A-Z]+)_(\d+)\]\]")

_IDENTIFIERS = (
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),  # e-mail
    re.compile(  # CURP (MX)
        r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z\d]\d\b", re.IGNORECASE
    ),
    re.compile(  # RFC (MX)
        r"\b[A-ZÑ&]{3,4}\d{6}[A-Z\d]{3}\b", re.IGNORECASE
    ),
    # Ten or more digits, optionally grouped: cards, CLABE/CBU, accounts, phones, CPF.
    # A number with exactly two decimals is an amount, not an identifier.
    re.compile(r"(?<![\w.,])(?!\d+[.,]\d{2}(?![\w.,]))\+?\d(?:[ .-]?\d){9,}(?![\w])"),
)


def redact_identifiers(text: str) -> tuple[str, int]:
    """Text without card, account, document or contact numbers, and how many were removed."""
    count = 0
    for pattern in _IDENTIFIERS:
        text, found = pattern.subn(REDACTED, text)
        count += found
    return text, count


def format_value(kind: Kind, value: Any) -> str:
    """The text a placeholder resolves to: what the customer reads."""
    if kind is Kind.AMOUNT and isinstance(value, int | float):
        return f"{value:.2f}"
    if kind is Kind.DATE and isinstance(value, datetime | date):
        return value.strftime("%d/%m/%Y")
    return str(value)


class Pseudonyms:
    """Issues and resolves the placeholders of one session."""

    def __init__(self, session: SessionState) -> None:
        """Work on the session's placeholder table."""
        self._session = session

    def placeholder(self, kind: Kind, value: Any) -> Any:
        """The placeholder for a value, issued once per distinct value and kind.

        Missing values stay missing: the model must know a merchant is unknown.
        """
        if value is None or value == "":
            return value
        text = format_value(kind, value)
        table = self._session.pseudonyms
        for token, known in table.items():
            if known == text and token.startswith(f"[[{kind.value}_"):
                return token
        issued = sum(1 for token in table if token.startswith(f"[[{kind.value}_"))
        token = f"[[{kind.value}_{issued + 1}]]"
        self._session.pseudonyms = {**table, token: text}
        return token

    def reveal(self, text: str) -> tuple[str, list[str]]:
        """Text with every placeholder resolved, and the placeholders this session never issued."""
        table = self._session.pseudonyms
        unknown: list[str] = []

        def resolve(match: re.Match[str]) -> str:
            token = match.group(0)
            if token == REDACTED:
                return token
            if token in table:
                return table[token]
            unknown.append(token)
            return UNKNOWN_PLACEHOLDER

        return _PLACEHOLDER.sub(resolve, text), unknown

    def reveal_value(self, value: Any) -> Any:
        """A tool argument (text, list or mapping) with its placeholders resolved."""
        if isinstance(value, str):
            return self.reveal(value)[0]
        if isinstance(value, list):
            return [self.reveal_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.reveal_value(item) for key, item in value.items()}
        return value

    def conceal_value(self, value: Any) -> Any:
        """A tool argument or result (text, list or mapping) with known values concealed."""
        if isinstance(value, str):
            return self.conceal(value)
        if isinstance(value, list):
            return [self.conceal_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.conceal_value(item) for key, item in value.items()}
        return value

    def conceal(self, text: str, kinds: Iterable[Kind] = tuple(Kind)) -> str:
        """Text with the known values of `kinds` replaced by their placeholders.

        Replies the customer read hold the resolved values; they go back to the model as
        conversation history, so they are concealed again on every request.
        """
        prefixes = tuple(f"[[{kind.value}_" for kind in kinds)
        pairs = sorted(
            (
                (token, value)
                for token, value in self._session.pseudonyms.items()
                if token.startswith(prefixes)
            ),
            key=lambda pair: len(pair[1]),
            reverse=True,  # "Uber Eats" before "Uber"
        )
        for token, value in pairs:
            text = re.sub(
                rf"(?<![\w.,]){re.escape(value)}(?![\w])",
                token,
                text,
                flags=re.IGNORECASE,
            )
        return text

    def protect_customer_text(self, text: str) -> str:
        """The customer's words as an external model may read them."""
        redacted, _ = redact_identifiers(text)
        return self.conceal(redacted, TEXT_KINDS)
