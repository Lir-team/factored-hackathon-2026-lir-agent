"""SEC-04: idle-timeout case sessions, and approvals that need the bank's sign-in (step-up)."""

import asyncio
import base64
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from lir_agent.application.use_cases import RequestApproval
from lir_agent.container import build_container
from lir_agent.domain.approvals import (
    ApprovalDetail,
    ApprovalDraft,
    ApprovalError,
    Approver,
)
from lir_agent.domain.models import Lane, Outcome
from lir_agent.domain.session import SessionState, utc_now
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.interface.http import create_app
from tests.interface.test_http_api import FakeConversations

CUSTOMER = "CLI-DEMO-001"
LINK = "https://web.example/a?id={approval_id}&t={token}"


# ---- idle timeout ------------------------------------------------------------------------
def test_activity_extends_an_idle_session_up_to_its_ceiling():
    session = SessionState({})
    start = utc_now()
    session.start(CUSTOMER, timedelta(minutes=30), "case_intake", max_ttl=timedelta(hours=2))

    session.touch(start + timedelta(minutes=20))
    assert session.auth_error(start + timedelta(minutes=45)) is None  # extended

    session.touch(start + timedelta(minutes=110))
    assert session.auth_error(start + timedelta(minutes=125)) is not None  # the ceiling holds


def test_an_idle_session_expires_and_activity_does_not_revive_it():
    session = SessionState({})
    start = utc_now()
    session.start(CUSTOMER, timedelta(minutes=30), "case_intake", max_ttl=timedelta(hours=2))

    session.touch(start + timedelta(minutes=31))

    assert session.auth_error(start + timedelta(minutes=31)) is not None


def test_a_fixed_session_is_not_extended():
    session = SessionState({})
    start = utc_now()
    session.start(CUSTOMER, timedelta(minutes=15), "iap_operator")
    session.touch(start + timedelta(minutes=10))
    assert session.auth_error(start + timedelta(minutes=16)) is not None


# ---- step-up on the web card ---------------------------------------------------------------
def userinfo(customer_id: str) -> str:
    """API Gateway's userinfo header: the verified JWT payload, base64url without padding."""
    raw = json.dumps({"sub": customer_id, "iss": "idp.example"}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.fixture
def step_up(settings):
    stepped = settings.model_copy(
        update={"approval_link_template": LINK, "approval_requires_sign_in": True}
    )
    container = build_container(stepped, audit=InMemoryAuditSink())
    state: dict = {}
    session = SessionState(state)
    session.start(CUSTOMER, timedelta(minutes=15), "test")
    request = RequestApproval(container.approvals, container.audit, timedelta(minutes=60)).execute(
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
    links = asyncio.run(container.present_approvals.execute([request.approval_id]))
    token = (links[request.approval_id] or "").rsplit("t=", 1)[1]
    client = TestClient(create_app(stepped, conversations=FakeConversations(), container=container))
    return client, container, request, token


def test_the_link_alone_is_not_enough(step_up):
    client, _, request, token = step_up
    response = client.get(
        f"/v1/approvals/{request.approval_id}", headers={"X-Approval-Token": token}
    )
    assert (response.status_code, response.json()["detail"]) == (401, "sign_in_required")


def test_someone_else_signed_in_cannot_use_the_link(step_up):
    client, container, request, token = step_up
    response = client.get(
        f"/v1/approvals/{request.approval_id}",
        headers={"X-Approval-Token": token, "X-Apigateway-Api-Userinfo": userinfo("CLI-OTHER")},
    )
    assert (response.status_code, response.json()["detail"]) == (403, "not_your_request")
    refused = [e for e in container.audit.entries if e["event"] == "approval_link_refused"]
    assert refused[-1]["reason"] == "signed_in_as_someone_else"


def test_the_signed_in_customer_approves_and_the_proof_is_audited(step_up):
    client, container, request, token = step_up
    headers = {"X-Apigateway-Api-Userinfo": userinfo(CUSTOMER)}
    body = {"decision": "approve", "token": token, "content_hash": request.content_hash}

    response = client.post(
        f"/v1/approvals/{request.approval_id}/decision", json=body, headers=headers
    )

    assert response.status_code == 200
    [decided] = [e for e in container.audit.entries if e["event"] == "approval_decided"]
    assert decided["proof"] == "link+sign_in"


def test_a_signed_in_mismatch_is_refused_in_the_core():
    from lir_agent.application.use_cases import VerifyApprovalLink
    from lir_agent.infrastructure.approvals import InMemoryApprovalRepository

    verify = VerifyApprovalLink(InMemoryApprovalRepository(), InMemoryAuditSink())
    with pytest.raises(ApprovalError, match="not_found"):
        verify.execute("APR-NOPE", "token", signed_in_as=CUSTOMER)
