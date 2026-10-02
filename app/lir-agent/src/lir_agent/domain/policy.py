"""Declarative, versioned policy engine.

The rules are data (resources/policy.yaml); this module validates and evaluates them.
The LLM never chooses a lane: it reads it from tool results.
"""

import operator
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, field_validator

from lir_agent.domain.models import Lane, Outcome

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


class OutputGuard(BaseModel):
    """Deterministic check on model replies (refund promises, credential requests)."""

    forbidden_patterns: list[str]
    fallback_message: str

    def violations(self, text: str) -> list[str]:
        """The forbidden patterns found in a reply."""
        return [
            pattern for pattern in self.forbidden_patterns if re.search(pattern, text)
        ]


class SearchSettings(BaseModel):
    """Limits and thresholds for matching the customer's description."""

    amount_tolerance_pct: float
    max_candidates: int
    merchant_min_probability: float


class LlmExposure(BaseModel):
    """Fields that may reach the external LLM (data minimization)."""

    customer_fields: list[str]
    candidate_fields: list[str]
    evidence_fields: list[str]
    outcome_fields: list[str]


class PolicyConfig(BaseModel):
    """The whole versioned policy file, validated at startup."""

    version: str
    decision_keys: dict[str, str]
    turn_rules: list[Rule]
    case_rules: list[Rule]
    tools_by_turn_lane: dict[Lane, list[str]]
    evidence: dict[str, float]
    search: SearchSettings
    confirmation: dict[str, float]
    llm_exposure: LlmExposure
    output_guard: OutputGuard

    @field_validator("turn_rules", "case_rules")
    @classmethod
    def _ends_with_default(cls, rules: list[Rule]) -> list[Rule]:
        if not rules or rules[-1].when:
            raise ValueError(
                "Each rule list must end with a default rule (empty `when`)"
            )
        return rules


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
        return {
            name for names in self._config.tools_by_turn_lane.values() for name in names
        }

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
