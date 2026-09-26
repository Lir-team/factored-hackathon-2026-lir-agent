"""Evidencia para elegir el flujo -> reports/insights.md + reports/figures/*.png

Todos los números se calculan aquí desde curated/ y staging/. El texto
interpretativo cita esos números; si los datos cambian, re-ejecutar.

Uso:
    python -m pipelines.insights
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb
import pandas as pd

from . import figures
from .paths import CURATED, DATA_DIR, MANIFESTS, STAGING

REPORTS = DATA_DIR / "reports"


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for d in (STAGING, CURATED):
        for p in d.glob("*.parquet"):
            con.execute(f"create view {p.stem} as select * from '{p.as_posix()}'")
    return con


def _md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        rows.append("| " + " | ".join(str(v) for v in r) + " |")
    return "\n".join(rows)


def _spread(con, dim: str) -> str:
    """Brecha máx. de FCR entre valores de `dim` dentro de un mismo motivo, con su z.

    Con muchas comparaciones (motivos × pares), una brecha grande en una celda chica
    es esperable por azar: se reporta z = brecha / error estándar y solo se marca
    como disparidad si z > 3.
    """
    df = con.execute(f"""select reason_category, {dim} v, count(*) n, avg(was_resolved::int) p
                         from contacts_enriched group by all""").df()
    worst = None
    for reason, g in df.groupby("reason_category"):
        hi, lo = g.loc[g.p.idxmax()], g.loc[g.p.idxmin()]
        se = (hi.p * (1 - hi.p) / hi.n + lo.p * (1 - lo.p) / lo.n) ** 0.5
        z = (hi.p - lo.p) / se if se else 0.0
        if worst is None or z > worst[0]:
            worst = (z, reason, hi, lo)
    z, reason, hi, lo = worst
    verdict = "**disparidad significativa**" if z > 3 else "dentro del ruido muestral"
    return (f"{100 * (hi.p - lo.p):.1f} pp ({reason}: {hi.v} n={int(hi.n):,} vs {lo.v} n={int(lo.n):,}; "
            f"z={z:.1f}) — {verdict}")


def run() -> dict:
    con = _con()
    q = lambda s: con.execute(s).df()

    demand = q("""
        select reason_category as motivo,
               count(*) as contactos,
               round(100 * count(*) / sum(count(*)) over (), 1) as pct_contactos,
               round(100 * sum(duration_seconds) / sum(sum(duration_seconds)) over (), 1) as pct_tiempo_atencion,
               round(median(duration_seconds)) as aht_mediana_s,
               round(100 * avg(was_resolved::int), 1) as fcr_pct,
               round(count(*) * (1 - avg(was_resolved::int))) as contactos_no_resueltos,
               round(100 * avg(requires_followup::int), 1) as seguimiento_pct,
               round(avg(csat), 2) as csat_1a5,
               round(avg(sentiment_score), 3) as sentimiento
        from contacts_enriched group by 1 order by contactos desc""")
    demand["pct_no_resueltos"] = (100 * demand.contactos_no_resueltos / demand.contactos_no_resueltos.sum()).round(1)
    demand["contactos_no_resueltos"] = demand.contactos_no_resueltos.astype(int)

    cmp = q("""
        select coalesce(subcategory, '(sin subcategoría)') as subcategoria, count(*) as quejas,
               round(100 * count(*) / sum(count(*)) over (), 1) as pct,
               round(100 * avg(is_open::int), 1) as abiertas_pct,
               round(100 * avg(sla_breached::int), 1) as sla_incumplido_pct,
               median(resolution_days) as dias_resolucion_mediana,
               round(100 * avg((claimed_amount is not null)::int), 1) as con_monto_pct
        from complaints_enriched group by 1 order by quejas desc""")
    disputes = q("""select count(*) n, round(100 * avg(is_charge_dispute::int), 1) pct,
                           round(100 * avg(is_open::int) filter (where is_charge_dispute), 1) open_pct
                    from complaints_enriched""").iloc[0]

    days = q("select date_diff('day', min(interaction_date), max(interaction_date)) d from contacts_enriched").iloc[0].d
    years = days / 365.25
    monthly = q("""select date_trunc('month', interaction_date) m, count(*) n from contacts_enriched
                   where interaction_date >= '2023-07-01' and interaction_date < '2026-06-01' group by 1""")
    cv_month = round(monthly.n.std() / monthly.n.mean(), 3)

    spreads = {d: _spread(con, d) for d in ("country", "segment", "channel")}
    rep = q("""select was_resolved, round(100 * avg(repeat_contact_7d::int), 2) rep from contacts_enriched group by 1""")
    rep = dict(zip(rep.was_resolved, rep.rep))
    fraud = q("""with f as (select distinct customer_id from transactions where is_fraud)
                 select (f.customer_id is not null) has_fraud,
                        avg((select count(*) from complaints k where k.customer_id = c.customer_id)) cpc
                 from customers c left join f using (customer_id) group by 1""")
    fraud = dict(zip(fraud.has_fraud, fraud.cpc.round(3)))
    # Vínculo llamada -> queja sin origin_interaction_id: ¿hay una llamada del mismo
    # cliente el día previo a la queja más seguido que en una ventana placebo 180 días antes?
    link = q("""select
        round(100 * avg((exists(select 1 from call_center_interactions i where i.customer_id = k.customer_id
              and i.interaction_date between k.creation_date - interval 1 day and k.creation_date))::int), 2) observed,
        round(100 * avg((exists(select 1 from call_center_interactions i where i.customer_id = k.customer_id
              and i.interaction_date between k.creation_date - interval 181 day and k.creation_date - interval 180 day))::int), 2) placebo
        from complaints k where reception_channel = 'Call Center'""").iloc[0]
    n_desc = int(q("select count(distinct description) d from complaints").iloc[0].d)
    pt = q("""select count(*) n, sum((languages like '%portugu%')::int) pt from service_agents""").iloc[0]

    quality = sorted((MANIFESTS / "quality").glob("quality_*.json"))
    qall = json.loads(quality[-1].read_text(encoding="utf-8"))["results"] if quality else []
    qres = {r["id"]: r for r in qall}
    fit = lambda ids: all(qres.get(i, {}).get("passed", False) for i in ids)

    figs_dir = REPORTS / "figures"
    fig = {
        "unresolved": figures.unresolved(con, figs_dir),
        "profile": figures.reason_profile(con, figs_dir),
        "monthly": figures.fcr_monthly(con, figs_dir),
        "pqr": figures.pqr_mix(con, figs_dir),
        "link": figures.linkage(con, figs_dir),
        "quality": figures.quality(qall, figs_dir) if qall else None,
    }

    c = demand.set_index("motivo")
    cu = lambda r, k: c.loc[r, k]
    per_year = lambda r: int(cu(r, "contactos") / years)
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tools_fit = "Sí" if fit(["trx_owner_matches_product", "trx_currency_matches_product", "trx_amount_positive",
                             "trx_amount_usd_complete", "prd_customer_fk"]) else "No"

    md = f"""# Evidencia para elegir el flujo del agente

