"""Decisiones D1–D4 de la propuesta (§5.2)."""
from __future__ import annotations

from typing import Mapping

from .base import Choice, Noul, Question

INTENTS = {
    "cargo_no_reconocido": "No reconoce un cargo o compra en su cuenta o tarjeta",
    "cobro_indebido": "Reconoce el cargo pero cree que el monto o la comisión es incorrecto",
    "consulta_movimiento": "Pregunta por un movimiento, saldo o estado de un pago, sin reclamar",
    "otra_queja": "Otro reclamo sobre el banco (atención, sucursal, app)",
    "fuera_de_alcance": "Otro trámite: crédito, cupo, apertura de productos, etc.",
}

D1_INTENCION = Choice("¿Qué quiere el cliente de su banco?", INTENTS)

D2_PIDE_HUMANO = Noul(
    "¿El cliente pide explícitamente hablar con una persona?",
    true="Pide un agente o persona",
    false="No lo pide",
)

D3_SOSPECHA_ROBO = Noul(
    "¿El cliente dice o sugiere que le robaron la tarjeta o sus datos?",
    true="Menciona robo, clonación o uso por terceros",
    false="No lo menciona",
)

TURN_QUESTIONS: Mapping[str, Question] = {
    "intencion": D1_INTENCION,
    "pide_humano": D2_PIDE_HUMANO,
    "sospecha_robo": D3_SOSPECHA_ROBO,
}


def d4_comercio(candidates: Mapping[str, str]) -> Choice:
    """D4: ¿qué comercio describe el cliente?

    `candidates`: id opaco local -> "nombre (categoría)". Sin montos ni fechas:
    Jev no es confiable con números y no deben salir del perímetro.
    """
    options = dict(candidates)
    options["ninguno"] = "Ninguno de los anteriores"
    return Choice("¿Cuál de estos comercios describe el cliente?", options)
