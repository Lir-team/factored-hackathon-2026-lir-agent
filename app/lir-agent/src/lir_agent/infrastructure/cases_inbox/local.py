"""CaseInbox on a local directory, for development and tests."""

import json
from pathlib import Path
from typing import Any


class LocalCaseInbox:
    """Writes `cases/<case_id>.json` and its attributes next to it, like the bucket layout."""

    def __init__(self, root: Path) -> None:
        """Keep the root directory; it is created on the first write."""
        self._cases = root / "cases"

    def put(
        self, case_id: str, payload: dict[str, Any], attributes: dict[str, str]
    ) -> None:
        """Write the payload and its attributes as UTF-8 JSON files."""
        self._cases.mkdir(parents=True, exist_ok=True)
        self._write(self._cases / f"{case_id}.attributes.json", attributes)
        # The case file last: it is the one a reader waits for.
        self._write(self._cases / f"{case_id}.json", payload)

    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
