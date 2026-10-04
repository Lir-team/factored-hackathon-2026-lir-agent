"""CasePublisher kept in process memory: tests and local runs without Pub/Sub."""

from typing import Any


class InMemoryCasePublisher:
    """Keeps published cases in memory; nothing delivers them to the agent."""

    def __init__(self) -> None:
        """Start with no messages."""
        self.published: list[tuple[dict[str, Any], dict[str, str], str]] = []

    def publish(
        self, payload: dict[str, Any], attributes: dict[str, str], ordering_key: str
    ) -> None:
        """Keep the message."""
        self.published.append((payload, attributes, ordering_key))
