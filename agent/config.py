"""Carga de configuración: variables de entorno primero, luego `.env` y `data/.env`.

Nunca se imprimen los valores. Los `.env` están en .gitignore.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_FILES = (REPO / ".env", REPO / "data" / ".env")


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def get(key: str) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    for path in ENV_FILES:
        value = _read_env_file(path).get(key)
        if value:
            return value
    return None
