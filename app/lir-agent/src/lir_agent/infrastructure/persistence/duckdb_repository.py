"""TransactionRepository over the staged parquet produced by the data pipeline (`data/staging`)."""

from pathlib import Path

import duckdb

from lir_agent.domain.models import Customer, Transaction

# DECIMAL columns in the data contracts; cast to DOUBLE so the domain never mixes Decimal and float.
NUMERIC_COLUMNS = frozenset({"amount", "amount_usd", "fraud_score"})


def _select_list(columns: list[str]) -> str:
    return ", ".join(
        f"cast({c} as double) as {c}" if c in NUMERIC_COLUMNS else c for c in columns
    )


class DuckDbTransactionRepository:
    """Read-only repository over the staged parquet tables."""

    def __init__(self, staging_dir: Path) -> None:
        """Bind to a staging directory; DuckDB reads the parquet in place."""
        self.name = f"duckdb:{staging_dir.as_posix()}"
        self._connection = duckdb.connect()
        self._transactions_path = (staging_dir / "transactions.parquet").as_posix()
        self._customers_path = (staging_dir / "customers.parquet").as_posix()

    def list_transactions(self, customer_id: str) -> list[Transaction]:
        """Return the customer's transactions, most recent first (never `is_fraud`)."""
        columns = list(
            Transaction.model_fields
        )  # never selects `is_fraud` (evaluation label)
        rows = self._query(
            f"select {_select_list(columns)} from read_parquet('{self._transactions_path}') "
            "where customer_id = ? order by transaction_date desc",
            [customer_id],
        )
        return [Transaction.model_validate(row) for row in rows]

    def get_customer(self, customer_id: str) -> Customer | None:
        """Return the customer, or None when the id is unknown."""
        rows = self._query(
            f"select {_select_list(list(Customer.model_fields))} from read_parquet('{self._customers_path}') "
            "where customer_id = ?",
            [customer_id],
        )
        return Customer.model_validate(rows[0]) if rows else None

    def _query(self, sql: str, params: list) -> list[dict]:
        cursor = (
            self._connection.cursor()
        )  # one cursor per query: connections are not shared across threads
        cursor.execute(sql, params)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
