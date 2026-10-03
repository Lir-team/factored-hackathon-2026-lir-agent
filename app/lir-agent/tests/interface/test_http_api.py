from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from lir_agent.interface.http import (
    CustomerNotFoundError,
    SessionNotFoundError,
    create_app,
)
from lir_agent.interface.http.gateway import StartedSession

IAP_HEADER = {
    "X-Goog-Authenticated-User-Email": "accounts.google.com:tester@example.com"
}
EXPIRES = datetime(2026, 10, 5, tzinfo=UTC)


class FakeGateway:
    def __init__(self) -> None:
        self.sessions: dict[str, tuple[str, str]] = {}
        self.messages: list[tuple[str, str, str]] = []

    async def start_session(self, operator: str, customer_id: str) -> StartedSession:
        if customer_id == "CLI-UNKNOWN":
            raise CustomerNotFoundError(customer_id)
        session_id = f"s{len(self.sessions) + 1}"
        self.sessions[session_id] = (operator, customer_id)
        return StartedSession(session_id=session_id, expires_at=EXPIRES)

    async def send(self, operator: str, session_id: str, text: str) -> str:
        owner = self.sessions.get(session_id, (None, None))[0]
        if owner != operator:
            raise SessionNotFoundError(session_id)
        self.messages.append((operator, session_id, text))
        return f"echo: {text}"


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def client(settings, gateway) -> TestClient:
    return TestClient(create_app(settings, gateway=gateway))  # type: ignore[arg-type]


def test_health_needs_no_identity(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_session_is_owned_by_the_iap_identity(client, gateway):
    response = client.post(
        "/v1/sessions", json={"customer_id": " cli-demo-001 "}, headers=IAP_HEADER
    )
    assert response.status_code == 201
    assert response.json() == {"session_id": "s1", "expires_at": EXPIRES.isoformat()}
    assert gateway.sessions["s1"] == ("tester@example.com", "CLI-DEMO-001")


def test_message_round_trip(client, gateway):
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    response = client.post(
        "/v1/sessions/s1/messages", json={"text": "  hola "}, headers=IAP_HEADER
    )
    assert response.json() == {"reply": "echo: hola"}
    assert gateway.messages == [("tester@example.com", "s1", "hola")]


def test_requests_without_identity_are_rejected(client, gateway):
    assert client.post("/v1/sessions", json={"customer_id": "CLI-1"}).status_code == 401
    assert gateway.sessions == {}


def test_local_operator_when_identity_is_not_required(settings, gateway):
    local = settings.model_copy(update={"require_identity": False})
    client = TestClient(create_app(local, gateway=gateway))  # type: ignore[arg-type]
    client.post("/v1/sessions", json={"customer_id": "CLI-1"})
    assert gateway.sessions["s1"][0] == local.local_operator


def test_another_operators_session_is_not_found(client):
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    other = {"X-Goog-Authenticated-User-Email": "accounts.google.com:other@example.com"}
    response = client.post(
        "/v1/sessions/s1/messages", json={"text": "hola"}, headers=other
    )
    assert response.status_code == 404


@pytest.mark.parametrize("customer_id", ["", "CLI 1", "x" * 65, "CLI-1; DROP"])
def test_invalid_customer_ids_are_rejected(client, gateway, customer_id):
    response = client.post(
        "/v1/sessions", json={"customer_id": customer_id}, headers=IAP_HEADER
    )
    assert response.status_code == 422
    assert gateway.sessions == {}


@pytest.mark.parametrize("text", ["", "x" * 2001])
def test_message_length_is_bounded(client, text):
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    response = client.post(
        "/v1/sessions/s1/messages", json={"text": text}, headers=IAP_HEADER
    )
    assert response.status_code == 422


def test_unknown_customer_is_not_found(client, gateway):
    response = client.post(
        "/v1/sessions", json={"customer_id": "cli-unknown"}, headers=IAP_HEADER
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Customer not found"}
