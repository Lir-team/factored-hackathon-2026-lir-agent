import logging

import pytest

from lir_agent.config.settings import Settings
from lir_agent.logging_config import NOISY_LOGGERS, configure_logging


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_root_logger_uses_the_configured_level():
    configure_logging(Settings(log_level="DEBUG"))

    assert logging.getLogger().level == logging.DEBUG


def test_module_loggers_inherit_the_single_configuration(capsys):
    configure_logging(Settings(log_level="INFO"))

    logging.getLogger("lir_agent.some.module").info("hello")
    logging.getLogger("lir_agent.some.module").debug("hidden")

    err = capsys.readouterr().err
    assert "INFO" in err
    assert "lir_agent.some.module" in err
    assert "hello" in err
    assert "hidden" not in err


def test_calling_twice_does_not_duplicate_handlers():
    configure_logging(Settings())
    configure_logging(Settings())

    assert len(logging.getLogger().handlers) == 1


def test_third_party_loggers_are_quieted_unless_debugging():
    configure_logging(Settings(log_level="INFO"))
    assert all(
        logging.getLogger(name).level == logging.WARNING for name in NOISY_LOGGERS
    )

    configure_logging(Settings(log_level="DEBUG"))
    assert all(logging.getLogger(name).level == logging.DEBUG for name in NOISY_LOGGERS)
