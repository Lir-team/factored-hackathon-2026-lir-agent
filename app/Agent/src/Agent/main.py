"""Application entry point."""

from .agent.agent import build_root_agent
from .config import get_settings
from .logging_config import configure_logging


def main() -> None:
    """Run the application."""
    settings = get_settings()
    configure_logging(settings)
    agent = build_root_agent()
    print(
        f"running in {settings.environment} at log level {settings.log_level}; "
        f"agent '{agent.name}' uses {settings.llm_model} via {settings.llm_api_base}"
    )


if __name__ == "__main__":
    main()
