"""Handoff report: the case file a bank specialist reads, rendered from the handoff packet.

Every fact comes from the packet, which is built from session state (verified evidence,
policy outcomes, actions read back from the case service). Only the model summary is
model-written, and the report labels it as such.
"""

from collections.abc import Mapping
from typing import Any

from lir_agent.domain.models import Evidence, HandoffPacket, Outcome

type Labels = Mapping[str, Any]


class HandoffReportRenderer:
    """Renders a handoff packet as Markdown in the specialist's language."""

    def __init__(self, labels: Mapping[str, Labels], default_language: str) -> None:
        """Keep the labels per language (resources/reports/handoff_report.yaml)."""
        if default_language not in labels:
            raise ValueError(f"No report labels for default language {default_language!r}")
        self._labels = labels
        self._default = default_language

    @property
    def languages(self) -> list[str]:
        """Languages the report can be rendered in."""
        return sorted(self._labels)

    def markdown(self, packet: HandoffPacket, language: str | None = None) -> str:
        """The report as Markdown."""
        t = self._labels.get(language or self._default) or self._labels[self._default]
        none = t["none"]
        lines = [
            f"# {t['title']} {packet.handoff_id}",
            "",
            f"- **{t['created_at']}:** {packet.created_at.isoformat(timespec='seconds')}",
            f"- **{t['customer_id']}:** {packet.customer_id}",
            f"- **{t['customer_request']}:** {_cell(packet.customer_request, none)}",
            f"- **{t['case_report']}:** {_case_report(packet, none)}",
            "",
            f"## {t['policy']}",
            "",
            *_outcome_lines(t["turn_lane"], packet.turn_outcome, t),
            *_outcome_lines(t["case_lane"], packet.case_outcome, t),
            "",
            f"## {t['evidence']}",
            "",
            *_evidence_table(packet.verified_evidence, t),
            "",
            f"## {t['actions']}",
            "",
            *(
                [f"- {_action(action)}" for action in packet.actions_taken]
                or [t["no_actions"]]
            ),
            "",
            f"## {t['decisions']}",
            "",
            *_decision_table(packet.decisions, t),
            "",
            f"## {t['open_questions']}",
            "",
            *(
                [f"- {question}" for question in packet.open_questions]
                or [t["no_open_questions"]]
            ),
            "",
            f"## {t['model_summary']}",
            "",
            f"> {packet.model_summary}" if packet.model_summary else t["no_model_summary"],
            "",
        ]
        return "\n".join(lines)


def _cell(value: Any, none: str) -> str:
    """A table-safe cell: pipes and line breaks would break the Markdown table."""
    if value is None or value == "" or value == []:
        return none
    return str(value).replace("|", "/").replace("\n", " ")


def _outcome_lines(title: str, outcome: Outcome | None, t: Labels) -> list[str]:
    if outcome is None:
        return [f"- **{title}:** {t['none']}"]
    return [
        f"- **{title}:** `{outcome.lane.value}` · {t['rule']} `{outcome.rule_id}` · "
        f"{t['policy_version']} `{outcome.policy_version}`",
        f"  - {t['reason']}: {outcome.reason}",
    ]


def _evidence_table(evidence: list[Evidence], t: Labels) -> list[str]:
    if not evidence:
        return [t["no_evidence"]]
    none = t["none"]
    rows = [
        "| " + " | ".join(t["evidence_columns"]) + " |",
        "|" + "---|" * len(t["evidence_columns"]),
    ]
    for item in evidence:
        amount = (
            f"{item.amount:,.2f} {item.currency or ''}".strip()
            if item.amount is not None
            else none
        )
        cells = [
            item.date.isoformat(timespec="minutes"),
            amount,
            item.merchant_name,
            item.merchant_category,
            item.status,
            item.transaction_country,
            ", ".join(item.duplicate_of),
            f"{item.fraud_score:.2f}" if item.fraud_score is not None else None,
        ]
        rows.append("| " + " | ".join(_cell(cell, none) for cell in cells) + " |")
    return rows


def _decision_table(decisions: dict, t: Labels) -> list[str]:
    if not decisions:
        return [t["none"]]
    rows = [
        "| " + " | ".join(t["decision_columns"]) + " |",
        "|" + "---|" * len(t["decision_columns"]),
    ]
    for key, answer in decisions.items():
        probability = answer.get("probability")
        shown = f"{probability:.2f}" if isinstance(probability, int | float) else None
        rows.append(
            f"| {key} | {_cell(answer.get('value'), t['none'])} | "
            f"{_cell(shown, t['none'])} |"
        )
    return rows


def _case_report(packet: HandoffPacket, none: str) -> str:
    """The form's structured answers (`key=value`), e.g. a requested card freeze."""
    report = packet.case_report
    if report is None:
        return none
    return ", ".join(f"{key}={value}" for key, value in report.model_dump().items())


def _action(action: dict) -> str:
    """One action as `name: key=value, ...` (ids, verification flags)."""
    name = action.get("action", "action")
    details = ", ".join(f"{k}={v}" for k, v in action.items() if k != "action")
    return f"{name}: {details}" if details else str(name)
