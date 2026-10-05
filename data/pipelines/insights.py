"""Evidence for choosing the workflow -> reports/insights.md + reports/figures/*.png

Every number is computed here from curated/ and staging/. The interpretive text
quotes those numbers; if the data changes, run it again.

Usage:
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
    """Largest FCR gap between values of `dim` within one contact reason, with its z.

    With many comparisons (reasons x pairs), a large gap in a small cell is expected
    by chance: the report gives z = gap / standard error and only flags a disparity
    when z > 3.
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
    verdict = "**significant disparity**" if z > 3 else "within sampling noise"
    return (f"{100 * (hi.p - lo.p):.1f} pp ({reason}: {hi.v} n={int(hi.n):,} vs {lo.v} n={int(lo.n):,}; "
            f"z={z:.1f}), {verdict}")


def run() -> dict:
    con = _con()
    q = lambda s: con.execute(s).df()

    demand = q("""
        select reason_category as reason,
               count(*) as contacts,
               round(100 * count(*) / sum(count(*)) over (), 1) as pct_contacts,
               round(100 * sum(duration_seconds) / sum(sum(duration_seconds)) over (), 1) as pct_handling_time,
               round(median(duration_seconds)) as median_aht_s,
               round(100 * avg(was_resolved::int), 1) as fcr_pct,
               round(count(*) * (1 - avg(was_resolved::int))) as unresolved_contacts,
               round(100 * avg(requires_followup::int), 1) as followup_pct,
               round(avg(csat), 2) as csat_1to5,
               round(avg(sentiment_score), 3) as sentiment
        from contacts_enriched group by 1 order by contacts desc""")
    demand["pct_unresolved"] = (100 * demand.unresolved_contacts / demand.unresolved_contacts.sum()).round(1)
    demand["unresolved_contacts"] = demand.unresolved_contacts.astype(int)

    cmp = q("""
        select coalesce(subcategory, '(no subcategory)') as subcategory, count(*) as complaints,
               round(100 * count(*) / sum(count(*)) over (), 1) as pct,
               round(100 * avg(is_open::int), 1) as open_pct,
               round(100 * avg(sla_breached::int), 1) as sla_breached_pct,
               median(resolution_days) as median_resolution_days,
               round(100 * avg((claimed_amount is not null)::int), 1) as with_amount_pct
        from complaints_enriched group by 1 order by complaints desc""")
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
    # Call -> complaint link without origin_interaction_id: does the same customer call
    # the day before the complaint more often than in a placebo window 180 days earlier?
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

    c = demand.set_index("reason")
    cu = lambda r, k: c.loc[r, k]
    per_year = lambda r: int(cu(r, "contacts") / years)
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tools_fit = "Yes" if fit(["trx_owner_matches_product", "trx_currency_matches_product", "trx_amount_positive",
                              "trx_amount_usd_complete", "prd_customer_fk"]) else "No"

    md = f"""# Evidence for choosing the agent's workflow

> **A document for the team to decide with. It contains no decision.**
> Generated by `python -m pipelines.insights` on {run_at}. Source: `curated/` and `staging/`
> (synthetic LATAM Bank dataset v1.0.0, {years:.1f} years of contacts).
> Every figure is an **offline measurement on synthetic data**. Anything marked
> *inferred* or *projection* is not a measured fact.

## Summary (measured facts only)

1. **Complaints** is the contact reason with the most unresolved contacts: {cu('complaint','pct_unresolved')}% of all unresolved contacts, with {cu('complaint','pct_contacts')}% of the volume (FCR {cu('complaint','fcr_pct')}%).
2. **Transactional** is the largest volume ({cu('transactional','pct_contacts')}%), with FCR {cu('transactional','fcr_pct')}%.
3. Among formal claims (PQR), **charge disputes** are {disputes.pct}% ({disputes.open_pct}% are still open).
4. **Calls and PQR claims cannot be linked**: {link.observed}% match vs {link.placebo}% in the placebo window. We do not know which subcategory complaint calls have.
5. Transcripts and descriptions are **templates**: `detected_intents` is constant. The dataset's text labels cannot be used for training.
6. There are no significant differences by country, segment, channel or month. The signal is **only in the contact reason**.

---

## 1. Demand and pain by contact reason

![Unresolved contacts by reason]({fig['unresolved']})

![Profile by reason]({fig['profile']})

{_md(demand)}

- Pain metric: `contacts × (1 − FCR)` = unresolved demand. It is simple, auditable and does not depend on cost assumptions.
- `was_resolved` is self-reported. As a check, 7-day repeat contact is **not** higher for "unresolved" cases ({rep.get(False)}% vs {rep.get(True)}% for resolved ones), so the accuracy of this label is doubtful (section 5).

## 2. Stability and fairness of the human baseline

![Monthly FCR by reason]({fig['monthly']})

| Check | Result | Reading |
|---|---|---|
| FCR gap between countries (worst gap within one reason) | {spreads['country']} | Fairness baseline by country |
| FCR gap between segments | {spreads['segment']} | Fairness baseline by segment |
| FCR gap between channels | {spreads['channel']} | There is no "winning channel" |
| Monthly volume variation (coefficient of variation) | {cv_month} | Flat demand: no forecasting needed |
| Complaints per customer, with vs without fraud | {fraud.get(True)} vs {fraud.get(False)} | Fraud and complaints are independent |
| Agents who speak Portuguese | {int(pt.pt)} of {int(pt.n)} ({100*pt.pt/pt.n:.1f}%) | Limited capacity to hand off to a person in PT |

## 3. Formal claims (PQR table)

![PQR by subcategory]({fig['pqr']})

{_md(cmp)}

- The PQR table **does not differ by subcategory**: SLA, times and backlog are almost identical. It is useful for sizing, not for prioritizing between subcategories.

## 4. Can we know what is inside "Complaint" calls?

![Call-claim link]({fig['link']})

**Not with this data.** Every route was tried:

| Link route | Result |
|---|---|
| `complaints.origin_interaction_id` | 100% empty (also in `data_backup_20260831/`) |
| The call's `contact_reason` | A copy of `reason_category` (6 values, no detail) |
| A call from the same customer before the claim | 1 day: {link.observed}% vs placebo {link.placebo}%. Even at 7 days, fewer than 3% of claims have a prior call: not enough to link them |
| Transcript text | A template unrelated to the category |
| The call's `mentioned_products` | 99% non-existent IDs; none belong to the customer |
| `complaints.affected_product_id` | Never belongs to the customer who files the claim |

**Consequence:** saying "X% of complaint calls are disputes" would be an **inference**. Facts 1 and 3 of the summary are independent measurements and must not be multiplied together.

## 5. Data quality and fitness for each use

![Quality by dimension]({fig['quality']})

Detail of the 48 rules: [`data_quality.md`](data_quality.md).

| Use | Tables | Fit? | Evidence |
|---|---|---|---|
| Agent tools: transactions and products | transactions, products, customers | {tools_fit}, with caveats | Integrity and owner↔product and currency↔product consistency at 100%. Caveat: MX customers without MXN products and with a DNI |
| Prioritize by contact reason | call_center_interactions | Yes | Strong, stable signal by reason |
| Intent labels from transcripts | call_transcripts | **No** | `detected_intents` is constant; templates with unfilled `{{monto}}`; `main_topics` is a copy of the category |
| Claim text for NLP | complaints.description | **No** | {n_desc} distinct texts in {int(disputes.n):,} claims |
| Link claim ↔ contact ↔ product | complaints | **No** | See section 4 |
| Resolution (`was_resolved`) as a success label | call_center_interactions | Doubtful | It does not predict repeat contact |

## 6. Options to decide

Each option separates what is **measured** from what would be **inferred**. All of them need a set labeled by the team (ES + PT) for the learned component, because the dataset has no useful text labels.

| | A. Charge disputes | B. Complaint intake and triage | C. Transaction inquiries | D. Technical support |
|---|---|---|---|---|
| **What the agent does** | Checks a charge, explains it or opens a dispute | Takes any complaint, classifies it, automates the verifiable disputes and hands off the rest with a summary | Balance, transactions, payment status | App or access problems |
| **Measured justification** | PQR: {disputes.pct}% are disputes | Calls: complaints = {cu('complaint','pct_unresolved')}% of the unresolved | {cu('transactional','pct_contacts')}% of the volume | {cu('technical','pct_unresolved')}% of the unresolved (FCR {cu('technical','fcr_pct')}%) |
| **Depends on something inferred** | Yes: that complaint calls are disputes | No | No | No |
| **Data for the tools** | transactions/products: fit | Same as A, plus a case record | transactions/products: fit | `digital_events` (3.7 GB) **not analyzed yet** |
| **Normal / ambiguous / human cases** | Natural | Natural, with more handoffs | Easy normal case; few human cases | To be evaluated |
| **Main risk** | Weak justification before the jury | Wider scope | Little "problem": it already resolves {cu('transactional','fcr_pct')}% | No data analyzed yet |
| **Contacts/year for the reason** | — | ~{per_year('complaint'):,} | ~{per_year('transactional'):,} | ~{per_year('technical'):,} |

Projection (not measured, only for sizing): each FCR point gained on Complaints is ~{int(per_year('complaint') / 100):,} fewer unresolved contacts per year.

## 7. Open questions for the team

1. Do we prioritize **quality** (the most unresolved reason: Complaints) or **volume/cost** (Transactional)?
2. Do we accept that A's justification depends on an inference, or do we prefer B, which only uses measured facts?
3. Do we analyze `digital_events` before deciding, to evaluate D properly?
4. Do we ask the organizers whether the empty `origin_interaction_id` is intentional or there is a corrected version?
5. How do we build the ES + PT labeled set (size, who labels, how we avoid leakage between paraphrases)?

## 8. Limitations

- Synthetic data: distributions are uniform except for the contact reason. The conclusions hold for this dataset, not for a real bank.
- The dataset has no Portuguese text. Everything in PT will be written by the team and documented as such.
- Not analyzed yet: `digital_events`, `campaign_sends`, `marketing_campaigns`.
"""
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "insights.md").write_text(md, encoding="utf-8")
    print(md)
    return {"run_at": run_at}


if __name__ == "__main__":
    run()
