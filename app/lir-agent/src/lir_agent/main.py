"""Application entry point: prints the resolved configuration."""

from lir_agent.config.settings import get_settings
from lir_agent.container import build_repository
from lir_agent.logging_config import configure_logging


def main() -> None:
    """Show which model, data store and environment the agent would use."""
    settings = get_settings()
    configure_logging(settings)
    repository = build_repository(settings)
    print(
        f"running in {settings.environment} at log level {settings.log_level}; "
        f"model {settings.llm_model} via {settings.llm_api_base or 'provider default'}; "
        f"data from {repository.name}"
    )


if __name__ == "__main__":
    main()
