"""Contrato común para los modelos de decisión (Jev, Laya, LLM, reglas).

El agente solo depende de esta interfaz: preguntas tipadas -> respuestas con
probabilidad. Así se puede cambiar de proveedor sin tocar el agente (§5.6 de
docs/propuesta-opcion-1-disputas.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Union


@dataclass(frozen=True)
class Noul:
    """Pregunta sí/no. La respuesta es P(verdadero)."""

    instructions: str
    true: str
    false: str


@dataclass(frozen=True)
class Choice:
    """Elegir una opción entre `options` (nombre -> descripción)."""

    instructions: str
    options: Mapping[str, str]


Question = Union[Noul, Choice]


@dataclass(frozen=True)
class Answer:
    """Respuesta tipada.

    - Noul: `value` es bool (p >= 0.5) y `probability` es P(verdadero).
    - Choice: `value` es la opción elegida y `probabilities` la distribución.
    """

    value: Union[bool, str]
    probability: float
    probabilities: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResult:
    answers: Mapping[str, Answer]
    model: str
    latency_ms: float
    input_tokens: int | None = None


class DecisionError(Exception):
    """El modelo no pudo decidir. El llamador debe caer al siguiente modelo o derivar."""


class DecisionModel(Protocol):
    name: str

    def decide(self, state: str, questions: Mapping[str, Question]) -> DecisionResult:
        """`state` es solo texto del cliente (y nombres de comercio para D4).

        Nunca IDs, documentos, saldos, montos ni fechas de la cuenta (§5.4).
        """
        ...
