"""Full pipeline: [ingest] -> staging -> quality -> curated -> insights.

Usage:
    python -m pipelines            # from the already downloaded raw/
    python -m pipelines --ingest   # incremental download from S3 first
"""
import sys

from . import curated, ingest, insights, quality, staging

if "--ingest" in sys.argv:
    ingest.run(ingest.DEFAULT_TABLES)
staging.run(staging.TABLES)
quality.run()
curated.run()
insights.run()
