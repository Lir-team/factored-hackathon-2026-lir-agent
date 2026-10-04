"""HandoffNotifier over a Slack incoming webhook."""

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from lir_agent.domain.models import HandoffPacket

REPORT_PATH = "/v1/handoffs/{handoff_id}/report.md"


class SlackHandoffNotifier:
    """Posts one Block Kit notice per handoff. The webhook URL is a secret: never log it."""

    def __init__(
        self,
        webhook_url: str,
        labels: Mapping[str, Any],
        public_base_url: str | None = None,
        assignees: Sequence[str] = (),
        fallback_mention: str = "<!here>",
        client: httpx.Client | None = None,
    ) -> None:
        """Keep the webhook, labels, case file base URL and who takes cases in rotation."""
        self._webhook_url = webhook_url
        self._labels = labels
        self._base_url = (public_base_url or "").rstrip("/")
        self._assignees = list(assignees)
        self._fallback_mention = fallback_mention
        self._client = client or httpx.Client(timeout=5.0)

    def notify(self, packet: HandoffPacket) -> None:
        """Post the notice; raises on a network error or an error status."""
        response = self._client.post(self._webhook_url, json=self.message(packet))
        response.raise_for_status()

    def message(self, packet: HandoffPacket) -> dict:
        """The Slack payload: why the case was handed off, who takes it, and the link."""
        t = self._labels
        outcome = packet.case_outcome or packet.turn_outcome
        rule = outcome.rule_id if outcome else "-"
        lane = outcome.lane.value if outcome else "-"
        explanation = t["rule_explanations"].get(rule, t["default_explanation"])
        title = t["title"].format(handoff_id=packet.handoff_id)
        report_path = REPORT_PATH.format(handoff_id=packet.handoff_id)
        report_url = f"{self._base_url}{report_path}" if self._base_url else report_path
        assigned = t["assigned"].format(mention=self._mention(packet.handoff_id))
        return {
            "text": f"{title}: {explanation}",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f":rotating_light: {title}"},
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{t['why']}* {explanation}"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*{t['rule']}*\n`{rule}`"},
                        {"type": "mrkdwn", "text": f"*{t['lane']}*\n`{lane}`"},
                        {
                            "type": "mrkdwn",
                            "text": f"*{t['facts']}*\n{self._facts(packet)}",
                        },
                    ],
                },
                {"type": "section", "text": {"type": "mrkdwn", "text": assigned}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": t["open_report"]},
                            "url": report_url,
                            "style": "primary",
                        }
                    ],
                },
            ],
        }

    def _mention(self, handoff_id: str) -> str:
        """The same case always goes to the same person; empty rotation mentions everyone."""
        if not self._assignees:
            return self._fallback_mention
        digest = int(hashlib.sha256(handoff_id.encode()).hexdigest(), 16)
        return f"<@{self._assignees[digest % len(self._assignees)]}>"

    def _facts(self, packet: HandoffPacket) -> str:
        """Risk facts only: never the customer, merchants, amounts or dates."""
        t = self._labels
        evidence = packet.verified_evidence
        if not evidence:
            return t["no_facts"]
        facts = [t["transactions"].format(count=len(evidence))]
        scores = [e.fraud_score for e in evidence if e.fraud_score is not None]
        if scores:
            facts.append(t["fraud_score"].format(value=f"{max(scores):.0f}"))
        if any(e.foreign for e in evidence):
            facts.append(t["foreign"])
        if any(e.duplicate_of for e in evidence):
            facts.append(t["duplicate"])
        return " · ".join(facts)
