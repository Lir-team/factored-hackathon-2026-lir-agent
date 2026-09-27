"""Pipeline completo: [ingest] -> staging -> quality -> curated -> insights.

Uso:
    python -m pipelines            # desde raw/ ya descargado
    python -m pipelines --ingest   # descarga incremental desde S3 primero
"""
import sys

from . import curated, ingest, insights, quality, staging

if "--ingest" in sys.argv:
    ingest.run(ingest.DEFAULT_TABLES)
staging.run(staging.TABLES)
quality.run()
curated.run()
insights.run()
