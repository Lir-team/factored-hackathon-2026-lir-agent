"""The demo bank sign-in: a short-lived customer JWT, only when configured."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lir_agent.application.ports import SigningError
from lir_agent.application.use_cases import IssueDemoSession
from lir_agent.container import build_container
from lir_agent.infrastructure.audit.in_memory import InMemoryAuditSink
from lir_agent.infrastructure.identity import IamJwtSigner
from lir_agent.interface.http import create_app
from tests.interface.test_http_api import FakeConversations

ISSUER = "lir-demo-idp@lir-agent.iam.gserviceaccount.com"
NOW = datetime(2026, 10, 5, 16, 0, tzinfo=UTC)


class FakeSigner:
    def __init__(self, fail=False):
        self.claims, self.fail = [], fail

    def sign(self, claims):
        if self.fail:
            raise SigningError("iam down")
        self.claims.append(dict(claims))
        return "signed." + claims["sub"]


def test_the_token_names_the_configured_customer_and_expires():
    signer, audit = FakeSigner(), InMemoryAuditSink()
    issue = IssueDemoSession(
        signer, audit, customer_id="CLI-DEMO-001", issuer=ISSUER, audience="lir-web",
        ttl=timedelta(minutes=60), clock=lambda: NOW,
    )

    session = issue.execute()

    assert session.token == "signed.CLI-DEMO-001"
    assert session.expires_at == NOW + timedelta(minutes=60)
    [claims] = signer.claims
    assert claims == {
        "iss": ISSUER, "sub": "CLI-DEMO-001", "aud": "lir-web",
        "iat": int(NOW.timestamp()), "exp": int((NOW + timedelta(hours=1)).timestamp()),
    }
    assert [e["event"] for e in audit.entries] == ["demo_sign_in"]


@pytest.fixture
def conversations() -> FakeConversations:
    return FakeConversations()


def configured(settings):
    return settings.model_copy(
        update={"demo_sign_in_customer_id": "CLI-DEMO-001", "demo_sign_in_issuer": ISSUER}
    )


def test_the_route_is_absent_unless_configured(settings, conversations):
    client = TestClient(create_app(settings, conversations=conversations))
    assert client.post("/v1/demo/sign-in").status_code == 404


def test_the_route_returns_a_fresh_token(settings, conversations):
    demo = configured(settings)
    container = build_container(demo, demo_signer=FakeSigner())
    client = TestClient(create_app(demo, conversations=conversations, container=container))

    response = client.post("/v1/demo/sign-in")

    assert response.status_code == 200
    assert response.json()["token"] == "signed.CLI-DEMO-001"
    assert datetime.fromisoformat(response.json()["expires_at"]) > datetime.now(UTC)


def test_a_signing_failure_is_a_retryable_503(settings, conversations):
    demo = configured(settings)
    container = build_container(demo, demo_signer=FakeSigner(fail=True))
    client = TestClient(create_app(demo, conversations=conversations, container=container))
    assert client.post("/v1/demo/sign-in").status_code == 503


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, response):
        self.response, self.calls = response, []

    def post(self, url, json, timeout):
        self.calls.append((url, json))
        return self.response


def test_the_iam_signer_sends_the_claims_as_the_payload():
    session = FakeSession(FakeResponse(200, {"signedJwt": "a.b.c"}))

    token = IamJwtSigner(ISSUER, session).sign({"sub": "CLI-DEMO-001"})

    assert token == "a.b.c"
    [(url, body)] = session.calls
    assert url.endswith(f"/serviceAccounts/{ISSUER}:signJwt")
    assert json.loads(body["payload"]) == {"sub": "CLI-DEMO-001"}


def test_an_iam_refusal_is_a_signing_error():
    with pytest.raises(SigningError):
        IamJwtSigner(ISSUER, FakeSession(FakeResponse(403, {}))).sign({"sub": "x"})
