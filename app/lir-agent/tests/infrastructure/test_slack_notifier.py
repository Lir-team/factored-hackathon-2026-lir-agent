import json

import httpx
import pytest

from lir_agent.infrastructure.messaging import SlackHandoffNotifier
from lir_agent.infrastructure.resources import ResourceLoader
from tests.application.test_handoff_report import packet

WEBHOOK = "https://hooks.slack.com/services/T000/B000/secret"


def notifier(settings, handler, assignees=("U1", "U2")) -> SlackHandoffNotifier:
    labels = ResourceLoader().load_mapping(settings.slack_notice_path)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SlackHandoffNotifier(
        WEBHOOK, labels, "https://lir.example.run.app", assignees, "<!here>", client
    )


def ok(request):
    return httpx.Response(200)


def test_notice_explains_assigns_and_links_without_customer_data(settings):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    notifier(settings, handler).notify(packet())
    [body] = sent
    text = json.dumps(body, ensure_ascii=False)
    assert "HND-ABC" in body["text"]
    assert "<@U1>" in text or "<@U2>" in text
    assert "score de fraude" in text and "fuera del país del cliente" in text
    assert "https://lir.example.run.app/v1/handoffs/HND-ABC/report.md" in text
    for private in ("CLI-DEMO-001", "TIENDA", "38,900", "38900"):
        assert private not in text


def test_notice_links_the_back_office_to_resolve_the_case(settings):
    text = json.dumps(notifier(settings, ok).message(packet()), ensure_ascii=False)
    assert "https://lir.example.run.app/backoffice" in text


def test_resolution_notice_says_who_decided_and_how(settings):
    from datetime import UTC, datetime

    from lir_agent.domain.models import HandoffResolution

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    resolution = HandoffResolution(
        accepted=False, resolved_by="ana@bank", resolved_at=datetime(2026, 10, 5, tzinfo=UTC)
    )
    notifier(settings, handler).notify_resolution(packet(resolution=resolution))
    [body] = sent
    assert "HND-ABC" in body["text"] and "ana@bank" in body["text"]
    assert "reclamo rechazado" in body["text"]


def test_same_case_always_goes_to_the_same_person(settings):
    assert notifier(settings, ok).message(packet()) == notifier(settings, ok).message(
        packet()
    )


def test_without_assignees_everyone_is_mentioned(settings):
    body = notifier(settings, ok, assignees=()).message(packet())
    assert "<!here>" in json.dumps(body)


def test_error_status_raises(settings):
    with pytest.raises(httpx.HTTPStatusError):
        notifier(settings, lambda request: httpx.Response(404)).notify(packet())
