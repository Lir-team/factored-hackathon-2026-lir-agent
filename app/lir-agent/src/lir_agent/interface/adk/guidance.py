"""Texts attached to a turn: guidance for the model and fixed replies for the customer."""

from datetime import date

from lir_agent.domain.errors import AuthError
from lir_agent.domain.language import DEFAULT_LANGUAGE, Language
from lir_agent.domain.session import SessionState


class TurnGuidance:
    """Renders the per-turn guidance appended to each model request."""

    def __init__(self, templates: dict[str, str]) -> None:
        """Keep the guidance templates keyed by turn lane or auth error."""
        self._templates = templates

    def for_auth_error(self, error: AuthError) -> str:
        """Guidance for the model when the session is not usable."""
        return self._templates[error.value]

    def date_context(self, today: date) -> str:
        """Today's date, so the model resolves relative or yearless dates correctly."""
        return self._templates["date_context"].format(today=today.isoformat())

    def for_turn(self, session: SessionState) -> str:
        """Guidance for the current turn lane, with the rule that produced it."""
        outcome = session.turn_outcome
        return self._templates[session.turn_lane.value].format(
            rule_id=outcome.rule_id if outcome else None,
            reason=outcome.reason if outcome else None,
            handoff_id=session.handoff_id,
        )


class CustomerMessages:
    """Fixed replies sent to the customer without calling the model."""

    def __init__(self, messages: dict[str, dict[str, str]]) -> None:
        """Keep the customer-facing messages keyed by situation, then language."""
        self._messages = messages

    def for_auth_error(self, error: AuthError, language: Language) -> str:
        """Reply for a session without a valid signed-in customer, in their language."""
        by_language = self._messages[error.value]
        return by_language.get(language) or by_language[DEFAULT_LANGUAGE]
