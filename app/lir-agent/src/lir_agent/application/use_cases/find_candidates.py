"""Use case: find the customer's transactions that match what they describe."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date

from decision_layer import DecisionError, DecisionModel
from decision_layer.questions import d4_merchant

from lir_agent.application.ports import TransactionRepository
from lir_agent.application.presenter import DATA_NOT_INSTRUCTIONS, LlmPresenter
from lir_agent.domain.errors import InvalidSearchCriteriaError
from lir_agent.domain.models import Transaction
from lir_agent.domain.policy import SearchSettings
from lir_agent.domain.session import SessionState, utc_now

NO_MERCHANT_MATCH = "ninguno"  # option added by decision_layer.questions.d4_merchant
ISO_DATE = "YYYY-MM-DD"
ASK_FOR_A_DETAIL = (
    "Do not list the customer's charges yet. Ask ONE short question for a concrete detail: "
    "the amount, the merchant name, or the exact date of the charge. For an exact date, "
    "search with date_from and date_to both set to that day; date_to defaults to today."
)
NO_MERCHANT_FOUND = (
    "None of the customer's charges is from that merchant. Tell the customer you found no "
    "charge from it. Do NOT present any other charge as that merchant. Ask for the amount "
    "or the date of the charge instead."
)


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
        """Build criteria from tool arguments, rejecting malformed or inverted dates."""
        start, end = cls._date(date_from, "date_from"), cls._date(date_to, "date_to")
        if start and end and end < start:
            raise InvalidSearchCriteriaError("date_to", "a date on or after date_from")
        return cls(amount, start, end, merchant_hint)

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
        today: Callable[[], date] | None = None,
    ) -> None:
        """Keep the data source, decision model, search settings, presenter and clock."""
        self._repository = repository
        self._decisions = decisions
        self._settings = settings
        self._merchant_key = merchant_question_key
        self._presenter = presenter
        self._today = today or (lambda: utc_now().date())

    def execute(self, session: SessionState, criteria: SearchCriteria) -> dict:
        """Return minimized candidates, most recent first, with session references."""
        criteria = self._with_default_date_to(criteria)
        if not self._has_concrete_detail(criteria):
            return {"status": "needs_detail", "instruction": ASK_FOR_A_DETAIL}
        matches = [
            txn
            for txn in self._repository.list_transactions(session.require_customer_id())
            if self._matches(txn, criteria)
        ]
        hint = (criteria.merchant_hint or "").strip()
        if hint:
            # The merchant is a filter, not a hint: other charges must never be returned as
            # if they were that merchant (the model would present them as such).
            matches = self._filter_by_merchant(hint, matches)
            if not matches:
                return {
                    "status": "no_merchant_match",
                    "merchant_hint": hint,
                    "total_matches": 0,
                    "candidates": [],
                    "instruction": NO_MERCHANT_FOUND,
                    "note": DATA_NOT_INSTRUCTIONS,
                }
        matches = self._prefer_exact_amount(matches, criteria)
        shown = matches[: self._settings.max_candidates]
        return {
            "status": "ok",
            "total_matches": len(matches),
            "candidates": [self._presenter.candidate(session, txn) for txn in shown],
            "note": DATA_NOT_INSTRUCTIONS,
        }

    def _with_default_date_to(self, criteria: SearchCriteria) -> SearchCriteria:
        """Default date_to to today when a start date is given (date_from is the bound)."""
        if criteria.date_from and not criteria.date_to:
            return replace(criteria, date_to=self._today())
        return criteria

    def _has_concrete_detail(self, criteria: SearchCriteria) -> bool:
        """Whether the customer gave an amount, a merchant or a short closed date range."""
        if criteria.amount is not None or (criteria.merchant_hint or "").strip():
            return True
        if criteria.date_from and criteria.date_to:
            span = (criteria.date_to - criteria.date_from).days
            return 0 <= span <= self._settings.max_date_only_range_days
        return False

    @staticmethod
    def _prefer_exact_amount(
        matches: list[Transaction], criteria: SearchCriteria
    ) -> list[Transaction]:
        """Keep only exact-amount matches when there are any.

        The tolerance exists for approximate amounts ("unos 250"). When some charges match
        the stated amount to the cent, near ones (245.50 for "250") only add noise.
        """
        if criteria.amount is None:
            return matches
        exact = [
            txn
            for txn in matches
            if round(txn.amount or 0.0, 2) == round(criteria.amount, 2)
        ]
        return exact or matches

    def _matches(self, txn: Transaction, criteria: SearchCriteria) -> bool:
        if criteria.amount is not None:
            tolerance = abs(criteria.amount) * self._settings.amount_tolerance_pct / 100
            if abs((txn.amount or 0.0) - criteria.amount) > tolerance:
                return False
        day = txn.transaction_date.date()
        if criteria.date_from and day < criteria.date_from:
            return False
        return not (criteria.date_to and day > criteria.date_to)

    def _filter_by_merchant(
        self, hint: str, candidates: list[Transaction]
    ) -> list[Transaction]:
        """Keep the candidates of the merchant the customer named, the D4 pick first.

        A charge matches when its merchant name contains the hint, or when the typed D4
        decision picked it (fuzzy names such as "oxxo tienda" vs "OXXO"). Charges without a
        merchant never match and are never offered to D4. The decision runs even for a
        single candidate, so one charge of another merchant cannot slip through. Only
        merchant names are sent (no amounts or dates leave the service). When the decision
        model fails, only the substring match applies.
        """
        named = [txn for txn in candidates if txn.merchant_name]
        if not named:
            return []
        needle = hint.casefold()
        matched = [
            txn for txn in named if needle in (txn.merchant_name or "").casefold()
        ]
        best = self._decided_merchant(hint, named)
        if best is None:
            return matched
        return [best, *[txn for txn in matched if txn is not best]]

    def _decided_merchant(
        self, hint: str, named: list[Transaction]
    ) -> Transaction | None:
        """The candidate the D4 decision picked with enough confidence, if any."""
        options = {
            f"c{i}": f"{txn.merchant_name} ({txn.merchant_category})"
            for i, txn in enumerate(named)
        }
        try:
            result = self._decisions.decide(
                hint, {self._merchant_key: d4_merchant(options)}
            )
            answer = result.answers[self._merchant_key]
        except DecisionError:
            return None
        if (
            answer.value == NO_MERCHANT_MATCH
            or answer.probability < self._settings.merchant_min_probability
        ):
            return None
        return named[int(str(answer.value)[1:])]
