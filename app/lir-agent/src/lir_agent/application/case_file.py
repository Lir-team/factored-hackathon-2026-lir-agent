"""The case file as an HTML page for the bank specialist: summary first, then the evidence.

Built from the same handoff packet as the Markdown report. Thresholds in the charts come
from the policy (`PolicyConfig.condition`), so the page shows exactly what the policy acted
on. Every value is HTML-escaped: the packet holds customer text.
"""

from collections.abc import Mapping
from html import escape
from typing import Any

from lir_agent.domain.models import Evidence, HandoffPacket, Outcome
from lir_agent.domain.policy import OPERATORS, PolicyConfig

type Labels = Mapping[str, Any]

_FORM_FIELDS = ("fraud_suspected", "freeze_card_requested", "card_in_possession", "shared_credentials")


class CaseFileRenderer:
    """Renders a handoff packet as a standalone HTML page in the specialist's language."""

    def __init__(
        self, config: Mapping[str, Any], policy: PolicyConfig, default_language: str
    ) -> None:
        """Keep the page configuration (`resources/reports/case_file.yaml`) and the policy."""
        self._languages = {k for k, v in config.items() if isinstance(v, Mapping) and "title" in v}
        if default_language not in self._languages:
            raise ValueError(f"No case file labels for default language {default_language!r}")
        self._config = config
        self._policy = policy
        self._default = default_language

    def html(
        self,
        packet: HandoffPacket,
        language: str | None = None,
        backoffice_url: str = "/backoffice",
    ) -> str:
        """The case file page."""
        lang = language if language in self._languages else self._default
        t: Labels = self._config[lang]
        outcome = packet.case_outcome or packet.turn_outcome
        rule = outcome.rule_id if outcome else "default"
        priority = self._config["priority"]["rules"].get(rule, self._config["priority"]["default"])
        explanation = t["explanations"].get(rule, t["explanations"]["default"])
        action = t["actions"].get(rule, t["actions"]["default"])
        markdown_url = f"/v1/handoffs/{escape(packet.handoff_id)}/report.md?language={lang}"
        return _PAGE.format(
            lang=lang,
            title=escape(f"{t['title']} {packet.handoff_id}"),
            heading=escape(t["title"]),
            handoff_id=escape(packet.handoff_id),
            priority_class=escape(priority),
            priority=escape(t["priority"][priority]),
            status_class="resolved" if packet.resolution else "open",
            status=escape(_status(packet, t)),
            created_label=escape(t["created"]),
            created=escape(packet.created_at.strftime("%Y-%m-%d %H:%M UTC")),
            customer_label=escape(t["customer"]),
            customer=escape(packet.customer_id),
            why_label=escape(t["why"]),
            why=escape(explanation),
            rules=_rules(packet.turn_outcome, packet.case_outcome),
            action_label=escape(t["action"]),
            action=escape(action),
            reported_label=escape(t["reported"]),
            reported=self._reported(packet, t),
            risk_label=escape(t["risk"]),
            gauges=self._gauges(packet.verified_evidence, t),
            decisions_label=escape(t["decisions"]),
            decisions=self._decisions(packet.decisions, t),
            evidence_label=escape(t["evidence"]),
            evidence=_evidence(packet.verified_evidence, t),
            questions_label=escape(t["open_questions"]),
            questions=_items(packet.open_questions) or f"<p class='muted'>{escape(t['no_value'])}</p>",
            actions_label=escape(t["actions_taken"]),
            actions=_items([_action(a) for a in packet.actions_taken]) or f"<p class='muted'>{escape(t['no_value'])}</p>",
            summary_label=escape(t["summary"]),
            summary=escape(packet.model_summary) if packet.model_summary else escape(t["no_value"]),
            resolve=escape(t["resolve"]),
            backoffice_url=escape(backoffice_url),
            markdown=escape(t["markdown"]),
            markdown_url=markdown_url,
        )

    def _reported(self, packet: HandoffPacket, t: Labels) -> str:
        rows = []
        report = packet.case_report
        if report is not None:
            rows.append((t["form"]["category"], t["categories"].get(report.category, report.category)))
            for name in _FORM_FIELDS:
                value = getattr(report, name)
                if value is None:
                    continue
                key = str(value).lower()
                rows.append((t["form"][name], t["answers"].get(key, str(value))))
        if packet.customer_request:
            rows.append((t["request"], f"“{packet.customer_request}”"))
        if not rows:
            return f"<p class='muted'>{escape(t['no_value'])}</p>"
        return "<dl>" + "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows) + "</dl>"

    def _gauges(self, evidence: list[Evidence], t: Labels) -> str:
        bars = []
        for gauge in self._config["gauges"]:
            values = [getattr(e, gauge["field"]) for e in evidence if getattr(e, gauge["field"]) is not None]
            condition = self._policy.condition(gauge["rule"], gauge["fact"])
            value = max(values) if values else None
            limit = condition[1] if condition else None
            scale = gauge.get("max") or max(v for v in (limit and limit * 1.5, value and value * 1.1, 1) if v)
            fires = bool(condition and value is not None and OPERATORS[condition[0]](value, condition[1]))
            bars.append(_bar(t["gauge_labels"][gauge["label"]], value, scale, limit, fires, t, decimals=0))
        return "".join(bars)

    def _decisions(self, decisions: dict, t: Labels) -> str:
        bars = []
        for item in self._config["decisions"]:
            answer = decisions.get(item["key"]) or {}
            p = answer.get("probability")
            condition = self._policy.condition(item["rule"], item["fact"])
            fires = bool(condition and isinstance(p, int | float) and OPERATORS[condition[0]](p, condition[1]))
            name = t["decision_labels"][item["label"]]
            if answer.get("value") not in (None, True, False):
                name = f"{name}: {answer['value']}"
            bars.append(_bar(name, p, 1.0, condition[1] if condition else None, fires, t, decimals=2))
        return "".join(bars) or f"<p class='muted'>{escape(t['no_value'])}</p>"


