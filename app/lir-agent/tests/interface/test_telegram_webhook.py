import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from lir_agent.application.ports import (
    FileNotDownloadedError,
    MessageNotSentError,
    TranscriptionError,
)
from lir_agent.container import build_container
from lir_agent.domain.case_intake import CaseStart
from lir_agent.domain.language import Language
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
    "Cargos que reporto: T1."  # the reported charge, by session reference only
)
OWNER = f"case:{CASE_ID}"
AUDIENCE = "https://lir-agent.example/pubsub/push"
PUSHER = "pubsub-push@lir.iam.gserviceaccount.com"
GOOD_TOKEN = "good-oidc-token"


class RecordingMessenger:
    """Records what is sent; the next `failures` sends raise as Telegram being down would."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.buttons: list[tuple[int, list]] = []
        self.answers: list[tuple[str, str, bool]] = []
        self.failures = 0

    async def send(self, chat_id: int, text: str) -> None:
        if self.failures:
            self.failures -= 1
            raise MessageNotSentError("Telegram is down")
        self.sent.append((chat_id, text))

    async def send_buttons(self, chat_id: int, text: str, rows: list) -> None:
        await self.send(chat_id, text)
        self.buttons.append((chat_id, rows))

    async def answer_callback(self, callback_id: str, text: str, alert: bool = False) -> None:
        self.answers.append((callback_id, text, alert))


class FakeChatFiles:
    """Voice notes Telegram holds, by file id; records every download."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.downloads: list[str] = []

    async def download_file(self, file_id: str) -> bytes:
        self.downloads.append(file_id)
        if file_id not in self.files:
            raise FileNotDownloadedError("status 400")
        return self.files[file_id]


