"""Case inbox adapters: Cloud Storage in production, a local directory for dev and tests."""

from lir_agent.infrastructure.cases_inbox.gcs import GcsCaseInbox
from lir_agent.infrastructure.cases_inbox.local import LocalCaseInbox

__all__ = ["GcsCaseInbox", "LocalCaseInbox"]