def _status(packet: HandoffPacket, t: Labels) -> str:
    resolution = packet.resolution
    if resolution is None:
        return t["status_open"]
    key = "status_accepted" if resolution.accepted else "status_rejected"
    return t[key].format(by=resolution.resolved_by)


def _rules(*outcomes: Outcome | None) -> str:
    chips = [
        f"<span class='chip'>{escape(o.lane.value)} · {escape(o.rule_id)}</span>"
        for o in outcomes
        if o is not None
    ]
    return "".join(dict.fromkeys(chips))


def _bar(label: str, value: float | None, scale: float, limit: float | None,
         fires: bool, t: Labels, decimals: int) -> str:
    shown = t["no_value"] if value is None else f"{value:,.{decimals}f}"
    width = 0 if value is None else min(100.0, 100 * value / scale)
    marker = ""
    if limit is not None:
        left = min(100.0, 100 * limit / scale)
        marker = (f"<span class='marker' style='left:{left:.1f}%'></span>"
                  f"<span class='marker-label' style='left:{left:.1f}%'>{escape(t['limit'])} {limit:,.{decimals}f}</span>")
    state = "over" if fires else "under"
    return (f"<div class='bar'><div class='bar-head'><span>{escape(label)}</span>"
            f"<strong class='{state}'>{escape(shown)}</strong></div>"
            f"<div class='track'><span class='fill {state}' style='width:{width:.1f}%'></span>{marker}</div></div>")


