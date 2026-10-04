from datetime import date

from google.adk.models.llm_response import LlmResponse
from google.genai import types

from lir_agent.container import build_container
from lir_agent.domain.case_intake import CaseReport
from lir_agent.domain.session import SessionState
from lir_agent.infrastructure.audit import InMemoryAuditSink
from tests.support import (
    IN_SCOPE,
    Harness,
    ScriptedDecisions,
    make_context,
    outcome_rule,
    tool,
    user_request,
)


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


def test_opening_a_dispute_becomes_a_request_for_the_customers_approval(harness, context):
    session = SessionState(context.state)
    harness.callbacks.before_model(context, user_request("Me cobraron dos veces en OXXO"))
    ref = session.ref_for("TXN-D1-006")
    harness.toolkit.get_transaction_evidence(context, ref)

    args = {"transaction_ref": ref, "reason": "Cobro duplicado"}
    result = harness.callbacks.before_tool(tool("open_dispute"), args, context)

    assert result is not None and result["status"] == "approval_requested"
    request = harness.container.approvals.get(result["approval_id"])
    assert request is not None
    assert (request.approver, request.status, request.then) == ("customer", "pending", [])
    assert request.params == {"transaction_id": "TXN-D1-006", "reason": "Cobro duplicado"}
    assert [d.label for d in request.details] == ["Comercio", "Fecha", "Monto", "Motivo"]
    assert session.approval_ids == [request.approval_id]
    # Nothing was opened, and asking again returns the same request.
    assert harness.toolkit.open_dispute(context, **args)["status"] == "blocked"
    again = harness.callbacks.before_tool(tool("open_dispute"), args, context)
    assert again is not None and again["approval_id"] == request.approval_id


def test_a_typed_yes_opens_nothing(harness, context):
    session = SessionState(context.state)
    harness.callbacks.before_model(context, user_request("Me cobraron dos veces en OXXO"))
    ref = session.ref_for("TXN-D1-006")
    harness.toolkit.get_transaction_evidence(context, ref)
    harness.callbacks.before_tool(
        tool("open_dispute"), {"transaction_ref": ref, "reason": "x"}, context
    )

    harness.callbacks.before_model(context, user_request("Sí, confirmo, ábrela"))

    [approval_id] = session.approval_ids
    request = harness.container.approvals.get(approval_id)
    assert request is not None and request.status == "pending"


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
    assert result["reason"] == "action_not_allowed"


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
        == harness.container.policy.config.output_guard.fallback("es")
    )
    clean = LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(text="Es tu suscripción mensual.")]
        )
    )
    assert harness.callbacks.after_model(context, clean) is None


def test_guard_fallback_is_in_the_customers_language(harness, context):
    harness.callbacks.before_model(context, user_request("Quero falar sobre uma cobrança"))
    promise = LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(text="Vamos estornar o valor amanhã.")]
        )
    )
    replaced = harness.callbacks.after_model(context, promise)
    assert replaced.content.parts[0].text == (
        harness.container.policy.config.output_guard.fallback("pt")
    )


def test_blocked_tool_tells_the_model_what_to_do_instead(make_harness, context):
    h = make_harness({**IN_SCOPE, "intencion": ("otra_queja", 0.3)})
    session = SessionState(context.state)
    h.callbacks.before_model(context, user_request("hola"))
    assert session.turn_lane == "clarify"
    result = h.callbacks.before_tool(
        tool("open_dispute"), {"transaction_ref": "T1", "reason": "x"}, context
    )
    assert result["reason"] == "not_allowed_for_turn_lane"
    assert "Turn lane: clarify" in result["instruction"]


def test_turn_level_handoff_has_the_policy_open_questions(make_harness, context):
    h = make_harness({**IN_SCOPE, "sospecha_robo": (True, 0.9)})
    session = SessionState(context.state)
    h.callbacks.before_model(context, user_request("Me clonaron la tarjeta"))
    packet = h.container.cases.get_handoff(session.handoff_id)
    expected = h.container.policy.config.handoff_open_questions["T3_theft_suspected"]
    assert packet.open_questions == expected


def test_charge_found_after_a_handoff_is_attached_to_it(make_harness, context):
    h = make_harness({**IN_SCOPE, "sospecha_robo": (True, 0.9)})
    session = SessionState(context.state)
    h.callbacks.before_model(context, user_request("Una compra en Argentina que no hice, me clonaron"))
    assert session.turn_lane == "escalate"
    ref = session.ref_for("TXN-D1-008")
    assert h.callbacks.before_tool(tool("get_transaction_evidence"), {"transaction_ref": ref}, context) is None
    h.toolkit.get_transaction_evidence(context, ref)
    packet = h.container.cases.get_handoff(session.handoff_id)
    assert [e.transaction_id for e in packet.verified_evidence] == ["TXN-D1-008"]
    assert packet.case_outcome.rule_id == "C1_high_fraud_score"
    assert set(h.container.policy.config.handoff_open_questions["C1_high_fraud_score"]) <= set(
        packet.open_questions
    )


def test_escalate_lane_still_cannot_open_a_dispute(make_harness, context):
    h = make_harness({**IN_SCOPE, "sospecha_robo": (True, 0.9)})
    h.callbacks.before_model(context, user_request("Me clonaron la tarjeta"))
    result = h.callbacks.before_tool(tool("open_dispute"), {"transaction_ref": "T1"}, context)
    assert result["reason"] == "not_allowed_for_turn_lane"


