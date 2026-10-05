"""staging/ -> curated/ : analytical tables for prioritizing the flow.

- contacts_enriched: one row per contact + customer segment/country, contact
  CSAT and repeat_contact_7d (defined in contracts/glossary.yaml).
- complaints_enriched: complaints + segment/country + charge dispute flag.

Usage:
    python -m pipelines.curated
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb

from .paths import CURATED, MANIFESTS, STAGING

SQL = {
    "contacts_enriched": """
        select i.*, c.segment, c.country, c.customer_status,
               coalesce(lead(i.interaction_date) over w <= i.interaction_date + interval 7 day, false)
                   as repeat_contact_7d,
               s.csat
        from call_center_interactions i
        join customers c using (customer_id)
        left join (select interaction_id, avg(main_score) as csat from satisfaction_surveys
                   where survey_type = 'CSAT' group by 1) s using (interaction_id)
        window w as (partition by i.customer_id, i.reason_category order by i.interaction_date)
    """,
    "complaints_enriched": """
        select k.*, c.segment, c.country,
               k.subcategory in ('unrecognized_charge', 'improper_fee') as is_charge_dispute,
               k.status in ('Open', 'In Process', 'Escalated') as is_open
        from complaints k join customers c using (customer_id)
    """,
}
INPUTS = {
    "contacts_enriched": ["call_center_interactions", "customers", "satisfaction_surveys"],
    "complaints_enriched": ["complaints", "customers"],
}


def run() -> dict:
    con = duckdb.connect()
    for p in STAGING.glob("*.parquet"):
        con.execute(f"create view {p.stem} as select * from '{p.as_posix()}'")
    CURATED.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, sql in SQL.items():
        dest = CURATED / f"{name}.parquet"
        con.execute(f"copy ({sql}) to '{dest.as_posix()}' (format parquet, compression zstd)")
        n = con.execute(f"select count(*) from '{dest.as_posix()}'").fetchone()[0]
        out[name] = {"rows": n, "inputs": [f"staging/{t}.parquet" for t in INPUTS[name]]}
        print(f"{name:22} {n:>10,}")
    manifest = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tables": out}
    d = MANIFESTS / "curated"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"curated_{manifest['run_at'][:19].replace(':', '')}.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    run()
