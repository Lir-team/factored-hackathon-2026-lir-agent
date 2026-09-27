"""Carga de contratos YAML y validación de DataFrames contra ellos.

La validación devuelve un reporte en vez de lanzar excepciones: el dataset trae
duplicados, nulos y huérfanos a propósito, y lo que interesa es medirlos.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import yaml

from .paths import CONTRACTS


def load_contract(table: str) -> dict:
    with open(CONTRACTS / f"{table}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_tables() -> list[str]:
    return sorted(p.stem for p in CONTRACTS.glob("*.yaml") if p.stem != "relationships")


@dataclass
class ValidationReport:
    table: str
    rows: int
    missing_columns: list[str] = field(default_factory=list)
    unexpected_columns: list[str] = field(default_factory=list)
    null_violations: dict[str, int] = field(default_factory=dict)
    duplicate_pk_rows: int = 0
    null_rates: dict[str, float] = field(default_factory=dict)

    @property
    def schema_ok(self) -> bool:
        return not self.missing_columns and not self.unexpected_columns


def validate(df: pd.DataFrame, table: str) -> ValidationReport:
    contract = load_contract(table)
    cols = {c["name"]: c for c in contract["columns"]}
    report = ValidationReport(table=table, rows=len(df))

    report.missing_columns = [c for c in cols if c not in df.columns]
    report.unexpected_columns = [c for c in df.columns if c not in cols]

    for name, spec in cols.items():
        if name not in df.columns:
            continue
        nulls = int(df[name].isna().sum())
        report.null_rates[name] = nulls / len(df) if len(df) else 0.0
        if not spec.get("nullable", True) and nulls:
            report.null_violations[name] = nulls

    pk = [n for n, s in cols.items() if s.get("primary_key") and n in df.columns]
    if pk:
        report.duplicate_pk_rows = int(df.duplicated(subset=pk, keep="first").sum())

    return report
