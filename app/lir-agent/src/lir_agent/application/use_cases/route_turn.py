"""Use case: classify a new customer message and route the turn through the policy engine."""

import time

from decision_layer import DecisionError, DecisionModel
from decision_layer.questions import TURN_QUESTIONS

from lir_agent.application.ports import AuditSink
from lir_agent.application.questions import CONFIRMATION, REJECTS_EXPLANATION
from lir_agent.application.use_cases.request_handoff import RequestHandoff
from lir_agent.domain.models import Lane, Outcome
from lir_agent.domain.policy import PolicyEngine
from lir_agent.domain.pseudonyms import Pseudonyms
from lir_agent.domain.session import SessionState


class RouteTurn:
    """Use case: classify a new message and route the turn through the policy."""

    def __init__(
        self,
        decisions: DecisionModel,
        policy: PolicyEngine,
        handoff: RequestHandoff,
        audit: AuditSink,
    ) -> None:
        """Keep the decision model, policy, handoff use case and audit sink."""
        self._decisions = decisions
        self._policy = policy
        self._handoff = handoff
        self._audit = audit
        self._confirms_key = policy.config.decision_keys["confirms"]
        self._rejects_key = policy.config.decision_keys["rejects_explanation"]
        self._confirm_min = policy.config.confirmation["min_probability"]

    def execute(
        self, session: SessionState, text: str, session_id: str | None = None
    ) -> Outcome:
        """Decide the turn lane for a new customer message and record why."""
        session.last_user_text = text
        pending = session.pending_confirmation
        questions = dict(TURN_QUESTIONS)
        if pending:
            questions[self._confirms_key] = CONFIRMATION
        elif session.explained_transaction:
            questions[self._rejects_key] = REJECTS_EXPLANATION
        # The decision model may be external: it reads the message without identifiers
        # and with the bank's records (merchants, the customer's name) as placeholders.
        model_text = Pseudonyms(session).protect_customer_text(text)
        decisions, session.decision_meta = self._classify(
            model_text, questions, session_id
        )

        confirmed_now = self._resolve_confirmation(session, pending, decisions)
        facts = {
            **self._policy.turn_facts(decisions),
            **(session.case_report.facts() if session.case_report else {}),
            "confirmed_now": confirmed_now,
            "awaiting_confirmation": bool(pending) and not confirmed_now,
            "review_requested": bool(session.review_transaction),
        }
        outcome = self._policy.route_turn(facts)
        session.decisions = decisions
        session.turn_outcome = outcome
        self._audit.record(
            "turn_routed",
            session_id,
            outcome=outcome.model_dump(mode="json"),
            decisions=decisions,
            confirmed_now=confirmed_now,
        )
        if outcome.lane == Lane.REVIEW and not session.review_transaction:
            # Registered in code: the next message is checked for an explicit "yes" to the
            # dispute details, whatever the model says or does.
            session.review_transaction = session.explained_transaction
            session.pending_confirmation = session.explained_transaction
        if outcome.lane == Lane.ESCALATE and not session.handoff_id:
            result = self._handoff.execute(session)
            self._audit.record(
                "handoff_created", session_id, trigger=outcome.rule_id, **result
            )
        return outcome

    def _resolve_confirmation(
        self, session: SessionState, pending: str | None, decisions: dict | None
    ) -> bool:
        if not (pending and decisions):
            return False
        confirmed = (
            decisions.get(self._confirms_key, {}).get("probability", 0.0)
            >= self._confirm_min
        )
        session.confirmed_transaction = pending if confirmed else None
        if (
            confirmed
        ):  # an unclear answer keeps the request pending; the agent asks again
            session.pending_confirmation = None
        return confirmed

    def _classify(
        self, text: str, questions: dict, session_id: str | None
    ) -> tuple[dict | None, dict | None]:
        """Serialized typed decisions and who answered them.

        Decisions are None when no model could decide (the policy escalates); the metadata
        names the model that answered, its latency and the models that failed before it.
        """
        started = time.perf_counter()
        try:
            result = self._decisions.decide(text, questions)
        except DecisionError as error:
            self._audit.record("decision_failed", session_id, error=str(error))
            return None, {"model": None, "latency_ms": None, "fallback": [str(error)]}
        self._audit.record(
            "decision",
            session_id,
            model=result.model,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            wall_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        # A chain answered, but a model before it failed: record why, so a provider that
        # always fails (billing, auth, an unsupported parameter) does not go unnoticed.
        failures = getattr(self._decisions, "failures", None) or []
        if failures:
            self._audit.record(
                "decision_fallback",
                session_id,
                answered_by=result.model,
                failures=[[name, reason] for name, reason in failures],
            )
        decisions = {
            key: {"value": a.value, "probability": a.probability}
            for key, a in result.answers.items()
        }
        meta = {
            "model": result.model,
            "latency_ms": result.latency_ms,
            "fallback": [f"{name}: {reason}" for name, reason in failures] or None,
        }
        return decisions, meta
