from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lir_agent.application.ports import (
    ConversationNotFoundError,
    CustomerNotFoundError,
    StartedConversation,
    Turn,
    TurnTrace,
)
from lir_agent.domain.case_intake import CaseReport
from lir_agent.interface.http import create_app

IAP_HEADER = {
    "X-Goog-Authenticated-User-Email": "accounts.google.com:tester@example.com"
}
EXPIRES = datetime(2026, 10, 5, tzinfo=UTC)
TRACE = TurnTrace(
    decision_model="keywords-v1",
    decision_fallback=None,
    decisions={"intencion": {"value": "cargo_no_reconocido", "probability": 0.9}},
    turn_lane="proceed",
    turn_rule="T9_in_scope",
    case_lane="dispute",
    case_rule="C2_duplicate",
    policy_version="0.2.0-synthetic",
    tools=["find_candidate_transactions", "get_transaction_evidence"],
    handoff_id=None,
    llm_model="openai/gpt-4o",
    latency_ms=1234.5,
    input_tokens=900,
    output_tokens=120,
    cost_usd=0.0035,
)


class FakeConversations:
    def __init__(self) -> None:
        self.sessions: dict[str, tuple[str, str]] = {}
        self.policies: dict[str, tuple[timedelta, str]] = {}
        self.reports: dict[str, CaseReport | None] = {}
        self.next_approvals: tuple[str, ...] = ()
        self.max_ttls: dict[str, timedelta | None] = {}
        self.messages: list[tuple[str, str, str]] = []

    async def start(
        self,
        owner: str,
        customer_id: str,
        *,
        ttl: timedelta,
        auth_method: str,
        transaction_ids: Sequence[str] = (),
        case_report: CaseReport | None = None,
        max_ttl: timedelta | None = None,
    ) -> StartedConversation:
        if customer_id == "CLI-UNKNOWN":
            raise CustomerNotFoundError(customer_id)
        session_id = f"s{len(self.sessions) + 1}"
        self.sessions[session_id] = (owner, customer_id)
        self.policies[session_id] = (ttl, auth_method)
        self.max_ttls[session_id] = max_ttl
        self.reports[session_id] = case_report
        refs = tuple(f"T{i}" for i, _ in enumerate(transaction_ids, start=1))
        return StartedConversation(
            session_id=session_id, expires_at=EXPIRES, transaction_refs=refs
        )

    async def send(self, owner: str, session_id: str, text: str) -> str:
        return (await self.converse(owner, session_id, text)).reply

    async def converse(self, owner: str, session_id: str, text: str) -> Turn:
        if self.sessions.get(session_id, (None, None))[0] != owner:
            raise ConversationNotFoundError(session_id)
        self.messages.append((owner, session_id, text))
        return Turn(reply=f"echo: {text}", trace=TRACE, approvals=self.next_approvals)


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
    assert response.json() == {"reply": "echo: hola", "trace": None, "approvals": []}
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
    assert response.json()["detail"].startswith("Customer not found: expected")


def test_trace_is_hidden_by_default(client):
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    response = client.post(
        "/v1/sessions/s1/messages", json={"text": "hola"}, headers=IAP_HEADER
    )
    assert response.json() == {"reply": "echo: hola", "trace": None, "approvals": []}


def test_trace_shows_how_the_turn_was_decided(settings, conversations):
    traced = settings.model_copy(update={"expose_trace": True})
    client = TestClient(create_app(traced, conversations=conversations))
    client.post("/v1/sessions", json={"customer_id": "CLI-1"}, headers=IAP_HEADER)
    trace = client.post(
        "/v1/sessions/s1/messages", json={"text": "hola"}, headers=IAP_HEADER
    ).json()["trace"]
    assert trace["decision_model"] == "keywords-v1"
    assert trace["case_rule"] == "C2_duplicate"
    assert trace["decisions"]["intencion"]["probability"] == 0.9
    assert trace["cost_usd"] == 0.0035


def test_handoff_report_is_markdown_and_its_reading_is_audited(settings, conversations):
    from lir_agent.container import build_container
    from lir_agent.infrastructure.audit import InMemoryAuditSink
    from tests.application.test_handoff_report import packet

    audit = InMemoryAuditSink()
    container = build_container(settings, audit=audit)
    container.cases.submit_handoff(packet())
    client = TestClient(
        create_app(settings, conversations=conversations, container=container)
    )
    response = client.get("/v1/handoffs/HND-ABC/report.md", headers=IAP_HEADER)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "# Reporte de derivación HND-ABC" in response.text
    [entry] = [e for e in audit.entries if e["event"] == "handoff_report_viewed"]
    assert entry["operator"] == "tester@example.com"


def test_handoff_report_needs_identity_and_an_existing_handoff(client):
    assert client.get("/v1/handoffs/HND-ABC/report.md").status_code == 401
    missing = client.get("/v1/handoffs/HND-NOPE/report.md", headers=IAP_HEADER)
    assert missing.status_code == 404


