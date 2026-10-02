"""Typed application settings.

Configuration is read once, validated once, and exposed through a single
accessor. Reading os.environ at the point of use scatters defaults across the
codebase and defers failure until the unlucky code path actually runs.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Values sourced from the process environment, falling back to `.env`.

    A missing or malformed required value raises on first access rather than
    silently substituting a default.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `.env` is commonly shared with other tools, so unknown keys are
        # tolerated. Switch to "forbid" to catch typos in a dedicated file.
        extra="ignore",
    )

    environment: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # LiteLLM model string, "<provider>/<model>". The "ollama_chat/" provider
    # uses Ollama's chat endpoint, which is what supports tool calling.
    llm_model: str = "ollama_chat/llama3.1"
    # Base URL of a self-hosted server such as Ollama. Leave empty for hosted
    # providers (OpenAI, Anthropic...) so LiteLLM uses their default endpoint.
    llm_api_base: str = "http://localhost:11434"
    # Provider API key (OpenAI, Anthropic...). Leave unset for Ollama.
    # SecretStr keeps it out of reprs and logs.
    llm_api_key: SecretStr | None = None
    # Root of the data lake (staging/, curated/ ...). Defaults to the `data/`
    # folder at the repository root so the agent works from a fresh clone.
    data_dir: Path = Path(__file__).resolve().parents[4] / "data"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    The cache keeps validation to a single pass and gives tests a seam:
    call `get_settings.cache_clear()` after patching the environment.
    """
    return Settings()
