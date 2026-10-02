"""Use case: find the customer's transactions that match what they describe."""

from dataclasses import dataclass
from datetime import date

from decision_layer import DecisionError, DecisionModel
from decision_layer.questions import d4_merchant

from lir_agent.application.ports import TransactionRepository
from lir_agent.application.presenter import DATA_NOT_INSTRUCTIONS, LlmPresenter
from lir_agent.domain.errors import InvalidSearchCriteriaError
from lir_agent.domain.models import Transaction
from lir_agent.domain.policy import SearchSettings
from lir_agent.domain.session import SessionState

NO_MERCHANT_MATCH = "ninguno"  # option added by decision_layer.questions.d4_merchant
ISO_DATE = "YYYY-MM-DD"


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """What the customer said about the charge, validated."""

    amount: float | None = None
    date_from: date | None = None
    date_to: date | None = None
    merchant_hint: str | None = None

    @classmethod
    def parse(
        cls,
        amount: float | None,
        date_from: str | None,
        date_to: str | None,
        merchant_hint: str | None,
    ) -> "SearchCriteria":
        """Build criteria from tool arguments, rejecting malformed dates."""
        return cls(
            amount,
            cls._date(date_from, "date_from"),
            cls._date(date_to, "date_to"),
            merchant_hint,
        )

    @staticmethod
    def _date(value: str | None, field: str) -> date | None:
        try:
            return date.fromisoformat(value) if value else None
        except ValueError as error:
            raise InvalidSearchCriteriaError(field, ISO_DATE) from error


class FindCandidateTransactions:
    """Use case: find the customer's transactions that match a description."""

    def __init__(
        self,
        repository: TransactionRepository,
        decisions: DecisionModel,
        settings: SearchSettings,
        merchant_question_key: str,
        presenter: LlmPresenter,
    ) -> None:
        """Keep the data source, decision model, search settings and presenter."""
        self._repository = repository
        self._decisions = decisions
        self._settings = settings
        self._merchant_key = merchant_question_key
        self._presenter = presenter

    def execute(self, session: SessionState, criteria: SearchCriteria) -> dict:
        """Return minimized candidates, most recent first, with session references."""
        matches = [
            txn
            for txn in self._repository.list_transactions(session.require_customer_id())
            if self._matches(txn, criteria)
        ]
        if criteria.merchant_hint and len(matches) > 1:
            matches = self._rank_by_merchant(criteria.merchant_hint, matches)
        shown = matches[: self._settings.max_candidates]
        return {
            "status": "ok",
            "total_matches": len(matches),
            "candidates": [self._presenter.candidate(session, txn) for txn in shown],
            "note": DATA_NOT_INSTRUCTIONS,
        }

    def _matches(self, txn: Transaction, criteria: SearchCriteria) -> bool:
        if criteria.amount is not None:
            tolerance = abs(criteria.amount) * self._settings.amount_tolerance_pct / 100
            if abs((txn.amount or 0.0) - criteria.amount) > tolerance:
                return False
        day = txn.transaction_date.date()
        if criteria.date_from and day < criteria.date_from:
            return False
        return not (criteria.date_to and day > criteria.date_to)

    def _rank_by_merchant(
        self, hint: str, candidates: list[Transaction]
    ) -> list[Transaction]:
        """Rank candidates with the typed D4 merchant decision.

        Only merchant names are sent (no amounts or dates leave the service). Falls back
        to a substring match when no decision model answers.
        """
        options = {
            f"c{i}": f"{txn.merchant_name} ({txn.merchant_category})"
            for i, txn in enumerate(candidates)
        }
        try:
            result = self._decisions.decide(
                hint, {self._merchant_key: d4_merchant(options)}
            )
            answer = result.answers[self._merchant_key]
            if (
                answer.value != NO_MERCHANT_MATCH
                and answer.probability >= self._settings.merchant_min_probability
            ):
                best = candidates[int(str(answer.value)[1:])]
                return [best, *[txn for txn in candidates if txn is not best]]
        except DecisionError:
            pass
        needle = hint.casefold()
        return sorted(
            candidates,
            key=lambda txn: needle not in (txn.merchant_name or "").casefold(),
        )
