"""
figures.py  Constructeurs de figures Plotly purs (aucun état Dash).

Séparés des callbacks pour pouvoir être rendus et vérifiés hors application.
Chaque figure est construite en entier (pas de Patch) : les figures sont
petites (quelques Ko) et un Patch sur une figure vide provoque l'erreur
« Cannot patch undefined » au premier tick.
"""

from __future__ import annotations

import plotly.graph_objects as go
from dash import html

from .layout import FEATURE_COLS, FEATURE_LABELS

INK, INK3, INK4 = "#17191C", "#6B7280", "#9AA1AB"
LINE = "#EEF0F4"
ACCENT, DANGER, OK, VIOLET = "#0F6FB8", "#C2334D", "#2F7D5B", "#7A5AA8"
GREY = "#B9BEC6"
ZONE_OK, ZONE_ALERT = "#E7F3EC", "#F8E1E6"
FONT = "IBM Plex Sans, sans-serif"
MONO = "IBM Plex Mono, monospace"

MEMORY_LABEL = "Mémoire de la fenêtre (état caché h)"


def register_plotly_theme() -> None:
    """Thème commun à tous les graphiques (police, grilles, axes, survol,
    légende, marges, couleurs). Appelé une fois au démarrage : chaque figure
    en hérite, même construite ailleurs, sauf ce qu'elle redéfinit elle-même.
    Code couleur fixe : rouge = fraude/alerte, vert = normal, bleu = modèle,
    violet = mémoire (état caché)."""
    import plotly.io as pio
    axis = dict(showline=False, zeroline=False, gridcolor=LINE, gridwidth=1,
                ticks="", tickfont=dict(family=MONO, size=8, color=INK3),
                title=dict(font=dict(family=FONT, size=8.8, color=INK3), standoff=10),
                automargin=True)
    pio.templates["mkan"] = go.layout.Template(layout=go.Layout(
        font=dict(family=FONT, size=9.6, color=INK),
        paper_bgcolor="white", plot_bgcolor="white",
        colorway=[ACCENT, DANGER, OK, VIOLET, "#D98E04", GREY],
        margin=dict(t=28.8, b=32, l=41.6, r=12.8),
        xaxis=axis, yaxis=axis,
        # Position de légende laissée à chaque figure (elles la placent déjà) :
        # seuls fond et police sont harmonisés ici.
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=8.4, color=INK3)),
        hoverlabel=dict(bgcolor="white", bordercolor=LINE,
                        font=dict(family=MONO, size=8.8, color=INK)),
        title=dict(font=dict(family=FONT, size=9.6, color=INK3), x=0, xanchor="left"),
    ))
    pio.templates.default = "plotly_white+mkan"


def _base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, paper_bgcolor="white", plot_bgcolor="white",
                      font=dict(family=FONT, size=9.6, color=INK))
    return fig


# ── Jauge : score du compte inspecté ─────────────────────────────────────────

def gauge_state(score: float | None, tau: float, *, flash: int = 0) -> dict:
    """Style (variables CSS) de la jauge animée `.mk-gauge` (voir style.css).

    flash : parité 0/1, basculée à chaque changement de compte ; changer le nom
    de l'animation relance le surlignage du compte."""
    waiting = score is None
    p = 0.0 if waiting else min(max(float(score), 0.0), 1.0)
    color = GREY if waiting else (DANGER if p >= tau else OK)
    return {
        "--p": round(p, 4), "--n": int(round(p * 100)), "--c": color, "--tau": tau,
        "--flash": f"mk-flash-{flash}",
    }


def gauge_meta(score: float | None, tau: float, mode: str,
               history: list[float] | None = None):
    """Verdict, mode de sélection et historique des derniers scores du compte."""
    if score is None:
        word, sub = "EN ATTENTE", "aucune fenêtre complète pour l'instant"
    else:
        word = "ALERTE FRAUDE" if score >= tau else "TRANSACTION NORMALE"
        sub = f"score {score * 100:.1f} % · seuil τ = {tau * 100:.0f} %"
    hist = list(history or [])[-24:]
    bars = [html.I(style={"height": f"{max(s, 0.02) * 100:.0f}%",
                          "background": DANGER if s >= tau else OK,
                          "opacity": .35 + .65 * (i + 1) / len(hist)},
                   title=f"{s * 100:.1f} %")
            for i, s in enumerate(hist)]
    spark = html.Div([
        html.Div([html.Span(f"SES {len(hist)} DERNIERS SCORES" if hist else "AUCUN SCORE"),
                  html.Span("le plus récent →")], className="mk-g-spark-k"),
        html.Div(bars + [html.B(style={"bottom": f"{tau * 100:.0f}%"})], className="mk-g-bars"),
    ], className="mk-g-spark")
    return [html.Div(word, className="mk-g-word"),
            html.Div([sub, html.Br(), mode], className="mk-g-sub"),
            spark]


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
                           font=dict(size=10.4, color=INK4))
        fig.update_xaxes(visible=False); fig.update_yaxes(visible=False)
        return _base_layout(fig, 272)

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
            textfont=dict(family=MONO, size=8, color=INK3), cliponaxis=False,
            customdata=[r["detail"] for r in sub],
            hovertemplate="<b>%{y}</b><br>influence : %{x:.1f} %<br>%{customdata}<extra></extra>",
        ))
    top = max(r["share"] for r in rows)
    fig.update_layout(barmode="overlay", bargap=0.35,
                      legend=dict(orientation="h", y=1.12, x=0, font=dict(size=8)),
                      margin=dict(t=27.2, b=35.2, l=6.4, r=35.2))
    fig.update_xaxes(range=[0, max(top * 1.3, 10)], gridcolor=LINE, zeroline=False,
                     ticksuffix=" %", tickfont=dict(family=MONO, size=8, color=INK3),
                     title=dict(text="Part de l'influence sur la décision", font=dict(size=8.8)))
    fig.update_yaxes(automargin=True, tickfont=dict(size=8.8, color=INK))
    return _base_layout(fig, 272)
