"""Ingesta incremental S3 -> raw/.

Copia los archivos tal cual (misma estructura de particiones). Solo descarga lo
que falta o cambió (por ETag), así que re-ejecutar es barato y captura late
arrivals. Cada ejecución deja un manifiesto en manifests/ingest/.

Uso:
    python -m pipelines.ingest                      # tablas por defecto
    python -m pipelines.ingest complaints customers # tablas específicas
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import boto3
from dotenv import load_dotenv

from .paths import DATA_DIR, MANIFESTS, RAW, S3_BUCKET, S3_PREFIX

# digital_events (3.7 GB) y campaign_sends quedan fuera por defecto: no son
# centrales para atención al cliente. Pedirlas explícitamente si se necesitan.
DEFAULT_TABLES = [
    "customers", "products", "branches", "service_agents", "marketing_campaigns",
    "daily_exchange_rates", "call_center_interactions", "call_transcripts",
    "satisfaction_surveys", "complaints", "transactions",
]

STATE_FILE = MANIFESTS / "ingest" / "_etags.json"


def _list(s3, table: str) -> list[dict]:
    objs = []
    for prefix in (f"{S3_PREFIX}{table}.csv", f"{S3_PREFIX}{table}/"):
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=S3_BUCKET, Prefix=prefix):
            objs += page.get("Contents", [])
    return objs


def run(tables: list[str]) -> dict:
    load_dotenv(DATA_DIR / ".env")
    s3 = boto3.client("s3")
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    todo, summary = [], {}
    for t in tables:
        objs = _list(s3, t)
        new = [o for o in objs if state.get(o["Key"]) != o["ETag"]
               or not (RAW / o["Key"][len(S3_PREFIX):]).exists()]
        todo += new
        summary[t] = {"files": len(objs), "bytes": sum(o["Size"] for o in objs), "downloaded": len(new)}

    def fetch(o):
        dest = RAW / o["Key"][len(S3_PREFIX):]
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(S3_BUCKET, o["Key"], str(dest))
        return o["Key"], o["ETag"]

    with ThreadPoolExecutor(16) as pool:
        for key, etag in pool.map(fetch, todo):
            state[key] = etag

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=0, sort_keys=True))
    manifest = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": f"s3://{S3_BUCKET}/{S3_PREFIX}",
        "tables": summary,
    }
    out = MANIFESTS / "ingest" / f"ingest_{manifest['run_at'][:19].replace(':', '')}.json"
    out.write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1:] or DEFAULT_TABLES), indent=2))
