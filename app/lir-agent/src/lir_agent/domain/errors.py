"""Domain errors."""

from enum import StrEnum


class DomainError(Exception):
    """Base class for expected, recoverable business errors."""


class TransactionNotFoundError(DomainError):
    """The transaction reference cannot be used by this session.

    Unknown references and other customers' transactions share one error, so the
    existence of other customers' records is never revealed.
    """


class UnauthenticatedSessionError(DomainError):
    """A component that requires a signed-in customer ran without one.

    The session guard refuses such sessions before the agent runs, so reaching this is a
    wiring bug, not a customer error.
    """


class InvalidSearchCriteriaError(DomainError):
    """A tool argument is malformed, e.g. a date not in ISO format."""

    def __init__(self, field: str, expected: str) -> None:
        """Record which field failed and the expected format."""
        self.field = field
        self.expected = expected
        super().__init__(f"Invalid {field}; expected {expected}")


class AuthError(StrEnum):
    """Why a session cannot be used."""

    MISSING = "session_not_authenticated"
    EXPIRED = "session_expired"


class DisputeBlock(StrEnum):
    """Why a dispute cannot be put to the customer for approval."""

    NO_GROUND = "no_ground_for_dispute"