def test_docs_explain_the_customer_id_format_with_an_example(settings, conversations):
    documented = settings.model_copy(
        update={"api_example_customer_id": "CLI-REAL-0001"}
    )
    client = TestClient(create_app(documented, conversations=conversations))
    spec = client.get("/openapi.json").json()
    schema = spec["components"]["schemas"]["SessionRequest"]["properties"]["customer_id"]
    assert "CLI-" in schema["description"] and "not a name" in schema["description"]
    assert schema["examples"] == ["CLI-REAL-0001"]
    session_errors = spec["paths"]["/v1/sessions"]["post"]["responses"]
    assert {"401", "404", "422"} <= set(session_errors)
    assert "CLI-REAL-0001" in spec["info"]["description"]


def test_invalid_customer_id_error_says_what_is_expected(client):
    response = client.post(
        "/v1/sessions", json={"customer_id": "colette smith"}, headers=IAP_HEADER
    )
    assert response.status_code == 422
    assert "CLI-" in response.json()["detail"]


# ---- human in the loop: the approval API shared by every surface ------------------------
def _approval_app(settings, conversations):
    from lir_agent.application.use_cases import RequestApproval
    from lir_agent.container import build_container
    from lir_agent.domain.approvals import ApprovalDetail, ApprovalDraft, Approver
    from lir_agent.domain.models import Lane, Outcome
    from lir_agent.domain.session import SessionState

    linked = settings.model_copy(
        update={"approval_link_template": "https://web.example/a?id={approval_id}&t={token}"}
    )
    container = build_container(linked)
    state: dict = {}
    session = SessionState(state)
    session.start("CLI-DEMO-001", timedelta(minutes=15), "test")
    request = RequestApproval(
        container.approvals, container.audit, timedelta(minutes=60)
    ).execute(
        session,
        action="open_dispute",
        draft=ApprovalDraft(
            params={"transaction_id": "TXN-D1-006", "reason": "Cobro duplicado"},
            title="Abrir una disputa",
            details=[ApprovalDetail(label="Monto", value="245.50 MXN")],
            outcome=Outcome(lane=Lane.DISPUTE, rule_id="C7", reason="r", policy_version="v"),
        ),
        approvers=[Approver.CUSTOMER],
        language="es",
    )
    client = TestClient(create_app(linked, conversations=conversations, container=container))
    return client, container, request


def test_a_customer_approves_from_the_web_link(settings, conversations):
    import asyncio

    client, container, request = _approval_app(settings, conversations)
    links = asyncio.run(container.present_approvals.execute([request.approval_id]))
    token = (links[request.approval_id] or "").rsplit("t=", 1)[1]
    url = f"/v1/approvals/{request.approval_id}"

    assert client.get(url, headers={"X-Approval-Token": "wrong"}).status_code == 404
    assert client.get(url).status_code == 422  # no token, no card
    card = client.get(url, headers={"X-Approval-Token": token}).json()
    assert (card["title"], card["status"]) == ("Abrir una disputa", "pending")
    body = {"decision": "approve", "token": token, "content_hash": card["content_hash"]}
    decided = client.post(f"{url}/decision", json=body)

    assert decided.status_code == 200
    result = decided.json()["result"]
    assert container.cases.get_dispute(result["dispute_case_id"]) is not None
    assert client.post(f"{url}/decision", json=body).status_code == 404  # the link is spent


def test_a_changed_card_cannot_be_approved(settings, conversations):
    import asyncio

    client, container, request = _approval_app(settings, conversations)
    links = asyncio.run(container.present_approvals.execute([request.approval_id]))
    token = (links[request.approval_id] or "").rsplit("t=", 1)[1]
    response = client.post(
        f"/v1/approvals/{request.approval_id}/decision",
        json={"decision": "approve", "token": token, "content_hash": "not-what-was-shown"},
    )
    assert (response.status_code, response.json()["detail"]) == (409, "content_changed")


def test_the_back_office_routes_need_a_verified_identity(settings, conversations):
    client, _, request = _approval_app(settings, conversations)
    review = f"/v1/approvals/{request.approval_id}/review"
    body = {"decision": "approve", "content_hash": request.content_hash}

    assert client.get("/v1/approvals").status_code == 401
    assert client.post(review, json=body).status_code == 401
    # A specialist cannot decide what the customer must approve.
    response = client.post(review, json=body, headers=IAP_HEADER)
    assert (response.status_code, response.json()["detail"]) == (403, "wrong_approver")


def test_a_reply_carries_the_approvals_the_turn_created(settings, conversations):
    client, _, request = _approval_app(settings, conversations)
    conversations.next_approvals = (request.approval_id,)
    started = client.post(
        "/v1/sessions", json={"customer_id": "CLI-DEMO-001"}, headers=IAP_HEADER
    ).json()

    response = client.post(
        f"/v1/sessions/{started['session_id']}/messages",
        json={"text": "hola"},
        headers=IAP_HEADER,
    ).json()

    [card] = response["approvals"]
    assert card["approval_id"] == request.approval_id
    assert card["link"].startswith("https://web.example/a?id=")
