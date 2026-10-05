"""Serve the HTTP API with uvicorn (the container entry point on Cloud Run)."""

import uvicorn

from lir_agent.config.settings import get_settings
from lir_agent.interface.http import create_app
from lir_agent.logging_config import configure_logging


def main() -> None:
    """Start the API on the configured host and port."""
    settings = get_settings()
    configure_logging(settings)
    if settings.trace_to_cloud:
        if not settings.google_cloud_project:
            raise ValueError("TRACE_TO_CLOUD needs GOOGLE_CLOUD_PROJECT")
        from lir_agent.infrastructure.observability import configure_cloud_trace

        configure_cloud_trace(settings.google_cloud_project, settings.trace_service_name)
    uvicorn.run(
        create_app(settings),
        host=settings.http_host,
        port=settings.port,
        log_config=None,  # keep the application's logging configuration
    )


if __name__ == "__main__":
    main()
