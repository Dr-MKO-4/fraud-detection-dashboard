"""
figures.py  Constructeurs de figures Plotly purs (aucun état Dash).

Séparés des callbacks pour pouvoir être rendus et vérifiés hors application.
Chaque figure est construite en entier (pas de Patch) : les figures sont
petites (quelques Ko) et un Patch sur une figure vide provoque l'erreur
« Cannot patch undefined » au premier tick.
"""

from __future__ import annotations

import plotly.graph_objects as go

from .layout import FEATURE_COLS, FEATURE_LABELS

INK, INK3, INK4 = "#17191C", "#6B7280", "#9AA1AB"
LINE = "#EDECE6"
ACCENT, DANGER, OK, VIOLET = "#0F6FB8", "#C2334D", "#2F7D5B", "#7A5AA8"
GREY = "#B9BEC6"
ZONE_OK, ZONE_ALERT = "#E7F3EC", "#F8E1E6"
FONT = "IBM Plex Sans, sans-serif"
MONO = "IBM Plex Mono, monospace"

MEMORY_LABEL = "Mémoire de la fenêtre (état caché h)"


def _base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, paper_bgcolor="white", plot_bgcolor="white",
                      font=dict(family=FONT, size=12, color=INK))
    return fig


# ── Jauge : score du compte inspecté ─────────────────────────────────────────

def gauge_figure(score: float | None, tau: float, account: str = "") -> go.Figure:
    """Score de risque (0-100 %) d'UN compte, comparé au seuil de décision τ.

    Deux zones : verte sous τ (transaction jugée normale), rouge au-dessus
    (alerte). Le trait noir marque τ. score=None : aucun compte scoré."""
    waiting = score is None
    pct = 0.0 if waiting else round(score * 100, 1)
    alert = (not waiting) and score >= tau
    color = GREY if waiting else (DANGER if alert else OK)
    cut = tau * 100

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=pct,
        number=dict(suffix=" %", font=dict(size=42, family=MONO, color=color)),
        domain=dict(x=[0, 1], y=[0.2, 1]),
        gauge=dict(
            axis=dict(range=[0, 100], tickvals=[0, 25, 50, 75, 100],
                      ticktext=["0", "25", "50", "75", "100"],
                      tickfont=dict(size=10, color=INK4)),
            bar=dict(color=color, thickness=0.3),
            bgcolor="white", borderwidth=0,
            steps=[dict(range=[0, cut], color=ZONE_OK),
                   dict(range=[cut, 100], color=ZONE_ALERT)],
            threshold=dict(line=dict(color=INK, width=3), thickness=0.9, value=cut),
        ),
    ))
    if waiting:
        verdict = "<b style='color:#9AA1AB'>EN ATTENTE</b><br>" \
                  "<span style='color:#9AA1AB;font-size:11px'>aucune fenêtre complète</span>"
    else:
        word = "ALERTE FRAUDE" if alert else "TRANSACTION NORMALE"
        who = f"compte {account} · " if account else ""
        verdict = (f"<b style='color:{color}'>{word}</b><br>"
                   f"<span style='color:{INK3};font-size:11px'>{who}seuil τ = {cut:.0f} %</span>")
    fig.add_annotation(text=verdict, x=0.5, y=0.0, xref="paper", yref="paper",
                       showarrow=False, font=dict(size=13, family=FONT), align="center")
    fig.update_layout(margin=dict(t=25, b=8, l=28, r=28))
    return _base_layout(fig, 300)


# ── Contributions des variables ──────────────────────────────────────────────

def contribution_rows(top_features: list[dict]) -> list[dict]:
    """Regroupe les contributions brutes du modèle en lignes affichables.

    Le modèle explique 28 entrées : les 12 variables et les 16 unités de la
    mémoire récurrente h[0..15]. Ces dernières n'ont pas de sens métier
    individuel : elles sont additionnées en UNE ligne « mémoire ». Les parts
    sont renormalisées pour sommer à 100 %."""
    var, memory, units = {}, 0.0, []
    for f in top_features:
        label, c = f["label"], float(f["contribution"])
        if label.startswith("h["):
            memory += c
            units.append((label, c))
        else:
            var[label] = c
    total = sum(var.values()) + memory
    if total <= 0:
        return []
    rows = [dict(label=FEATURE_LABELS.get(k, k), key=k, share=v / total * 100,
                 kind="var", detail="") for k, v in var.items()]
    # Détail des 16 unités de mémoire, visible au survol de la barre violette
    units.sort(key=lambda u: -u[1])
    top = " · ".join(f"{name} {c / total * 100:.1f} %" for name, c in units[:6])
    rest = sum(c for _, c in units[6:]) / total * 100
    detail = (f"Détail des {len(units)} unités : {top}"
              + (f" · autres {rest:.1f} %" if rest > 0 else ""))
    rows.append(dict(label=MEMORY_LABEL, key="h", share=memory / total * 100,
                     kind="memory", detail=detail))
    rows.sort(key=lambda r: r["share"])
    return rows


def contribution_figure(rows: list[dict]) -> go.Figure:
    """Barres horizontales : part de l'influence de chaque variable (somme = 100 %)."""
    fig = go.Figure()
    if not rows:
        fig.add_annotation(text="En attente d'un compte à fenêtre complète",
                           xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
                           font=dict(size=13, color=INK4))
        fig.update_xaxes(visible=False); fig.update_yaxes(visible=False)
        return _base_layout(fig, 340)

    # Arrondi au plus grand reste : arrondir chaque part séparément donnait un total
    # affiché de 96 à 99 %, alors que les parts somment exactement à 100 %.
    raw = [r["share"] for r in rows]
    base = [int(v) for v in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)[
            :max(0, 100 - sum(base))]:
        base[i] += 1
    for r, b in zip(rows, base):
        r["shown"] = b

    for kind, name, color in (("var", "Variable d'entrée", ACCENT),
                              ("memory", MEMORY_LABEL, VIOLET)):
        sub = [r for r in rows if r["kind"] == kind]
        if not sub:
            continue
        fig.add_trace(go.Bar(
            x=[r["share"] for r in sub], y=[r["label"] for r in sub],
            orientation="h", name=name, marker_color=color,
            text=[f"{r['shown']} %" for r in sub], textposition="outside",
            textfont=dict(family=MONO, size=10, color=INK3), cliponaxis=False,
            customdata=[r["detail"] for r in sub],
            hovertemplate="<b>%{y}</b><br>influence : %{x:.1f} %<br>%{customdata}<extra></extra>",
        ))
    top = max(r["share"] for r in rows)
    fig.update_layout(barmode="overlay", bargap=0.35,
                      legend=dict(orientation="h", y=1.12, x=0, font=dict(size=10)),
                      margin=dict(t=34, b=44, l=8, r=44))
    fig.update_xaxes(range=[0, max(top * 1.3, 10)], gridcolor=LINE, zeroline=False,
                     ticksuffix=" %", tickfont=dict(family=MONO, size=10, color=INK3),
                     title=dict(text="Part de l'influence sur la décision", font=dict(size=11)))
    fig.update_yaxes(automargin=True, tickfont=dict(size=11, color=INK))
    return _base_layout(fig, 340)
