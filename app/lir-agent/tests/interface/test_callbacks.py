from datetime import date

from google.adk.models.llm_response import LlmResponse
from google.genai import types

from lir_agent.domain.session import SessionState
from tests.support import IN_SCOPE, make_context, outcome_rule, tool, user_request


def test_unauthenticated_tool_call_denied(harness):
    result = harness.callbacks.before_tool(
        tool("find_candidate_transactions"), {}, make_context(False)
    )
    assert result["error"] == "session_not_authenticated"


def test_expired_session_denied(harness):
    result = harness.callbacks.before_tool(
        tool("find_candidate_transactions"), {}, make_context(ttl_minutes=-1)
    )
    assert result["error"] == "session_expired"


def test_tool_not_allowed_for_lane(make_harness, context):
    h = make_harness({**IN_SCOPE, "intencion": ("otra_queja", 0.3)})
    h.callbacks.before_model(context, user_request("hola"))
    assert SessionState(context.state).turn_lane == "clarify"
    result = h.callbacks.before_tool(
        tool("get_transaction_evidence"), {"transaction_ref": "T1"}, context
    )
    assert result["reason"] == "not_allowed_for_turn_lane"


def test_dispute_requires_explicit_confirmation_then_verifies(make_harness, context):
    h = make_harness({**IN_SCOPE, "confirma": (True, 0.95)})
    session = SessionState(context.state)
    h.callbacks.before_model(context, user_request("Me cobraron dos veces en OXXO"))
    ref = session.ref_for("TXN-D1-006")
    h.toolkit.get_transaction_evidence(context, ref)

    args = {"transaction_ref": ref, "reason": "duplicate"}
    assert (
        h.callbacks.before_tool(tool("open_dispute"), args, context)["status"]
        == "confirmation_required"
    )

    h.callbacks.before_model(context, user_request("Sí, confirmo, abre la disputa"))
    assert outcome_rule(session) == "T2_confirmation_received"
    assert h.callbacks.before_tool(tool("open_dispute"), args, context) is None
    assert h.toolkit.open_dispute(context, **args)["status"] == "verified"


def test_unclear_answer_keeps_confirmation_pending(make_harness, context):
    h = make_harness({**IN_SCOPE, "confirma": (False, 0.2)})
    session = SessionState(context.state)
    h.callbacks.before_model(context, user_request("Me cobraron dos veces en OXXO"))
    h.toolkit.get_transaction_evidence(context, session.ref_for("TXN-D1-006"))
    request = user_request("mmm no sé")
    h.callbacks.before_model(context, request)
    assert session.turn_lane == "confirm"
    assert session.pending_confirmation == "TXN-D1-006"
    assert "Turn lane: confirm" in str(request.config.system_instruction)


def test_dispute_blocked_when_policy_says_explain(harness, context):
    session = SessionState(context.state)
    harness.callbacks.before_model(
        context, user_request("No reconozco el cargo de Spotify")
    )
    ref = session.ref_for("TXN-D1-004")
    harness.toolkit.get_transaction_evidence(context, ref)
    result = harness.callbacks.before_tool(
        tool("open_dispute"), {"transaction_ref": ref}, context
    )
    assert result["reason"] == "policy_does_not_allow_dispute"


def test_escalation_creates_handoff_with_verified_facts(make_harness, context):
    h = make_harness({**IN_SCOPE, "pide_humano": (True, 0.9)})
    session = SessionState(context.state)
    h.toolkit.get_transaction_evidence(context, session.ref_for("TXN-D1-008"))
    h.callbacks.before_model(context, user_request("Quiero hablar con una persona"))
    packet = h.container.cases.get_handoff(session.handoff_id)
    assert packet.turn_outcome.rule_id == "T1_wants_human"
    assert packet.verified_evidence[0].transaction_id == "TXN-D1-008"


def test_decision_failure_escalates(make_harness, context):
    h = make_harness(fail=True)
    h.callbacks.before_model(context, user_request("no reconozco un cargo"))
    assert outcome_rule(SessionState(context.state)) == "T0_decisions_unavailable"
    assert "decision_failed" in h.audit.events()


def test_lane_guidance_is_appended(harness, context):
    request = user_request("no reconozco un cargo")
    harness.callbacks.before_model(context, request)
    assert "Turn lane: proceed" in str(request.config.system_instruction)


def test_reference_date_is_appended(make_harness, settings, context):
    settings.reference_date = date(2026, 6, 17)
    request = user_request("no reconozco un cargo del 11 de junio")
    make_harness().callbacks.before_model(context, request)
    assert "Today is 2026-06-17" in str(request.config.system_instruction)


def test_reference_date_defaults_to_current_date(settings):
    assert settings.reference_date is None
    assert settings.today() == date.today()


def test_output_guard_replaces_refund_promise(harness, context):
    promise = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text="Listo, te vamos a devolver los 245,50 MXN.")],
        )
    )
    replaced = harness.callbacks.after_model(context, promise)
    assert (
        replaced.content.parts[0].text
        == harness.container.policy.config.output_guard.fallback_message
    )
    clean = LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(text="Es tu suscripción mensual.")]
        )
    )
    assert harness.callbacks.after_model(context, clean) is None
