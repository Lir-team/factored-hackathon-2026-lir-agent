import pytest
import yaml
from pydantic import ValidationError

from lir_agent.domain.models import Lane
from lir_agent.domain.policy import OutputGuard, PolicyConfig, PolicyEngine
from lir_agent.infrastructure.resources import ResourceLoader
from tests.support import IN_SCOPE


@pytest.fixture
def policy(settings) -> PolicyEngine:
    return ResourceLoader().load_policy(settings.policy_path)


def serialized(answers: dict[str, tuple]) -> dict:
    return {
        key: {"value": value, "probability": p} for key, (value, p) in answers.items()
    }


def test_theft_threshold_is_asymmetric(policy):
    facts = policy.turn_facts(serialized({**IN_SCOPE, "sospecha_robo": (False, 0.35)}))
    assert policy.route_turn(facts).rule_id == "T3_theft_suspected"


def test_missing_decisions_escalate(policy):
    assert policy.route_turn(policy.turn_facts(None)).lane is Lane.ESCALATE


def test_rules_must_end_with_default(policy):
    raw = policy.config.model_dump(mode="json")
    raw["case_rules"] = raw["case_rules"][:-1]
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(raw)


def test_unknown_operator_rejected(policy):
    raw = policy.config.model_dump(mode="json")
    raw["case_rules"][0]["when"] = {"fraud_score": {"approximately": 70}}
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(raw)


def test_output_guard_detects_refund_promise(policy):
    guard = policy.config.output_guard
    assert guard.violations("Listo, te vamos a devolver los 245,50 MXN mañana.")
    assert not guard.violations("Es tu suscripción mensual.")


@pytest.mark.parametrize(
    "reply",
    [
        "No puedo confirmar que te vamos a devolver el dinero; lo decide un especialista.",
        "No puedo prometer que te vamos a reembolsar.",
        "Não posso garantir que vamos estornar o valor.",
        "No te vamos a devolver el dinero sin revisar el caso.",
        "Todavía no puedo confirmar por este canal que te vamos a reembolsar.",
        "Ainda não posso confirmar que vamos estornar a compra.",
    ],
)
def test_negated_refund_statement_is_allowed(policy, reply):
    assert policy.config.output_guard.violations(reply) == []


@pytest.mark.parametrize(
    "reply",
    [
        "Listo, te vamos a devolver los 245,50 MXN.",
        "No te preocupes, te vamos a devolver el dinero.",
        "Tu reembolso aprobado llegará pronto.",
        "¿Me puedes dar tu CVV?",
    ],
)
def test_promises_and_credential_requests_are_still_blocked(policy, reply):
    assert policy.config.output_guard.violations(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "Si no lo reconoces te vamos a devolver el dinero.",
        "No te preocupes que te vamos a devolver el dinero.",
        "Sin problema te vamos a devolver todo.",
        "Aunque no lo autorizaste te vamos a reembolsar mañana.",
        "Se você não reconhece vamos estornar o valor.",
        "Não se preocupe que vamos estornar tudo.",
    ],
)
def test_negation_that_does_not_govern_the_promise_is_ignored(policy, reply):
    assert policy.config.output_guard.violations(reply)


def test_without_bridge_words_any_negation_in_the_clause_counts(policy):
    raw = policy.config.output_guard.model_dump(mode="json")
    raw["negation_bridge_words"] = []
    guard = OutputGuard.model_validate(raw)
    assert guard.violations("Si no lo reconoces te vamos a devolver el dinero.") == []


def test_fallback_message_follows_the_customer_language(policy):
    guard = policy.config.output_guard
    assert "especialista" in guard.fallback("es")
    assert "sessão" not in guard.fallback("es") and "/" not in guard.fallback("es")
    assert "especialista vai" in guard.fallback("pt")


def test_open_questions_must_name_existing_rules(settings):
    raw = yaml.safe_load(settings.policy_path.read_text(encoding="utf-8"))
    raw["handoff_open_questions"] = {"T99_typo": ["?"]}
    with pytest.raises(ValidationError, match="T99_typo"):
        PolicyConfig.model_validate(raw)


CASE = {
    "fraud_score": 5.0,
    "foreign": False,
    "country_resolved": True,
    "amount_usd": 450.0,
    "status": "Approved",
    "has_duplicate": False,
    "merchant_prior_count": 0,
}


def test_an_unverified_country_with_a_relevant_amount_escalates(policy):
    outcome = policy.decide_case({**CASE, "country_resolved": False})
    assert (outcome.rule_id, outcome.lane) == ("C2b_country_unverified", Lane.ESCALATE)


def test_an_unverified_country_with_a_small_amount_follows_the_usual_rules(policy):
    outcome = policy.decide_case(
        {**CASE, "country_resolved": False, "amount_usd": 20.0, "merchant_prior_count": 3}
    )
    assert outcome.rule_id == "C9_habitual_merchant"


def test_a_verified_domestic_charge_is_not_escalated_for_its_country(policy):
    assert policy.decide_case(CASE).rule_id != "C2b_country_unverified"


@pytest.mark.parametrize(
    ("form", "rule"),
    [
        ({"case_freeze_requested": True, "case_fraud_reported": True}, "T0b_card_freeze_requested"),
        ({"case_freeze_requested": False, "case_fraud_reported": True}, "T0c_fraud_reported"),
    ],
)
def test_the_form_answers_outrank_the_decision_model(policy, form, rule):
    facts = {**policy.turn_facts(serialized(IN_SCOPE)), **form}
    outcome = policy.route_turn(facts)
    assert (outcome.rule_id, outcome.lane) == (rule, Lane.ESCALATE)


@pytest.mark.parametrize(
    "reply",
    [
        "Listo, tu tarjeta ya está bloqueada.",
        "Tu tarjeta quedó congelada.",
        "Bloqueamos tu tarjeta por seguridad.",
        "Seu cartão já está bloqueado.",
    ],
)
def test_output_guard_blocks_a_card_freeze_nobody_made(policy, reply):
    assert policy.config.output_guard.violations(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "Tu tarjeta todavía no está bloqueada: un especialista la bloqueará primero.",
        "Aún no bloqueamos tu tarjeta; un especialista lo hará.",
    ],
)
def test_output_guard_lets_the_truth_about_a_freeze_through(policy, reply):
    assert not policy.config.output_guard.violations(reply)


def test_a_rejected_explanation_goes_to_human_review(policy):
    facts = policy.turn_facts(serialized({**IN_SCOPE, "rechaza_explicacion": (True, 0.9)}))
    outcome = policy.route_turn(facts)
    assert (outcome.rule_id, outcome.lane) == ("T3c_explanation_rejected", Lane.REVIEW)


def test_asking_for_a_person_outranks_the_review(policy):
    answers = {**IN_SCOPE, "rechaza_explicacion": (True, 0.9), "pide_humano": (True, 0.9)}
    assert policy.route_turn(policy.turn_facts(serialized(answers))).rule_id == "T1_wants_human"
