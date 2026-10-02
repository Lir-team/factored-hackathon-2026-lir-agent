"""Tests for the agent tools backed by DuckDB over parquet."""

import logging
import pathlib
import types

import duckdb
import pytest
from google.adk.tools import FunctionTool, ToolContext

from Agent.agent.hardening.guardrails.authentication import (
    CUSTOMER_ID_STATE_KEY,
    UnauthenticatedSessionError,
)
from Agent.agent.tools.customers import MODEL_VISIBLE_FIELDS, get_my_customer_profile
from Agent.config import get_settings
from Agent.infrastructure.customers import CustomerRepository


@pytest.fixture
def data_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    staging = tmp_path / "staging"
    staging.mkdir()
    duckdb.sql(
        """
        SELECT * FROM (VALUES
            ('CLI-1', 'Ana', 'Perez', 'ana@example.com', 'active', 'CO', DATE '1990-01-02',
             'premium', TIMESTAMP '2020-03-04 05:06:07', true),
            ('CLI-2', 'Luis', 'Gomez', 'luis@example.com', 'inactive', 'MX', DATE '1985-05-06',
             'mass', TIMESTAMP '2021-01-01 00:00:00', false)
        ) t(customer_id, first_name, last_name, email, customer_status, country, date_of_birth,
            segment, registration_date, accepts_marketing)
        """
    ).write_parquet(str(staging / "customers.parquet"))
    return tmp_path


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch, data_dir: pathlib.Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()


def test_repository_returns_customer_fields(data_dir: pathlib.Path) -> None:
    repo = CustomerRepository(data_dir)
    customer = repo.get_by_id("CLI-2")
    assert customer is not None
    assert customer["first_name"] == "Luis"
    assert customer["customer_status"] == "inactive"


def test_repository_returns_none_for_unknown_id(data_dir: pathlib.Path) -> None:
    assert CustomerRepository(data_dir).get_by_id("CLI-404") is None


def test_repository_is_safe_against_injection(data_dir: pathlib.Path) -> None:
    assert CustomerRepository(data_dir).get_by_id("' OR 1=1 --") is None


def session(customer_id: object) -> ToolContext:
    """Fake tool context whose session state holds the authenticated ID."""
    return types.SimpleNamespace(state={CUSTOMER_ID_STATE_KEY: customer_id})  # type: ignore[return-value]


def test_tool_returns_the_session_customer() -> None:
    result = get_my_customer_profile(session("CLI-1"))
    assert result == {
        "found": True,
        "customer": {
            "first_name": "Ana",
            "segment": "premium",
            "customer_status": "active",
            "registration_date": "2020-03-04T05:06:07",
            "country": "CO",
            "accepts_marketing": True,
        },
    }


def test_tool_never_returns_fields_outside_the_allowlist() -> None:
    result = get_my_customer_profile(session("CLI-1"))
    assert set(result["customer"]) <= set(MODEL_VISIBLE_FIELDS)
    # PII present in the source row must not reach the model.
    for pii in ("Perez", "ana@example.com", "1990-01-02"):
        assert pii not in str(result)


def test_tool_does_not_return_the_customer_id() -> None:
    result = get_my_customer_profile(session("CLI-1"))
    assert "customer_id" not in result["customer"]
    assert "CLI-1" not in str(result)


def test_tool_reports_unknown_customer_without_echoing_the_id() -> None:
    result = get_my_customer_profile(session("CLI-404"))
    assert result == {"found": False}


@pytest.mark.parametrize("bad_id", [None, "", "   "])
def test_tool_without_authenticated_customer_raises(bad_id: str | None) -> None:
    with pytest.raises(UnauthenticatedSessionError):
        get_my_customer_profile(session(bad_id))


def test_tool_without_state_key_raises() -> None:
    context = types.SimpleNamespace(state={})
    with pytest.raises(UnauthenticatedSessionError):
        get_my_customer_profile(context)  # type: ignore[arg-type]


def test_tool_logs_found_lookup_without_customer_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="Agent"):
        get_my_customer_profile(session("CLI-1"))

    assert "CLI-1" in caplog.text
    # Customer records are PII and must never reach the logs.
    assert "ana@example.com" not in caplog.text
    assert "Ana" not in caplog.text


def test_tool_logs_missing_customer_at_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="Agent.agent.tools.customers"):
        get_my_customer_profile(session("CLI-404"))

    assert [(r.levelno, r.customer_id) for r in caplog.records] == [  # type: ignore[attr-defined]
        (logging.INFO, "CLI-404")
    ]


def test_model_facing_declaration_has_no_parameters() -> None:
    # The model must not be able to supply or change the customer ID.
    declaration = FunctionTool(get_my_customer_profile)._get_declaration()
    assert declaration is not None
    assert declaration.parameters is None


def test_repository_logs_query_timing_at_debug(
    data_dir: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = CustomerRepository(data_dir)

    with caplog.at_level(logging.DEBUG, logger="Agent.infrastructure.customers"):
        repo.get_by_id("CLI-1")

    assert [r.levelno for r in caplog.records] == [logging.DEBUG]
    assert "ms" in caplog.text
