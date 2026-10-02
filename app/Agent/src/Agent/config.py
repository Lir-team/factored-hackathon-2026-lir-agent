"""Typed application settings.

Configuration is read once, validated once, and exposed through a single
accessor. Reading os.environ at the point of use scatters defaults across the
codebase and defers failure until the unlucky code path actually runs.
"""

from functools import lru_cache
from typing import Literal

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
    # Base URL of the Ollama server. Swap it to target a remote host or
    # another OpenAI-compatible provider without touching code.
    llm_api_base: str = "http://localhost:11434"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    The cache keeps validation to a single pass and gives tests a seam:
    call `get_settings.cache_clear()` after patching the environment.
    """
    return Settings()
