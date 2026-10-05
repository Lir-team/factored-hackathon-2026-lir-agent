"""Typed application settings.

Configuration is read once, validated once, and exposed through a single
accessor. Values come from the process environment, then from `.env` in the
working directory (run commands from `app/lir-agent/`). A missing or malformed
value raises on first access instead of failing mid-conversation.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parents[1]
RESOURCES_DIR = PACKAGE_DIR / "resources"
APP_DIR = PACKAGE_DIR.parents[1]
REPO_ROOT = APP_DIR.parents[1]
ENV_FILE = ".env"


class Settings(BaseSettings):
    """Values sourced from the process environment, falling back to `.env`."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # `.env` may hold keys this app does not declare; ignore them.
        extra="ignore",
    )

    environment: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # LiteLLM model string, "<provider>/<model>". "ollama_chat/" supports tool calling.
    llm_model: str = "ollama_chat/llama3.1"
    # Base URL of a self-hosted server such as Ollama. Leave empty for hosted providers.
    llm_api_base: str = "http://localhost:11434"
    # Provider API key (OpenAI, Anthropic...). SecretStr keeps it out of reprs and logs.
    llm_api_key: SecretStr | None = None

    # Typed decisions: "default" = Jev when JEV_ENABLED=1, else the keyword baseline;
    # "llm" = an LLM with structured output, falling back to the keyword baseline.
    decisions: Literal["default", "llm"] = "default"
    # LiteLLM model for "llm" decisions; empty reuses LLM_MODEL (same key and base URL).
    decision_llm_model: str | None = None
    # Key for DECISION_LLM_MODEL when it is another provider than LLM_MODEL. Empty: LiteLLM
    # reads the provider's own variable (e.g. OPENROUTER_API_KEY). LLM_API_KEY and
    # LLM_API_BASE are only reused when the decisions reuse LLM_MODEL itself.
    decision_llm_api_key: SecretStr | None = None
    # Optional LiteLLM reasoning_effort for "llm" decisions ("none" cut gpt-6-luna's p50 from
    # ~2.5 s to ~1.9 s with the same answers on a 5-message check). Empty: provider default.
    decision_llm_reasoning_effort: str | None = None
    # Jev (TypeSafe) on Cloudflare Workers AI leads the "default" decisions only when
    # enabled and both credentials are set; otherwise the keyword baseline decides.
    jev_enabled: bool = False
    cloudflare_account_id: str | None = None
    cloudflare_api_token: SecretStr | None = None

    # Root of the data lake (staging/, curated/ ...).
    data_dir: Path = REPO_ROOT / "data"
    store: Literal["auto", "duckdb", "fixture"] = Field(
        default="auto",
        description="'auto' uses DuckDB when the pipeline produced staging files, else the fixture.",
    )

    fixture_path: Path = RESOURCES_DIR / "fixtures" / "demo.json"
    policy_path: Path = RESOURCES_DIR / "policy.yaml"
    reference_path: Path = RESOURCES_DIR / "reference.yaml"
    instruction_path: Path = RESOURCES_DIR / "prompts" / "instruction.md"
    turn_guidance_path: Path = RESOURCES_DIR / "prompts" / "turn_guidance.yaml"
    customer_messages_path: Path = RESOURCES_DIR / "prompts" / "customer_messages.yaml"
    handoff_report_path: Path = RESOURCES_DIR / "reports" / "handoff_report.yaml"
    approval_labels_path: Path = RESOURCES_DIR / "approvals.yaml"
    # Web link to an approval card, e.g. "https://lir-web.example/aprobar.html?id={approval_id}&t={token}".
    # Unset: no web link is issued (surfaces such as Telegram still present the request).
    approval_link_template: str | None = None
    # Step-up: approving needs the customer signed in to the bank (the JWT API Gateway
    # verifies), not only the link or the chat. Telegram then links to the web card instead
    # of approving with a button, so whoever holds the Telegram account cannot approve.
    approval_requires_sign_in: bool = False
    # Language of the handoff report when the request does not ask for one.
    report_default_language: str = "es"
    audit_path: Path = APP_DIR / ".audit" / "audit.jsonl"
    # Where audit entries go: a local JSONL file, or stdout as structured JSON (Cloud Run
    # forwards it to Cloud Logging).
    audit_sink: Literal["jsonl", "stdout"] = "jsonl"

    session_ttl_minutes: int = Field(default=15, gt=0)
    reference_date: date | None = Field(
        default=None,
        description=(
            "Date the agent treats as today, so 'June 11' or 'last week' resolve to a full "
            "date. Empty means the current date; set it to replay a static data snapshot."
        ),
    )
    dev_customer_id: str | None = Field(
        default=None,
        description=(
            "Seeds a clearly labeled trusted TEST session (local `adk web` only). "
            "Never set in production."
        ),
    )

    # ---- HTTP API (interface/http) -------------------------------------------------------
    # Cloud Run injects PORT; uvicorn listens on every interface inside the container.
    http_host: str = "0.0.0.0"
    port: int = Field(default=8080, gt=0, lt=65536)
    # Header carrying the operator identity verified upstream. IAP sets it to
    # "accounts.google.com:<email>" and strips any value sent by the client.
    identity_header: str = "X-Goog-Authenticated-User-Email"
    # Reject requests without the identity header. Disable only for local runs without IAP.
    require_identity: bool = True
    # Operator id used when `require_identity` is off and no header is present.
    local_operator: str = "local-operator"
    # How HTTP sessions are authenticated, recorded in the session state and audit log.
    http_auth_method: str = "iap_operator"
    # Upper bound on one customer message, to cap cost and abuse.
    max_message_chars: int = Field(default=2000, gt=0)
    # Return how each turn was decided (decision model, probabilities, lanes, tools, cost)
    # with the reply. Only for the operator API behind IAP, never for customer channels.
    expose_trace: bool = False
    # Serve the specialist back office page (GET /backoffice). Only for the operator API
    # behind IAP: the page calls the approval review routes with the IAP identity.
    backoffice_enabled: bool = False
    backoffice_path: Path = RESOURCES_DIR / "backoffice" / "index.html"
    # Team notice for each handoff (Slack incoming webhook); empty: no notice. The notice
    # carries the handoff id, rule and lane, never customer data.
    slack_webhook_url: SecretStr | None = None
    # Public URL of this service, for the case file link in the notice.
    public_base_url: str | None = None
    slack_notice_path: Path = RESOURCES_DIR / "reports" / "slack_notice.yaml"
    # Comma-separated Slack member ids that take handoffs in rotation.
    slack_assignees: str = ""
    slack_fallback_mention: str = "<!here>"
    # Approval outcomes emailed to the customer (SMTP, e.g. Gmail with an app password).
    # The dataset has no real customer addresses: the demo sends to CUSTOMER_EMAIL_OVERRIDE.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, gt=0, lt=65536)
    smtp_user: str | None = None
    smtp_app_password: SecretStr | None = None
    customer_email_override: str | None = None
    # Upper bound of ?limit= on GET /v1/me/transactions.
    transactions_max_limit: int = Field(default=50, gt=0, le=500)
    # Accepted customer ids (checked before a session is created).
    customer_id_pattern: str = r"^[A-Z0-9-]{1,64}$"
    # Shown in the API docs and in validation errors: what a customer id looks like, and one
    # that exists in the configured data (the demo fixture locally, real data on Cloud Run).
    customer_id_format: str = "the bank customer id, e.g. CLI-0A1B2C3D4E5F (not a name)"
    api_example_customer_id: str = "CLI-DEMO-001"
    api_example_message: str = "No reconozco un cargo de 245.50 en OXXO"
    # Comma-separated browser origins allowed to call the API (CORS). Empty: CORS off,
    # as on Cloud Run where API Gateway answers it. Set it for local runs with lir-web.
    cors_origins: str = ""

    # ---- Case intake (`POST /v1/cases`) --------------------------------------------------
    # API Gateway verifies the customer JWT and forwards its claims in this header
    # (base64url JSON). Without it, `require_identity` decides: 401, or (local runs only)
    # the payload's `customer.customer_id` is trusted.
    customer_identity_header: str = "X-Apigateway-Api-Userinfo"
    # JWT claim holding the customer id.
    customer_claim: str = "sub"
    # Where accepted cases are archived: a local directory, or a Cloud Storage bucket.
    cases_inbox: Literal["local", "gcs"] = "local"
    cases_bucket: str = "cases-inbox"
    cases_local_dir: Path = APP_DIR / ".cases"
    # Bot behind the Telegram start link (without "@"). Empty: no link is issued.
    telegram_bot_username: str | None = None
    # How long a Telegram start link stays usable.
    start_token_ttl_minutes: int = Field(default=1440, gt=0)
    # How accepted cases reach the agent: "pubsub" publishes them to CASES_TOPIC (the
    # emulator when PUBSUB_EMULATOR_HOST is set); "none" keeps them in memory, unworked.
    cases_publisher: Literal["none", "pubsub"] = "none"
    # Google Cloud project of the topic (required with CASES_PUBLISHER=pubsub).
    google_cloud_project: str | None = None
    cases_topic: str = "lir-cases"

    # ---- Case processing (`POST /pubsub/push`) -------------------------------------------
    # Pub/Sub push signs each call with an OIDC token for this audience (the push
    # subscription's audience). Without it, and with verification on, the route is absent.
    pubsub_push_audience: str | None = None
    # Service account the push subscription signs as; empty accepts any Google-signed token
    # for the audience.
    pubsub_push_service_account: str | None = None
    # Turn off only for the local emulator, which sends no token.
    pubsub_verify_token: bool = True

    # ---- Telegram channel (`POST /channels/telegram`) ------------------------------------
    # Bot API token (from BotFather) and the `secret_token` registered with `setWebhook`.
    # Without both, the webhook route does not exist (404).
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    # Conversations opened from a case (Telegram): an idle timeout extended by every message,
    # never past the ceiling. Then the customer files a new case, signing in to the bank again.
    case_session_idle_minutes: int = Field(default=30, gt=0)
    case_session_max_minutes: int = Field(default=1440, gt=0)

    # ---- Case store (receipts, start tokens, chat links, conversations, replies) ---------
    # "memory" is lost on restart and not shared between instances (tests, single-instance
    # local runs); "firestore" keeps it in Firestore (the emulator when
    # FIRESTORE_EMULATOR_HOST is set) in project GOOGLE_CLOUD_PROJECT.
    case_store: Literal["memory", "firestore"] = "memory"
    # Disputes and handoffs: "memory" lives per instance; "firestore" is shared by the
    # operator and case flow services, so case files open from either.
    case_repository: Literal["memory", "firestore"] = "memory"
    # Approval requests: "firestore" lets the operator service decide the requests the case
    # flow service creates (specialist reviews in the back office).
    approval_repository: Literal["memory", "firestore"] = "memory"
    firestore_database: str = "(default)"
    # Start of every collection name, to keep environments apart in one database.
    firestore_collection_prefix: str = "lir_"

    @field_validator("reference_date", mode="before")
    @classmethod
    def _empty_reference_date_is_today(cls, value: object) -> object:
        """`REFERENCE_DATE=` left empty in `.env` means the current date, not an error."""
        return None if value == "" else value

    def today(self) -> date:
        """The agent's 'today': `reference_date` when set, otherwise the current date."""
        return self.reference_date or date.today()

    @property
    def staging_dir(self) -> Path:
        """Directory with the staged parquet tables produced by the data pipeline."""
        return self.data_dir / "staging"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Tests call `get_settings.cache_clear()` after patching the environment.
    """
    return Settings()
