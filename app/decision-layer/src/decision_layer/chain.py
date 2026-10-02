"""Fallback chain: try each model in order.

If every model fails it raises DecisionError and the agent hands off to a human
(safe fallback). Each failure is kept in `failures` for the trace.
"""

from collections.abc import Mapping, Sequence

from decision_layer.base import DecisionError, DecisionModel, DecisionResult, Question


class ChainDecisionModel:
    def __init__(self, models: Sequence[DecisionModel]) -> None:
        if not models:
            raise ValueError("The chain needs at least one model")
        self.models = list(models)
        self.name = " > ".join(m.name for m in self.models)
        self.failures: list[tuple[str, str]] = []

    def decide(self, state: str, questions: Mapping[str, Question]) -> DecisionResult:
        self.failures = []
        for model in self.models:
            try:
                return model.decide(state, questions)
            except DecisionError as e:
                self.failures.append((model.name, str(e)))
        raise DecisionError(f"No model could decide: {self.failures}")
