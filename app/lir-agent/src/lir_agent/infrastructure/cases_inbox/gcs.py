"""CaseInbox on Cloud Storage: the bucket notification hands each new case to the agent."""

import json
from typing import Any


class GcsCaseInbox:
    """Writes each case to `cases/<case_id>.json` with its attributes as object metadata."""

    def __init__(self, bucket: str, client: Any = None) -> None:
        """Bind the bucket; `client` replaces `google.cloud.storage.Client` in tests."""
        if client is None:
            from google.cloud import storage  # only when the GCS inbox is selected

            client = storage.Client()
        self._bucket = client.bucket(bucket)

    def put(
        self, case_id: str, payload: dict[str, Any], attributes: dict[str, str]
    ) -> None:
        """Upload the payload as UTF-8 JSON; metadata travels in the OBJECT_FINALIZE event."""
        blob = self._bucket.blob(f"cases/{case_id}.json")
        blob.metadata = attributes
        blob.upload_from_string(
            json.dumps(payload, ensure_ascii=False), content_type="application/json"
        )
