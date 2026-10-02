"""Typed decision layer for the customer-service agent."""

from decision_layer import config
from decision_layer.base import (
    Answer,
    Choice,
    DecisionError,
    DecisionModel,
    DecisionResult,
    Noul,
    Question,
)
from decision_layer.chain import ChainDecisionModel
from decision_layer.jev import JevAuthError, JevBillingError, JevClient
from decision_layer.keywords import KeywordDecisionModel

__all__ = [
    "Answer",
    "ChainDecisionModel",
    "Choice",
    "DecisionError",
    "DecisionModel",
    "DecisionResult",
    "JevAuthError",
    "JevBillingError",
    "JevClient",
    "KeywordDecisionModel",
    "Noul",
    "Question",
    "build_default",
    "jev_from_env",
]


def jev_from_env() -> JevClient | None:
    account, token = config.get("CLOUDFLARE_ACCOUNT_ID"), config.get("CLOUDFLARE_API_TOKEN")
    if not account or not token:
        return None
    return JevClient(account_id=account, api_token=token)


def build_default() -> DecisionModel:
    """Jev when enabled (JEV_ENABLED=1 plus credentials); otherwise, or on failure, the baseline.

    Jev stays off by default until the AI Gateway balance is topped up.
    """
    models: list[DecisionModel] = []
    if config.get("JEV_ENABLED") == "1" and (jev := jev_from_env()):
        models.append(jev)
    models.append(KeywordDecisionModel())
    return ChainDecisionModel(models)
