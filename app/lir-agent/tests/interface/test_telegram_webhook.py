import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from lir_agent.container import build_container
from lir_agent.domain.case_intake import CaseStart
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.infrastructure.case_store import InMemoryCaseStore
from lir_agent.interface.http import create_app
from tests.interface.test_cases_api import (
    BOT,
    CASE_ID,
    PAYLOAD,
    RecordingInbox,
    case,
    headers,
    start_token,
)
from tests.interface.test_http_api import FakeConversations

SECRET = "webhook-secret"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
BOT_TOKEN = "123456:bot-token"
CHAT = 7001
FOLIO = "LB-2026-6F1C2D"
SUMMARY = (
    "No reconozco este cargo de Spotify en mi tarjeta.\n"
    "Cargos que reporto: SPOTIFY P1A2B3, 179.0 MXN, 2026-03-14."
)
OWNER = f"case:{CASE_ID}"
AUDIENCE = "https://lir-agent.example/pubsub/push"
PUSHER = "pubsub-push@lir.iam.gserviceaccount.com"
GOOD_TOKEN = "good-oidc-token"


class RecordingMessenger:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def fake_verifier(token: str) -> dict:
    """Stands in for Google's OIDC check: only GOOD_TOKEN is valid, issued to PUSHER."""
    if token != GOOD_TOKEN:
        raise ValueError("invalid token")
    return {"aud": AUDIENCE, "email": PUSHER, "email_verified": True}


def envelope(payload: object, message_id: str = "m-1") -> dict:
    """A Pub/Sub push body carrying `payload` as base64 JSON."""
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {
        "message": {"data": data, "attributes": {}, "messageId": message_id},
        "subscription": "projects/lir/subscriptions/lir-cases-push",
    }


class Bot:
    """The app with the Telegram channel and the Pub/Sub push on, recording what the bot says."""

    def __init__(self, settings) -> None:
        self.store = InMemoryCaseStore()
        self.audit = InMemoryAuditSink()
        self.messenger = RecordingMessenger()
        self.conversations = FakeConversations()
        container = build_container(
            settings,
            audit=self.audit,
            case_inbox=RecordingInbox(),
            case_store=self.store,
        )
        self.client = TestClient(
            create_app(
                settings,
                conversations=self.conversations,
                container=container,
                messenger=self.messenger,
                push_token_verifier=fake_verifier,
            )
        )
        self._next_update = 1

    def issue(self, token: str = "tok-1", language: str = "es") -> str:
        start = CaseStart(CASE_ID, FOLIO, language)
        self.store.add_start_token(
            token, start, datetime.now(UTC) + timedelta(minutes=5)
        )
        return token

    def say(self, text: str | None, chat_type: str = "private"):
        message: dict = {"message_id": 1, "chat": {"id": CHAT, "type": chat_type}}
        if text is not None:
            message["text"] = text
        body = {"update_id": self._next_update, "message": message}
        self._next_update += 1
        return self.post(body)

    def post(self, body: dict, secret: str | None = SECRET):
        request_headers = {} if secret is None else {SECRET_HEADER: secret}
        return self.client.post(
            "/channels/telegram", json=body, headers=request_headers
        )

    def push(self, body: dict | None = None, token: str | None = GOOD_TOKEN):
        """Deliver a case as Pub/Sub would (the default case when `body` is None)."""
        request_headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        return self.client.post(
            "/pubsub/push",
            json=envelope(PAYLOAD) if body is None else body,
            headers=request_headers,
        )

    def texts(self) -> list[str]:
        return [text for _, text in self.messenger.sent]


def telegram_on(settings, **changes):
    """Settings with Telegram and the push route configured (`model_copy` does not validate)."""
    return settings.model_copy(
        update={
            "telegram_bot_token": SecretStr(BOT_TOKEN),
            "telegram_webhook_secret": SecretStr(SECRET),
            "pubsub_push_audience": AUDIENCE,
            **changes,
        }
    )


@pytest.fixture
def bot(settings) -> Bot:
    return Bot(telegram_on(settings))


@pytest.mark.parametrize("secret", [None, "wrong"], ids=["missing", "wrong"])
def test_a_wrong_secret_is_unauthorized(bot, secret):
    response = bot.post({"update_id": 1}, secret=secret)

    assert response.status_code == 401
    assert bot.messenger.sent == []


def test_the_route_is_absent_when_the_channel_is_not_configured(settings):
    response = Bot(settings).post({"update_id": 1})

    assert response.status_code == 404


def test_start_links_the_chat_without_starting_a_conversation(bot):
    response = bot.say(f"/start {bot.issue()}")

    assert response.status_code == 200
    assert bot.conversations.sessions == {}
    assert bot.conversations.messages == []
    [confirmation] = bot.texts()
    assert FOLIO in confirmation
    assert bot.messenger.sent[0][0] == CHAT


def test_start_delivers_the_replies_queued_by_the_case(bot):
    bot.push()
    assert bot.messenger.sent == []

    bot.say(f"/start {bot.issue()}")

    confirmation, first_turn = bot.texts()
    assert FOLIO in confirmation
    assert first_turn == f"echo: {SUMMARY}"
    assert len(bot.conversations.sessions) == 1
    assert bot.store.pop_replies(CASE_ID) == []


def test_the_case_reply_is_sent_at_once_to_a_linked_chat(bot):
    bot.say(f"/start {bot.issue()}")
    bot.messenger.sent.clear()

    bot.push()

    assert bot.messenger.sent == [(CHAT, f"echo: {SUMMARY}")]
    assert "case_reply_sent" in bot.audit.events()


def test_linking_is_audited_without_the_token(bot):
    token = bot.issue()
    bot.say(f"/start {token}")

    entry = next(e for e in bot.audit.entries if e["event"] == "telegram_linked")
    assert entry["case_id"] == CASE_ID
    assert token not in json.dumps(bot.audit.entries)


def test_a_start_token_works_once(bot):
    token = bot.issue()
    bot.say(f"/start {token}")
    bot.messenger.sent.clear()

    bot.say(f"/start {token}")

    [reply] = bot.texts()
    assert "expiró o ya fue usado" in reply


def test_an_unknown_token_is_refused(bot):
    bot.say("/start not-a-token")

    [reply] = bot.texts()
    assert "expiró o ya fue usado" in reply
    assert bot.store.get_chat_link(CHAT) is None


def test_an_expired_token_is_refused(bot):
    start = CaseStart(CASE_ID, FOLIO, "es")
    bot.store.add_start_token("old", start, datetime.now(UTC) - timedelta(seconds=1))

    bot.say("/start old")

    assert "expiró o ya fue usado" in bot.texts()[0]


def test_a_portuguese_case_is_answered_in_portuguese(bot):
    bot.say(f"/start {bot.issue(language='pt')}")

    assert "Recebemos" in bot.texts()[0]


@pytest.mark.parametrize("text", ["hola", "/start"])
def test_an_unlinked_chat_is_asked_to_use_the_form_link(bot, text):
    bot.say(text)

    [reply] = bot.texts()
    assert "enlace" in reply
    assert bot.conversations.messages == []


def test_a_linked_chat_talks_to_its_case_conversation(bot):
    bot.push()
    bot.say(f"/start {bot.issue()}")
    session_id = next(iter(bot.conversations.sessions))
    bot.messenger.sent.clear()

    bot.say("  Sí, fue ayer  ")

    assert bot.conversations.messages[-1] == (OWNER, session_id, "Sí, fue ayer")
    assert bot.messenger.sent == [(CHAT, "echo: Sí, fue ayer")]


def test_a_chat_linked_before_the_case_is_worked_is_asked_to_wait(bot):
    bot.say(f"/start {bot.issue()}")
    bot.messenger.sent.clear()

    bot.say("hola")

    [reply] = bot.texts()
    assert FOLIO in reply
    assert bot.conversations.messages == []


def test_a_lost_conversation_is_reported(bot):
    bot.push()
    bot.say(f"/start {bot.issue()}")
    bot.conversations.sessions.clear()
    bot.messenger.sent.clear()

    bot.say("hola")

    [reply] = bot.texts()
    assert "expiró" in reply


def test_a_too_long_message_is_refused_politely(settings):
    bot = Bot(telegram_on(settings, max_message_chars=10))
    bot.push()
    bot.say(f"/start {bot.issue()}")
    sent = len(bot.conversations.messages)
    bot.messenger.sent.clear()

    bot.say("x" * 11)

    [reply] = bot.texts()
    assert "10" in reply
    assert len(bot.conversations.messages) == sent


def test_a_repeated_update_is_processed_once(bot):
    body = {
        "update_id": 99,
        "message": {"message_id": 1, "chat": {"id": CHAT, "type": "private"}},
    }
    body["message"]["text"] = "hola"

    assert bot.post(body).status_code == 200
    assert bot.post(body).status_code == 200

    assert len(bot.messenger.sent) == 1


@pytest.mark.parametrize(
    "update",
    [
        {"text": None},
        {"text": "hola", "chat_type": "group"},
    ],
    ids=["no-text", "group"],
)
def test_updates_without_private_text_are_ignored(bot, update):
    response = bot.say(update["text"], update.get("chat_type", "private"))

    assert response.status_code == 200
    assert bot.messenger.sent == []


@pytest.mark.parametrize(
    "body",
    [
        {"update_id": 5, "edited_message": {"chat": {"id": CHAT, "type": "private"}}},
        {"update_id": 6, "callback_query": {"id": "1"}},
        {"not": "an update"},
    ],
    ids=["edited", "callback", "unreadable"],
)
def test_other_updates_are_acknowledged_and_ignored(bot, body):
    response = bot.post(body)

    assert response.status_code == 200
    assert bot.messenger.sent == []


def test_a_case_filed_on_the_web_is_answered_on_telegram(settings):
    bot = Bot(telegram_on(settings, telegram_bot_username=BOT))
    accepted = bot.client.post("/v1/cases", json=case(), headers=headers())
    token = start_token(accepted.json()["telegram_start_url"])
    bot.push()

    bot.say(f"/start {token}")

    assert bot.store.get_chat_link(CHAT) is not None
    confirmation, first_turn = bot.texts()
    assert FOLIO in confirmation
    assert first_turn.startswith(f"echo: {case()['description']}")
