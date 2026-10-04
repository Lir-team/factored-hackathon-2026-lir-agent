import json

import httpx
import pytest

from lir_agent.infrastructure.messaging import SlackHandoffNotifier
from tests.application.test_handoff_report import packet

WEBHOOK = "https://hooks.slack.com/services/T000/B000/secret"


def notifier(handler, base_url="https://lir.example.run.app") -> SlackHandoffNotifier:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SlackHandoffNotifier(WEBHOOK, "{handoff_id} {rule} {lane} {report_url}", base_url, client)


def test_notice_names_the_case_and_links_the_report_without_customer_data():
    sent = []

    def handler(request):
        sent.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200)

    notifier(handler).notify(packet())
    [(url, body)] = sent
    assert url == WEBHOOK
    assert body["text"] == (
        "HND-ABC C4_high_risk escalate "
        "https://lir.example.run.app/v1/handoffs/HND-ABC/report.md"
    )
    assert "CLI-DEMO-001" not in body["text"] and "TIENDA" not in body["text"]


def test_error_status_raises():
    with pytest.raises(httpx.HTTPStatusError):
        notifier(lambda request: httpx.Response(404)).notify(packet())