def _evidence(evidence: list[Evidence], t: Labels) -> str:
    if not evidence:
        return f"<p class='muted'>{escape(t['no_evidence'])}</p>"
    head = "".join(f"<th>{escape(c)}</th>" for c in t["evidence_columns"])
    rows = []
    for e in evidence:
        amount = f"{e.amount:,.2f} {e.currency or ''}".strip() if e.amount is not None else t["no_value"]
        cells = [
            e.date.strftime("%Y-%m-%d %H:%M"),
            amount,
            e.merchant_name or t["no_value"],
            e.status or t["no_value"],
            e.transaction_country or t["no_value"],
            f"{e.fraud_score:.0f}" if e.fraud_score is not None else t["no_value"],
        ]
        rows.append("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in cells) + "</tr>")
    return f"<div class='table'><table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"


def _items(values: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(v)}</li>" for v in values) + "</ul>" if values else ""


def _action(action: dict) -> str:
    name = action.get("action", "action")
    details = ", ".join(f"{k}={v}" for k, v in action.items() if k != "action")
    return f"{name}: {details}" if details else str(name)


_PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#f5f7f6; --surface:#ffffff; --text:#17201c; --muted:#5b6862; --border:#d9e0dc;
  --accent:#1f7a4d; --danger:#b3261e; --danger-soft:#fbe9e7; --warn:#9a5a16; --warn-soft:#fff4e5;
  --ok-soft:#e3f2ea; --track:#e8ecea; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111614; --surface:#1a211e; --text:#e6ece9;
  --muted:#9aa8a1; --border:#2c3631; --accent:#4cc38a; --danger:#f2827b; --danger-soft:#3a1d1b;
  --warn:#f2a65a; --warn-soft:#3a2f1c; --ok-soft:#1c3a2c; --track:#2c3631; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:960px; margin:0 auto; padding:24px 16px 48px; display:flex; flex-direction:column; gap:16px; }}
header {{ display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:8px; }}
h1 {{ margin:0; font-size:24px; }} h2 {{ margin:0 0 12px; font-size:15px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }}
.meta {{ color:var(--muted); font-size:13px; }}
.pills {{ display:flex; gap:8px; flex-wrap:wrap; }}
.pill {{ font-size:12px; font-weight:600; padding:4px 10px; border-radius:999px; }}
.pill.high {{ background:var(--danger-soft); color:var(--danger); }} .pill.medium {{ background:var(--warn-soft); color:var(--warn); }}
.pill.low, .pill.resolved {{ background:var(--ok-soft); color:var(--accent); }} .pill.open {{ background:var(--track); color:var(--text); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:18px; min-width:0; }}
.lead {{ font-size:17px; margin:0 0 10px; }}
.chip {{ display:inline-block; font:12px ui-monospace,monospace; background:var(--track); border-radius:6px; padding:2px 8px; margin:0 6px 6px 0; }}
dl {{ display:grid; grid-template-columns:max-content 1fr; gap:6px 16px; margin:0; }} dt {{ color:var(--muted); }} dd {{ margin:0; overflow-wrap:anywhere; }}
.bar {{ margin-bottom:22px; }} .bar-head {{ display:flex; justify-content:space-between; gap:8px; font-size:14px; margin-bottom:6px; }}
.track {{ position:relative; height:12px; background:var(--track); border-radius:6px; }}
.fill {{ position:absolute; left:0; top:0; bottom:0; border-radius:6px; }} .fill.under {{ background:var(--accent); }} .fill.over {{ background:var(--danger); }}
strong.over {{ color:var(--danger); }}
.marker {{ position:absolute; top:-4px; bottom:-4px; width:2px; background:var(--text); }}
.marker-label {{ position:absolute; top:14px; transform:translateX(-50%); font-size:11px; color:var(--muted); white-space:nowrap; }}
.table {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); white-space:nowrap; }} th {{ color:var(--muted); font-weight:600; }}
ul {{ margin:0; padding-left:20px; }} .muted {{ color:var(--muted); margin:0; }}
.actions {{ display:flex; gap:12px; flex-wrap:wrap; }}
.button {{ display:inline-block; padding:10px 18px; border-radius:8px; text-decoration:none; font-weight:600; }}
.button.primary {{ background:var(--accent); color:#fff; }} .button.secondary {{ border:1px solid var(--border); color:var(--text); }}
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>{heading} <span class="meta">{handoff_id}</span></h1>
      <p class="meta">{created_label}: {created} · {customer_label}: {customer}</p>
    </div>
    <div class="pills"><span class="pill {priority_class}">{priority}</span><span class="pill {status_class}">{status}</span></div>
  </header>
  <div class="grid">
    <section class="card"><h2>{why_label}</h2><p class="lead">{why}</p>{rules}</section>
    <section class="card"><h2>{action_label}</h2><p class="lead">{action}</p></section>
  </div>
  <div class="grid">
    <section class="card"><h2>{reported_label}</h2>{reported}</section>
    <section class="card"><h2>{risk_label}</h2>{gauges}<h2>{decisions_label}</h2>{decisions}</section>
  </div>
  <section class="card"><h2>{evidence_label}</h2>{evidence}</section>
  <div class="grid">
    <section class="card"><h2>{questions_label}</h2>{questions}</section>
    <section class="card"><h2>{actions_label}</h2>{actions}</section>
  </div>
  <section class="card"><h2>{summary_label}</h2><p class="muted">{summary}</p></section>
  <div class="actions"><a class="button primary" href="{backoffice_url}">{resolve}</a><a class="button secondary" href="{markdown_url}">{markdown}</a></div>
</main>
</body>
</html>
"""
