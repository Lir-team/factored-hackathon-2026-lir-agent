"""Writes the demo fixture as parquet with the contract's DECIMAL types, as the pipeline does."""

import duckdb

from lir_agent.infrastructure.persistence import (
    DuckDbTransactionRepository,
    FixtureTransactionRepository,
)
from tests.support import CUSTOMER

TEXT_COLUMNS = (
    "product_id",
    "transaction_type",
    "transaction_category",
    "currency",
    "channel",
    "merchant_category",
    "transaction_country",
    "transaction_city",
)


def write_staging(tmp_path, settings) -> int:
    rows = FixtureTransactionRepository(settings.fixture_path).list_transactions(
        CUSTOMER
    )
    con = duckdb.connect()
    con.execute(
        "create table t (customer_id varchar, transaction_id varchar, transaction_date timestamp, "
        "amount decimal(15,2), amount_usd decimal(15,2), fraud_score decimal(5,2), "
        "merchant_name varchar, transaction_status varchar, is_fraud boolean)"
    )
    con.executemany(
        "insert into t values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            [
                CUSTOMER,
                r.transaction_id,
                r.transaction_date,
                r.amount,
                r.amount_usd,
                r.fraud_score,
                r.merchant_name,
                r.transaction_status,
                False,
            ]
            for r in rows
        ],
    )
    extra = ", ".join(f"null::varchar as {column}" for column in TEXT_COLUMNS)
    con.execute(
        f"copy (select *, {extra} from t) to '{(tmp_path / 'transactions.parquet').as_posix()}'"
    )
    return len(rows)


def test_decimal_columns_come_back_as_float(tmp_path, settings):
    expected = write_staging(tmp_path, settings)
    result = DuckDbTransactionRepository(tmp_path).list_transactions(CUSTOMER)
    assert len(result) == expected
    assert isinstance(result[0].amount, float)
    assert isinstance(result[0].fraud_score, float)
    assert not hasattr(result[0], "is_fraud")  # evaluation label never loaded
