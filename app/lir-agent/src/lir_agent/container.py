"""Composition root: builds every dependency from settings.

Tests pass overrides instead of patching globals.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from decision_layer import (
    ChainDecisionModel,
    DecisionModel,
    JevClient,
    KeywordDecisionModel,
)
from google.cloud import firestore

from lir_agent.application.handoff_report import HandoffReportRenderer
from lir_agent.application.ports import (
    ApprovalRepository,
    ApprovalSurface,
    AuditSink,
    CaseInbox,
    CasePublisher,
    CaseRepository,
    CaseStore,
    ChatChannel,
    HandoffNotifier,
    TransactionRepository,
)
from lir_agent.application.presenter import LlmPresenter
from lir_agent.application.use_cases import (
    AnswerApprovalButton,
    DecideApproval,
    FindCandidateTransactions,
    GatherTransactionEvidence,
    GetCustomerProfile,
    OpenDisputeAction,
    PresentApprovals,
    RequestActionApproval,
    RequestApproval,
    RequestHandoff,
    RouteTurn,
    SubmitCase,
    VerifyApprovalLink,
)
from lir_agent.config.settings import Settings
from lir_agent.domain.dispute_guard import DisputeGuard
from lir_agent.domain.evidence import EvidenceBuilder
from lir_agent.domain.policy import PolicyEngine
from lir_agent.infrastructure.approvals import (
    InMemoryApprovalRepository,
    TelegramApprovalSurface,
)
from lir_agent.infrastructure.audit import JsonlAuditSink, StdoutAuditSink
from lir_agent.infrastructure.case_store import FirestoreCaseStore, InMemoryCaseStore
from lir_agent.infrastructure.cases import InMemoryCaseRepository
from lir_agent.infrastructure.cases_inbox import GcsCaseInbox, LocalCaseInbox
from lir_agent.infrastructure.decisions import LlmDecisionModel
from lir_agent.infrastructure.persistence import (
    DuckDbTransactionRepository,
    FixtureTransactionRepository,
    RetryingTransactionRepository,
)
from lir_agent.infrastructure.publishing import (
    InMemoryCasePublisher,
    PubSubCasePublisher,
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
    handoff_report: HandoffReportRenderer
    decisions: DecisionModel
    policy: PolicyEngine
    dispute_guard: DisputeGuard
    get_profile: GetCustomerProfile
    find_candidates: FindCandidateTransactions
    gather_evidence: GatherTransactionEvidence
    approvals: ApprovalRepository
    request_action_approval: RequestActionApproval
    present_approvals: PresentApprovals
    decide_approval: DecideApproval
    verify_approval_link: VerifyApprovalLink
    # The customer chat (Telegram) when configured, and its approval buttons.
    messenger: ChatChannel | None
    answer_approval_button: AnswerApprovalButton | None
    request_handoff: RequestHandoff
    route_turn: RouteTurn
    case_store: CaseStore
    submit_case: SubmitCase


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


def build_audit(settings: Settings) -> AuditSink:
    """The audit sink chosen by `AUDIT_SINK`."""
    if settings.audit_sink == "stdout":
        return StdoutAuditSink()
    return JsonlAuditSink(settings.audit_path)


def build_case_inbox(settings: Settings) -> CaseInbox:
    """The case inbox chosen by `CASES_INBOX`."""
    if settings.cases_inbox == "gcs":
        return GcsCaseInbox(settings.cases_bucket)
    return LocalCaseInbox(settings.cases_local_dir)


def build_case_publisher(settings: Settings) -> CasePublisher:
    """The case publisher chosen by `CASES_PUBLISHER`."""
    if settings.cases_publisher == "pubsub":
        if not settings.google_cloud_project:
            raise ValueError("CASES_PUBLISHER=pubsub needs GOOGLE_CLOUD_PROJECT")
        return PubSubCasePublisher(settings.google_cloud_project, settings.cases_topic)
    return InMemoryCasePublisher()


def build_case_store(settings: Settings) -> CaseStore:
    """The case store chosen by `CASE_STORE`."""
    if settings.case_store == "firestore":
        if not settings.google_cloud_project:
            raise ValueError("CASE_STORE=firestore needs GOOGLE_CLOUD_PROJECT")
        client = firestore.Client(
            project=settings.google_cloud_project,
            database=settings.firestore_database,
        )
        return FirestoreCaseStore(client, settings.firestore_collection_prefix)
    return InMemoryCaseStore()


def build_messenger(settings: Settings) -> ChatChannel | None:
    """The Telegram bot when its token and webhook secret are set."""
    token, secret = settings.telegram_bot_token, settings.telegram_webhook_secret
    if not (token and secret and token.get_secret_value() and secret.get_secret_value()):
        return None
    from lir_agent.infrastructure.messaging import TelegramBotMessenger

    return TelegramBotMessenger(token.get_secret_value())


def build_notice_surfaces(
    settings: Settings, approval_labels: Mapping[str, Mapping[str, Any]]
) -> list[ApprovalSurface]:
    """Slack (specialist reviews) and email (customer outcomes), when configured."""
    surfaces: list[ApprovalSurface] = []
    if settings.slack_webhook_url:
        from lir_agent.infrastructure.approvals import SlackApprovalSurface

        assignees = [a.strip() for a in settings.slack_assignees.split(",") if a.strip()]
        surfaces.append(
            SlackApprovalSurface(
                settings.slack_webhook_url.get_secret_value(),
                ResourceLoader().load_mapping(settings.slack_notice_path),
                settings.public_base_url,
                " ".join(f"<@{a}>" for a in assignees) or settings.slack_fallback_mention,
            )
        )
    if settings.smtp_app_password and settings.smtp_user and settings.customer_email_override:
        from lir_agent.infrastructure.approvals import EmailApprovalSurface
        from lir_agent.infrastructure.messaging.email import SmtpEmailSender

        sender = SmtpEmailSender(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_user,
            settings.smtp_app_password.get_secret_value(),
        )
        surfaces.append(
            EmailApprovalSurface(sender, approval_labels, settings.customer_email_override)
        )
    return surfaces


def build_notifier(settings: Settings) -> HandoffNotifier | None:
    """The Slack handoff notice when SLACK_WEBHOOK_URL is set."""
    if not settings.slack_webhook_url:
        return None
    from lir_agent.infrastructure.messaging import SlackHandoffNotifier

    return SlackHandoffNotifier(
        settings.slack_webhook_url.get_secret_value(),
        ResourceLoader().load_mapping(settings.slack_notice_path),
        settings.public_base_url,
        [a.strip() for a in settings.slack_assignees.split(",") if a.strip()],
        settings.slack_fallback_mention,
    )


def build_decisions(
    settings: Settings, completion: Callable[..., Any] | None = None
) -> DecisionModel:
    """The typed decision chain chosen by `DECISIONS`; the keyword baseline is always last.

    `completion` replaces LiteLLM's for the LLM model (tests, or metering in the evals).
    """
    if settings.decisions == "llm":
        # The agent's key and base URL belong to LLM_MODEL's provider: sending them to
        # another provider fails (401) and silently falls back to the baseline.
        reuses_agent_model = not settings.decision_llm_model
        key = settings.decision_llm_api_key or (
            settings.llm_api_key if reuses_agent_model else None
        )
        llm = LlmDecisionModel(
            settings.decision_llm_model or settings.llm_model,
            api_key=key.get_secret_value() if key else None,
            api_base=(settings.llm_api_base or None) if reuses_agent_model else None,
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
    case_inbox: CaseInbox | None = None,
    case_publisher: CasePublisher | None = None,
    case_store: CaseStore | None = None,
    today: Callable[[], date] | None = None,
    approvals: ApprovalRepository | None = None,
    approval_surfaces: Iterable[ApprovalSurface] = (),
    messenger: ChatChannel | None = None,
) -> Container:
    """Build the container; keyword overrides replace real adapters in tests."""
    resources = ResourceLoader()
    policy = resources.load_policy(settings.policy_path)
    # Bounded retries, then "records unavailable": the agent hands off instead of guessing.
    repository = RetryingTransactionRepository(repository or build_repository(settings))
    cases = cases or InMemoryCaseRepository()
    audit = audit or build_audit(settings)
    decisions = decisions or build_decisions(settings)
    presenter = LlmPresenter(policy.config.llm_exposure)
    evidence_builder = EvidenceBuilder(
        resources.load_country_resolver(settings.reference_path),
        duplicate_window=timedelta(
            hours=policy.config.evidence["duplicate_window_hours"]
        ),
    )
    dispute_guard = DisputeGuard()
    request_handoff = RequestHandoff(cases, policy.config, build_notifier(settings))
    case_store = case_store or build_case_store(settings)
    approvals = approvals or InMemoryApprovalRepository()
    approval_labels = resources.load_labels(settings.approval_labels_path)
    messenger = messenger or build_messenger(settings)
    surfaces = [*approval_surfaces, *build_notice_surfaces(settings, approval_labels)]
    if messenger is not None:
        surfaces.append(
            TelegramApprovalSurface(
                case_store, messenger, approval_labels, settings.approval_requires_sign_in
            )
        )
    actions = {"open_dispute": OpenDisputeAction(cases, dispute_guard)}
    present_approvals = PresentApprovals(
        approvals, surfaces, audit, settings.approval_link_template
    )
    decide_approval = DecideApproval(
        approvals, actions, present_approvals, surfaces, audit
    )
    return Container(
        settings=settings,
        resources=resources,
        repository=repository,
        cases=cases,
        audit=audit,
        handoff_report=HandoffReportRenderer(
            resources.load_labels(settings.handoff_report_path),
            settings.report_default_language,
        ),
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
        approvals=approvals,
        request_action_approval=RequestActionApproval(
            RequestApproval(
                approvals, audit, timedelta(minutes=policy.config.approvals.ttl_minutes)
            ),
            actions,
            policy.config.approvals.actions,
            approval_labels,
        ),
        present_approvals=present_approvals,
        decide_approval=decide_approval,
        verify_approval_link=VerifyApprovalLink(approvals, audit),
        messenger=messenger,
        answer_approval_button=AnswerApprovalButton(
            approvals,
            decide_approval,
            case_store,
            messenger,
            audit,
            approval_labels,
            settings.approval_requires_sign_in,
        )
        if messenger is not None
        else None,
        request_handoff=request_handoff,
        route_turn=RouteTurn(decisions, policy, request_handoff, audit),
        case_store=case_store,
        submit_case=SubmitCase(
            repository,
            case_inbox or build_case_inbox(settings),
            case_publisher or build_case_publisher(settings),
            case_store,
            audit,
            telegram_bot_username=settings.telegram_bot_username,
            start_token_ttl=timedelta(minutes=settings.start_token_ttl_minutes),
        ),
    )
