"""Customer lookups over the parquet data lake.

DuckDB reads the parquet files in place: there is no database server and no
copy of the data. One connection per repository instance is enough; opening a
connection per call would re-parse file metadata every time.
"""

import logging
import time
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)


class CustomerRepository:
    """Read-only access to the `customers` dimension in staging."""

    def __init__(self, data_dir: Path) -> None:
        """Bind the repository to a data lake root and register the view."""
        self._conn = duckdb.connect()
        parquet = data_dir / "staging" / "customers.parquet"
        # The file path is interpolated once, under our control; user input
        # only ever travels as a bound parameter.
        self._conn.execute(
            f"CREATE VIEW customers AS SELECT * FROM read_parquet('{parquet}')"
        )
        logger.info("Customer repository reading %s", parquet)

    def get_by_id(self, customer_id: str) -> dict[str, Any] | None:
        """Return the customer row as a dict, or None when absent."""
        started = time.perf_counter()
        cursor = self._conn.execute(
            "SELECT * FROM customers WHERE customer_id = ? LIMIT 1", [customer_id]
        )
        row = cursor.fetchone()
        logger.debug(
            "Customer lookup took %.1f ms",
            (time.perf_counter() - started) * 1000,
            extra={"customer_id": customer_id},
        )
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row, strict=True))
