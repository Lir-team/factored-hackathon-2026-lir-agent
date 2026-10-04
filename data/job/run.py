"""Cloud Run job entry point: ingest from S3, run every stage, publish to the lake.

    python -m job.run

The pipeline writes to its usual local folders (raw/, staging/, curated/). Only when every
stage succeeded are staging/ and curated/ copied to LAKE_DIR, the Cloud Storage bucket that
Cloud Run mounts and the agent reads (DATA_DIR=/mnt/data on the service). A failed pipeline
run never touches the lake; a published run replaces each table file and removes tables that
no longer exist, and a manifest records what was published.

Environment:
    LAKE_DIR                      mounted bucket (required)
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION   read by boto3 (ingest)
    PIPELINE_SKIP_INGEST=1        reuse raw/ already in the image (local tests)
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from pipelines.paths import CURATED, DATA_DIR, STAGING

PUBLISHED_DIRS = {"staging": STAGING, "curated": CURATED}
MANIFEST_NAME = "_published.json"


def run_pipeline(skip_ingest: bool) -> None:
    """Run `python -m pipelines` (with S3 ingest unless skipped); raise if any stage fails."""
    args = [sys.executable, "-m", "pipelines"]
    if not skip_ingest:
        args.append("--ingest")
    subprocess.run(args, cwd=DATA_DIR, check=True)


def publish(lake: Path, sources: dict[str, Path] = PUBLISHED_DIRS) -> dict:
    """Copy each source folder to `lake/<name>`, replacing it, and write a manifest."""
    listed = {
        name: sorted(p for p in source.glob("*") if p.is_file() and not p.name.startswith("."))
        for name, source in sources.items()
    }
    # Check every source before touching the lake, so a run never publishes half its output.
    empty = [str(sources[name]) for name, files in listed.items() if not files]
    if empty:
        raise RuntimeError(f"Nothing to publish in {', '.join(empty)}")
    published: dict[str, list[str]] = {}
    for name, files in listed.items():
        # Cloud Storage FUSE cannot rename folders, so files are copied in place (each one
        # replaced whole) and tables that no longer exist are removed afterwards.
        target = lake / name
        target.mkdir(parents=True, exist_ok=True)
        for file in files:
            shutil.copyfile(file, target / file.name)
        keep = {file.name for file in files}
        for stale in target.glob("*"):
            if stale.is_file() and stale.name not in keep:
                stale.unlink()
        published[name] = sorted(keep)
    manifest = {
        "published_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tables": published,
    }
    (lake / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    lake_dir = os.environ.get("LAKE_DIR")
    if not lake_dir:
        print("LAKE_DIR is required (the mounted data lake bucket)", file=sys.stderr)
        return 2
    run_pipeline(skip_ingest=os.environ.get("PIPELINE_SKIP_INGEST") == "1")
    manifest = publish(Path(lake_dir))
    counts = {name: len(files) for name, files in manifest["tables"].items()}
    print(f"Published to {lake_dir}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
