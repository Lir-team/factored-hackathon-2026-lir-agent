"""Decision models that need the agent's dependencies (the baselines live in decision_layer)."""

from lir_agent.infrastructure.decisions.llm import LlmDecisionModel

__all__ = ["LlmDecisionModel"]
