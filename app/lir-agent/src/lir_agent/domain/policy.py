"""Declarative, versioned policy engine.

The rules are data (resources/policy.yaml); this module validates and evaluates them.
The LLM never chooses a lane: it reads it from tool results.
"""

import operator
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from lir_agent.domain.approvals import Approver
from lir_agent.domain.language import DEFAULT_LANGUAGE, Language
from lir_agent.domain.models import Lane, Outcome
from lir_agent.domain.pseudonyms import Kind

OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "in": lambda value, options: value in options,
}


class Rule(BaseModel):
    """One policy rule: when every condition holds, the case goes to `lane`."""

    id: str
    lane: Lane
    reason: str
    when: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("when")
    @classmethod
    def _known_operators(
        cls, when: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        for fact, conditions in when.items():
            unknown = set(conditions) - set(OPERATORS)
            if unknown:
                raise ValueError(
                    f"Unknown operator(s) {sorted(unknown)} for fact {fact!r}"
                )
        return when

    def matches(self, facts: dict[str, Any]) -> bool:
        """Whether every condition holds for the given facts."""
        for fact, conditions in self.when.items():
            value = facts.get(fact)
            for op, expected in conditions.items():
                if value is None or not OPERATORS[op](value, expected):
                    return False
        return True


# A negation in the same clause, before the match, turns a promise into its denial
# ("no puedo prometer que te vamos a devolver"). Clauses end at punctuation, so
# "No te preocupes, te vamos a devolver" is still a promise.
_NEGATION = re.compile(r"\b(no|não|nao|nunca|jamás|jamais|ni|nem|sin|sem)\b", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[,;:.!?\n]")
_WORD = re.compile(r"\w+")


class OutputGuard(BaseModel):
    """Deterministic check on model replies (refund promises, credential requests)."""

    # Always blocked, negated or not (e.g. asking for a CVV).
    forbidden_patterns: list[str]
    # Blocked unless negated in the same clause (e.g. refund promises).
    forbidden_unless_negated: list[str] = []
    # Words allowed between a negation and the promise for the negation to govern it
    # ("no puedo prometer que ..."). Any other word in between means the negation belongs
    # to something else ("si no lo reconoces, te vamos a devolver"), so the promise is
    # blocked. Empty keeps the clause-only rule.
    negation_bridge_words: frozenset[str] = frozenset()
    fallback_message: dict[Language, str]

    @field_validator("negation_bridge_words", mode="before")
    @classmethod
    def _lowercase_bridge_words(cls, words: Any) -> Any:
        return frozenset(w.lower() for w in words) if words is not None else words

    def violations(self, text: str) -> list[str]:
        """The forbidden patterns found in a reply."""
        always = [p for p in self.forbidden_patterns if re.search(p, text)]
        promised = [
            p
            for p in self.forbidden_unless_negated
            if any(not self._negated(text, m.start()) for m in re.finditer(p, text))
        ]
        return always + promised

    def fallback(self, language: Language) -> str:
        """The replacement reply, in the customer's language."""
        return self.fallback_message.get(language) or self.fallback_message[DEFAULT_LANGUAGE]

    def _negated(self, text: str, start: int) -> bool:
        clause = _CLAUSE_END.split(text[:start])[-1]
        negations = list(_NEGATION.finditer(clause))
        if not self.negation_bridge_words:
            return bool(negations)
        return any(
            all(
                word.lower() in self.negation_bridge_words
                for word in _WORD.findall(clause[negation.end() :])
            )
            for negation in negations
        )


class SearchSettings(BaseModel):
    """Limits and thresholds for matching the customer's description."""

    amount_tolerance_pct: float
    max_date_only_range_days: int = 1
    max_candidates: int
    merchant_min_probability: float


class LlmExposure(BaseModel):
    """Fields that may reach the external LLM (data minimization)."""

    customer_fields: list[str]
    candidate_fields: list[str]
    evidence_fields: list[str]
    outcome_fields: list[str]
    # Allowed fields that reach the LLM only as placeholders (`[[COMERCIO_1]]`), resolved
    # inside the service before the reply reaches the customer.
    pseudonymized_fields: dict[str, Kind] = Field(default_factory=dict)


class ApprovalSettings(BaseModel):
    """Human in the loop: who approves each kind of action, and for how long a request lives."""

    ttl_minutes: int = Field(gt=0)
    # Tools that are important actions, with their approvers in order: a call creates an
    # approval request instead of running, and only the last approval runs the action.
    actions: dict[str, list[Approver]]

    @field_validator("actions")
    @classmethod
    def _non_empty(cls, actions: dict[str, list[Approver]]) -> dict[str, list[Approver]]:
        if any(not approvers for approvers in actions.values()):
            raise ValueError("Every approval needs at least one approver")
        return actions


class PolicyConfig(BaseModel):
    """The whole versioned policy file, validated at startup."""

    version: str
    decision_keys: dict[str, str]
    turn_rules: list[Rule]
    case_rules: list[Rule]
    tools_by_turn_lane: dict[Lane, list[str]]
    evidence: dict[str, float]
    search: SearchSettings
    approvals: ApprovalSettings
    llm_exposure: LlmExposure
    output_guard: OutputGuard
    handoff_open_questions: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("turn_rules", "case_rules")
    @classmethod
    def _ends_with_default(cls, rules: list[Rule]) -> list[Rule]:
        if not rules or rules[-1].when:
            raise ValueError(
                "Each rule list must end with a default rule (empty `when`)"
            )
        return rules

    @model_validator(mode="after")
    def _open_questions_name_rules(self) -> "PolicyConfig":
        known = {rule.id for rule in (*self.turn_rules, *self.case_rules)}
        unknown = sorted(set(self.handoff_open_questions) - known)
        if unknown:
            raise ValueError(f"handoff_open_questions names unknown rules: {unknown}")
        return self

    def condition(self, rule_id: str, fact: str) -> tuple[str, float] | None:
        """How a rule compares `fact` (operator and value, e.g. C1: gte 70), or None."""
        for rule in (*self.turn_rules, *self.case_rules):
            if rule.id == rule_id and fact in rule.when:
                op, value = next(iter(rule.when[fact].items()))
                return (op, float(value)) if isinstance(value, int | float) else None
        return None

    def open_questions_for(self, *outcomes: Outcome | None) -> list[str]:
        """The policy's open questions for the rules that sent the case to a human."""
        return [
            question
            for outcome in outcomes
            if outcome
            for question in self.handoff_open_questions.get(outcome.rule_id, [])
        ]


class PolicyEngine:
    """Evaluates the declarative rules; the only component that chooses a lane."""

    def __init__(self, config: PolicyConfig) -> None:
        """Keep the validated policy configuration."""
        self._config = config

    @property
    def config(self) -> PolicyConfig:
        """The validated policy configuration."""
        return self._config

    @property
    def version(self) -> str:
        """Policy version recorded in every outcome."""
        return self._config.version

    def route_turn(self, facts: dict[str, Any]) -> Outcome:
        """Lane for the current turn, from the typed decisions on the message."""
        return self._first_match(self._config.turn_rules, facts)

    def decide_case(self, facts: dict[str, Any]) -> Outcome:
        """Lane for a case, from the evidence of one transaction."""
        return self._first_match(self._config.case_rules, facts)

    def allowed_tools(self, turn_lane: Lane) -> list[str]:
        """Tools the agent may call in a turn lane."""
        return self._config.tools_by_turn_lane.get(turn_lane, [])

    def all_referenced_tools(self) -> set[str]:
        """Every tool name the policy refers to, checked against the toolkit at startup."""
        lanes = {name for names in self._config.tools_by_turn_lane.values() for name in names}
        return lanes | set(self._config.approvals.actions)

    def turn_facts(self, decisions: dict[str, dict] | None) -> dict[str, Any]:
        """Policy facts from serialized decisions ({question_key: {value, probability}})."""
        if decisions is None:
            return {"decisions_available": False}
        facts: dict[str, Any] = {"decisions_available": True}
        for fact, key in self._config.decision_keys.items():
            answer = decisions.get(key)
            if answer is not None:
                facts[fact] = answer.get("value")
                facts[f"{fact}_p"] = answer.get("probability")
        return facts

    def _first_match(self, rules: list[Rule], facts: dict[str, Any]) -> Outcome:
        rule = next(
            r for r in rules if r.matches(facts)
        )  # guaranteed by the default rule
        return Outcome(
            lane=rule.lane,
            rule_id=rule.id,
            reason=rule.reason,
            policy_version=self.version,
        )
