"""Common contract for decision models (Jev, Laya, LLM, keyword rules).

The agent depends only on this interface: typed questions in, answers with a
probability out. Swapping the provider never touches the agent.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Noul:
    """Yes/no question. The answer carries P(true)."""

    instructions: str
    true: str
    false: str


@dataclass(frozen=True, slots=True)
class Choice:
    """Pick one of `options` (name -> description)."""

    instructions: str
    options: Mapping[str, str]


type Question = Noul | Choice


@dataclass(frozen=True, slots=True)
class Answer:
    """Typed answer.

    - Noul: `value` is a bool (p >= 0.5) and `probability` is P(true).
    - Choice: `value` is the chosen option and `probabilities` the distribution.
    """

    value: bool | str
    probability: float
    probabilities: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecisionResult:
    answers: Mapping[str, Answer]
    model: str
    latency_ms: float
    input_tokens: int | None = None


class DecisionError(Exception):
    """The model could not decide. The caller falls back to the next model or hands off."""


class DecisionModel(Protocol):
    name: str

    def decide(self, state: str, questions: Mapping[str, Question]) -> DecisionResult:
        """`state` is customer text only (plus merchant names for D4).

        Never account IDs, documents, balances, amounts or dates: they would leave
        the perimeter (data/AGENTS.md rule 3).
        """
        ...
