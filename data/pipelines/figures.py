"""Gráficos estáticos (PNG) para reports/. Se generan desde insights.py.

Convenciones (skill dataviz): paleta categórica validada, énfasis = un color
contra gris, marcas finas, grilla sólida tenue, un solo eje por gráfico
(medidas distintas -> small multiples), texto en tinta neutra, no en color de serie.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
MUTED = "#c9c8c2"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]  # validado: validate_palette.js
FOCUS = SERIES[0]

REASON_ORDER = ["transactional", "product", "complaint", "technical", "commercial", "retention"]
REASON_LABEL = {"transactional": "Transaccional", "product": "Producto", "complaint": "Queja",
                "technical": "Técnico", "commercial": "Comercial", "retention": "Retención"}

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "text.color": INK, "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
    "axes.axisbelow": True, "legend.frameon": False,
})


def _save(fig, out: Path, name: str) -> str:
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / name, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f"figures/{name}"


def _hbar(ax, labels, values, focus, fmt):
    colors = [FOCUS if f else MUTED for f in focus]
    ax.barh(labels, values, color=colors, height=0.62, edgecolor=SURFACE, linewidth=2)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    vmax = max(values)
    for y, v in enumerate(values):
        ax.text(v + vmax * 0.015, y, fmt(v), va="center", fontsize=9, color=INK_2)
    ax.set_xlim(0, vmax * 1.22)


def unresolved(con, out: Path) -> str:
    df = con.execute("""select reason_category r, count(*) * (1 - avg(was_resolved::int)) u
                        from contacts_enriched group by 1 order by u desc""").df()
    total = df.u.sum()
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    _hbar(ax, [REASON_LABEL[r] for r in df.r], list(df.u), [r == "complaint" for r in df.r],
          lambda v: f"{v:,.0f}  ({100 * v / total:.0f}%)")
    ax.set_title("Contactos no resueltos por motivo (3 años)")
    ax.set_xlabel("contactos × (1 − FCR)")
    return _save(fig, out, "01_no_resueltos_por_motivo.png")


def reason_profile(con, out: Path) -> str:
    df = con.execute("""select reason_category r,
            100 * count(*) / sum(count(*)) over () vol, 100 * avg(was_resolved::int) fcr,
            median(duration_seconds) aht, avg(csat) csat
        from contacts_enriched group by 1""").df().set_index("r").loc[REASON_ORDER]
    panels = [("vol", "% de contactos", "{:.0f}%"), ("fcr", "FCR (%)", "{:.0f}%"),
              ("aht", "AHT mediana (s)", "{:.0f}"), ("csat", "CSAT medio (1–5)", "{:.2f}")]
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.2))
    labels = [REASON_LABEL[r] for r in df.index]
    for i, (ax, (col, title, fmt)) in enumerate(zip(axes, panels)):
        _hbar(ax, labels, list(df[col]), [r == "complaint" for r in df.index], lambda v, f=fmt: f.format(v))
        ax.set_title(title, fontsize=10)
        if i:
            ax.set_yticklabels([])
    fig.suptitle("Perfil por motivo de contacto: volumen, resolución, duración y satisfacción",
                 x=0.01, ha="left", fontweight="bold", fontsize=11, y=1.04)
    return _save(fig, out, "02_perfil_por_motivo.png")


def fcr_monthly(con, out: Path) -> str:
    df = con.execute("""select date_trunc('month', interaction_date) m, reason_category r, 100 * avg(was_resolved::int) fcr
                        from contacts_enriched
                        where interaction_date >= '2023-07-01' and interaction_date < '2026-06-01'
                        group by all order by m""").df()
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ends = []
    for i, r in enumerate(REASON_ORDER):
        s = df[df.r == r]
        ax.plot(s.m, s.fcr, color=SERIES[i], linewidth=2, label=REASON_LABEL[r])
        ends.append([s.fcr.iloc[-1], REASON_LABEL[r], s.m.iloc[-1]])
    ends.sort(key=lambda e: e[0])
    for k in range(1, len(ends)):  # separa etiquetas finales que chocan (mín. 4.5 pp)
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 4.5)
    for y, label, x in ends:
        ax.text(x, y, f"  {label}", va="center", fontsize=9, color=INK_2)
    ax.set_ylim(0, 100)
    ax.set_ylabel("FCR mensual (%)")
    ax.set_title("FCR mensual por motivo: estable en el tiempo, la brecha de Queja es persistente")
    ax.legend(ncol=6, loc="upper left", bbox_to_anchor=(0, -0.12), fontsize=8.5)
    ax.margins(x=0.12)
    return _save(fig, out, "03_fcr_mensual_por_motivo.png")


def pqr_mix(con, out: Path) -> str:
    df = con.execute("""select coalesce(subcategory, '(sin subcategoría)') s, count(*) n, 100 * avg(is_open::int) op
                        from complaints_enriched group by 1 order by n desc""").df()
    names = {"unrecognized_charge": "Cargo no reconocido", "improper_fee": "Cobro indebido",
             "app_issue": "Problema con app", "branch_service": "Atención en sucursal",
             "service_quality": "Calidad de servicio"}
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    open_pct = dict(zip(df.n, df.op))
    _hbar(ax, [names.get(s, s) for s in df.s], list(df.n),
          [s in ("unrecognized_charge", "improper_fee") for s in df.s],
          lambda v: f"{v:,.0f}  ·  {open_pct[v]:.0f}% abiertas")
    ax.set_xlim(0, ax.get_xlim()[1] * 1.25)
    ax.set_title("Reclamos formales (PQR) por subcategoría — azul: disputas de cargos")
    ax.set_xlabel("reclamos")
    return _save(fig, out, "04_pqr_por_subcategoria.png")


def linkage(con, out: Path) -> str:
    rows = []
    for w in (1, 3, 7):
        r = con.execute(f"""select
            100 * avg((exists(select 1 from call_center_interactions i where i.customer_id = k.customer_id
                  and i.interaction_date between k.creation_date - interval {w} day and k.creation_date))::int),
            100 * avg((exists(select 1 from call_center_interactions i where i.customer_id = k.customer_id
                  and i.interaction_date between k.creation_date - interval {180 + w} day
                                             and k.creation_date - interval 180 day))::int)
            from complaints k where reception_channel = 'Call Center'""").fetchone()
        rows.append((w, *r))
    fig, ax = plt.subplots(figsize=(7, 3.4))
    x = range(len(rows))
    wbar = 0.36
    ax.bar([i - wbar / 2 - 0.01 for i in x], [r[1] for r in rows], wbar, color=FOCUS, label="Ventana real (antes del reclamo)")
    ax.bar([i + wbar / 2 + 0.01 for i in x], [r[2] for r in rows], wbar, color=MUTED, label="Placebo (misma ventana, 180 días antes)")
    for i, r in enumerate(rows):
        ax.text(i - wbar / 2, r[1], f"{r[1]:.2f}%", ha="center", va="bottom", fontsize=8.5, color=INK_2)
        ax.text(i + wbar / 2, r[2], f"{r[2]:.2f}%", ha="center", va="bottom", fontsize=8.5, color=INK_2)
    ax.set_xticks(list(x), [f"{r[0]} día{'s' if r[0] > 1 else ''}" for r in rows])
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("% de reclamos con una llamada\ndel mismo cliente en la ventana")
    ax.set_title("Menos del 3% de los reclamos por Call Center tiene una llamada previa del cliente")
    ax.legend(loc="upper left", fontsize=8.5)
    return _save(fig, out, "05_vinculo_llamada_reclamo.png")


def quality(results: list[dict], out: Path) -> str:
    order = ["integrity", "timeliness", "validity", "uniqueness", "consistency", "completeness", "accuracy"]
    names = {"integrity": "Integridad", "timeliness": "Oportunidad", "validity": "Validez", "uniqueness": "Unicidad",
             "consistency": "Consistencia", "completeness": "Completitud", "accuracy": "Exactitud"}
    agg = {d: [0, 0] for d in order}
    for r in results:
        agg[r["dimension"]][0] += r["passed"]
        agg[r["dimension"]][1] += 1
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    labels = [names[d] for d in order]
    ax.barh(labels, [agg[d][1] for d in order], color="#efeeea", height=0.62)
    ax.barh(labels, [agg[d][0] for d in order], color=FOCUS, height=0.62)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    for y, d in enumerate(order):
        ax.text(agg[d][1] + 0.2, y, f"{agg[d][0]}/{agg[d][1]} reglas OK", va="center", fontsize=9, color=INK_2)
    ax.set_xlim(0, max(v[1] for v in agg.values()) * 1.3)
    ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.set_xlabel("reglas")
    ax.set_title("Reglas de calidad que pasan, por dimensión (staging)")
    return _save(fig, out, "06_calidad_por_dimension.png")
