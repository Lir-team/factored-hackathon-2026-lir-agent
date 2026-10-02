"""Tests for the agent tools backed by DuckDB over parquet."""

import pathlib

import duckdb
import pytest

from Agent.agent.tools.customers import get_customer_by_id
from Agent.config import get_settings
from Agent.infrastructure.customers import CustomerRepository


@pytest.fixture
def data_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    staging = tmp_path / "staging"
    staging.mkdir()
    duckdb.sql(
        """
        SELECT * FROM (VALUES
            ('CLI-1', 'Ana', 'Perez', 'ana@example.com', 'active', 'CO', DATE '1990-01-02'),
            ('CLI-2', 'Luis', 'Gomez', 'luis@example.com', 'inactive', 'MX', DATE '1985-05-06')
        ) t(customer_id, first_name, last_name, email, customer_status, country, date_of_birth)
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


def test_tool_returns_customer() -> None:
    result = get_customer_by_id("CLI-1")
    assert result["found"] is True
    assert result["customer"]["email"] == "ana@example.com"
    assert result["customer"]["date_of_birth"] == "1990-01-02"


def test_tool_reports_missing_customer() -> None:
    result = get_customer_by_id("CLI-404")
    assert result == {"found": False, "customer_id": "CLI-404"}


def test_tool_normalizes_the_id() -> None:
    assert get_customer_by_id("  cli-1 ")["found"] is True


@pytest.mark.parametrize("missing", ["", "   "])
def test_tool_rejects_a_missing_id(missing: str) -> None:
    result = get_customer_by_id(missing)
    assert result["found"] is False
    assert "customer_id is required" in result["error"]