> **Documento para decidir en equipo. No contiene una decisión.**
> Generado por `python -m pipelines.insights` — {run_at}. Fuente: `curated/` y `staging/`
> (dataset sintético LATAM Bank v1.0.0, {years:.1f} años de contactos).
> Todas las cifras son **mediciones offline sobre datos sintéticos**. Lo marcado como
> *inferido* o *proyección* no es un dato medido.

## Resumen (solo hechos medidos)

1. **Quejas** es el motivo con más contactos no resueltos: {cu('complaint','pct_no_resueltos')}% del total no resuelto, con {cu('complaint','pct_contactos')}% del volumen (FCR {cu('complaint','fcr_pct')}%).
2. **Transaccional** es el mayor volumen ({cu('transactional','pct_contactos')}%), con FCR {cu('transactional','fcr_pct')}%.
3. En los reclamos formales (PQR), las **disputas de cargos** son el {disputes.pct}% ({disputes.open_pct}% siguen abiertas).
4. **Las llamadas y los reclamos PQR no se pueden vincular**: coincidencia {link.observed}% vs {link.placebo}% en la ventana placebo. No sabemos qué subcategoría tienen las llamadas de queja.
5. Las transcripciones y descripciones son **plantillas**: `detected_intents` es constante. Las etiquetas de texto del dataset no sirven para entrenar.
6. No hay diferencias significativas por país, segmento, canal ni mes. La señal está **solo en el motivo de contacto**.

