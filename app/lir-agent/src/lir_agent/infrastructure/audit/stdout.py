"""AuditSink writing one JSON object per event to stdout (Cloud Run).

Cloud Run forwards stdout to Cloud Logging and parses JSON lines into structured entries:
`severity` and `message` are recognized, every other field lands in `jsonPayload`, where it
can be queried or exported to BigQuery with a log sink.
"""

import json
import sys
import threading
from datetime import UTC, datetime
from typing import Any, TextIO

AUDIT_LOG_NAME = "lir_audit"


class StdoutAuditSink:
    """AuditSink printing one structured JSON line per event."""

    def __init__(self, stream: TextIO | None = None) -> None:
        """Write to `stream` (stdout by default); lines are serialized with a lock."""
        self._stream = stream
        self._lock = threading.Lock()

    def record(self, event: str, session_id: str | None, **fields: Any) -> dict:
        """Print a timestamped entry and return it."""
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event,
            "session_id": session_id,
            **fields,
        }
        line = {
            "severity": "INFO",
            "message": f"audit {event}",
            "log": AUDIT_LOG_NAME,
            **entry,
        }
        stream = self._stream or sys.stdout
        with self._lock:
            stream.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
            stream.flush()
        return entry