class FakeSpeechToText:
    """Transcripts by audio; unknown audio raises as a speech service failure would."""

    def __init__(self) -> None:
        self.transcripts: dict[bytes, str] = {}
        self.calls: list[tuple[bytes, Language]] = []

    async def transcribe(self, audio: bytes, language: Language) -> str:
        self.calls.append((audio, language))
        if audio not in self.transcripts:
            raise TranscriptionError("ServiceUnavailable")
        return self.transcripts[audio]


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

    def __init__(self, settings, speech: bool = True) -> None:
        self.store = InMemoryCaseStore()
        self.audit = InMemoryAuditSink()
        self.messenger = RecordingMessenger()
        self.conversations = FakeConversations()
        self.files = FakeChatFiles()
        self.speech = FakeSpeechToText()
        self.container = container = build_container(
            settings,
            audit=self.audit,
            case_inbox=RecordingInbox(),
            case_store=self.store,
            messenger=self.messenger,
            chat_files=self.files,
            speech_to_text=self.speech if speech else None,
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

    def voice(self, file_id: str = "voice-1", duration: int = 5):
        """A voice note, as Telegram sends it (OGG/Opus, `duration` in seconds)."""
        voice = {"file_id": file_id, "file_unique_id": "u1", "duration": duration}
        body = {
            "update_id": self._next_update,
            "message": {
                "message_id": 1,
                "chat": {"id": CHAT, "type": "private"},
                "voice": voice,
            },
        }
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

    def press(self, data: str, chat_id: int = CHAT, callback_id: str = "cb-1"):
        """A press on a button under a bot message, as Telegram sends it."""
        body = {
            "update_id": self._next_update,
            "callback_query": {
                "id": callback_id,
                "data": data,
                "message": {"message_id": 9, "chat": {"id": chat_id, "type": "private"}},
            },
        }
        self._next_update += 1
        return self.post(body)

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


def test_replies_that_failed_at_start_are_sent_with_the_next_answer(bot):
    bot.push()
    bot.messenger.failures = 2  # the confirmation and the queued reply

    assert bot.say(f"/start {bot.issue()}").status_code == 200
    assert bot.store.get_chat_link(CHAT) is not None

    bot.say("hola")

    assert bot.texts() == [f"echo: {SUMMARY}", "echo: hola"]


def test_an_answer_that_failed_is_sent_with_the_next_one(bot):
    bot.push()
    bot.say(f"/start {bot.issue()}")
    bot.messenger.sent.clear()
    bot.messenger.failures = 1

    assert bot.say("hola").status_code == 200
    bot.say("sigues ahí?")

    assert bot.texts() == ["echo: hola", "echo: sigues ahí?"]
    assert len(bot.conversations.messages) == 3  # the case summary, then one per message


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


def test_an_update_that_failed_is_answered_when_telegram_retries_it(bot):
    bot.push()
    bot.say(f"/start {bot.issue()}")
    bot.messenger.sent.clear()
    body = {
        "update_id": 99,
        "message": {
            "message_id": 1,
            "chat": {"id": CHAT, "type": "private"},
            "text": "hola",
        },
    }

    async def broken(*args, **kwargs):
        raise RuntimeError("model down")

    bot.conversations.converse = broken  # type: ignore[method-assign]
    bot.client = TestClient(bot.client.app, raise_server_exceptions=False)
    assert bot.post(body).status_code == 500

    del bot.conversations.converse  # back to the class method
    assert bot.post(body).status_code == 200
    assert bot.messenger.sent == [(CHAT, "echo: hola")]


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


# ---- voice notes ---------------------------------------------------------------------------
def linked(bot: Bot, language: str = "es") -> Bot:
    """`bot` with the case worked and the chat linked, nothing sent yet."""
    bot.push()
    bot.say(f"/start {bot.issue(language=language)}")
    bot.messenger.sent.clear()
    return bot


def test_a_voice_note_is_answered_as_the_text_it_says(bot):
    linked(bot)
    session_id = next(iter(bot.conversations.sessions))
    bot.files.files["voice-1"] = b"ogg-1"
    bot.speech.transcripts[b"ogg-1"] = " Sí, fue ayer "

    response = bot.voice("voice-1")

    assert response.status_code == 200
    assert bot.speech.calls == [(b"ogg-1", "es")]
    assert bot.conversations.messages[-1] == (OWNER, session_id, "Sí, fue ayer")
    assert bot.messenger.sent == [(CHAT, "echo: Sí, fue ayer")]


def test_a_voice_note_is_transcribed_in_the_case_language(bot):
    linked(bot, language="pt")
    bot.files.files["voice-1"] = b"ogg-1"
    bot.speech.transcripts[b"ogg-1"] = "Sim"

    bot.voice("voice-1")

    assert bot.speech.calls == [(b"ogg-1", "pt")]


def test_a_spoken_start_command_is_not_a_link(bot):
    linked(bot)
    bot.files.files["voice-1"] = b"ogg-1"
    bot.speech.transcripts[b"ogg-1"] = "/start tok-2"

    bot.voice("voice-1")

    assert bot.conversations.messages[-1][2] == "/start tok-2"


def test_voice_notes_are_declined_politely_when_speech_is_off(settings):
    bot = linked(Bot(telegram_on(settings), speech=False))

    assert bot.voice().status_code == 200

    [reply] = bot.texts()
    assert "texto" in reply
    assert bot.files.downloads == []


def test_a_too_long_voice_note_is_refused_before_download(bot):
    linked(bot)

    bot.voice(duration=61)

    [reply] = bot.texts()
    assert "60" in reply
    assert bot.files.downloads == []


@pytest.mark.parametrize(
    "transcript", [None, "", "   "], ids=["service-failed", "empty", "blank"]
)
def test_a_voice_note_not_understood_is_answered_and_acknowledged(bot, transcript):
    linked(bot)
    bot.files.files["voice-1"] = b"ogg-1"
    if transcript is not None:
        bot.speech.transcripts[b"ogg-1"] = transcript
    sent = len(bot.conversations.messages)

    response = bot.voice("voice-1")

    assert response.status_code == 200
    [reply] = bot.texts()
    assert "entender" in reply
    assert len(bot.conversations.messages) == sent


def test_a_voice_note_that_cannot_be_downloaded_is_answered(bot):
    linked(bot)

    assert bot.voice("missing").status_code == 200

    [reply] = bot.texts()
    assert "entender" in reply
    assert bot.speech.calls == []


def test_a_too_long_transcript_is_refused_politely(settings):
    bot = linked(Bot(telegram_on(settings, max_message_chars=10)))
    bot.files.files["voice-1"] = b"ogg-1"
    bot.speech.transcripts[b"ogg-1"] = "x" * 11
    sent = len(bot.conversations.messages)

    bot.voice("voice-1")

    [reply] = bot.texts()
    assert "10" in reply
    assert len(bot.conversations.messages) == sent


def test_a_voice_note_from_an_unlinked_chat_is_asked_to_use_the_form_link(bot):
    bot.voice()

    [reply] = bot.texts()
    assert "enlace" in reply
    assert bot.files.downloads == []
