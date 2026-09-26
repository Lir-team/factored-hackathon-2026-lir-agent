"""Rutas centrales del workspace de datos. Importar desde aquí; no hardcodear rutas."""
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1]

RAW = DATA_DIR / "raw"
STAGING = DATA_DIR / "staging"
CURATED = DATA_DIR / "curated"
SAMPLES = DATA_DIR / "samples"
CONTRACTS = DATA_DIR / "contracts"
MANIFESTS = DATA_DIR / "manifests"
KNOWLEDGE_BASE = DATA_DIR / "knowledge_base"
EVAL = DATA_DIR / "eval"
FIXTURES = DATA_DIR / "fixtures"

S3_BUCKET = "factored-datathon-2026-s3-157725502942-us-east-2-an"
S3_PREFIX = "data/"
