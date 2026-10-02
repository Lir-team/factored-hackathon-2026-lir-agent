"""Decisions D1-D4 asked on every customer turn.

Question texts are in Spanish on purpose: they are part of the model input,
next to Spanish/Portuguese customer messages.
"""

from collections.abc import Mapping

from decision_layer.base import Choice, Noul, Question

INTENTS = {
    "cargo_no_reconocido": "No reconoce un cargo o compra en su cuenta o tarjeta",
    "cobro_indebido": "Reconoce el cargo pero cree que el monto o la comisión es incorrecto",
    "consulta_movimiento": "Pregunta por un movimiento, saldo o estado de un pago, sin reclamar",
    "otra_queja": "Otro reclamo sobre el banco (atención, sucursal, app)",
    "fuera_de_alcance": "Otro trámite: crédito, cupo, apertura de productos, etc.",
}

D1_INTENT = Choice("¿Qué quiere el cliente de su banco?", INTENTS)

D2_WANTS_HUMAN = Noul(
    "¿El cliente pide explícitamente hablar con una persona?",
    true="Pide un agente o persona",
    false="No lo pide",
)

D3_THEFT_SUSPECTED = Noul(
    "¿El cliente dice o sugiere que le robaron la tarjeta o sus datos?",
    true="Menciona robo, clonación o uso por terceros",
    false="No lo menciona",
)

TURN_QUESTIONS: Mapping[str, Question] = {
    "intencion": D1_INTENT,
    "pide_humano": D2_WANTS_HUMAN,
    "sospecha_robo": D3_THEFT_SUSPECTED,
}


def d4_merchant(candidates: Mapping[str, str]) -> Choice:
    """D4: which merchant is the customer describing?

    `candidates`: opaque local id -> "name (category)". No amounts or dates:
    Jev is unreliable with numbers, and they must not leave the perimeter.
    """
    options = dict(candidates)
    options["ninguno"] = "Ninguno de los anteriores"
    return Choice("¿Cuál de estos comercios describe el cliente?", options)
