"""HandoffNotifier over a Slack incoming webhook."""

import httpx

from lir_agent.domain.models import HandoffPacket

REPORT_PATH = "/v1/handoffs/{handoff_id}/report.md"


class SlackHandoffNotifier:
    """Posts one notice per handoff. The webhook URL is a secret: never log it."""

    def __init__(
        self,
        webhook_url: str,
        template: str,
        public_base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        """Keep the webhook, the message template and the base URL of the case file link."""
        self._webhook_url = webhook_url
        self._template = template
        self._base_url = (public_base_url or "").rstrip("/")
        self._client = client or httpx.Client(timeout=5.0)

    def notify(self, packet: HandoffPacket) -> None:
        """Post the notice; raises on a network error or an error status."""
        outcome = packet.case_outcome or packet.turn_outcome
        report_path = REPORT_PATH.format(handoff_id=packet.handoff_id)
        text = self._template.format(
            handoff_id=packet.handoff_id,
            rule=outcome.rule_id if outcome else "-",
            lane=outcome.lane.value if outcome else "-",
            report_url=f"{self._base_url}{report_path}" if self._base_url else report_path,
        )
        response = self._client.post(self._webhook_url, json={"text": text})
        response.raise_for_status()
