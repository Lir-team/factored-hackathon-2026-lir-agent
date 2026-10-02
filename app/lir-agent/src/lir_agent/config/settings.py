"""Typed application settings.

Configuration is read once, validated once, and exposed through a single
accessor. Values come from the process environment, then from `.env` in the
working directory (run commands from `app/lir-agent/`). A missing or malformed
value raises on first access instead of failing mid-conversation.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
        # `.env` also holds keys read by other libraries (decision layer, LiteLLM).
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

    session_ttl_minutes: int = Field(default=15, gt=0)
    dev_customer_id: str | None = Field(
        default=None,
        description=(
            "Seeds a clearly labeled trusted TEST session (local `adk web` only). "
            "Never set in production."
        ),
    )

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
