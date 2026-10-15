"""
layout.py  Dashboard MKAN v3.0
Modes : SIMULÉ (flux MoMTSim + injection de scénarios) | LIVE (API REST / KAFKA)
"""

from __future__ import annotations

from dash import dcc, html


FEATURE_COLS = [
    "delta_B_orig", "delta_B_dest", "r1", "r2",
    "flag_anomalie", "delta_commission", "var_agent_split",
    "rho_rupture", "rho_refund", "v1h", "flag_nuit", "rho_nouveau",
]

FEATURE_LABELS = {
    "delta_B_orig":     "Δ Solde origine",
    "delta_B_dest":     "Δ Solde destination",
    "r1":               "Ratio r1",
    "r2":               "Ratio r2",
    "flag_anomalie":    "Flag anomalie",
    "delta_commission": "Δ Commission",
    "var_agent_split":  "Var. agent split",
    "rho_rupture":      "ρ Rupture",
    "rho_refund":       "ρ Remboursement",
    "v1h":              "Vol. 1h",
    "flag_nuit":        "Flag nuit",
    "rho_nouveau":      "ρ Nouveaux dest.",
}

LOG_FEATURES = {"delta_B_orig", "delta_B_dest", "r1", "r2", "v1h"}
SIM_ACCOUNTS = [f"ACCT_{i:03d}" for i in range(1, 11)]
W = 10

STAGES = [
    ("input", "Entrée",         "12 × 10",      "features × W"),
    ("norm",  "Normalisation",  "log1p + z",    "5 queues lourdes"),
    ("cell",  "Cellule MKAN",   "hidden 16",    "4 portes KAN"),
    ("basis", "Base",           "M=8 · K=2",    "gaussiennes + Fourier"),
    ("head",  "Tête",           "linéaire",     "16 → 1"),
    ("out",   "Sortie",         "σ(z) ∈ [0,1]", "seuil τ"),
]

VIEWS = [
    ("scoring", "Scoring live"), ("window", "Fenêtres"), ("metrics", "Métriques"),
    ("model", "Modèle"), ("audit", "Audit"), ("env", "Environnement"), ("aide", "Aide"),
]

# ── Cadence de scoring (valeur = intervalle en ms) ───────────────────────────
SPEED_OPTIONS = [
    {"label": "0,5 s", "value": "500"},
    {"label": "1 s",   "value": "1000"},
    {"label": "2 s",   "value": "2000"},
    {"label": "5 s",   "value": "5000"},
]

# ── Scénarios de fraude à injecter ────────────────────────────────────────────
SCENARIOS = [
    ("btn-inject-ato",    "ATO",          "Prise de contrôle de compte"),
    ("btn-inject-refund", "REMBOURSEMENT","Fraude au remboursement"),
    ("btn-inject-fake",   "FAUX CRED.",   "Fausses accréditations"),
    ("btn-inject-split",  "SPLIT",        "Fractionnement agent"),
    ("btn-inject-smurf",  "SMURFING",     "Structuration multi-mules"),
    ("btn-inject-anomaly","ANOMALIE",     "Transaction anormale / erreur"),
]


# ── Fragments UI ─────────────────────────────────────────────────────────────

def _kicker(txt: str) -> html.Div:
    return html.Div(txt, className="mk-kicker")


def chart_help(what: str, how: str, legend: list | None = None) -> html.Div:
    """Description courte d'un graphique : ce qu'il montre, comment le lire,
    et une légende de couleurs [(couleur, libellé), ...]."""
    items = [html.Div([html.B("Ce que ça montre. "), what]),
             html.Div([html.B("Comment le lire. "), how])]
    if legend:
        items.append(html.Div(
            [html.Span([html.I(style={"background": c}), lbl], className="mk-chip")
             for c, lbl in legend], className="mk-chips"))
    return html.Div(items, className="mk-help")


def _tele(key: str, value_id: str, cls: str = "") -> html.Div:
    return html.Div([
        html.Span(key, className="k"),
        html.Span("", id=value_id, className=f"v {cls}".strip()),
    ], className="mk-tele")


def _header() -> html.Header:
    return html.Header([
        html.Div([
            html.Div([
                html.Div("M", className="mk-mark"),
                html.Span("MKAN", style={"fontSize": "14px", "fontWeight": 600}),
                html.Span("v3.0", className="mk-ver"),
            ], className="mk-brand"),

            dcc.Tabs(
                id="tabs-main", value="scoring", className="mk-nav",
                parent_className="mk-nav",
                children=[dcc.Tab(label=lbl, value=val, className="tab",
                                  selected_className="tab--selected")
                          for val, lbl in VIEWS],
            ),

            html.Div(style={"flex": "1 1 0", "minWidth": 0}),

            html.Div([
                html.Div([html.Span(className="mk-dot", id="stream-dot"),
                          html.Span("FLUX EN PAUSE", id="stream-label")], className="mk-chip"),
                html.Div([html.Span("ENV", style={"color": "var(--ink-4)"}),
                          html.Span("SIMULÉ · BAC À SABLE", id="env-label")], className="mk-chip"),
                html.Button("AN", id="btn-profile", n_clicks=0, title="Profil analyste",
                            style={"width": "27px", "height": "27px", "padding": 0,
                                   "border": "1px solid #DCDBD4", "background": "#fff",
                                   "fontFamily": "var(--mono)", "fontSize": "10.5px",
                                   "fontWeight": 600, "cursor": "pointer"}),
            ], style={"display": "flex", "alignItems": "center", "gap": "10px", "flex": "none"}),
        ], className="mk-bar"),

        html.Div([
            _tele("Compte inspecté", "tele-account"),
            _tele("Sa fenêtre",     "tele-window",  "accent"),
            _tele("Décisions",       "tele-count"),
            _tele("Alertes",         "tele-alerts",  "danger"),
            _tele("Score du compte", "tele-last"),
            _tele("Heure simulée",   "tele-clock"),
            _tele("Dernier lot",      "tele-cursor"),
            _tele("Dérive JS (bits)", "tele-drift"),
            _tele("Seuil τ",        "tele-tau"),
            _tele("Latence p50",    "tele-latency"),
            html.Div(style={"flex": 1}),
            html.Div([
                html.Span("checkpoint mkan_final.pt · 4f7ac2e",
                          style={"fontFamily": "var(--mono)", "fontSize": "10.5px",
                                 "color": "var(--ink-4)"}),
                html.A("JOURNAL D'AUDIT →", href="#", id="link-audit",
                       style={"fontFamily": "var(--mono)", "fontSize": "10.5px"}),
            ], style={"display": "flex", "alignItems": "center", "gap": "14px",
                      "padding": "0 20px", "borderLeft": "1px solid var(--line-2)"}),
        ], className="mk-telemetry"),
    ], className="mk-header")


# ── Vue 1 : scoring live ─────────────────────────────────────────────────────

