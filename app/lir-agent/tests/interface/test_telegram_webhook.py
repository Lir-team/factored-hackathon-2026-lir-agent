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
    "No reconozco este cargo.\nCargos que reporto: SPOTIFY, 179.0 MXN, 2026-03-14."
)
OWNER = f"case:{CASE_ID}"


class RecordingMessenger:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class Bot:
    """The app with the Telegram channel on, recording what the bot says."""

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
            )
        )
        self._next_update = 1

    def issue(self, token: str = "tok-1", language: str = "es") -> str:
        start = CaseStart(CASE_ID, "CLI-DEMO-001", FOLIO, language, SUMMARY)
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

    def texts(self) -> list[str]:
        return [text for _, text in self.messenger.sent]


def telegram_on(settings, **changes):
    """Settings with the Telegram channel configured (`model_copy` does not validate)."""
    return settings.model_copy(
        update={
            "telegram_bot_token": SecretStr(BOT_TOKEN),
            "telegram_webhook_secret": SecretStr(SECRET),
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


def test_start_links_the_chat_and_runs_the_first_turn(bot):
    response = bot.say(f"/start {bot.issue()}")

    assert response.status_code == 200
    session_id, (owner, customer) = next(iter(bot.conversations.sessions.items()))
    assert (owner, customer) == (OWNER, "CLI-DEMO-001")
    assert bot.conversations.policies[session_id] == (
        timedelta(days=7),
        "telegram_case_link",
    )
    assert bot.conversations.messages == [(OWNER, session_id, SUMMARY)]
    confirmation, first_turn = bot.texts()
    assert FOLIO in confirmation
    assert first_turn == f"echo: {SUMMARY}"
    assert {chat for chat, _ in bot.messenger.sent} == {CHAT}


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
    assert len(bot.conversations.sessions) == 1


def test_an_unknown_token_is_refused(bot):
    bot.say("/start not-a-token")

    [reply] = bot.texts()
    assert "expiró o ya fue usado" in reply
    assert bot.conversations.sessions == {}


def test_an_expired_token_is_refused(bot):
    start = CaseStart(CASE_ID, "CLI-DEMO-001", FOLIO, "es", SUMMARY)
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
    bot.say(f"/start {bot.issue()}")
    session_id = next(iter(bot.conversations.sessions))
    bot.messenger.sent.clear()

    bot.say("  Sí, fue ayer  ")

    assert bot.conversations.messages[-1] == (OWNER, session_id, "Sí, fue ayer")
    assert bot.messenger.sent == [(CHAT, "echo: Sí, fue ayer")]


def test_a_new_start_link_relinks_the_chat(bot):
    bot.say(f"/start {bot.issue('tok-1')}")
    bot.say(f"/start {bot.issue('tok-2')}")
    latest = list(bot.conversations.sessions)[-1]

    bot.say("hola")

    assert bot.conversations.messages[-1] == (OWNER, latest, "hola")


def test_a_lost_conversation_is_reported(bot):
    bot.say(f"/start {bot.issue()}")
    bot.conversations.sessions.clear()
    bot.messenger.sent.clear()

    bot.say("hola")

    [reply] = bot.texts()
    assert "expiró" in reply


def test_a_too_long_message_is_refused_politely(settings):
    bot = Bot(telegram_on(settings, max_message_chars=10))
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


def test_a_case_filed_on_the_web_links_on_telegram(settings):
    bot = Bot(telegram_on(settings, telegram_bot_username=BOT))
    accepted = bot.client.post("/v1/cases", json=case(), headers=headers())
    token = start_token(accepted.json()["telegram_start_url"])

    bot.say(f"/start {token}")

    assert bot.store.get_chat_link(CHAT) is not None
    assert FOLIO in bot.texts()[0]
    [(_, _, first_turn)] = bot.conversations.messages
    assert first_turn.startswith(case()["description"])
