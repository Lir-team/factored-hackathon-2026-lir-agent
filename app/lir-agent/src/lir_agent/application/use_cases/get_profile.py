"""Use case: the minimal profile of the customer signed in to the session."""

import logging

from lir_agent.application.ports import TransactionRepository
from lir_agent.application.presenter import LlmPresenter
from lir_agent.domain.session import SessionState

logger = logging.getLogger(__name__)


class GetCustomerProfile:
    """Returns only allowlisted, non-sensitive fields: what the model never sees, it cannot leak."""

    def __init__(
        self, repository: TransactionRepository, presenter: LlmPresenter
    ) -> None:
        """Keep the repository and the field allowlist presenter."""
        self._repository = repository
        self._presenter = presenter

    def execute(self, session: SessionState) -> dict:
        """Look up the session's customer. Logs carry the id, never the record."""
        customer_id = session.require_customer_id()
        customer = self._repository.get_customer(customer_id)
        if customer is None:
            logger.info("Customer %s not found", customer_id)
            return {"found": False}
        return {"found": True, "customer": self._presenter.customer(customer)}
