"""Telegram channel rules: chat links, the `/start` command and the bot's fixed replies.

A chat is linked to one case by a single-use start token; from then on its messages go to
that case's conversation, and the agent's replies about the case come back to it. Fixed replies never reach the model, so they live here in both
languages the agent speaks.
"""

from dataclasses import dataclass

from lir_agent.domain.language import DEFAULT_LANGUAGE, Language

# Telegram's Bot API limit for one text message.
MESSAGE_LIMIT = 4096

_START = "/start"

_MESSAGES: dict[str, dict[Language, str]] = {
    "linked": {
        "es": "Recibimos tu caso {folio}. Si necesito algo más, te escribo por aquí.",
        "pt": "Recebemos seu caso {folio}. Se eu precisar de mais alguma coisa, escrevo por aqui.",
    },
    "link_invalid": {
        "es": "Este enlace expiró o ya fue usado. Envía el formulario de nuevo o sigue en el chat donde abriste tu caso.",
        "pt": "Este link expirou ou já foi usado. Envie o formulário novamente ou continue no chat onde você abriu seu caso.",
    },
    "use_form": {
        "es": "Para empezar, usa el enlace que recibiste al enviar el formulario.",
        "pt": "Para começar, use o link que você recebeu ao enviar o formulário.",
    },
    "processing": {
        "es": "Todavía estoy revisando tu caso {folio}. Te escribo por aquí en cuanto pueda.",
        "pt": "Ainda estou analisando seu caso {folio}. Escrevo por aqui assim que puder.",
    },
    "conversation_expired": {
        "es": "Esta conversación expiró. Envía un nuevo reporte desde el formulario.",
        "pt": "Esta conversa expirou. Envie um novo relato pelo formulário.",
    },
    "too_long": {
        "es": "Tu mensaje es muy largo. Envíalo en partes de hasta {limit} caracteres.",
        "pt": "Sua mensagem é muito longa. Envie em partes de até {limit} caracteres.",
    },
    "voice_off": {
        "es": "Por ahora no puedo escuchar notas de voz. Escríbeme tu mensaje como texto, por favor.",
        "pt": "Por enquanto não consigo ouvir mensagens de voz. Escreva sua mensagem em texto, por favor.",
    },
    "voice_too_long": {
        "es": "Tu nota de voz es muy larga. Envía notas de hasta {limit} segundos o escríbeme como texto.",
        "pt": "Sua mensagem de voz é muito longa. Envie mensagens de até {limit} segundos ou escreva em texto.",
    },
    "voice_not_understood": {
        "es": "No pude entender tu nota de voz. ¿Puedes enviarla de nuevo o escribirme como texto?",
        "pt": "Não consegui entender sua mensagem de voz. Pode enviá-la de novo ou escrever em texto?",
    },
}


@dataclass(frozen=True)
class ChatLink:
    """A Telegram chat bound to a case (the latest start link wins).

    The case's conversation is looked up by `case_id`: it may start after the chat links.
    """

    case_id: str
    folio: str
    language: Language


def case_owner(case_id: str) -> str:
    """Owner of a case's conversation: only that case's chat can continue it."""
    return f"case:{case_id}"


def start_payload(text: str) -> str | None:
    """The token of a `/start <token>` command, `""` for a bare `/start`, else None."""
    command, _, payload = text.strip().partition(" ")
    return payload.strip() if command == _START else None


def bot_language(language: str) -> Language:
    """The reply language for a case language: Portuguese, else Spanish (`en` included)."""
    return "pt" if language == "pt" else DEFAULT_LANGUAGE


def bot_message(key: str, language: Language, **values: object) -> str:
    """A fixed bot reply in `language`."""
    return _MESSAGES[key][language].format(**values)


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Cut `text` into parts Telegram accepts (at most `limit` characters each)."""
    return [text[i : i + limit] for i in range(0, len(text), limit)]


# ---- approval buttons (human in the loop) ------------------------------------------------
_APPROVAL_CALLBACK = "apr"


@dataclass(frozen=True)
class InlineButton:
    """A button under a bot message: it sends `callback_data` back, or opens `url`."""

    text: str
    callback_data: str | None = None
    url: str | None = None


def approval_callback(approval_id: str, approve: bool) -> str:
    """The callback data of an approval button (Telegram allows 64 bytes)."""
    return f"{_APPROVAL_CALLBACK}:{approval_id}:{'a' if approve else 'r'}"


def parse_approval_callback(data: str | None) -> tuple[str, bool] | None:
    """The approval id and the decision of a button press, or None for other data."""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != _APPROVAL_CALLBACK or parts[2] not in ("a", "r"):
        return None
    return parts[1], parts[2] == "a"
