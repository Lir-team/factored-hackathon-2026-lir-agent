"""Runs one trial of a scenario against the real agent and records transcript and outcome.

Every trial is isolated: a fresh container, case service, audit sink and session, so
trials never share state (Anthropic, "Demystifying evals for AI agents", step 4).

A scenario talks to the agent in one of two ways:
- `script.turns`: scripted customer messages, sent in order (deterministic customer side).
  They are nested in an object because promptfoo expands any list in `vars` into one test
  per element.
- `persona`: a simulated customer played by `SIM_MODEL` until it writes ###STOP###
  or `max_turns` is reached.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import litellm
from dotenv import load_dotenv
from google.adk.runners import InMemoryRunner
from google.genai import types
from lir_agent.chat import APP_NAME, USER_ID
from lir_agent.config.settings import Settings
from lir_agent.container import build_container
from lir_agent.domain.models import DisputeCase, HandoffPacket
from lir_agent.domain.session import SessionState
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.infrastructure.cases import InMemoryCaseRepository
from lir_agent.interface.adk import build_agent

EVALS_DIR = Path(__file__).resolve().parent
AGENT_ENV = EVALS_DIR.parent / "lir-agent" / ".env"
WORLD = EVALS_DIR / "fixtures" / "eval_world.json"
# End of the LATAM Bank v1.0.0 snapshot; the eval world is dated around it.
REFERENCE_DATE = date(2026, 6, 17)
STOP = "###STOP###"
RESULT_MARKER = "@@TRIAL@@"
DEFAULT_SIM_MODEL = "openai/gpt-5.4-mini"

# Exports LLM_MODEL / LLM_API_KEY for the agent settings, the simulator and promptfoo.
load_dotenv(AGENT_ENV, override=False)


class RecordingCaseRepository(InMemoryCaseRepository):
    """Case service that also lists what was written, for outcome checks."""

    def __init__(self) -> None:
        super().__init__()
        self.disputes: list[DisputeCase] = []
        self.handoffs: list[HandoffPacket] = []

    def open_dispute(
        self, customer_id: str, transaction_id: str, reason: str
    ) -> DisputeCase:
        case = super().open_dispute(customer_id, transaction_id, reason)
        self.disputes.append(case)
        return case

    def submit_handoff(self, packet: HandoffPacket) -> str:
        self.handoffs.append(packet)
        return super().submit_handoff(packet)


@dataclass
class Turn:
    user: str
    agent: str
    tools: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)


@dataclass
class Trial:
    scenario_id: str
    customer_id: str | None
    agent_model: str
    turns: list[Turn]
    disputes: list[dict]
    handoffs: list[dict]
    turn_rules: list[str]
    case_outcome: dict | None
    evidence_transactions: list[str]
    audit_events: list[dict]
    input_tokens: int
    output_tokens: int
    model_calls: int
    cost_usd: float
    latency_ms: float
    stopped_by: str

    def transcript(self) -> str:
        return transcript(self.as_dict())

    def as_dict(self) -> dict:
        return asdict(self)


def transcript(trial: dict) -> str:
    """Readable conversation: customer turns, tool calls with their results, agent replies.

    Tool results are included so a model grader can check that replies are grounded.
    """
    lines = []
    for t in trial["turns"]:
        lines.append(f"cliente: {t['user']}")
        for call, result in zip(t["tools"], t["tool_results"], strict=False):
            response = json.dumps(result["response"], ensure_ascii=False, default=str)
            lines.append(f"  [tool] {call['name']}({json.dumps(call['args'], ensure_ascii=False)}) -> {response[:2000]}")
        lines.append(f"agente: {t['agent']}")
    return "\n".join(lines)


def scenario_turns(scenario: dict) -> list[str] | None:
    """Scripted customer messages, or None for a simulated customer.

    Raises instead of guessing: a string here would be sent one character per turn.
    """
    script = scenario.get("script")
    if script is None:
        if not scenario.get("persona"):
            raise ValueError(f"{scenario.get('id')}: needs `script.turns` or `persona`")
        return None
    turns = script.get("turns") if isinstance(script, dict) else None
    if not isinstance(turns, list) or not all(isinstance(t, str) and t for t in turns):
        raise ValueError(f"{scenario.get('id')}: `script.turns` must be a list of messages")
    return turns


def run_trial(scenario: dict, agent_model: str | None = None) -> Trial:
    """Run one isolated trial of `scenario` and return what happened."""
    settings_kwargs: dict[str, Any] = {
        "store": "fixture",
        "fixture_path": WORLD,
        "reference_date": REFERENCE_DATE,
        "audit_path": EVALS_DIR / "out" / "unused-audit.jsonl",
    }
    if agent_model:
        settings_kwargs["llm_model"] = agent_model
    settings = Settings(**settings_kwargs)
    audit, cases = InMemoryAuditSink(), RecordingCaseRepository()
    container = build_container(settings, audit=audit, cases=cases)
    runner = InMemoryRunner(agent=build_agent(container=container), app_name=APP_NAME)
    customer_id = scenario.get("customer_id")
    session_id = _start_session(runner, customer_id, scenario.get("session", "ok"))

    usage = {"in": 0, "out": 0, "calls": 0, "cost": 0.0}
    turns: list[Turn] = []
    t0 = time.perf_counter()
    stopped_by = "script_end"
    script = scenario_turns(scenario)
    if script:
        for text in script:
            turns.append(_send(runner, session_id, text, settings.llm_model, usage))
    else:
        stopped_by = _simulate(runner, session_id, scenario, settings.llm_model, usage, turns)
    latency_ms = (time.perf_counter() - t0) * 1000

    state = SessionState(_session_state(runner, session_id))
    events = audit.entries
    case_outcome = state.case_outcome
    return Trial(
        scenario_id=scenario["id"],
        customer_id=customer_id,
        agent_model=settings.llm_model,
        turns=turns,
        disputes=[d.model_dump(mode="json") for d in cases.disputes],
        handoffs=[_handoff_view(h) for h in cases.handoffs],
        turn_rules=[
            e["outcome"]["rule_id"] for e in events if e.get("event") == "turn_routed"
        ],
        case_outcome=case_outcome.model_dump(mode="json") if case_outcome else None,
        evidence_transactions=[e.transaction_id for e in state.evidence],
        audit_events=events,
        input_tokens=usage["in"],
        output_tokens=usage["out"],
        model_calls=usage["calls"],
        cost_usd=round(usage["cost"], 6),
        latency_ms=round(latency_ms, 1),
        stopped_by=stopped_by,
    )


def _start_session(runner: InMemoryRunner, customer_id: str | None, mode: str) -> str:
    """Create the session: signed in (`ok`), `expired`, or `anonymous` (no customer)."""
    state: dict = {}
    if mode != "anonymous" and customer_id:
        ttl = timedelta(minutes=-1 if mode == "expired" else 15)
        SessionState(state).start(customer_id, ttl, "eval_harness")
    session_id = uuid.uuid4().hex
    asyncio.run(
        runner.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id, state=state
        )
    )
    return session_id


def _send(runner: InMemoryRunner, session_id: str, text: str, model: str, usage: dict) -> Turn:
    content = types.Content(role="user", parts=[types.Part(text=text)])
    turn = Turn(user=text, agent="")
    reply: list[str] = []
    for event in runner.run(user_id=USER_ID, session_id=session_id, new_message=content):
        turn.tools += [{"name": c.name, "args": dict(c.args or {})} for c in event.get_function_calls()]
        turn.tool_results += [
            {"name": r.name, "response": r.response} for r in event.get_function_responses()
        ]
        um = getattr(event, "usage_metadata", None)
        if um and um.prompt_token_count:
            prompt, completion = um.prompt_token_count or 0, um.candidates_token_count or 0
            usage["in"] += prompt
            usage["out"] += completion
            usage["calls"] += 1
            usage["cost"] += _cost(model, prompt, completion)
        if event.is_final_response() and event.content and event.content.parts:
            reply += [p.text for p in event.content.parts if p.text]
    turn.agent = "".join(reply)
    return turn


def _simulate(runner, session_id, scenario, model, usage, turns) -> str:
    """Let a simulated customer talk to the agent until it stops or runs out of turns."""
    sim_model = scenario.get("sim_model") or _env("SIM_MODEL") or DEFAULT_SIM_MODEL
    system = SIMULATOR_PROMPT.format(persona=scenario["persona"], lang=scenario.get("lang", "es"))
    history: list[dict] = [{"role": "system", "content": system}]
    for _ in range(int(scenario.get("max_turns", 6))):
        reply = litellm.completion(
            model=sim_model,
            messages=[*history, {"role": "user", "content": "(tu turno)"}] if len(history) == 1 else history,
            api_key=_env("LLM_API_KEY"),
        )
        text = (reply.choices[0].message.content or "").strip()
        if STOP in text:
            return "simulator_stop"
        turn = _send(runner, session_id, text, model, usage)
        turns.append(turn)
        history += [{"role": "assistant", "content": text}, {"role": "user", "content": turn.agent}]
    return "max_turns"


SIMULATOR_PROMPT = """Eres un CLIENTE de un banco latinoamericano chateando con el asistente del banco.
No eres un asistente: escribe solo lo que el cliente diría, en mensajes cortos y naturales.
Idioma del cliente: {lang} (es = español, pt = portugués de Brasil).

