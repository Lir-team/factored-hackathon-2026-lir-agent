import pytest
import yaml
from pydantic import ValidationError

from lir_agent.domain.models import Lane
from lir_agent.domain.policy import PolicyConfig, PolicyEngine
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


def test_confirmation_overrides_uncertain_intent(policy):
    facts = {
        **policy.turn_facts(serialized({**IN_SCOPE, "intencion": ("otra_queja", 0.2)})),
        "confirmed_now": True,
    }
    assert policy.route_turn(facts).rule_id == "T2_confirmation_received"


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
