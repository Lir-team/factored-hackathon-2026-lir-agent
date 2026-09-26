"""raw/ -> staging/ : tipado por contrato, canonicalización por glosario, dedupe, linaje.

Principios aplicados:
- Contratos: el tipo de cada columna sale de contracts/<tabla>.yaml, no del código.
- Semántica compartida: valores crudos -> códigos canónicos vía contracts/glossary.yaml.
- Idempotencia + late arrivals: dedupe por PK quedándose con la versión de
  process_date más reciente, así re-procesar particiones tardías no duplica.
- Transparencia: nunca se descartan valores en silencio. Fallas de casteo, valores
  sin mapear y filas deduplicadas quedan contadas en el manifiesto.
- Linaje: cada fila conserva `_source_file`; cada corrida escribe manifests/staging/.

Uso:
    python -m pipelines.staging                  # todas las tablas descargadas
    python -m pipelines.staging complaints       # una tabla
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import duckdb
import yaml

from .contracts import load_contract
from .paths import CONTRACTS, MANIFESTS, RAW, STAGING

TABLES = [
    "daily_exchange_rates", "branches", "service_agents", "customers", "products",
    "call_center_interactions", "call_transcripts", "satisfaction_surveys",
    "complaints", "transactions",
]

# Columnas derivadas por tabla (SQL sobre la tabla ya tipada y canonicalizada `t`).
DERIVED = {
    "call_center_interactions": {
        "process_lag_days": "datediff('day', process_date, interaction_date::date)",
    },
    "call_transcripts": {
        "has_unfilled_placeholder": r"regexp_matches(full_text, '\{[a-z_]+\}')",
        "template_id": "md5(full_text)",
    },
    "complaints": {
        "process_lag_days": "datediff('day', process_date, creation_date::date)",
    },
    "transactions": {
        "process_lag_days": "datediff('day', process_date, transaction_date::date)",
        "amount_usd_imputed": "amount_usd is null",
    },
}


def _sql_type(t: str) -> str:
    t = t.upper()
    if t.startswith(("VARCHAR", "TEXT")):
        return "VARCHAR"
    if t == "INTEGER":
        return "BIGINT"
    return t  # DATE, TIMESTAMP, TIME, BOOLEAN, DECIMAL(p,s)


def _value_maps() -> dict[str, dict[str, str]]:
    g = yaml.safe_load(open(CONTRACTS / "glossary.yaml", encoding="utf-8"))
    out = {}
    for m in g["value_maps"].values():
        for target in m["applies_to"]:
            out[target] = m["values"]
    return out


def _source(table: str) -> str:
    single = RAW / f"{table}.csv"
    return str(single if single.exists() else RAW / table / "**" / "*.csv").replace("\\", "/")


def stage(con: duckdb.DuckDBPyConnection, table: str, maps: dict) -> dict:
    contract = load_contract(table)
    cols = contract["columns"]
    pk = [c["name"] for c in cols if c.get("primary_key")]

    con.execute(f"""create or replace temp table r as
        select *, filename as _source_file
        from read_csv('{_source(table)}', all_varchar=true, header=true,
                      union_by_name=true, filename=true)""")
    raw_cols = {c.lower() for c in con.execute("select * from r limit 0").fetchdf().columns}
    missing = [c["name"] for c in cols if c["name"] not in raw_cols]

    selects, cast_checks, unmapped = [], [], {}
    for c in cols:
        name, typ = c["name"], _sql_type(c["type"])
        if name in missing:
            selects.append(f"null::{typ} as {name}")
            continue
        src = f"nullif(trim({name}), '')"
        vmap = maps.get(f"{table}.{name}")
        if vmap:
            cases = " ".join(f"when {src} = '{k}' then '{v}'" for k, v in vmap.items())
            expr = f"case {cases} else {src} end"
            known = ", ".join(f"'{k}'" for k in vmap) + ", " + ", ".join(f"'{v}'" for v in set(vmap.values()))
            unmapped[name] = f"count(*) filter ({src} is not null and {src} not in ({known}))"
        elif typ == "BIGINT":
            expr = f"try_cast(try_cast({src} as double) as bigint)"  # "139.0" -> 139
        elif typ == "VARCHAR":
            expr = src
        else:
            expr = f"try_cast({src} as {typ})"
        selects.append(f"{expr} as {name}")
        if typ != "VARCHAR":
            cast_checks.append(f"count(*) filter ({src} is not null and ({expr}) is null) as {name}")

    n_raw = con.execute("select count(*) from r").fetchone()[0]
    cast_failures = {}
    if cast_checks:
        row = con.execute(f"select {', '.join(cast_checks)} from r").fetchdf().iloc[0]
        cast_failures = {k: int(v) for k, v in row.items() if v}
    unmapped_counts = {}
    if unmapped:
        row = con.execute(f"select {', '.join(f'{v} as {k}' for k, v in unmapped.items())} from r").fetchdf().iloc[0]
        unmapped_counts = {k: int(v) for k, v in row.items() if v}

    con.execute(f"create or replace temp table t as select {', '.join(selects)}, _source_file from r")

    if table == "transactions":
        # amount_usd: USD -> el mismo monto; faltante -> última tasa disponible <= fecha
        # (asof). Hay transacciones posteriores al fin de daily_exchange_rates.
        con.execute("""create or replace temp table t as
            select t.* replace (
                coalesce(t.amount_usd,
                         case when t.currency = 'USD' then t.amount
                              else round(t.amount * fx.exchange_rate, 2) end)::decimal(15,2) as amount_usd),
                   t.amount_usd is null as amount_usd_imputed,
                   t.amount_usd is null and t.currency <> 'USD'
                       and fx.date < t.transaction_date::date as fx_rate_stale
            from t asof left join staging_fx fx
              on fx.source_currency = t.currency and fx.target_currency = 'USD'
             and fx.date <= t.transaction_date::date""")
    derived = {k: v for k, v in DERIVED.get(table, {}).items()
               if not (table == "transactions" and k == "amount_usd_imputed")}
    if derived:
        extra = ", ".join(f"{v} as {k}" for k, v in derived.items())
        con.execute(f"create or replace temp table t as select *, {extra} from t")

    order = "process_date desc nulls last, _source_file desc" if "process_date" in raw_cols else "_source_file desc"
    con.execute(f"""create or replace temp table s as select * from t
        qualify row_number() over (partition by {', '.join(pk)} order by {order}) = 1""")
    n_out = con.execute("select count(*) from s").fetchone()[0]

    STAGING.mkdir(parents=True, exist_ok=True)
    out = STAGING / f"{table}.parquet"
    con.execute(f"copy s to '{str(out).replace(chr(92), '/')}' (format parquet, compression zstd)")
    if table == "daily_exchange_rates":
        con.execute("create or replace temp table staging_fx as select * from s")

    return {
        "rows_raw": n_raw,
        "rows_staged": n_out,
        "pk_duplicates_dropped": n_raw - n_out,
        "missing_columns": missing,
        "cast_failures": cast_failures,
        "unmapped_values": unmapped_counts,
        "contract_version": contract["contract_version"],
        "output": str(out.relative_to(STAGING.parent)).replace("\\", "/"),
    }


def run(tables: list[str]) -> dict:
    con = duckdb.connect()
    maps = _value_maps()
    todo = tables if "daily_exchange_rates" in tables or "transactions" not in tables \
        else ["daily_exchange_rates", *tables]
    results = {}
    for t in [x for x in TABLES if x in todo]:
        results[t] = stage(con, t, maps)
        print(f"{t:26} {results[t]['rows_raw']:>10,} -> {results[t]['rows_staged']:>10,}"
              f"  cast_fail={sum(results[t]['cast_failures'].values())}"
              f"  unmapped={sum(results[t]['unmapped_values'].values())}", flush=True)
    manifest = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tables": results}
    d = MANIFESTS / "staging"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"staging_{manifest['run_at'][:19].replace(':', '')}.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    run(sys.argv[1:] or TABLES)
