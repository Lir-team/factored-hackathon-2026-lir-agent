"""Builds verifiable evidence for a transaction.

Dates and amounts are compared here in code; they are never compared by Jev or the LLM
(see the Jev 1.13 jaggedness notes).
"""

from datetime import timedelta

from lir_agent.domain.models import Customer, Evidence, Transaction


class CountryResolver:
    """Normalizes country values ("Mexico", "MX") to ISO codes."""

    def __init__(self, aliases: dict[str, list[str]]) -> None:
        """Index every alias of every ISO code."""
        self._index = {
            alias.lower(): code for code, names in aliases.items() for alias in names
        }

    def code(self, value: str | None) -> str | None:
        """ISO code for a country value, or None when unknown."""
        return self._index.get(value.strip().lower()) if value else None


class EvidenceBuilder:
    """Computes the evidence signals of a transaction from the customer's history."""

    def __init__(self, countries: CountryResolver, duplicate_window: timedelta) -> None:
        """Keep the country resolver and the duplicate time window."""
        self._countries = countries
        self._duplicate_window = duplicate_window

    def build(
        self, txn: Transaction, history: list[Transaction], customer: Customer | None
    ) -> Evidence:
        """Evidence for one transaction against the rest of the customer's history."""
        same_merchant = [
            other
            for other in history
            if other.transaction_id != txn.transaction_id
            and txn.merchant_name
            and other.merchant_name == txn.merchant_name
        ]
        prior = [
            other
            for other in same_merchant
            if other.transaction_date < txn.transaction_date
        ]
        duplicates = [
            other.transaction_id
            for other in same_merchant
            if other.amount == txn.amount
            and abs(other.transaction_date - txn.transaction_date)
            <= self._duplicate_window
        ]
        home = self._countries.code(customer.country if customer else None)
        where = self._countries.code(txn.transaction_country)
        return Evidence(
            transaction_id=txn.transaction_id,
            date=txn.transaction_date,
            amount=txn.amount,
            currency=txn.currency,
            amount_usd=txn.amount_usd,
            merchant_name=txn.merchant_name,
            merchant_category=txn.merchant_category,
            status=txn.transaction_status,
            channel=txn.channel,
            merchant_prior_count=len(prior),
            merchant_last_seen=max(
                (other.transaction_date for other in prior), default=None
            ),
            duplicate_of=duplicates,
            transaction_country=where,
            customer_country=home,
            foreign=bool(home and where and home != where),
            fraud_score=txn.fraud_score,
        )
