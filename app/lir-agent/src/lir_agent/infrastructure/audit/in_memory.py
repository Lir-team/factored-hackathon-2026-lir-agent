"""AuditSink kept in memory, for tests and evaluation runs."""

from typing import Any


class InMemoryAuditSink:
    """AuditSink kept in memory, for tests and evaluation runs."""

    def __init__(self) -> None:
        """Start with no entries."""
        self.entries: list[dict] = []

    def record(self, event: str, session_id: str | None, **fields: Any) -> dict:
        """Keep the entry in memory and return it."""
        entry = {"event": event, "session_id": session_id, **fields}
        self.entries.append(entry)
        return entry

    def events(self) -> list[str]:
        """Event names recorded so far, in order."""
        return [entry["event"] for entry in self.entries]
