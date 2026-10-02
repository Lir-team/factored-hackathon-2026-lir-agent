"""Single logging configuration for the whole application.

Entry points call `configure_logging` once at startup. Every other module only
does `logger = logging.getLogger(__name__)` and never attaches handlers or sets
levels itself, so format, destination and verbosity are decided here alone.
"""

import logging.config

from .config import Settings, get_settings

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Dependencies that log heavily at INFO. They are held at WARNING so the
# application's own messages stay readable, and only opened up when debugging.
NOISY_LOGGERS = ("LiteLLM", "httpx", "httpcore")


def configure_logging(settings: Settings | None = None) -> None:
    """Configure the root logger from settings.

    Safe to call more than once: `dictConfig` replaces the root handlers
    instead of stacking new ones.
    """
    settings = settings or get_settings()
    noisy_level = "DEBUG" if settings.log_level == "DEBUG" else "WARNING"
    logging.config.dictConfig(
        {
            "version": 1,
            # Loggers created at import time, before this runs, must keep working.
            "disable_existing_loggers": False,
            "formatters": {"default": {"format": LOG_FORMAT}},
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"level": settings.log_level, "handlers": ["stderr"]},
            "loggers": {name: {"level": noisy_level} for name in NOISY_LOGGERS},
        }
    )
