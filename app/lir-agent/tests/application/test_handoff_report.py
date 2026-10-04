from datetime import UTC, datetime

import pytest

from lir_agent.application.handoff_report import HandoffReportRenderer
from lir_agent.domain.models import Evidence, HandoffPacket, Lane, Outcome
from lir_agent.infrastructure.resources import ResourceLoader

CREATED = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def renderer(settings) -> HandoffReportRenderer:
    labels = ResourceLoader().load_labels(settings.handoff_report_path)
    return HandoffReportRenderer(labels, settings.report_default_language)


def packet(**overrides) -> HandoffPacket:
    evidence = Evidence(
        transaction_id="TX-1",
        date=CREATED,
        amount=38900.0,
        currency="ARS",
        amount_usd=40.0,
        merchant_name="TIENDA | NUEVA",
        merchant_category="retail",
        status="approved",
        transaction_type="purchase",
        channel="online",
        merchant_prior_count=0,
        merchant_last_seen=None,
        duplicate_of=[],
        transaction_country="AR",
        customer_country="MX",
        foreign=True,
        country_resolved=True,
        fraud_score=0.91,
    )
    fields = {
        "handoff_id": "HND-ABC",
        "created_at": CREATED,
        "customer_id": "CLI-DEMO-001",
        "customer_request": "Veo una compra que no hice",
        "turn_outcome": Outcome(
            lane=Lane.PROCEED, rule_id="T9_in_scope", reason="in scope",
            policy_version="0.2.0-synthetic",
        ),
        "case_outcome": Outcome(
            lane=Lane.ESCALATE, rule_id="C4_high_risk", reason="high fraud score",
            policy_version="0.2.0-synthetic",
        ),
        "verified_evidence": [evidence],
        "actions_taken": [{"action": "handoff", "handoff_id": "HND-ABC", "verified": True}],
        "decisions": {"sospecha_robo": {"value": True, "probability": 0.82}},
        "open_questions": ["Did the customer travel recently?"],
        "model_summary": "Foreign purchase the customer does not recognize.",
    }
    return HandoffPacket(**{**fields, **overrides})


def test_report_contains_the_verified_facts(renderer):
    report = renderer.markdown(packet())
    assert report.startswith("# Reporte de derivación HND-ABC")
    assert "`escalate`" in report and "`C4_high_risk`" in report
    assert "38,900.00 ARS" in report and "0.91" in report
    assert "| sospecha_robo | True | 0.82 |" in report
    assert "Did the customer travel recently?" in report


def test_model_summary_is_labelled_as_unverified(renderer):
    report = renderer.markdown(packet())
    assert "(generado por IA, no verificado)" in report
    assert "> Foreign purchase the customer does not recognize." in report


def test_pipes_in_data_cannot_break_the_table(renderer):
    assert "TIENDA / NUEVA" in renderer.markdown(packet())


def test_report_in_portuguese_and_unknown_language_falls_back(renderer):
    assert renderer.markdown(packet(), "pt").startswith("# Relatório de encaminhamento")
    assert renderer.markdown(packet(), "fr").startswith("# Reporte de derivación")


def test_empty_sections_say_so(renderer):
    report = renderer.markdown(
        packet(verified_evidence=[], actions_taken=[], open_questions=[], model_summary=None)
    )
    assert "Sin evidencia de transacciones asociada." in report
    assert "El modelo no entregó resumen." in report


def test_default_language_must_have_labels():
    with pytest.raises(ValueError):
        HandoffReportRenderer({"pt": {}}, "es")
