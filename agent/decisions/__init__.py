"""Capa de decisiones tipadas del agente (§5 de docs/propuesta-opcion-1-disputas.md)."""
from __future__ import annotations

from .. import config
from .base import Answer, Choice, DecisionError, DecisionModel, DecisionResult, Noul, Question
from .chain import ChainDecisionModel
from .jev import JevBillingError, JevClient
from .keywords import KeywordDecisionModel

__all__ = [
    "Answer", "Choice", "DecisionError", "DecisionModel", "DecisionResult", "Noul", "Question",
    "ChainDecisionModel", "JevBillingError", "JevClient", "KeywordDecisionModel", "build_default",
]


def jev_from_env() -> JevClient | None:
    account, token = config.get("CLOUDFLARE_ACCOUNT_ID"), config.get("CLOUDFLARE_API_TOKEN")
    if not account or not token:
        return None
    return JevClient(account_id=account, api_token=token)


def build_default() -> DecisionModel:
    """Jev si está habilitado (JEV_ENABLED=1 y credenciales); si no, o si falla, el baseline.

    Jev queda apagado por defecto hasta cargar saldo en AI Gateway.
    """
    models: list[DecisionModel] = []
    if config.get("JEV_ENABLED") == "1" and (jev := jev_from_env()):
        models.append(jev)
    models.append(KeywordDecisionModel())
    return ChainDecisionModel(models)
