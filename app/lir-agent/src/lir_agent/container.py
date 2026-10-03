"""Composition root: builds every dependency from settings.

Tests pass overrides instead of patching globals.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from decision_layer import (
    ChainDecisionModel,
    DecisionModel,
    JevClient,
    KeywordDecisionModel,
)

from lir_agent.application.ports import AuditSink, CaseRepository, TransactionRepository
from lir_agent.application.presenter import LlmPresenter
from lir_agent.application.use_cases import (
    FindCandidateTransactions,
    GatherTransactionEvidence,
    GetCustomerProfile,
    OpenDispute,
    RequestHandoff,
    RouteTurn,
)
from lir_agent.config.settings import Settings
from lir_agent.domain.dispute_guard import DisputeGuard
from lir_agent.domain.evidence import EvidenceBuilder
from lir_agent.domain.policy import PolicyEngine
from lir_agent.infrastructure.audit import JsonlAuditSink
from lir_agent.infrastructure.cases import InMemoryCaseRepository
from lir_agent.infrastructure.decisions import LlmDecisionModel
from lir_agent.infrastructure.persistence import (
    DuckDbTransactionRepository,
    FixtureTransactionRepository,
)
from lir_agent.infrastructure.resources import ResourceLoader

STAGED_TRANSACTIONS = "transactions.parquet"


@dataclass(frozen=True)
class Container:
    """Every dependency of the agent, built once."""

    settings: Settings
    resources: ResourceLoader
    repository: TransactionRepository
    cases: CaseRepository
    audit: AuditSink
    decisions: DecisionModel
    policy: PolicyEngine
    dispute_guard: DisputeGuard
    get_profile: GetCustomerProfile
    find_candidates: FindCandidateTransactions
    gather_evidence: GatherTransactionEvidence
    open_dispute: OpenDispute
    request_handoff: RequestHandoff
    route_turn: RouteTurn


def build_repository(settings: Settings) -> TransactionRepository:
    """DuckDB over the staged parquet when available (or forced), else the demo fixture."""
    kind = settings.store
    if kind == "auto":
        kind = (
            "duckdb"
            if (settings.staging_dir / STAGED_TRANSACTIONS).exists()
            else "fixture"
        )
    if kind == "duckdb":
        return DuckDbTransactionRepository(settings.staging_dir)
    return FixtureTransactionRepository(settings.fixture_path)


def build_decisions(
    settings: Settings, completion: Callable[..., Any] | None = None
) -> DecisionModel:
    """The typed decision chain chosen by `DECISIONS`; the keyword baseline is always last.

    `completion` replaces LiteLLM's for the LLM model (tests, or metering in the evals).
    """
    if settings.decisions == "llm":
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        llm = LlmDecisionModel(
            settings.decision_llm_model or settings.llm_model,
            api_key=api_key,
            api_base=settings.llm_api_base or None,
            reasoning_effort=settings.decision_llm_reasoning_effort,
            completion=completion,
        )
        return ChainDecisionModel([llm, KeywordDecisionModel()])
    models: list[DecisionModel] = []
    if (
        settings.jev_enabled
        and settings.cloudflare_account_id
        and settings.cloudflare_api_token
    ):
        models.append(
            JevClient(
                account_id=settings.cloudflare_account_id,
                api_token=settings.cloudflare_api_token.get_secret_value(),
            )
        )
    return ChainDecisionModel([*models, KeywordDecisionModel()])


def build_container(
    settings: Settings,
    *,
    repository: TransactionRepository | None = None,
    cases: CaseRepository | None = None,
    audit: AuditSink | None = None,
    decisions: DecisionModel | None = None,
    today: Callable[[], date] | None = None,
) -> Container:
    """Build the container; keyword overrides replace real adapters in tests."""
    resources = ResourceLoader()
    policy = resources.load_policy(settings.policy_path)
    repository = repository or build_repository(settings)
    cases = cases or InMemoryCaseRepository()
    audit = audit or JsonlAuditSink(settings.audit_path)
    decisions = decisions or build_decisions(settings)
    presenter = LlmPresenter(policy.config.llm_exposure)
    evidence_builder = EvidenceBuilder(
        resources.load_country_resolver(settings.reference_path),
        duplicate_window=timedelta(
            hours=policy.config.evidence["duplicate_window_hours"]
        ),
    )
    dispute_guard = DisputeGuard()
    request_handoff = RequestHandoff(cases, policy.config)
    return Container(
        settings=settings,
        resources=resources,
        repository=repository,
        cases=cases,
        audit=audit,
        decisions=decisions,
        policy=policy,
        dispute_guard=dispute_guard,
        get_profile=GetCustomerProfile(repository, presenter),
        find_candidates=FindCandidateTransactions(
            repository,
            decisions,
            policy.config.search,
            policy.config.decision_keys["merchant"],
            presenter,
            today,
        ),
        gather_evidence=GatherTransactionEvidence(
            repository, evidence_builder, policy, presenter
        ),
        open_dispute=OpenDispute(cases, dispute_guard),
        request_handoff=request_handoff,
        route_turn=RouteTurn(decisions, policy, request_handoff, audit),
    )