def _view_scoring() -> html.Div:
    from .figures import gauge_figure, contribution_figure, ZONE_OK, ZONE_ALERT

    # ── Panneau latéral ───────────────────────────────────────────────────────
    aside = html.Div([

        # 1. État du flux de transactions
        html.Div([
            _kicker("Flux de transactions"),
            html.Div(id="live-status",
                     style={"fontSize": "11px", "color": "var(--ink-3)",
                            "lineHeight": 1.5, "fontFamily": "var(--mono)"}),
            html.Div(
                "Le dashboard ne fait que lire le flux. La population et le débit "
                "se règlent côté MoMTSim, au lancement du producteur (run_live.py).",
                style={"fontSize": "10.5px", "color": "var(--ink-4)", "lineHeight": 1.5},
            ),
        ], style={"display": "flex", "flexDirection": "column", "gap": "8px"}),

        # 2. Scoring : cadence + suspendre/reprendre
        html.Div([
            _kicker("Scoring"),
            html.Div("Toutes les combien de secondes le dashboard score les "
                     "transactions arrivées depuis la dernière fois.",
                     style={"fontSize": "10.5px", "color": "var(--ink-4)", "lineHeight": 1.5}),
            dcc.RadioItems(
                id="radio-speed",
                options=SPEED_OPTIONS,
                value="1000",
                inline=True,
                persistence=True, persistence_type="session",
                labelStyle={
                    "fontSize": "11px", "fontWeight": 700, "cursor": "pointer",
                    "fontFamily": "var(--mono)", "letterSpacing": ".04em",
                    "padding": "4px 10px",
                    "border": "1px solid var(--line)",
                    "background": "var(--surface-2)",
                    "marginRight": "4px", "marginBottom": "4px",
                    "display": "inline-block",
                },
                inputStyle={"display": "none"},
            ),
            html.Div([
                html.Button("▶ Reprendre", id="btn-play", n_clicks=0,
                            className="btn btn-primary",
                            style={"fontSize": "11px", "padding": "5px 14px",
                                   "fontFamily": "var(--mono)", "fontWeight": 700}),
                html.Button("⏸ Suspendre", id="btn-pause", n_clicks=0,
                            className="btn btn-secondary",
                            style={"fontSize": "11px", "padding": "5px 14px",
                                   "fontFamily": "var(--mono)"}),
            ], style={"display": "flex", "gap": "6px", "marginTop": "2px"}),
            html.Div("Suspendre gèle l'affichage ; le flux continue d'arriver et "
                     "sera scoré à la reprise.",
                     style={"fontSize": "10.5px", "color": "var(--ink-4)", "lineHeight": 1.5}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "8px"}),

        # 3. Compte inspecté
        html.Div([
            _kicker("Compte inspecté"),
            dcc.Input(
                id="inp-account-id", type="text", value="", debounce=True,
                placeholder="automatique",
                className="form-control",
                persistence=True, persistence_type="session",
                style={"textAlign": "left", "padding": "9px 11px", "fontSize": "13px"},
            ),
            html.Div(
                "Laisser vide : le dashboard inspecte automatiquement le compte au "
                "score le plus élevé du dernier lot. Vous pouvez saisir un identifiant "
                "(ex. C42, U17, M3) pour suivre un compte précis. Jauge, contributions, "
                "fenêtre glissante et vue « Fenêtres » portent sur ce compte ; le "
                "scoring, lui, couvre tous les comptes du flux.",
                style={"fontSize": "10.5px", "color": "var(--ink-4)", "lineHeight": 1.5}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "6px"}),

        # 4. Fenêtre glissante du compte inspecté
        html.Div([
            html.Div([
                _kicker("Fenêtre du compte inspecté"),
                html.Span("0/10", id="progress-window", className="mk-num",
                          style={"fontSize": "12px", "fontWeight": 600}),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "alignItems": "baseline"}),
            html.Div([html.Span() for _ in range(W)], id="window-cells",
                     className="mk-window"),
            html.Div("Chaque case est une de ses 10 dernières transactions. Le compte "
                     f"n'est scoré qu'une fois les {W} cases remplies ; ensuite la "
                     "fenêtre glisse d'une case à chaque nouvelle transaction.",
                     style={"fontSize": "10.5px", "color": "var(--ink-4)", "lineHeight": 1.5}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "8px"}),

        # 5. Injection de scénarios
        html.Div([
            _kicker("Injection de scénario"),
            html.Div("Ajoute une transaction synthétique de ce type à la fenêtre du "
                     "compte inspecté, puis la re-score : permet de vérifier que MKAN "
                     "réagit à chaque type de fraude.",
                     style={"fontSize": "10.5px", "color": "var(--ink-4)", "lineHeight": 1.5}),
            html.Div([
                html.Button(
                    lbl, id=btn_id, n_clicks=0,
                    title=tooltip,
                    className="btn btn-secondary",
                    style={
                        "fontSize": "10.5px", "fontFamily": "var(--mono)",
                        "letterSpacing": ".06em", "padding": "6px 4px",
                        "fontWeight": 600,
                        "borderColor": "#C2334D" if "ANOMALIE" in lbl else "var(--line)",
                        "color": "#C2334D" if "ANOMALIE" in lbl else "var(--ink)",
                    },
                )
                for btn_id, lbl, tooltip in SCENARIOS
            ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                      "gap": "6px"}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "8px"}),

    ], style={"display": "flex", "flexDirection": "column", "gap": "26px"})

    # ── Zone principale (jauge + contributions + onglets) ─────────────────────
    main = html.Div([
        html.Div([
            # Jauge de risque
            html.Div([
                _kicker("Risque du compte inspecté"),
                dcc.Graph(id="graph-gauge", figure=gauge_figure(None, 0.5),
                          config={"displayModeBar": False},
                          style={"height": "300px"}),
                html.Div([
                    html.Div(id="verdict-badge"),
                    html.Span(id="verdict-note",
                              style={"fontSize": "12.5px", "color": "var(--ink-3)"}),
                ], style={"display": "flex", "alignItems": "center",
                          "gap": "10px", "flexWrap": "wrap"}),
                chart_help(
                    "Le score de risque (0 à 100 %) que MKAN attribue aux 10 dernières "
                    "transactions d'un seul compte, le compte inspecté.",
                    "Le trait noir est le seuil de décision τ. Score à gauche "
                    "(zone verte) : fenêtre jugée normale. Score à droite (zone rouge) : "
                    "alerte de fraude. Près de 0 ou de 100 %, le modèle est catégorique ; "
                    "près de τ, il hésite.",
                    [(ZONE_OK, "Score < τ : normal"), (ZONE_ALERT, "Score ≥ τ : alerte"),
                     ("#17191C", "Seuil τ")]),
            ], style={"display": "flex", "flexDirection": "column", "gap": "14px"}),

            # Contributions
            html.Div([
                _kicker("Pourquoi ce score ? Influence des variables"),
                dcc.Graph(id="graph-contributions", figure=contribution_figure([]),
                          config={"displayModeBar": False},
                          style={"height": "340px"}),
                chart_help(
                    "Quelles variables ont le plus pesé dans le score du compte "
                    "inspecté : la part (en %) de l'activité des fonctions apprises du "
                    "modèle qu'elles concentrent, moyennée sur les 10 transactions de la "
                    "fenêtre et les 4 portes de la cellule. Le total fait 100 %.",
                    "Plus la barre est longue, plus la variable a influencé la décision. "
                    "C'est une mesure d'influence, pas de sens : elle ne dit pas si la "
                    "variable pousse vers « fraude » ou vers « normal ». Les 12 variables "
                    "sont toujours toutes affichées. La barre violette regroupe les 16 "
                    "unités de mémoire h[0] à h[15] de la cellule : ce que le modèle "
                    "retient des transactions précédentes de la fenêtre. Ce ne sont pas "
                    "des variables métier, donc elles sont additionnées en une seule "
                    "barre ; survolez-la pour voir le détail de chaque unité.",
                    [("#0F6FB8", "Variable d'entrée"), ("#7A5AA8", "Mémoire de la fenêtre")]),
            ], style={"display": "flex", "flexDirection": "column", "gap": "14px",
                      "minWidth": 0}),
        ], className="mk-cols"),

        # Onglets bas
        html.Div([
            dcc.Tabs(id="tabs-bottom", value="tab-history",
                     parent_className="tab-container",
                     children=[
                         dcc.Tab(label="Historique",     value="tab-history",
                                 className="tab", selected_className="tab--selected"),
                         dcc.Tab(label="Dérive JS",      value="tab-drift",
                                 className="tab", selected_className="tab--selected"),
                         dcc.Tab(label="Rapport COBAC",  value="tab-symbolic",
                                 className="tab", selected_className="tab--selected"),
                     ]),
            html.Div(id="tab-content",
                     style={"minHeight": "220px", "paddingTop": "18px"}),
            html.Div(
                html.Button("Générer le rapport COBAC", id="btn-symbolic",
                            n_clicks=0, className="btn btn-warning no-print"),
                id="div-btn-symbolic", style={"display": "none"}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "18px"}),
    ], style={"display": "flex", "flexDirection": "column", "gap": "38px",
              "minWidth": 0})

    return html.Div([aside, main], className="mk-grid")


# ── Vue 2 : fenêtres ─────────────────────────────────────────────────────────

def _view_window() -> html.Div:
    return html.Div([
        html.Div([
            _kicker("Comptes actifs · une carte par compte"),
            html.Div([
                html.Div([
                    html.Div("Filtrer par remplissage", style={"fontSize": "11px",
                             "color": "var(--ink-3)", "marginBottom": "4px"}),
                    dcc.Dropdown(id="win-filter", value="all", clearable=False,
                                 options=[
                                     {"label": "Toutes les fenêtres", "value": "all"},
                                     {"label": "Complètes (10/10, scorées)", "value": "full"},
                                     {"label": "En cours (5 à 9)", "value": "mid"},
                                     {"label": "Début (moins de 5)", "value": "low"}]),
                ], style={"minWidth": "260px"}),
                html.Div([
                    html.Div("Compte à visualiser (carte de chaleur)", style={
                             "fontSize": "11px", "color": "var(--ink-3)", "marginBottom": "4px"}),
                    dcc.Dropdown(id="win-account", value=None, clearable=True, options=[],
                                 placeholder="Automatique : compte inspecté, sinon le plus rempli"),
                ], style={"minWidth": "340px", "flex": "1"}),
            ], style={"display": "flex", "gap": "20px", "flexWrap": "wrap"}),
            html.Div(id="window-summary",
                     style={"fontSize": "12px", "color": "var(--ink-3)",
                            "fontFamily": "var(--mono)"}),
            html.Div(id="buffers-grid",
                     style={"display": "grid", "gap": "1px",
                            "gridTemplateColumns": "repeat(auto-fill, minmax(230px, 1fr))",
                            "background": "var(--line-2)",
                            "border": "1px solid var(--line-2)"}),
            chart_help(
                "L'état de la fenêtre glissante de chaque compte actif du flux : le nombre de "
                "ses transactions récentes déjà reçues, sur les 10 nécessaires pour être scoré.",
                "Un compte n'est scoré que lorsque sa barre est pleine (10/10). Ensuite, à "
                "chaque nouvelle transaction, la plus ancienne sort et la fenêtre glisse. Le "
                "compte inspecté est encadré en bleu.",
                [("#9AA1AB", "moins de 5/10"), ("#0F6FB8", "5 à 9/10 : en cours de remplissage"),
                 ("#2F7D5B", "10/10 : scoré")]),
        ], style={"display": "flex", "flexDirection": "column", "gap": "14px"}),
        html.Div([
            _kicker("Fenêtre du compte inspecté · évolution de chaque variable"),
            dcc.Graph(id="graph-heatmap", config={"displayModeBar": False},
                      style={"height": "340px"}),
            chart_help(
                "Les 10 dernières transactions du compte inspecté, de la plus ancienne (t−9, "
                "à gauche) à la plus récente (à droite). Chaque ligne est une des 12 variables "
                "que MKAN reçoit en entrée.",
                "La couleur situe chaque valeur entre le minimum (clair) et le maximum (foncé) "
                "de sa propre ligne, car les variables ont des échelles très différentes. Une "
                "ligne qui passe brusquement de clair à foncé signale un changement de "
                "comportement du compte. Une ligne vide (blanche) est une valeur constante sur "
                "les 10 transactions. Survolez une case pour lire la valeur brute.",
                [("#EFF6FF", "minimum de la variable"), ("#1E3A8A", "maximum de la variable")]),
        ], style={"display": "flex", "flexDirection": "column", "gap": "14px"}),
    ], style={"display": "flex", "flexDirection": "column", "gap": "40px"})


# ── Vue 3 : métriques ────────────────────────────────────────────────────────

def _view_metrics() -> html.Div:
    return html.Div([
        html.Div([
            html.Div(id="metrics-kpis",
                     style={"display": "flex", "flexWrap": "wrap", "gap": "40px"}),
            html.Div([
                html.Div([_kicker("Seuil de décision τ"),
                          html.Span("0.500", id="tau-value", className="mk-num",
                                    style={"fontSize": "13px", "fontWeight": 600})],
                         style={"display": "flex", "justifyContent": "space-between",
                                "alignItems": "baseline", "gap": "12px"}),
                dcc.Slider(id="slider-tau", min=0.02, max=0.98, step=0.01, value=0.5,
                           marks=None, tooltip={"placement": "bottom"},
                           persistence=True, persistence_type="session"),
                html.Div(id="tau-note",
                         style={"fontSize": "11.5px", "color": "var(--ink-4)"}),
            ], style={"display": "flex", "flexDirection": "column",
                      "gap": "7px", "minWidth": "240px"}),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "26px",
                  "justifyContent": "space-between", "alignItems": "flex-end"}),

        html.Div(id="metrics-updated"),

        html.Div([
            _kicker("Évolution lot par lot"),
            dcc.Graph(id="graph-metrics-trend", config={"displayModeBar": False},
                      style={"height": "250px"}),
            chart_help(
                "Comment la qualité de MKAN évolue au fil du flux : pour chaque lot de "
                "décisions (un point), le rappel, la précision et le taux d'alerte, au seuil "
                "τ courant.",
                "Le rappel est la part des fraudes détectées, la précision la part des "
                "alertes qui sont de vraies fraudes, le taux d'alerte la part des comptes "
                "signalés. Une courbe qui s'écarte durablement de son niveau habituel "
                "signale un changement dans le flux. Elles bougent avec la phase du "
                "simulateur (le volume de transactions varie sur un cycle de 720 heures "
                "simulées).",
                [("#2F7D5B", "Rappel"), ("#0F6FB8", "Précision"), ("#C2334D", "Taux d'alerte")]),
        ], style={"display": "flex", "flexDirection": "column", "gap": "12px"}),

        chart_help(
            "La qualité des décisions de MKAN, mesurée sur les décisions de la session dont "
            "la vérité (fraude ou non) est connue du simulateur.",
            "AUC ROC : probabilité qu'une vraie fraude reçoive un score plus élevé qu'une "
            "transaction légitime (1 = parfait, 0,5 = hasard). KS : écart maximal entre les "
            "scores des fraudes et des légitimes (plus haut = mieux). Brier et Log-loss : "
            "erreur des probabilités annoncées (plus bas = mieux). ECE : écart entre "
            "probabilité annoncée et fréquence observée (plus bas = mieux calibré)."),

        html.Div([
            html.Div([_kicker("Matrice de confusion"),
                      html.Div(id="confusion-table"),
                      chart_help(
                          "Le décompte des décisions de MKAN comparé à la réalité.",
                          "VP : fraude détectée. FN : fraude manquée. FP : fausse alerte. "
                          "VN : transaction légitime correctement laissée passer. Précision : "
                          "part des alertes qui sont de vraies fraudes. Rappel : part des "
                          "fraudes détectées. Taux de faux positifs : part des légitimes "
                          "alertées à tort.")],
                     style={"display": "flex", "flexDirection": "column", "gap": "12px"}),
            html.Div([_kicker("Courbe ROC"),
                      dcc.Graph(id="graph-roc", config={"displayModeBar": False},
                                style={"height": "260px"}),
                      chart_help(
                          "Pour chaque seuil τ possible, le compromis entre les fraudes "
                          "détectées (axe vertical, TPR) et les fausses alertes (axe "
                          "horizontal, FPR).",
                          "Plus la courbe bleue s'éloigne de la diagonale pointillée vers le "
                          "coin en haut à gauche, meilleur est le modèle. Le point rouge est "
                          "la situation au seuil τ actuel.",
                          [("#0F6FB8", "Courbe de MKAN"), ("#DCDBD4", "Hasard"),
                           ("#C2334D", "Seuil τ actuel")])],
                     style={"display": "flex", "flexDirection": "column",
                            "gap": "12px", "minWidth": 0}),
            html.Div([_kicker("Fiabilité (calibration)"),
                      dcc.Graph(id="graph-calibration", config={"displayModeBar": False},
                                style={"height": "260px"}),
                      chart_help(
                          "Quand MKAN annonce un score de 80 %, y a-t-il vraiment fraude 80 % "
                          "du temps ? Chaque point regroupe des décisions de score voisin "
                          "(plus il est gros, plus il en regroupe).",
                          "Les points doivent suivre la diagonale pointillée. Au-dessus : le "
                          "modèle sous-estime le risque. En dessous : il le surestime.",
                          [("#7A5AA8", "Fréquence observée"), ("#DCDBD4", "Calibration idéale")])],
                     style={"display": "flex", "flexDirection": "column",
                            "gap": "12px", "minWidth": 0}),
        ], className="mk-cols"),

        html.Div([
            html.Div([_kicker("Analyse par décile de score"),
                      html.Div(id="deciles-table"),
                      chart_help(
                          "Les décisions triées par score et découpées en bandes : le taux "
                          "de fraude réel dans chaque bande.",
                          "Le taux de fraude doit croître avec le score. Une bande haute qui "
                          "ne contient presque que des fraudes est le signe d'un bon "
                          "classement ; des taux voisins d'une bande à l'autre, d'un modèle "
                          "qui ne sépare pas bien.")],
                     style={"display": "flex", "flexDirection": "column",
                            "gap": "12px", "minWidth": 0}),
            html.Div([_kicker("Stabilité de population (PSI) & alertes graduées"),
                      html.Div(id="stability-table"),
                      html.Div("Palier 1 informatif, palier 2 investigation, palier 3 action "
                               "requise (ré-entraînement ou repli). PSI > 0.2 et KS > 0.3 "
                               "déclenchent le palier 3.",
                               style={"fontSize": "12px", "color": "var(--ink-3)",
                                      "lineHeight": 1.6, "maxWidth": "56ch"})],
                     style={"display": "flex", "flexDirection": "column",
                            "gap": "12px", "minWidth": 0}),
        ], style={"display": "grid", "gap": "36px", "alignItems": "start",
                  "gridTemplateColumns": "repeat(auto-fit, minmax(300px, 1fr))"}),
    ], style={"display": "flex", "flexDirection": "column", "gap": "40px"})


# ── Vue 4 : modèle ───────────────────────────────────────────────────────────

def _view_model() -> html.Div:
    stage_buttons = [
        html.Button([
            html.Span(f"{i + 1:02d} {label.upper()}",
                      style={"fontFamily": "var(--mono)", "fontSize": "9.5px",
                             "letterSpacing": ".1em", "color": "var(--ink-4)"}),
            html.Span(value, style={"fontSize": "14px", "fontWeight": 600}),
            html.Span(detail, style={"fontFamily": "var(--mono)", "fontSize": "10.5px",
                                     "color": "var(--ink-3)"}),
        ], id={"type": "stage", "index": sid}, n_clicks=0,
            style={"display": "flex", "flexDirection": "column", "gap": "5px",
                   "padding": "13px 16px", "border": "1px solid var(--line)",
                   "background": "#fff", "cursor": "pointer", "textAlign": "left",
                   "flex": "1 1 140px", "minWidth": "140px"})
        for i, (sid, label, value, detail) in enumerate(STAGES)
    ]
    return html.Div([
        html.Div([
            _kicker("Architecture MKANScorer"),
            html.Div(stage_buttons, style={"display": "flex", "flexWrap": "wrap", "gap": "8px"}),
            html.Div(id="stage-detail",
                     style={"display": "grid", "gap": "32px", "padding": "22px",
                            "border": "1px solid var(--line)", "background": "#fff",
                            "alignItems": "start",
                            "gridTemplateColumns": "repeat(auto-fit, minmax(280px, 1fr))"}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "16px"}),

        html.Div([_kicker("Fonctions apprises · 4 portes × 12 variables (gaussiennes M=8 + Fourier K=2)"),
                  chart_help(
                      "Une petite courbe par variable d'entrée. Chaque courbe colorée est la "
                      "fonction que l'une des 4 portes de la cellule (oubli, entrée, candidat, "
                      "sortie) a apprise pour cette variable, calculée à partir des poids réels "
                      "du checkpoint. Axe horizontal : valeur normalisée de la variable. Axe "
                      "vertical : amplitude moyenne |φ(x)| de la fonction.",
                      "Une courbe presque plate signifie que la porte réagit peu à cette "
                      "variable. Une courbe qui varie fortement montre les valeurs de la "
                      "variable auxquelles la porte est sensible.",
                      [("#0F6FB8", "Porte d'oubli"), ("#2F7D5B", "Porte d'entrée"),
                       ("#C9A227", "Porte candidat"), ("#C2334D", "Porte de sortie")]),
                  html.Div(id="graph-splines")],
                 style={"display": "flex", "flexDirection": "column", "gap": "14px"}),

        html.Div([_kicker("Normalisation d'entrée (mu / std d'entraînement)"),
                  html.Div(id="norm-table"),
                  chart_help(
                      "Les moyennes (μ) et écarts-types (σ) mesurés sur les données "
                      "d'entraînement, utilisés pour recentrer chaque variable avant de la "
                      "donner au modèle.",
                      "Chaque valeur reçue est transformée (log1p pour les 5 variables à "
                      "queue lourde), puis ramenée à (valeur − μ) / σ. Un σ quasi nul (par "
                      "exemple Δ Commission) signifie que la variable était constante à "
                      "l'entraînement : elle est neutralisée au scoring.")],
                 style={"display": "flex", "flexDirection": "column", "gap": "14px"}),
    ], style={"display": "flex", "flexDirection": "column", "gap": "40px"})


# ── Vue 5 : audit ────────────────────────────────────────────────────────────

def _view_audit() -> html.Div:
    return html.Div([
        html.Div([
            html.Div(id="audit-kpis",
                     style={"display": "flex", "flexWrap": "wrap", "gap": "44px"}),
            html.Div([
                html.Button("Exporter le journal (CSV)", id="btn-export-csv",
                            n_clicks=0, className="btn btn-primary"),
                html.Button("Purger la session", id="btn-purge",
                            n_clicks=0, className="btn btn-secondary"),
                dcc.Download(id="download-journal"),
                html.Div(id="audit-purge-msg",
                         style={"fontSize": "12px", "flexBasis": "100%", "lineHeight": 1.5}),
            ], style={"display": "flex", "gap": "8px", "flexWrap": "wrap"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-end", "gap": "24px", "flexWrap": "wrap"}),
        html.Div([_kicker("Journal des décisions"),
                  html.Div(id="audit-table"),
                  chart_help(
                      "Les 100 décisions les plus récentes de MKAN, la plus récente en haut.",
                      "Score : probabilité de fraude estimée (0 à 1). Verdict : FRAUDE si le "
                      "score atteint le seuil τ. La variable dominante n'est calculée que pour "
                      "les scores élevés (> 0,7), en arrière-plan : elle peut apparaître avec un "
                      "léger retard. Les compteurs du haut couvrent toute la session, pas "
                      "seulement ces lignes.")],
                 style={"display": "flex", "flexDirection": "column", "gap": "12px"}),
    ], style={"display": "flex", "flexDirection": "column", "gap": "26px"})


# ── Vue 6 : environnement ────────────────────────────────────────────────────

def _view_env() -> html.Div:
    return html.Div([
        html.Div([
            html.Div([
                _kicker("Mode d'exécution"),
                dcc.RadioItems(
                    id="radio-mode", value="sim", className="mk-modes",
                    persistence=True, persistence_type="session",
                    options=[
                        {"label": " Mode démo : flux MoMTSim (transactions simulées, vérité "
                                  "connue). Points de connexion préremplis et non modifiables, "
                                  "journal purgeable, aucune valeur probante.",
                         "value": "sim"},
                        {"label": " Mode live : flux de l'opérateur. Vous renseignez vous-même "
                                  "l'API REST ou le connecteur Kafka. Aucune vérité terrain : les "
                                  "métriques de précision ne sont pas calculables. Journal "
                                  "scellé, opposable au dossier COBAC.",
                         "value": "live"},
                    ],
                    labelStyle={"display": "block", "padding": "14px 16px",
                                "border": "1px solid var(--line)", "background": "#fff",
                                "marginBottom": "8px", "fontSize": "13px",
                                "lineHeight": 1.6, "cursor": "pointer"},
                ),
                html.Div(id="live-confirm", style={"display": "none"}),
                html.Button("CONFIRMER LE BASCULEMENT", id="btn-confirm-live",
                            n_clicks=0, style={"display": "none"}),
            ], style={"display": "flex", "flexDirection": "column", "gap": "14px"}),

            html.Div([
                _kicker("Source de données"),
                dcc.RadioItems(
                    id="radio-source", value="rest",
                    persistence=True, persistence_type="session",
                    options=[
                        {"label": " KAFKA · mm.transactions.momtsim", "value": "kafka"},
                        {"label": " API REST · /live/transactions",   "value": "rest"},
                    ],
                    labelStyle={"display": "block", "fontSize": "12.5px",
                                "padding": "4px 0"}),
                html.Div(id="source-uri", className="mk-num",
                         style={"fontSize": "11.5px", "color": "var(--accent)",
                                "wordBreak": "break-all"}),
                html.Div("Mode démo : ce point de connexion est celui de MoMTSim, il n'est pas "
                         "modifiable.", id="demo-endpoint-note",
                         style={"fontSize": "11px", "color": "var(--ink-4)", "lineHeight": 1.5}),

                html.Div([
                    _kicker("Points de connexion de l'opérateur"),
                    html.Label("API REST : URL du flux", style={"fontSize": "12px"}),
                    dcc.Input(id="inp-rest-url", type="text", debounce=False,
                              placeholder="https://api.operateur.example/v2/transactions",
                              className="form-control", persistence=True,
                              persistence_type="session",
                              style={"textAlign": "left", "fontSize": "12px"}),
                    html.Label("API REST : jeton d'accès (facultatif, non conservé)",
                               style={"fontSize": "12px"}),
                    dcc.Input(id="inp-rest-token", type="password", debounce=False,
                              placeholder="Bearer …", className="form-control",
                              style={"textAlign": "left", "fontSize": "12px"}),
                    html.Label("Kafka : serveurs (bootstrap)", style={"fontSize": "12px"}),
                    dcc.Input(id="inp-kafka-bootstrap", type="text", debounce=False,
                              placeholder="broker1:9092,broker2:9092",
                              className="form-control", persistence=True,
                              persistence_type="session",
                              style={"textAlign": "left", "fontSize": "12px"}),
                    html.Label("Kafka : topic", style={"fontSize": "12px"}),
                    dcc.Input(id="inp-kafka-topic", type="text", debounce=False,
                              placeholder="mm.transactions", className="form-control",
                              persistence=True, persistence_type="session",
                              style={"textAlign": "left", "fontSize": "12px"}),
                    html.Button("Se connecter", id="btn-connect-live", n_clicks=0,
                                className="btn btn-primary"),
                    html.Div(id="live-connect-msg",
                             style={"fontSize": "11.5px", "lineHeight": 1.5}),
                    chart_help(
                        "Le contrat de données que le flux doit respecter.",
                        "Chaque transaction doit fournir : step (entier croissant, 1 = 1 heure), "
                        "action (CASH_IN, CASH_OUT, DEBIT, PAYMENT, TRANSFER, DEPOSIT, REFUND), "
                        "amount, nameOrig, nameDest, oldBalanceOrig, newBalanceOrig, "
                        "oldBalanceDest, newBalanceDest. isFraud est facultatif. REST : "
                        "GET <url>?since_id=N doit répondre {\"transactions\": [...], "
                        "\"last_id\": N}. Kafka : un message JSON par transaction. Un flux qui "
                        "fournit un horodatage au lieu de step nécessite un adaptateur, à écrire."),
                ], id="div-live-endpoints",
                   style={"display": "none", "flexDirection": "column", "gap": "8px",
                          "padding": "14px", "border": "1px solid var(--line)",
                          "background": "var(--surface-2)"}),
            ], style={"display": "flex", "flexDirection": "column", "gap": "12px"}),

        ], style={"display": "flex", "flexDirection": "column", "gap": "34px",
                  "minWidth": 0}),

        html.Div([
            html.Div([_kicker("État de l'environnement"),
                      html.Div(id="env-state")],
                     style={"display": "flex", "flexDirection": "column", "gap": "10px"}),
            html.Div([
                _kicker("Arrêt d'urgence"),
                html.Div("Coupe l'ingestion et gèle le journal en l'état pour instruction.",
                         style={"fontSize": "12.5px", "color": "var(--ink-3)",
                                "lineHeight": 1.6}),
                html.Button("Suspendre le scoring", id="btn-kill", n_clicks=0,
                            className="btn btn-danger"),
            ], style={"display": "flex", "flexDirection": "column", "gap": "10px",
                      "padding": "16px", "border": "1px solid var(--line)",
                      "background": "var(--surface-2)"}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "26px",
                  "minWidth": 0}),
    ], style={"display": "grid", "gap": "48px", "alignItems": "start",
              "gridTemplateColumns": "repeat(auto-fit, minmax(300px, 1fr))"})


# ── Vue 7 : profil ───────────────────────────────────────────────────────────
# Le contenu est rendu dynamiquement par switch_view selon store-user.

def _view_profile() -> html.Div:
    """Conteneur vide  rempli par switch_view avec les données de store-user."""
    return html.Div(id="profile-content")


# ── Vue login ────────────────────────────────────────────────────────────────

def _view_login() -> html.Div:
    _field = lambda lbl, id_, type_: html.Div([
        html.Label(lbl, style={"fontSize": "12px", "color": "var(--ink-3)",
                               "marginBottom": "4px", "display": "block"}),
        dcc.Input(id=id_, type=type_, debounce=True, className="form-control",
                  style={"fontSize": "13px", "padding": "9px 11px", "width": "100%"}),
    ], style={"display": "flex", "flexDirection": "column"})

    login_form = html.Div([
        _kicker("Connexion"),
        _field("Email", "login-email", "email"),
        _field("Mot de passe", "login-password", "password"),
        html.Button("Se connecter", id="btn-login", n_clicks=0,
                    className="btn btn-primary",
                    style={"marginTop": "4px", "width": "100%", "fontSize": "13px",
                           "padding": "10px"}),
        html.Div(id="login-msg",
                 style={"fontSize": "11.5px", "color": "#C2334D",
                        "fontFamily": "var(--mono)", "minHeight": "18px"}),
        html.Div([
            html.Div("Compte de démo préchargé :", className="mk-kicker"),
            html.Div("georgesmoukoko4@gmail.com", className="mk-num",
                     style={"fontSize": "11px", "color": "var(--accent)"}),
            html.Div("Mot de passe : demo1234", className="mk-num",
                     style={"fontSize": "11px", "color": "var(--ink-4)"}),
        ], style={"padding": "10px 12px", "background": "var(--surface-2)",
                  "border": "1px solid var(--line)"}),
    ], style={"display": "flex", "flexDirection": "column", "gap": "12px",
              "padding": "24px", "border": "1px solid var(--line)", "background": "#fff",
              "minWidth": 0})

    signup_form = html.Div([
        _kicker("Créer un compte"),
        _field("Nom complet", "signup-name", "text"),
        _field("Email", "signup-email", "email"),
        _field("Mot de passe", "signup-password", "password"),
        html.Button("Créer le compte", id="btn-signup", n_clicks=0,
                    className="btn btn-secondary",
                    style={"marginTop": "4px", "width": "100%", "fontSize": "13px",
                           "padding": "10px"}),
        html.Div(id="signup-msg",
                 style={"fontSize": "11.5px", "fontFamily": "var(--mono)", "minHeight": "18px"}),
    ], style={"display": "flex", "flexDirection": "column", "gap": "12px",
              "padding": "24px", "border": "1px solid var(--line)", "background": "#fff",
              "minWidth": 0})

    return html.Div([
        html.Div([
            html.Div("MKAN", style={"fontSize": "22px", "fontWeight": 700,
                                    "fontFamily": "var(--mono)", "letterSpacing": ".08em"}),
            html.Div("Système de détection de fraude mobile money · Zone CEMAC",
                     style={"fontSize": "13px", "color": "var(--ink-4)", "marginTop": "4px"}),
        ], style={"marginBottom": "32px"}),
        html.Div([login_form, signup_form],
                 style={"display": "grid", "gap": "24px",
                        "gridTemplateColumns": "repeat(auto-fit, minmax(280px, 1fr))"}),
    ], style={"maxWidth": "720px", "margin": "60px auto 0", "padding": "0 24px"})


# ── Vue 7 : aide ─────────────────────────────────────────────────────────────

def _view_aide() -> html.Div:
    def _sec(title, *items):
        return html.Div([
            html.H3(title, style={"fontSize":"14px","fontWeight":700,"marginBottom":"8px",
                                   "paddingBottom":"5px","borderBottom":"1px solid var(--line)",
                                   "color":"var(--ink)"}),
            *items,
        ], style={"marginBottom":"28px"})
    def _p(txt):
        return html.P(txt, style={"fontSize":"12.5px","color":"#3F4650","lineHeight":1.7,
                                   "maxWidth":"74ch","margin":"0 0 8px 0"})
    def _kv(k, v):
        return html.Div([
            html.Span(k, style={"fontFamily":"var(--mono)","fontSize":"10.5px","fontWeight":700,
                                  "color":"var(--accent)","minWidth":"200px","flex":"none"}),
            html.Span(v, style={"fontSize":"12px","color":"#3F4650","lineHeight":1.6}),
        ], style={"display":"flex","gap":"16px","padding":"5px 0",
                  "borderBottom":"1px solid var(--line-2)"})
    def _box(*items):
        return html.Div(list(items),
                        style={"background":"var(--surface-2)","padding":"12px 16px",
                               "border":"1px solid var(--line)","marginBottom":"12px"})

    return html.Div([
        html.Div([
            html.H2("Guide utilisateur  MKAN Dashboard v3.0",
                    style={"fontSize":"19px","fontWeight":700,"marginBottom":"4px"}),
            html.P("Système de détection de fraude mobile money · Zone CEMAC · "
                   "Modèle T-KAN récurrent · Fenêtre glissante W=10 · 12 features",
                   style={"fontSize":"13px","color":"var(--ink-3)","marginBottom":"28px"}),
        ]),

        _sec("1 · Démarrage rapide",
            _p("Le dashboard ne lit plus de fichier : il consomme en continu des transactions "
               "brutes produites par MoMTSim. Pour obtenir des scores :"),
            html.Ol([
                html.Li("Démarrer le producteur MoMTSim (voir LANCEMENT.md). La population et le débit "
                        "se règlent là-bas, jamais dans le dashboard.", style={"fontSize":"12.5px","lineHeight":1.7,"marginBottom":"4px"}),
                html.Li("Ouvrir l'onglet Scoring live. Le scoring démarre tout seul dès que le flux est "
                        "connecté : l'indicateur en haut à droite passe à « SCORING EN COURS ».", style={"fontSize":"12.5px","lineHeight":1.7,"marginBottom":"4px"}),
                html.Li("Un compte est scoré dès qu'il a reçu 10 transactions (fenêtre glissante). La jauge "
                        "et les contributions expliquent le compte inspecté : celui que vous saisissez, "
                        "sinon celui au score le plus élevé du dernier lot.", style={"fontSize":"12.5px","lineHeight":1.7,"marginBottom":"4px"}),
                html.Li("Choisir le transport (REST ou Kafka) dans l'onglet Environnement. ⏸ Suspendre gèle "
                        "l'affichage sans perdre le flux : il sera scoré à la reprise.",
                        style={"fontSize":"12.5px","lineHeight":1.7}),
            ], style={"paddingLeft":"20px","margin":"8px 0 12px 0"}),
            _p("Redis est optionnel. Sans Redis, les fenêtres sont en mémoire et se vident au redémarrage du serveur."),
        ),

        _sec("2 · Flux de transactions",
            _box(
                _kv("Champs reçus", "step · time_s · action · amount · nameOrig · nameDest · oldBalanceOrig · newBalanceOrig · oldBalanceDest · newBalanceDest · isSuccessful · isFraud · isFlaggedFraud · fraudScenario"),
                _kv("12 variables du modèle", "delta_B_orig · delta_B_dest · r1 · r2 · flag_anomalie · delta_commission · var_agent_split · rho_rupture · rho_refund · v1h · flag_nuit · rho_nouveau. Elles sont calculées par le dashboard à partir de l'historique récent : le flux ne contient aucune variable précalculée."),
                _kv("Transports", "REST : GET /live/transactions?since_id=N sur le producteur (port 8766). Kafka : topic mm.transactions.momtsim."),
                _kv("Vérité terrain", "isFraud est connue du simulateur. Elle ne sert qu'à calculer les métriques et la courbe « fraude réelle », jamais au scoring."),
                _kv("Valeurs manquantes", "NaN sur les variables → remplacés par 0.0 automatiquement"),
            ),
        ),

        _sec("3 · Fenêtre glissante W=10",
            _p("Le MKAN ne score pas une transaction isolée mais une séquence de 10 transactions "
               "pour un compte donné. Le scoring ne démarre qu'une fois les 10 cases remplies."),
            _box(
                _kv("Routage multi-comptes", "Chaque transaction est routée vers la fenêtre de son compte émetteur (nameOrig). Le scoring s'exécute sur TOUS les comptes : dès qu'une fenêtre atteint 10, le modèle score ce compte."),
                _kv("Compte inspecté", "Le champ « Compte inspecté » (vide = automatique) désigne le compte expliqué en détail. La jauge, les contributions, la fenêtre glissante du panneau latéral et la vue Fenêtres portent sur ce compte. En automatique, c'est le compte au score le plus élevé du dernier lot, conservé quelques secondes."),
                _kv("Injection de scénario", "Ajoute une transaction synthétique à la fenêtre du compte inspecté puis la re-score. Utile pour tester la réaction du modèle à un type de fraude."),
                _kv("Vue Fenêtres", "Affiche les 48 comptes les plus remplis (le compte inspecté en premier) et la fenêtre du compte inspecté sous forme de carte de chaleur."),
            ),
        ),

        _sec("4 · Injection de scénarios (6 boutons)",
            _box(
                _kv("ATO (Account Takeover)", "Vidage rapide nocturne  flag_nuit=1, r1/r2 élevés, grand delta_B_orig"),
                _kv("REMBOURSEMENT", "Fraude au remboursement circulaire  rho_refund dominant, peu de volume nocturne"),
                _kv("FAUX CRED.", "Fausses accréditations  rho_rupture élevé + flag_anomalie=1, dormance puis exfiltration"),
                _kv("SPLIT", "Fractionnement agent  var_agent_split très élevé, petits montants répétés"),
                _kv("SMURFING", "Structuration multi-mules  delta_commission élevé + rho_nouveau élevé"),
                _kv("ANOMALIE", "Erreur physique / anomalie système  flag_anomalie + rho_rupture, delta_B erratiques"),
            ),
            _p("Toutes les injections sont marquées isFraud=1 et alimentent la jauge, les compteurs et le journal d'audit."),
        ),

        _sec("5 · Jauge de risque et contributions",
            _box(
                _kv("Zone verte (score < τ)", "Fenêtre jugée normale"),
                _kv("Zone rouge (score ≥ τ)", "Alerte de fraude : le compte est signalé"),
                _kv("Trait noir", "Seuil de décision τ, modifiable dans l'onglet Métriques"),
            ),
            _p("La jauge porte sur UN compte (le compte inspecté), pas sur l'ensemble du flux : le "
               "taux d'alerte du lot est indiqué sous la jauge. Le graphe de contributions montre la "
               "part d'influence des 12 variables et de la mémoire de la fenêtre (total 100 %) ; c'est "
               "une mesure d'influence, elle n'indique pas le sens (fraude ou normal)."),
        ),

        _sec("6 · Métriques de validation (onglet Métriques)",
            _p("Calculées sur les transactions étiquetées (isFraud connu) scorées dans la session. Minimum 2 étiquettes requises."),
            _box(
                _kv("AUC ROC", "Aire sous la courbe ROC. ≥ 0.80 = bon discriminant"),
                _kv("KS", "sup|F₁−F₀|  pouvoir séparateur. ≥ 0.30 = discrimination significative"),
                _kv("Brier", "Erreur quadratique moyenne sur les probabilités. < 0.10 = excellent"),
                _kv("ECE", "Expected Calibration Error  écart score prédit / fréquence observée. < 0.05 = bien calibré"),
                _kv("PSI", "Population Stability Index. > 0.10 : surveillance · > 0.20 : re-entraînement requis"),
                _kv("Divergence JS", "Jensen-Shannon (moitié 1 vs 2 des scores). > 0.05 : dérive détectée"),
                _kv("Seuil τ", "Modifiable (0.02–0.98). τ > 0.5 : moins de faux positifs · τ < 0.5 : plus de rappel"),
                _kv("Palier 1", "Informatif · Palier 2 : investigation · Palier 3 : action (ré-entraînement)"),
            ),
        ),

        _sec("7 · Modèle MKAN (onglet Modèle)",
            _box(
                _kv("Architecture", "TKANCell récurrente × W=10 pas + projection linéaire → σ"),
                _kv("Entrée", "(batch, 10, 12)  10 pas de temps × 12 features"),
                _kv("Normalisation", "log1p(|x|) sur 5 features à queue lourde, puis z-score μ/σ entraînement"),
                _kv("Portes", "4 portes LSTM (oubli / entrée / candidat / sortie) chacune remplacée par HybridKANLayer"),
                _kv("Base KAN", "M=8 Gaussiennes RBF + K=2 harmoniques Fourier = 10 paramètres par arête"),
                _kv("hidden_size=16", "16 nœuds cachés par porte"),
                _kv("Sortie", "z = w⊤h_W + b → score = σ(z)"),
                _kv("Graphe splines", "|φ(x)| moyen par porte pour chaque feature  une courbe plate = feature peu utilisée"),
                _kv("Tableau normalisation", "μ et σ du jeu d'entraînement extraits du checkpoint mkan_final.pt"),
            ),
        ),

        _sec("8 · Audit et rapport COBAC",
            _box(
                _kv("Journal", "Chaque décision enregistrée : heure, compte, score, verdict, variable dominante, étiquette"),
                _kv("Export CSV", "Télécharge mkan_journal.csv  toutes les décisions de la session"),
                _kv("Purger", "Efface le journal (mode SIMULÉ uniquement  irréversible)"),
                _kv("Rapport COBAC", "Onglet Scoring · bouton 'Générer le rapport COBAC' · requiert W=10 complet"),
                _kv("Mode LIVE", "Journal scellé et non purgeable  chaque décision est opposable"),
            ),
        ),

        _sec("9 · Environnement",
            _box(
                _kv("Mode d'exécution", "Étiquette de l'environnement (simulé / live). Dans cette version elle n'active aucun traitement différent."),
                _kv("Source de données", "Transport utilisé pour lire le flux : REST ou Kafka, un seul à la fois. Le tableau d'état affiche le débit réellement mesuré."),
                _kv("Suspendre le scoring", "Arrêt d'urgence : désactive immédiatement le scoring. Le flux continue d'arriver."),
            ),
        ),

        _sec("10 · Compte analyste",
            _box(
                _kv("Connexion", "Bouton 'AN' en haut à droite → page connexion si non connecté"),
                _kv("Compte de démo", "Email : georgesmoukoko4@gmail.com · Mot de passe : demo1234"),
                _kv("Création de compte", "Formulaire sur la page connexion  stocké localement dans users.json"),
                _kv("Profil connecté", "Affiche nom, rôle, email, empreinte de session SHA-256"),
                _kv("Rapport COBAC", "Porte le nom de l'analyste connecté  'Session anonyme' si non connecté"),
            ),
        ),

        html.Div([
            html.Span("MKAN Dashboard v3.0 · MOUKOKO EKAMBI Georges Miguel · ENSPD / Université de Douala · LIDIA Lab · 2026",
                      style={"fontSize":"10.5px","color":"var(--ink-4)","fontFamily":"var(--mono)"}),
            html.Span(" · Non implémenté dans cette version : connexion à un flux réel d'opérateur (seul le flux MoMTSim est branché).",
                      style={"fontSize":"10.5px","color":"var(--ink-4)","fontFamily":"var(--mono)"}),
        ], style={"borderTop":"1px solid var(--line)","paddingTop":"14px","marginTop":"8px"}),

    ], style={"maxWidth":"860px"})


# ── Dispatch des vues ─────────────────────────────────────────────────────────

VIEW_BUILDERS = {
    "scoring":  _view_scoring,
    "window":   _view_window,
    "metrics":  _view_metrics,
    "model":    _view_model,
    "audit":    _view_audit,
    "env":      _view_env,
    "aide":     _view_aide,
    "profile":  _view_profile,
    "login":    _view_login,
}


def build_view(name: str) -> html.Div:
    return VIEW_BUILDERS.get(name, _view_scoring)()


def build_layout() -> html.Div:
    return html.Div([
        # ── Stores ────────────────────────────────────────────────────────────
        dcc.Store(id="store-last-result"),
        dcc.Store(id="store-journal",  data=[]),
        dcc.Store(id="store-cursor",   data=0),    # compteur d'injections de scénario
        dcc.Store(id="store-batch-cursor", data=0),  # nombre de lots live traités
        dcc.Store(id="store-account-id", data=""),   # "" = compte inspecté automatique
        dcc.Store(id="store-totals", data={}),        # compteurs cumulés de la session
        dcc.Store(id="store-config",   data={
            "mode":    "sim",
            "tau":     0.5,
            "source":  "rest",
            "cadence": 2000,
            "cap":     12,
            "guards":  ["psi", "dual"],
            "stage":   "input",
        }),
        dcc.Store(id="store-live-state", data={"connected": False, "source": "rest"}),
        dcc.Store(id="store-user", storage_type="session"),   # dict utilisateur connecté
        dcc.Interval(id="auto-interval", interval=1000, disabled=False, n_intervals=0),
        dcc.Interval(id="slow-interval", interval=1000, n_intervals=0),  # métriques / env
        # ── Chrome ────────────────────────────────────────────────────────────
        _header(),
        html.Main(id="view-root", className="mk-page",
                  children=build_view("scoring")),
    ])
