from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lir_agent.application.ports import (
    ConversationNotFoundError,
    CustomerNotFoundError,
    StartedConversation,
)
from lir_agent.interface.http import create_app

IAP_HEADER = {
    "X-Goog-Authenticated-User-Email": "accounts.google.com:tester@example.com"
}
EXPIRES = datetime(2026, 10, 5, tzinfo=UTC)


class FakeConversations:
    def __init__(self) -> None:
        self.sessions: dict[str, tuple[str, str]] = {}
        self.policies: dict[str, tuple[timedelta, str]] = {}
        self.messages: list[tuple[str, str, str]] = []

    async def start(
        self, owner: str, customer_id: str, *, ttl: timedelta, auth_method: str
    ) -> StartedConversation:
        if customer_id == "CLI-UNKNOWN":
            raise CustomerNotFoundError(customer_id)
        session_id = f"s{len(self.sessions) + 1}"
        self.sessions[session_id] = (owner, customer_id)
        self.policies[session_id] = (ttl, auth_method)
        return StartedConversation(session_id=session_id, expires_at=EXPIRES)

    async def send(self, owner: str, session_id: str, text: str) -> str:
        if self.sessions.get(session_id, (None, None))[0] != owner:
            raise ConversationNotFoundError(session_id)
        self.messages.append((owner, session_id, text))
        return f"echo: {text}"


@pytest.fixture
def conversations() -> FakeConversations:
    return FakeConversations()


@pytest.fixture
def client(settings, conversations) -> TestClient:
    return TestClient(create_app(settings, conversations=conversations))


def test_health_needs_no_identity(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_session_is_owned_by_the_iap_identity(client, conversations):
    response = client.post(
        "/v1/sessions", json={"customer_id": " cli-demo-001 "}, headers=IAP_HEADER
    )
    assert response.status_code == 201
    assert response.json() == {"session_id": "s1", "expires_at": EXPIRES.isoformat()}
    assert conversations.sessions["s1"] == ("tester@example.com", "CLI-DEMO-001")


def test_session_policy_comes_from_settings(client, conversations, settings):
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    assert conversations.policies["s1"] == (
        timedelta(minutes=settings.session_ttl_minutes),
        settings.http_auth_method,
    )


def test_message_round_trip(client, conversations):
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    response = client.post(
        "/v1/sessions/s1/messages", json={"text": "  hola "}, headers=IAP_HEADER
    )
    assert response.json() == {"reply": "echo: hola"}
    assert conversations.messages == [("tester@example.com", "s1", "hola")]


def test_requests_without_identity_are_rejected(client, conversations):
    assert client.post("/v1/sessions", json={"customer_id": "CLI-1"}).status_code == 401
    assert conversations.sessions == {}


def test_local_operator_when_identity_is_not_required(settings, conversations):
    local = settings.model_copy(update={"require_identity": False})
    client = TestClient(create_app(local, conversations=conversations))
    client.post("/v1/sessions", json={"customer_id": "CLI-1"})
    assert conversations.sessions["s1"][0] == local.local_operator


def test_another_operators_session_is_not_found(client):
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    other = {"X-Goog-Authenticated-User-Email": "accounts.google.com:other@example.com"}
    response = client.post(
        "/v1/sessions/s1/messages", json={"text": "hola"}, headers=other
    )
    assert response.status_code == 404


@pytest.mark.parametrize("customer_id", ["", "CLI 1", "x" * 65, "CLI-1; DROP"])
def test_invalid_customer_ids_are_rejected(client, conversations, customer_id):
    response = client.post(
        "/v1/sessions", json={"customer_id": customer_id}, headers=IAP_HEADER
    )
    assert response.status_code == 422
    assert conversations.sessions == {}


@pytest.mark.parametrize("text", ["", "x" * 2001])
def test_message_length_is_bounded(client, text):
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    response = client.post(
        "/v1/sessions/s1/messages", json={"text": text}, headers=IAP_HEADER
    )
    assert response.status_code == 422


def test_unknown_customer_is_not_found(client, conversations):
    response = client.post(
        "/v1/sessions", json={"customer_id": "cli-unknown"}, headers=IAP_HEADER
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Customer not found"}
