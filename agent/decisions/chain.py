"""Cadena de respaldo: prueba cada modelo en orden (§5.6).

Si todos fallan levanta DecisionError y el agente deriva al humano (safe fallback).
Cada fallo queda en `failures` para la traza.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from .base import DecisionError, DecisionModel, DecisionResult, Question


class ChainDecisionModel:
    def __init__(self, models: Sequence[DecisionModel]):
        if not models:
            raise ValueError("La cadena necesita al menos un modelo")
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
        raise DecisionError(f"Ningún modelo pudo decidir: {self.failures}")