Tu situación y objetivo:
{persona}

Reglas:
- Nunca inventes datos que no estén en tu situación. Si te preguntan algo que no sabes, dilo.
- No reveles todo de golpe: responde a lo que el asistente pregunta.
- Cuando tu objetivo se cumpla, o el asistente te derive a una persona, o quede claro que no
  puede ayudarte, escribe exactamente ###STOP### y nada más."""


def _session_state(runner: InMemoryRunner, session_id: str) -> dict:
    session = asyncio.run(
        runner.session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    )
    return dict(session.state) if session else {}


def _handoff_view(packet: HandoffPacket) -> dict:
    return {
        "handoff_id": packet.handoff_id,
        "trigger": (packet.turn_outcome.rule_id if packet.turn_outcome else None),
        "case_rule": (packet.case_outcome.rule_id if packet.case_outcome else None),
        "customer_request": packet.customer_request,
        "verified_evidence": [e.transaction_id for e in packet.verified_evidence],
        "actions_taken": packet.actions_taken,
        "open_questions": packet.open_questions,
        "has_model_summary": bool(packet.model_summary),
    }


def _cost(model: str, prompt: int, completion: int) -> float:
    try:
        return sum(litellm.cost_per_token(model=model, prompt_tokens=prompt, completion_tokens=completion))
    except Exception:  # unknown price: report 0 and let the report flag it
        return 0.0


def _env(key: str) -> str | None:
    return os.environ.get(key) or None


if __name__ == "__main__":
    # One trial per process: `python harness.py < {"scenario": ..., "agent_model": ...}`.
    # Process isolation keeps concurrent trials from sharing ADK or LiteLLM state.
    import json
    import sys

    request = json.loads(sys.stdin.read())
    result = run_trial(request["scenario"], agent_model=request.get("agent_model"))
    sys.stdout.write(RESULT_MARKER + json.dumps(result.as_dict(), ensure_ascii=False, default=str) + "\n")
