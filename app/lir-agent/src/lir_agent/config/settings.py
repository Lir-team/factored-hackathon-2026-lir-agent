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
    # Accepted customer ids (checked before a session is created).
    customer_id_pattern: str = r"^[A-Z0-9-]{1,64}$"
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
    # How long a case's conversation stays valid (7 days), counted from its delivery.
    case_session_ttl_minutes: int = Field(default=10080, gt=0)

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