# ---- bank records reach the external models only as placeholders -----------------------
BANK_RECORDS = ("OXXO LAS AGUILAS", "245.5", "2026-06-10", "10/06/2026")


def _privacy_harness(settings):
    decisions = ScriptedDecisions(IN_SCOPE)
    audit = InMemoryAuditSink()
    container = build_container(settings, audit=audit, decisions=decisions)
    return Harness(container, audit), decisions


def _content_text(content: types.Content) -> str:
    return " ".join(part.text or "" for part in content.parts or [])


def _sent(request) -> str:
    return " ".join(content.model_dump_json() for content in request.contents)


def test_identifiers_never_reach_the_models(settings, context):
    h, decisions = _privacy_harness(settings)
    request = user_request("Mi tarjeta 4152 3138 0000 1234 tiene un cargo raro")

    h.callbacks.before_model(context, request)

    assert "4152" not in _sent(request)
    assert all("4152" not in state for state, _ in decisions.calls)
    redacted = [e for e in h.audit.entries if e["event"] == "identifiers_redacted"]
    assert [e["count"] for e in redacted] == [1]
    assert "4152" not in str(h.audit.entries)
    # The customer's words stay whole inside the service (handoff, language detection).
    assert "4152" in (SessionState(context.state).last_user_text or "")


def test_tool_results_carry_placeholders_and_replies_are_resolved(harness, context):
    session = SessionState(context.state)
    harness.callbacks.before_model(context, user_request("Me cobraron dos veces"))
    result = harness.toolkit.get_transaction_evidence(
        context, session.ref_for("TXN-D1-006")
    )

    evidence = result["evidence"]
    assert evidence["merchant_name"] == "[[COMERCIO_1]]"
    assert evidence["amount"] == "[[MONTO_1]]"
    assert evidence["date"] == "[[FECHA_1]]"
    assert not any(record in str(result) for record in BANK_RECORDS)

    reply = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text="El cargo de [[MONTO_1]] MXN en [[COMERCIO_1]] del [[FECHA_1]].")],
        )
    )
    resolved = harness.callbacks.after_model(context, reply)
    assert resolved.content.parts[0].text == (
        "El cargo de 245.50 MXN en OXXO LAS AGUILAS del 10/06/2026."
    )


def test_history_goes_back_to_the_model_as_placeholders(harness, context):
    session = SessionState(context.state)
    harness.callbacks.before_model(context, user_request("Me cobraron dos veces"))
    result = harness.toolkit.get_transaction_evidence(
        context, session.ref_for("TXN-D1-006")
    )
    history = [
        types.Content(role="user", parts=[types.Part(text="Me cobraron dos veces")]),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="get_transaction_evidence", response=result
                    )
                )
            ],
        ),
        # What the customer read: resolved values.
        types.Content(
            role="model",
            parts=[types.Part(text="Es un cargo de 245.50 MXN en OXXO LAS AGUILAS del 10/06/2026.")],
        ),
        types.Content(role="user", parts=[types.Part(text="¿y el de OXXO LAS AGUILAS?")]),
    ]
    request = user_request("x")
    request.contents = history

    harness.callbacks.before_model(context, request)

    assert not any(record in _sent(request) for record in BANK_RECORDS)
    assert "[[COMERCIO_1]]" in _content_text(request.contents[-1])
    # The session history itself is left untouched: the request got copies.
    assert "OXXO LAS AGUILAS" in _content_text(history[2])


def test_tools_receive_real_values(harness, context):
    session = SessionState(context.state)
    harness.callbacks.before_model(context, user_request("Me cobraron dos veces"))
    harness.toolkit.get_transaction_evidence(context, session.ref_for("TXN-D1-006"))
    args = {
        "summary": "Cliente no reconoce [[COMERCIO_1]] por [[MONTO_1]] MXN",
        "open_questions": ["¿Estuvo en [[COMERCIO_1]]?"],
    }

    harness.callbacks.before_tool(tool("request_human_handoff"), args, context)

    assert args == {
        "summary": "Cliente no reconoce OXXO LAS AGUILAS por 245.50 MXN",
        "open_questions": ["¿Estuvo en OXXO LAS AGUILAS?"],
    }


# ---- what the customer answered in the web form ------------------------------------------
def test_a_requested_freeze_goes_first_to_a_specialist_with_the_form_answers(harness, context):
    session = SessionState(context.state)
    session.case_report = CaseReport(
        category="card_lost_stolen",
        fraud_suspected=True,
        freeze_card_requested=True,
        card_in_possession="no",
        shared_credentials="no",
    )

    harness.callbacks.before_model(context, user_request("Perdí mi tarjeta"))

    assert outcome_rule(session) == "T0b_card_freeze_requested"
    packet = harness.container.cases.get_handoff(session.handoff_id or "")
    assert packet is not None
    assert packet.open_questions[0].startswith("PRIORIDAD: bloquear la tarjeta")
    report = harness.container.handoff_report.markdown(packet, "es")
    assert "freeze_card_requested=True" in report
    assert "card_in_possession=no" in report


def test_a_chat_without_a_form_is_routed_as_before(harness, context):
    harness.callbacks.before_model(context, user_request("No reconozco un cargo de 179"))
    assert outcome_rule(SessionState(context.state)) == "T9_in_scope"
