"""TransactionRepository over the team-generated JSON fixture (development and tests)."""

import json
from pathlib import Path

from lir_agent.domain.models import Customer, Transaction


class FixtureTransactionRepository:
    """Repository over the team-generated JSON fixture."""

    def __init__(self, path: Path) -> None:
        """Load and validate the fixture once."""
        data = json.loads(path.read_text(encoding="utf-8"))
        self.name = f"fixture:{path.name}"
        self._customers = {
            row["customer_id"]: Customer.model_validate(row)
            for row in data["customers"]
        }
        self._transactions: dict[str, list[Transaction]] = {}
        for row in data["transactions"]:
            self._transactions.setdefault(row["customer_id"], []).append(
                Transaction.model_validate(row)
            )

    def list_transactions(self, customer_id: str) -> list[Transaction]:
        """Return the customer's transactions, most recent first."""
        return sorted(
            self._transactions.get(customer_id, []),
            key=lambda t: t.transaction_date,
            reverse=True,
        )

    def get_customer(self, customer_id: str) -> Customer | None:
        """Return the customer, or None when the id is unknown."""
        return self._customers.get(customer_id)
