import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx

from lir_agent.domain.approvals import ApprovalRequest, ApprovalStatus, Approver
from lir_agent.infrastructure.approvals import (
    EmailApprovalSurface,
    SlackApprovalSurface,
)
from lir_agent.infrastructure.resources import ResourceLoader

NOW = datetime(2026, 10, 4, 22, 0, tzinfo=UTC)


def request(**overrides) -> ApprovalRequest:
    fields = {
        "approval_id": "APR-1",
        "action": "open_dispute",
        "params": {"amount": 245.5},
        "customer_id": "CLI-DEMO-001",
        "approver": Approver.SPECIALIST,
        "language": "es",
        "title": "Abrir una disputa por este cargo",
        "details": [],
        "content_hash": "h",
        "policy_version": "v",
        "rule_id": "C7_duplicate",
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
    }
    return ApprovalRequest(**{**fields, **overrides})


class Outbox:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, to, subject, body):
        if self.fail:
            raise OSError("smtp down")
        self.sent.append((to, subject, body))


def email_surface(settings, outbox):
    labels = ResourceLoader().load_labels(settings.approval_labels_path)
    return EmailApprovalSurface(outbox, labels, "cliente@example.com")


def test_specialist_decision_is_emailed_to_the_customer(settings):
    outbox = Outbox()
    decided = request(status=ApprovalStatus.APPROVED, decided_role=Approver.SPECIALIST)
    asyncio.run(email_surface(settings, outbox).report(decided))
    [(to, subject, body)] = outbox.sent
    assert to == "cliente@example.com" and "APR-1" in subject
    assert "especialista" in body and "aprobó" in body
    assert "CLI-DEMO-001" not in body and "245" not in body


def test_rejection_and_portuguese(settings):
    outbox = Outbox()
    decided = request(
        status=ApprovalStatus.REJECTED, decided_role=Approver.SPECIALIST, language="pt"
    )
    asyncio.run(email_surface(settings, outbox).report(decided))
    assert "não aprovou" in outbox.sent[0][2]


def test_email_never_presents_and_a_failed_mail_does_not_raise(settings):
    surface = email_surface(settings, Outbox(fail=True))
    assert asyncio.run(surface.present(request(), "https://x")) is False
    asyncio.run(surface.report(request(status=ApprovalStatus.APPROVED)))


def slack_surface(settings, posts):
    async def handler(req):
        posts.append(json.loads(req.content))
        return httpx.Response(200)

    labels = ResourceLoader().load_mapping(settings.slack_notice_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SlackApprovalSurface(
        "https://hooks", labels, "https://lir.app", "<@U1>", client
    )


def test_slack_asks_a_specialist_and_posts_the_decision(settings):
    posts = []
    surface = slack_surface(settings, posts)
    assert asyncio.run(surface.present(request(), None)) is True
    asyncio.run(
        surface.report(
            request(
                status=ApprovalStatus.APPROVED,
                decided_role=Approver.SPECIALIST,
                decided_by="ana",
            )
        )
    )
    pending, decided = (p["text"] for p in posts)
    assert (
        "APR-1" in pending
        and "<@U1>" in pending
        and "https://lir.app/v1/approvals" in pending
    )
    assert "approved" in decided and "specialist" in decided
    assert all("CLI-DEMO-001" not in p["text"] for p in posts)


def test_slack_ignores_customer_requests(settings):
    posts = []
    customer = request(approver=Approver.CUSTOMER)
    assert asyncio.run(slack_surface(settings, posts).present(customer, None)) is False
    assert posts == []
