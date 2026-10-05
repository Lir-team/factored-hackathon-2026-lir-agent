"""The HTML case file: summary first, charts against the policy's thresholds, nothing unescaped."""

from datetime import UTC, datetime

import pytest

from lir_agent.application.case_file import CaseFileRenderer
from lir_agent.domain.case_intake import CaseReport
from lir_agent.domain.models import HandoffResolution, Lane, Outcome
from lir_agent.infrastructure.resources import ResourceLoader
from tests.application.test_handoff_report import packet

FRAUD = Outcome(lane=Lane.ESCALATE, rule_id="C1_high_fraud_score", reason="r", policy_version="v")


@pytest.fixture
def renderer(settings) -> CaseFileRenderer:
    loader = ResourceLoader()
    return CaseFileRenderer(
        loader.load_mapping(settings.case_file_path),
        loader.load_policy(settings.policy_path).config,
        "es",
    )


def fraud_case(**overrides):
    evidence = packet().verified_evidence[0].model_copy(update={"fraud_score": 89.0, "amount_usd": 400.0})
    report = CaseReport(
        category="unrecognized_charge", fraud_suspected=True, freeze_card_requested=False,
        card_in_possession="yes", shared_credentials="no", case_id="case-1",
    )
    return packet(case_outcome=FRAUD, verified_evidence=[evidence], case_report=report, **overrides)


def test_the_page_leads_with_why_priority_and_the_suggested_action(renderer):
    page = renderer.html(fraud_case())

    assert "Expediente" in page and "HND-ABC" in page
    assert "Prioridad alta" in page
    assert "El score de fraude del cargo supera el umbral de la política." in page
    assert "Revisar la transacción con el equipo de fraude" in page
    assert "Abierto: esperando a un especialista" in page


def test_the_form_reads_as_sentences_not_flags(renderer):
    page = renderer.html(fraud_case())

    assert "No reconoce un cargo" in page
    assert "Reporta fraude o un uso que no hizo</dt><dd>Sí" in page
    assert "fraud_suspected=True" not in page


def test_the_charts_use_the_policys_thresholds(renderer):
    page = renderer.html(fraud_case())

    assert "límite de la política 70" in page  # C1_high_fraud_score: fraud_score gte 70
    assert "límite de la política 1,000" in page  # C8_high_amount: amount_usd gte 1000
    assert "class='over'>89</strong>" in page  # above the fraud limit, drawn as such
    assert "class='under'>400</strong>" in page  # under the amount limit


def test_customer_text_is_escaped(renderer):
    page = renderer.html(fraud_case(customer_request="<script>alert(1)</script>"))

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_a_resolved_case_shows_who_decided_and_portuguese_is_available(renderer):
    resolution = HandoffResolution(
        accepted=True, resolved_by="ana@bank", resolved_at=datetime(2026, 10, 5, tzinfo=UTC)
    )

    page = renderer.html(fraud_case(resolution=resolution), "pt")

    assert 'lang="pt"' in page
    assert "Resolvido: reclamação aceita por ana@bank" in page


def test_an_unknown_language_falls_back_to_the_default(renderer):
    assert 'lang="es"' in renderer.html(fraud_case(), "fr")
