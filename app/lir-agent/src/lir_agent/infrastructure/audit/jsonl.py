"""AuditSink writing one JSON line per event (local runs).

Swappable for case-events, BigQuery or Cloud Logging without touching the callers.
"""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class JsonlAuditSink:
    """AuditSink appending one JSON line per event to a local file."""

    def __init__(self, path: Path) -> None:
        """Keep the target file; writes are serialized with a lock."""
        self._path = path
        self._lock = threading.Lock()

    def record(self, event: str, session_id: str | None, **fields: Any) -> dict:
        """Append a timestamped entry and return it."""
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event,
            "session_id": session_id,
            **fields,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry
