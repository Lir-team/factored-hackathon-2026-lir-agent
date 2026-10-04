import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.interface.test_cases_api import CASE_ID, CUSTOMER, PAYLOAD, case
from tests.interface.test_telegram_webhook import (
    CHAT,
    FOLIO,
    OWNER,
    PUSHER,
    SUMMARY,
    Bot,
    envelope,
    telegram_on,
)


@pytest.fixture
def bot(settings) -> Bot:
    return Bot(telegram_on(settings))


def test_a_pushed_case_starts_its_conversation_and_runs_the_first_turn(bot):
    response = bot.push()

    assert response.status_code == 204
    session_id, (owner, customer) = next(iter(bot.conversations.sessions.items()))
    assert (owner, customer) == (OWNER, CUSTOMER)
    assert bot.conversations.policies[session_id] == (timedelta(days=7), "case_intake")
    assert bot.conversations.messages == [(OWNER, session_id, SUMMARY)]
    conversation = bot.store.get_conversation(CASE_ID)
    assert conversation is not None
    assert (conversation.folio, conversation.language) == (FOLIO, "es")
    assert (conversation.owner, conversation.session_id) == (OWNER, session_id)


def test_the_first_reply_is_queued_while_no_chat_is_linked(bot):
    bot.push()

    assert bot.messenger.sent == []
    assert bot.store.pop_replies(CASE_ID) == [f"echo: {SUMMARY}"]


def test_processing_is_audited_without_message_text(bot):
    bot.push()

    processed = next(e for e in bot.audit.entries if e["event"] == "case_processed")
    assert processed["case_id"] == CASE_ID
    assert processed["session_id"] == "s1"
    assert "case_reply_queued" in bot.audit.events()
    assert "Spotify" not in json.dumps(bot.audit.entries)


def test_a_repeated_delivery_is_acknowledged_without_new_work(bot):
    assert bot.push().status_code == 204
    assert bot.push(envelope(PAYLOAD, message_id="m-2")).status_code == 204

    assert len(bot.conversations.sessions) == 1
    assert len(bot.conversations.messages) == 1
    assert bot.store.pop_replies(CASE_ID) == [f"echo: {SUMMARY}"]


@pytest.mark.parametrize("token", [None, "forged"], ids=["missing", "invalid"])
def test_an_unverified_push_is_unauthorized(bot, token):
    response = bot.push(token=token)

    assert response.status_code == 401
    assert bot.conversations.sessions == {}


def test_a_token_from_another_service_account_is_unauthorized(settings):
    bot = Bot(telegram_on(settings, pubsub_push_service_account="someone@else.example"))

    assert bot.push().status_code == 401


def test_the_expected_service_account_is_accepted(settings):
    bot = Bot(telegram_on(settings, pubsub_push_service_account=PUSHER))

    assert bot.push().status_code == 204


def test_verification_can_be_turned_off_for_the_emulator(settings):
    bot = Bot(
        telegram_on(settings, pubsub_verify_token=False, pubsub_push_audience=None)
    )

    assert bot.push(token=None).status_code == 204


def test_the_route_is_absent_without_an_audience(settings):
    bot = Bot(telegram_on(settings, pubsub_push_audience=None))

    assert bot.push().status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"message": {"data": "%%not base64%%", "messageId": "m-1"}},
        {"message": {"data": "bm90IGpzb24=", "messageId": "m-1"}},  # "not json"
        envelope({"case_id": CASE_ID}),
        envelope(case(category="lottery")),
        {"no": "message"},
    ],
    ids=["base64", "json", "incomplete", "schema", "envelope"],
)
def test_a_malformed_message_is_acknowledged_and_rejected(bot, body):
    response = bot.push(body)

    assert response.status_code == 204
    assert "case_rejected" in bot.audit.events()
    assert bot.conversations.sessions == {}


def test_an_unknown_customer_is_acknowledged_and_rejected(bot):
    payload = case()
    payload["customer"]["customer_id"] = "CLI-UNKNOWN"

    response = bot.push(envelope(payload))

    assert response.status_code == 204
    assert "case_rejected" in bot.audit.events()


def test_a_reply_that_failed_to_send_is_sent_once_on_redelivery(bot):
    bot.say(f"/start {bot.issue()}")
    bot.messenger.sent.clear()
    bot.messenger.failures = 1
    bot.client = TestClient(bot.client.app, raise_server_exceptions=False)

    assert bot.push().status_code == 500
    assert bot.messenger.sent == []

    assert bot.push().status_code == 204
    assert bot.push(envelope(PAYLOAD, message_id="m-2")).status_code == 204
    assert bot.messenger.sent == [(CHAT, f"echo: {SUMMARY}")]
    assert len(bot.conversations.messages) == 1


def test_an_agent_failure_is_retried(bot):
    async def broken(*args, **kwargs):
        raise RuntimeError("model down")

    bot.conversations.send = broken  # type: ignore[method-assign]
    bot.client = TestClient(bot.client.app, raise_server_exceptions=False)

    assert bot.push().status_code == 500
    assert bot.store.get_conversation(CASE_ID) is None

    del bot.conversations.send  # back to the class method
    assert bot.push().status_code == 204
    assert bot.store.get_conversation(CASE_ID) is not None
