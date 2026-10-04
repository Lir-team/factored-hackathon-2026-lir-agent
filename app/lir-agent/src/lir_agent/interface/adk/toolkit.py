"""ADK tools as methods of one class.

Each method adapts ADK's `tool_context` to a `SessionState`, delegates to a use case
and maps domain errors to tool results.

None of the tools accepts a customer id: it comes from the authenticated session, so a
manipulated conversation cannot reach another customer's records.
"""

from collections.abc import Callable

from google.adk.tools import ToolContext

from lir_agent.application.use_cases import (
    FindCandidateTransactions,
    GatherTransactionEvidence,
    GetCustomerProfile,
    RequestHandoff,
    SearchCriteria,
)
from lir_agent.domain.errors import InvalidSearchCriteriaError, TransactionNotFoundError
from lir_agent.domain.session import SessionState


class ChargeInvestigationToolkit:
    """The agent's tools, each delegating to one use case."""

    def __init__(
        self,
        get_profile: GetCustomerProfile,
        find_candidates: FindCandidateTransactions,
        gather_evidence: GatherTransactionEvidence,
        request_handoff: RequestHandoff,
    ) -> None:
        """Keep the use cases the tools delegate to."""
        self._get_profile = get_profile
        self._find_candidates = find_candidates
        self._gather_evidence = gather_evidence
        self._request_handoff = request_handoff

    def tools(self) -> list[Callable[..., dict]]:
        """The bound methods registered as ADK tools."""
        return [
            self.get_my_customer_profile,
            self.find_candidate_transactions,
            self.get_transaction_evidence,
            self.open_dispute,
            self.request_human_handoff,
        ]

    def get_my_customer_profile(self, tool_context: ToolContext) -> dict:
        """Returns the profile of the customer signed in to this session.

        Takes no arguments: the customer is already authenticated and identified by the
        session. Never ask the user for a customer ID, and never mention one.

        Returns:
            `found` set to True and a minimal, non-sensitive profile under `customer`
            (segment, account status, registration date, country and marketing opt-in),
            or `found` set to False. Names, contact details, identity documents and
            financial data are never available.
        """
        return self._get_profile.execute(SessionState(tool_context.state))

    def find_candidate_transactions(
        self,
        tool_context: ToolContext,
        amount: float | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        merchant_hint: str | None = None,
    ) -> dict:
        """Finds the authenticated customer's transactions that match what the customer describes.

        Args:
            amount: Approximate amount in the transaction currency, if the customer gave one.
            date_from: Earliest date to consider, ISO format YYYY-MM-DD. Required to search by
                date; for an exact date, set it and date_to to that same day.
            date_to: Latest date to consider, ISO format YYYY-MM-DD. Defaults to today.
            merchant_hint: Merchant name or description exactly as the customer wrote it.

        Returns:
            Matching candidates, most recent first, each with a `transaction_ref` to use in other tools.
        """
        try:
            criteria = SearchCriteria.parse(amount, date_from, date_to, merchant_hint)
        except InvalidSearchCriteriaError as error:
            return {
                "status": "error",
                "error": "invalid_date",
                "field": error.field,
                "expected": error.expected,
            }
        return self._find_candidates.execute(SessionState(tool_context.state), criteria)

    def get_transaction_evidence(
        self, tool_context: ToolContext, transaction_ref: str
    ) -> dict:
        """Gathers verifiable evidence about one of the customer's transactions.

        Returns the evidence and the policy lane to follow (explain, dispute, propose or
        escalate).

        Args:
            transaction_ref: Reference returned by find_candidate_transactions (e.g. "T1").
        """
        session = SessionState(tool_context.state)
        try:
            result = self._gather_evidence.execute(session, transaction_ref)
        except TransactionNotFoundError:
            return {"status": "not_found"}
        if session.handoff_id:  # the case is already with a human: send them the charge too
            self._request_handoff.attach_evidence(session)
        return result

    def open_dispute(
        self,
        tool_context: ToolContext,  # noqa: ARG002 - the tool guard answers this call
        transaction_ref: str,  # noqa: ARG002
        reason: str,  # noqa: ARG002
    ) -> dict:
        """Asks the customer to approve opening a dispute for a transaction.

        It does NOT open the dispute: the customer approves or rejects it with the buttons
        the bank shows next to your reply. Only when the policy lane is 'dispute', or when
        the customer still rejects a charge you explained.

        Args:
            transaction_ref: Reference of the disputed transaction (e.g. "T1").
            reason: The customer reads it on the approval card: one short, neutral phrase in
                their language, without "the customer" (e.g. "Cobro duplicado: dos cargos
                iguales el mismo día").
        """
        # The tool guard turns this call into an approval request (policy `approvals`); if
        # it ever got here, the action must still not run.
        return {"status": "blocked", "reason": "approval_required"}

    def request_human_handoff(
        self, tool_context: ToolContext, summary: str, open_questions: list[str]
    ) -> dict:
        """Transfers the case to a human specialist with the verified facts gathered so far.

        Args:
            summary: One or two sentences describing the customer's request.
            open_questions: Questions a specialist still needs to resolve.
        """
        return self._request_handoff.execute(
            SessionState(tool_context.state), summary, open_questions
        )
