"""The customer's language (Spanish or Portuguese), decided in code for fixed replies.

Fixed replies (session refusals, the output guard fallback) never reach the model, so the
language is detected deterministically from the customer's own words. Only markers that
belong to one language count; when nothing tips the balance, Spanish is the default.
"""

import re
from typing import Literal

Language = Literal["es", "pt"]
DEFAULT_LANGUAGE: Language = "es"

_PT_MARKERS = re.compile(
    r"[ãõç]|\b(você|voce|não|nao|quero|meu|minha|obrigad[oa]|oi|olá|cobrança|cartão"
    r"|duas|vezes|reconheço|atendente|estorno|pela|pelo)\b",
    re.IGNORECASE,
)
_ES_MARKERS = re.compile(
    r"[ñ¿¡]|\b(quiero|mi|usted|hola|cargo|cobro|reconozco|tarjeta|dos|veces|del|muéstrame)\b",
    re.IGNORECASE,
)


def detect_language(text: str | None) -> Language:
    """Portuguese when its markers outnumber Spanish ones; Spanish otherwise."""
    if not text:
        return DEFAULT_LANGUAGE
    pt, es = len(_PT_MARKERS.findall(text)), len(_ES_MARKERS.findall(text))
    return "pt" if pt > es else DEFAULT_LANGUAGE