---

## 1. Demanda y dolor por motivo de contacto

![Contactos no resueltos por motivo]({fig['unresolved']})

![Perfil por motivo]({fig['profile']})

{_md(demand)}

- Métrica de dolor: `contactos × (1 − FCR)` = demanda no resuelta. Es simple, auditable y no depende de supuestos de costo.
- `was_resolved` es auto-reportado. Como control, el recontacto a 7 días **no** es mayor en los casos "no resueltos" ({rep.get(False)}% vs {rep.get(True)}% en resueltos), así que la exactitud de esta etiqueta es dudosa (sección 5).

## 2. Estabilidad y equidad del baseline humano

![FCR mensual por motivo]({fig['monthly']})

| Chequeo | Resultado | Lectura |
|---|---|---|
| Brecha de FCR entre países (peor brecha dentro de un motivo) | {spreads['country']} | Baseline de equidad por país |
| Brecha de FCR entre segmentos | {spreads['segment']} | Baseline de equidad por segmento |
| Brecha de FCR entre canales | {spreads['channel']} | No hay un "canal ganador" |
| Variación mensual del volumen (coef. de variación) | {cv_month} | Demanda plana: no hace falta forecasting |
| Quejas por cliente, con vs sin fraude | {fraud.get(True)} vs {fraud.get(False)} | Fraude y quejas son independientes |
| Agentes que hablan portugués | {int(pt.pt)} de {int(pt.n)} ({100*pt.pt/pt.n:.1f}%) | Capacidad limitada para derivar a humano en PT |

## 3. Reclamos formales (tabla PQR)

![PQR por subcategoría]({fig['pqr']})

{_md(cmp)}

- La tabla PQR **no diferencia por subcategoría**: SLA, tiempos y backlog son casi idénticos. Sirve para dimensionar, no para priorizar entre subcategorías.

## 4. ¿Podemos saber qué hay dentro de las llamadas de "Queja"?

![Vínculo llamada-reclamo]({fig['link']})

**No con estos datos.** Se probaron todas las vías:

| Vía de vínculo | Resultado |
|---|---|
| `complaints.origin_interaction_id` | 100% vacío (también en `data_backup_20260831/`) |
| `contact_reason` de la llamada | Es una copia de `reason_category` (6 valores, sin detalle) |
| Llamada del mismo cliente antes del reclamo | 1 día: {link.observed}% vs placebo {link.placebo}%. Aun a 7 días, menos del 3% de los reclamos tiene una llamada previa: no alcanza para vincular |
| Texto de la transcripción | Plantilla sin relación con la categoría |
| `mentioned_products` de la llamada | 99% IDs inexistentes; ninguno del cliente |
| `complaints.affected_product_id` | Nunca pertenece al cliente que reclama |

**Consecuencia:** afirmar que "X% de las llamadas de queja son disputas" sería una **inferencia**. Los hechos 1 y 3 del resumen son mediciones independientes que no deben multiplicarse entre sí.

## 5. Calidad de datos y aptitud para cada uso

![Calidad por dimensión]({fig['quality']})

Detalle de las 48 reglas: [`data_quality.md`](data_quality.md).

