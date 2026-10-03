"""Serve the HTTP API with uvicorn (the container entry point on Cloud Run)."""

import uvicorn
from dotenv import load_dotenv

from lir_agent.config.settings import ENV_FILE, get_settings
from lir_agent.interface.http import create_app
from lir_agent.logging_config import configure_logging


def main() -> None:
    """Start the API on the configured host and port."""
    # LiteLLM and the decision layer read their keys from the environment.
    load_dotenv(ENV_FILE, override=False)
    settings = get_settings()
    configure_logging(settings)
    uvicorn.run(
        create_app(settings),
        host=settings.http_host,
        port=settings.port,
        log_config=None,  # keep the application's logging configuration
    )


if __name__ == "__main__":
    main()
