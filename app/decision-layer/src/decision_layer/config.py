"""Settings: environment variables first, then decision-layer/.env, then data/.env.

Values are never printed. Both .env files are gitignored.
"""

import os
from pathlib import Path

# config.py -> decision_layer -> src -> decision-layer -> app -> <repo root>
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[4]
ENV_FILES = (_PACKAGE_ROOT / ".env", _REPO_ROOT / "data" / ".env")


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get(key: str) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    for path in ENV_FILES:
        if value := _read_env_file(path).get(key):
            return value
    return None
