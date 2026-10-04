"""Case store adapters (in memory today; Firestore is planned)."""

from lir_agent.infrastructure.case_store.in_memory import InMemoryCaseStore

__all__ = ["InMemoryCaseStore"]