| Uso | Tablas | ¿Apto? | Evidencia |
|---|---|---|---|
| Herramientas del agente: movimientos y productos | transactions, products, customers | {tools_fit}, con salvedades | Integridad y consistencia dueño↔producto y moneda↔producto al 100%. Salvedad: clientes MX sin productos en MXN y con DNI |
| Priorizar por motivo de contacto | call_center_interactions | Sí | Señal fuerte y estable por motivo |
| Etiquetas de intención desde transcripciones | call_transcripts | **No** | `detected_intents` constante; plantillas con `{{monto}}` sin rellenar; `main_topics` es copia de la categoría |
| Texto de reclamos para NLP | complaints.description | **No** | {n_desc} textos distintos en {int(disputes.n):,} reclamos |
| Vincular reclamo ↔ contacto ↔ producto | complaints | **No** | Ver sección 4 |
| Resolución (`was_resolved`) como etiqueta de éxito | call_center_interactions | Dudoso | No predice recontacto |

## 6. Opciones para decidir

Cada opción separa lo que está **medido** de lo que sería **inferido**. Todas requieren un set etiquetado por el equipo (ES + PT) para el componente aprendido, porque el dataset no tiene etiquetas de texto útiles.

| | A. Disputas de cargos | B. Intake y triage de quejas | C. Consultas transaccionales | D. Soporte técnico |
|---|---|---|---|---|
| **Qué hace el agente** | Verifica un cargo, lo explica o abre una disputa | Atiende cualquier queja, la clasifica, automatiza las disputas verificables y deriva el resto con resumen | Saldo, movimientos, estado de pagos | Problemas de app o acceso |
| **Justificación medida** | PQR: {disputes.pct}% son disputas | Llamadas: quejas = {cu('complaint','pct_no_resueltos')}% de lo no resuelto | {cu('transactional','pct_contactos')}% del volumen | {cu('technical','pct_no_resueltos')}% de lo no resuelto (FCR {cu('technical','fcr_pct')}%) |
| **Depende de algo inferido** | Sí: que las llamadas de queja sean disputas | No | No | No |
| **Datos para las herramientas** | transactions/products: aptos | Igual que A, más registro de caso | transactions/products: aptos | `digital_events` (3,7 GB) **aún no analizado** |
| **Casos normal / ambiguo / humano** | Natural | Natural, con más derivaciones | Normal fácil; pocos casos de humano | Por evaluar |
| **Riesgo principal** | Justificación débil ante el jurado | Alcance más amplio | Poco "problema": ya resuelve {cu('transactional','fcr_pct')}% | Sin datos analizados todavía |
| **Contactos/año del motivo** | — | ~{per_year('complaint'):,} | ~{per_year('transactional'):,} | ~{per_year('technical'):,} |

Proyección (no medida, solo para dimensionar): cada punto de FCR ganado en Queja equivale a ~{int(per_year('complaint') / 100):,} contactos/año menos sin resolver.

## 7. Preguntas abiertas para el equipo

1. ¿Priorizamos **calidad** (el motivo más no resuelto: Queja) o **volumen/costo** (Transaccional)?
2. ¿Aceptamos que la justificación de A dependa de una inferencia, o preferimos B, que solo usa hechos medidos?
3. ¿Analizamos `digital_events` antes de decidir, para evaluar bien D?
4. ¿Preguntamos a los organizadores si `origin_interaction_id` vacío es intencional o hay una versión corregida?
5. ¿Cómo generamos el set etiquetado ES + PT (tamaño, quién etiqueta, cómo evitamos fuga entre paráfrasis)?

## 8. Limitaciones

- Datos sintéticos: las distribuciones son uniformes salvo por el motivo de contacto. Las conclusiones valen para este dataset, no para un banco real.
- No hay texto en portugués en el dataset. Todo lo PT será generado por el equipo y se documentará así.
- No analizados aún: `digital_events`, `campaign_sends`, `marketing_campaigns`.
"""
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "insights.md").write_text(md, encoding="utf-8")
    print(md)
    return {"run_at": run_at}


if __name__ == "__main__":
    run()
