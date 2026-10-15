"""
callbacks_extra.py  Callbacks de la v2.4.1 : navigation, seuil τ, mode
d'exécution, métriques, journal d'audit et rapport COBAC.

À brancher depuis app.py, APRÈS callbacks.register_callbacks(app) :

    from dashboard import callbacks, callbacks_extra
    callbacks.register_callbacks(app)
    callbacks_extra.register_extra(app, buffer, inference)
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io

import numpy as np
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from . import metrics as mx
from . import callbacks as _cb_mod   # accès par module (latence p50, état runtime)
from . import live_ingest as _live_ingest
from .layout import FEATURE_COLS, FEATURE_LABELS, STAGES, W, build_view, chart_help
from .users import authenticate, create_user

INK, INK3, INK4 = "#17191C", "#6B7280", "#9AA1AB"

# Cache des figures splines  calculées une seule fois par session (modèle immuable)
_spline_cache: dict = {}   # {id(inference): (grid_html, norm_tbl_html)}
ACCENT, DANGER, OK, WARN, VIOLET = "#0F6FB8", "#C2334D", "#2F7D5B", "#C9A227", "#7A5AA8"
LINE = "#EDECE6"
MONO = dict(family="IBM Plex Mono, monospace", size=11, color=INK3)

STAGE_TEXT = {
    "input": ("La fenêtre glissante brute",
              "Chaque compte alimente un buffer circulaire de 10 transactions. Le scoring n'est "
              "déclenché qu'une fois les 10 pas présents : la cellule lit la séquence, pas la "
              "transaction isolée.", "buffer.push(account_id, features) → (10, 12)"),
    "norm": ('log1p sur les queues lourdes, puis z-score',
              "Δ soldes, ratios r1/r2 et volume 1h passent par log1p(|x|) avant le centrage-réduction avec mu_train / std_train, dans l'ordre exact de l'entraînement. Les variables constantes à l'entraînement (delta_commission) sont neutralisées. Le graphique montre les valeurs réellement fournies au modèle pour le compte inspecté.",
              'w = (log1p(|x|) − mu_train) / std_train'),
    "cell": ('4 portes KAN, 16 unités cachées',
              "La cellule récurrente a quatre portes (oubli, entrée, candidat, sortie). Chacune est une couche KAN : chaque arête variable → porte applique sa propre fonction apprise φ(x) au lieu d'un simple poids. C'est cette décomposition qui rend la décision explicable variable par variable. Le graphique trace ces fonctions réelles pour la variable la plus influente.",
              'porte_j = Σ_i φ_ij(x_i)'),
    "basis": ('Base gaussienne + Fourier (M = 8, K = 2)',
              "Chaque fonction apprise φ est la somme de 8 gaussiennes réparties sur [−1, 1], pondérées par des poids appris, et de 2 harmoniques de Fourier (cos kx, sin kx, k = 1, 2). Ce sont ces poids, et eux seuls, que l'entraînement ajuste.",
              'φ(x) = Σₘ wₘ·exp(−(x−cₘ)²/2h²) + Σₖ [aₖ cos kx + bₖ sin kx]'),
    "head": ("Projection linéaire de l'état caché",
              "Le dernier état caché h (16 valeurs) est projeté sur un scalaire z par une couche linéaire ; le signe et l'amplitude de chaque poids donnent l'effet de chaque unité sur z. Le graphique montre les 16 poids réellement appris.",
              'z = wᵀ·h_T + b'),
    "out": ("Score de risque et verdict",
            "La sigmoïde borne z dans [0, 1]. Au-delà de τ la transaction est marquée FRAUDE et "
            "versée au journal d'audit avec sa variable dominante.",
            "score = σ(z) · verdict = score ≥ τ"),
}


# ── Helpers de rendu ─────────────────────────────────────────────────────────

def _theme(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, paper_bgcolor="white", plot_bgcolor="white",
                      font=dict(family="IBM Plex Sans, sans-serif", size=12, color=INK),
                      margin=dict(t=10, b=36, l=48, r=16), showlegend=False)
    fig.update_xaxes(gridcolor=LINE, zeroline=False, tickfont=MONO)
    fig.update_yaxes(gridcolor=LINE, zeroline=False, tickfont=MONO)
    return fig


def _kpi(key: str, value: str, detail: str = "", color: str = INK) -> html.Div:
    return html.Div([
        html.Span(key, className="mk-kicker", style={"whiteSpace": "nowrap"}),
        html.Span(value, className="mk-num",
                  style={"fontSize": "22px", "fontWeight": 600, "color": color}),
        html.Span(detail, style={"fontSize": "11.5px", "color": INK4, "whiteSpace": "nowrap"}),
    ], style={"display": "flex", "flexDirection": "column", "gap": "4px"})


def _table(headers, rows, aligns=None) -> html.Table:
    aligns = aligns or ["left"] * len(headers)
    return html.Table([
        html.Thead(html.Tr([html.Th(h, style={"textAlign": a})
                            for h, a in zip(headers, aligns)])),
        html.Tbody([html.Tr([html.Td(c, className="num" if a == "right" else "",
                                     style={"textAlign": a}) for c, a in zip(r, aligns)])
                    for r in rows]),
    ], className="mk-table")


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _labelled(journal):
    lab = [j for j in journal if j.get("label") is not None]
    return np.array([j["label"] for j in lab], int), np.array([j["score"] for j in lab], float)


# ── Rendu du profil utilisateur ──────────────────────────────────────────────

def _render_profile(user: dict):
    import datetime as dt
    initials = "".join(w[0] for w in (user.get("name") or "AN").split()[:2]).upper()
    name  = user.get("name",  "Analyste")
    role  = user.get("role",  "")
    email = user.get("email", "")
    uid   = user.get("id",    "")

    from dash import dcc, html
    _row = lambda k, v: html.Div([
        html.Span(k, style={"fontSize": "12px", "color": INK3, "minWidth": "160px"}),
        html.Span(v, className="mk-num", style={"fontSize": "12px", "fontWeight": 600}),
    ], style={"display": "flex", "gap": "12px", "padding": "8px 0",
              "borderBottom": f"1px solid {LINE}"})

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return html.Div([
        html.Div([
            html.Div([
                html.Div(initials, style={
                    "width": "66px", "height": "66px", "flex": "none",
                    "border": f"1px solid {INK}", "display": "grid",
                    "placeItems": "center", "fontFamily": "IBM Plex Mono, monospace",
                    "fontSize": "20px", "fontWeight": 600,
                }),
                html.Div([
                    html.Div(name, style={"fontSize": "19px", "fontWeight": 600}),
                    html.Div(role, style={"fontSize": "13px", "color": INK3}),
                    html.Div(email, className="mk-num",
                             style={"fontSize": "11px", "color": INK4}),
                ], style={"display": "flex", "flexDirection": "column", "gap": "5px"}),
            ], style={"display": "flex", "gap": "20px", "flexWrap": "wrap"}),

            html.Div([
                html.Div("Informations de session", className="mk-kicker"),
                _row("Identifiant", uid),
                _row("Email", email),
                _row("Connecté depuis", now),
                _row("Mode d'exécution", "Simulé · bac à sable"),
            ], style={"display": "flex", "flexDirection": "column", "gap": "0"}),

            html.Div([
                html.Div("Préférences d'alerte", className="mk-kicker"),
                dcc.RadioItems(id="pref-notif", value="t2",
                               options=[{"label": " Palier 2+",    "value": "t2"},
                                        {"label": " Palier 3 seul","value": "t3"},
                                        {"label": " Toutes",       "value": "all"}],
                               labelStyle={"display": "inline-block",
                                           "marginRight": "18px", "fontSize": "12.5px"}),
            ], style={"display": "flex", "flexDirection": "column", "gap": "10px"}),

            html.Button("Se déconnecter", id="btn-logout", n_clicks=0,
                        className="btn btn-secondary",
                        style={"alignSelf": "flex-start", "fontSize": "12px"}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "30px",
                  "minWidth": 0}),

        html.Div([
            html.Div([
                html.Div("Empreinte de session (SHA-256)", className="mk-kicker"),
                html.Div(
                    __import__("hashlib").sha256(
                        f"{uid}|{now}".encode()
                    ).hexdigest()[:32] + "…",
                    className="mk-num",
                    style={"fontSize": "11px", "color": INK3, "wordBreak": "break-all"},
                ),
                html.Div(
                    "Calculée sur id utilisateur + heure de connexion. "
                    "Attachée à chaque rapport COBAC généré dans cette session.",
                    style={"fontSize": "11.5px", "color": INK4, "lineHeight": 1.6},
                ),
            ], style={"display": "flex", "flexDirection": "column", "gap": "8px",
                      "padding": "16px", "border": f"1px solid {LINE}",
                      "background": "var(--surface-2)"}),
        ], style={"display": "flex", "flexDirection": "column", "gap": "26px",
                  "minWidth": 0}),
    ], style={"display": "grid", "gap": "48px", "alignItems": "start",
              "gridTemplateColumns": "repeat(auto-fit, minmax(300px, 1fr))"})


# ── Enregistrement ───────────────────────────────────────────────────────────

def register_extra(app, buffer=None, inference=None) -> None:

    # 1 · navigation principale + accès profil
    @app.callback(Output("view-root", "children"),
                  Input("tabs-main", "value"), Input("btn-profile", "n_clicks"),
                  State("store-user", "data"),
                  prevent_initial_call=True)
    def switch_view(tab, n_profile, user):
        if ctx.triggered_id == "btn-profile":
            return build_view("login") if not user else _render_profile(user)
        return build_view(tab)

    # 2 · seuil τ → config + libellés
    @app.callback(Output("store-config", "data"), Output("tau-value", "children"),
                  Output("tau-note", "children"),
                  Input("slider-tau", "value"), State("store-config", "data"),
                  prevent_initial_call=True)
    def on_tau(tau, cfg):
        cfg = {**(cfg or {}), "tau": float(tau)}
        note = ("Au-dessus de 0.5 : moins de faux positifs, plus de fraudes manquées."
                if tau > 0.5 else
                "Sous 0.5 : plus de rappel, davantage de dossiers à instruire."
                if tau < 0.5 else "Seuil réglementaire de référence.")
        return cfg, f"{tau:.3f}", note

    # 3 · mode d'exécution  le passage en LIVE exige une confirmation
    @app.callback(Output("live-confirm", "children"), Output("live-confirm", "style"),
                  Output("store-config", "data", allow_duplicate=True),
                  Output("env-label", "children"),
                  Input("radio-mode", "value"), Input("btn-confirm-live", "n_clicks"),
                  State("store-config", "data"), prevent_initial_call=True)
    def on_mode(mode, n_confirm, cfg):
        cfg = dict(cfg or {})
        hidden = {"display": "none"}
        if ctx.triggered_id == "btn-confirm-live":
            cfg["mode"] = "live"
            return None, hidden, cfg, "LIVE · CEMAC"
        if mode == "live" and cfg.get("mode") != "live":
            panel = html.Div([
                html.Div("Confirmation requise · habilitation Analyste risque senior",
                         className="mk-kicker", style={"color": DANGER}),
                html.Div("Le passage en mode LIVE branche le scoring sur le flux de production et "
                         "rend chaque décision opposable au titre du dossier COBAC. Le journal "
                         "d'audit devient non purgeable.",
                         style={"fontSize": "13px", "lineHeight": 1.65, "maxWidth": "64ch"}),
                html.Button("CONFIRMER LE BASCULEMENT", id="btn-confirm-live", n_clicks=0,
                            className="btn btn-danger"),
            ], style={"display": "flex", "flexDirection": "column", "gap": "12px",
                      "padding": "18px", "border": f"1px solid {DANGER}", "background": "#FDF4F5"})
            return panel, {"display": "block"}, cfg, "SIMULÉ · BAC À SABLE"
        cfg["mode"] = "sim"
        return None, hidden, cfg, "SIMULÉ · BAC À SABLE"

    # 3bis · formulaire de connexion : visible seulement en mode live
    @app.callback(Output("div-live-endpoints", "style"),
                  Output("demo-endpoint-note", "style"),
                  Input("store-config", "data"), Input("tabs-main", "value"))
    def toggle_endpoints(cfg, tab):
        if tab != "env":
            raise PreventUpdate
        live = (cfg or {}).get("mode") == "live"
        form = {"display": "flex" if live else "none", "flexDirection": "column",
                "gap": "8px", "padding": "14px", "border": f"1px solid {LINE}",
                "background": "#FCFCFA"}
        note = {"fontSize": "11px", "color": INK4, "lineHeight": 1.5,
                "display": "none" if live else "block"}
        return form, note

    # 3ter · connexion à la source de l'opérateur (mode live)
    @app.callback(Output("live-connect-msg", "children"),
                  Output("store-journal", "data", allow_duplicate=True),
                  Output("store-totals", "data", allow_duplicate=True),
                  Input("btn-connect-live", "n_clicks"),
                  State("radio-source", "value"), State("inp-rest-url", "value"),
                  State("inp-rest-token", "value"), State("inp-kafka-bootstrap", "value"),
                  State("inp-kafka-topic", "value"), State("store-config", "data"),
                  prevent_initial_call=True)
    def on_connect_live(n, source, rest_url, token, bootstrap, topic, cfg):
        if not n:
            raise PreventUpdate
        err = lambda m: (html.Span(m, style={"color": DANGER}), no_update, no_update)
        if (cfg or {}).get("mode") != "live":
            return err("Cette saisie ne s'applique qu'au mode live.")
        rest_url, bootstrap, topic = ((rest_url or "").strip(), (bootstrap or "").strip(),
                                      (topic or "").strip())
        if source == "kafka":
            if not bootstrap or not topic:
                return err("Renseignez les serveurs Kafka et le topic.")
            ep = {"kafka_bootstrap": bootstrap, "kafka_topic": topic}
        else:
            if not rest_url.lower().startswith(("http://", "https://")):
                return err("L'URL doit commencer par http:// ou https://.")
            ep = {"rest_url": rest_url, "rest_token": (token or "").strip()}
        _live_ingest.start(source or "rest", ep)
        return (html.Span("Connexion lancée : l'état du flux s'affiche ci-dessous.",
                          style={"color": OK}), [], {})

    # 4 · état de l'environnement (valeurs réellement mesurées, rien de simulé)
    _env_last: list = [None]   # clé du dernier rendu : évite de redessiner pour rien

    @app.callback(Output("source-uri", "children"), Output("env-state", "children"),
                  Input("radio-source", "value"),
                  Input("store-config", "data"), Input("slow-interval", "n_intervals"),
                  Input("tabs-main", "value"), Input("auto-interval", "disabled"),
                  State("store-totals", "data"))
    def on_env(source, cfg, _slow, tab, paused, totals):
        if tab != "env":
            raise PreventUpdate
        cfg, totals = cfg or {}, totals or {}
        live = _live_ingest.stats()
        scored, alerts = int(totals.get("scored", 0)), int(totals.get("alerts", 0))
        state_key = (source, cfg.get("mode"), cfg.get("tau"), live["connected"],
                     live["received"], scored, alerts, bool(paused))
        if ctx.triggered_id != "tabs-main" and state_key == _env_last[0]:
            raise PreventUpdate
        _env_last[0] = state_key

        src = source or "rest"
        uri = live.get("endpoint") or ""
        if not live["connected"]:
            uri += "  (déconnecté)"

        rows = [
            ("Mode", "LIVE" if cfg.get("mode") == "live" else "SIMULÉ"),
            ("Source", src.upper()),
            ("Flux", "CONNECTÉ" if live["connected"] else "DÉCONNECTÉ"),
            ("Transactions reçues", _fmt(live["received"])),
            ("Débit reçu", f"{live['rate']:.0f} tx/s"),
            ("Scoring", "EN PAUSE" if paused else "EN COURS"),
            ("Décisions scorées", _fmt(scored)),
            ("Alertes de MKAN", _fmt(alerts)),
            ("Taux d'alerte", f"{alerts / scored * 100:.1f} %" if scored else "n/d"),
            ("Seuil τ", f"{cfg.get('tau', 0.5):.3f}"),
            *([("Dernière erreur", str(live["error"])[:70])]
              if live.get("error") and not live["connected"] else []),
            ("Rétention du journal",
             "10 ans (COBAC)" if cfg.get("mode") == "live" else "session"),
        ]
        table = html.Div([
            html.Div([html.Span(k, style={"fontSize": "12.5px", "color": INK3}),
                      html.Span(v, className="mk-num",
                                style={"fontSize": "12px", "fontWeight": 600,
                                       "color": DANGER if v in ("LIVE", "DÉCONNECTÉ", "EN PAUSE")
                                       else INK})],
                     style={"display": "flex", "justifyContent": "space-between",
                            "gap": "12px", "padding": "8px 0",
                            "borderBottom": f"1px solid {LINE}"})
            for k, v in rows
        ])
        return uri, table

    # 5 · étape d'architecture sélectionnée
    @app.callback(Output("stage-detail", "children"),
                  Input({"type": "stage", "index": ALL}, "n_clicks"))
    def on_stage(_clicks):
        sid = ctx.triggered_id["index"] if isinstance(ctx.triggered_id, dict) else "input"
        title, body, code = STAGE_TEXT[sid]
        idx = [s[0] for s in STAGES].index(sid)
        return [
            html.Div([
                html.Div(f"ÉTAPE {idx + 1:02d} · {STAGES[idx][1].upper()}", className="mk-kicker"),
                html.Div(title, style={"fontSize": "15px", "fontWeight": 600}),
                html.Div(body, style={"fontSize": "13px", "lineHeight": 1.65, "maxWidth": "44ch",
                                      "color": "#3F4650"}),
                html.Div(code, className="mk-num", style={"fontSize": "11.5px", "color": ACCENT}),
            ], style={"display": "flex", "flexDirection": "column", "gap": "12px"}),
            dcc.Graph(figure=_stage_figure(sid, inference, buffer), config={"displayModeBar": False},
                      style={"height": "250px"}),
        ]

    # 6 · métriques (matrice, ROC, calibration, déciles, stabilité)
    _metrics_last: list = [None, -1.0]   # [identifiant de la dernière décision, tau]

    def _trend_figure(journal, tau):
        """Évolution lot par lot (un lot = un tick) du rappel, de la précision et du
        taux d'alerte, recalculés au seuil τ courant à partir des scores."""
        groups: dict = {}
        for j in journal:
            if j.get("label") is not None:
                groups.setdefault(j["time"], []).append(j)
        times = list(groups)[-60:]
        rec, prec, alr = [], [], []
        for t in times:
            g = groups[t]
            tp = sum(1 for e in g if e["score"] >= tau and e["label"] == 1)
            fp = sum(1 for e in g if e["score"] >= tau and e["label"] == 0)
            fn = sum(1 for e in g if e["score"] < tau and e["label"] == 1)
            prec.append(100 * tp / (tp + fp) if tp + fp else None)
            rec.append(100 * tp / (tp + fn) if tp + fn else None)
            alr.append(100 * (tp + fp) / len(g))
        fig = go.Figure([
            go.Scatter(x=times, y=rec, mode="lines", name="Rappel", connectgaps=True,
                       line=dict(color=OK, width=2)),
            go.Scatter(x=times, y=prec, mode="lines", name="Précision", connectgaps=True,
                       line=dict(color=ACCENT, width=2)),
            go.Scatter(x=times, y=alr, mode="lines", name="Taux d'alerte",
                       line=dict(color=DANGER, width=2, dash="dot")),
        ])
        _theme(fig, 250)
        fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.3, x=0),
                          margin=dict(t=10, b=70, l=52, r=16))
        fig.update_yaxes(range=[0, 100], ticksuffix=" %")
        fig.update_xaxes(title="Heure du lot (un point = un lot scoré)", nticks=8)
        return fig

    @app.callback(Output("metrics-kpis", "children"), Output("confusion-table", "children"),
                  Output("graph-roc", "figure"), Output("graph-calibration", "figure"),
                  Output("deciles-table", "children"), Output("stability-table", "children"),
                  Output("metrics-updated", "children"), Output("graph-metrics-trend", "figure"),
                  Input("slow-interval", "n_intervals"),
                  Input("tabs-main", "value"),
                  State("store-journal", "data"), State("store-config", "data"),
                  State("store-totals", "data"))
    def on_metrics(_slow, tab, journal, cfg, totals):
        if tab != "metrics":
            raise PreventUpdate   # les composants n'existent que dans cette vue
        journal, cfg = journal or [], cfg or {}
        tau = float(cfg.get("tau", 0.5))
        # On saute le recalcul si rien n'a changé, mais la clé est l'IDENTIFIANT de la
        # dernière décision et non la longueur du journal : celle-ci se fige à 2000
        # (plafond), ce qui bloquait la vue vide pour toujours. À l'ouverture de la vue
        # (les composants viennent d'être recréés, vides), on recalcule toujours.
        last = journal[-1] if journal else None
        key = (last.get("id", len(journal)) if last else None, tau)
        if ctx.triggered_id != "tabs-main" and key == tuple(_metrics_last):
            raise PreventUpdate
        _metrics_last[0], _metrics_last[1] = key
        y, s = _labelled(journal)
        if len(y) < 2 or len(set(y.tolist())) < 2:
            vide = html.Div("Pas encore assez de décisions pour calculer des métriques : il "
                            "faut au moins une fraude et une transaction légitime scorées. "
                            "Un compte n'est scoré qu'après 10 transactions ; patientez "
                            "quelques instants.",
                            style={"fontSize": "13.5px", "color": INK4, "maxWidth": "58ch",
                                   "lineHeight": 1.65})
            empty = _theme(go.Figure(), 240)
            return [], vide, empty, empty, None, None, "", _theme(go.Figure(), 250)
        R = mx.session_report(y, s, tau)
        kpis = [
            _kpi("AUC ROC", f"{R['auc']:.3f}", "aire sous la courbe", OK if R["auc"] >= .8 else INK),
            _kpi("KS", f"{R['ks']:.3f}", "sup |F₁ − F₀|"),
            _kpi("Brier", f"{R['brier']:.4f}", "erreur quadratique"),
            _kpi("Log-loss", f"{R['log_loss']:.4f}", "−Σ y·log p / n"),
            _kpi("ECE", f"{R['ece']:.4f}", "calibration, 10 bacs",
                 WARN if R["ece"] > .1 else OK),
            _kpi("Étiquetées", str(R["n"]), f"{int(y.sum())} fraudes / {int((1 - y).sum())} légitimes"),
        ]
        conf = html.Div([
            _table(["", "prédit fraude", "prédit légitime"],
                   [["réel fraude", f"VP {R['tp']}", f"FN {R['fn']}"],
                    ["réel légitime", f"FP {R['fp']}", f"VN {R['tn']}"]],
                   ["left", "right", "right"]),
            _table(["Taux", "Valeur"], [
                ["Précision", f"{R['precision'] * 100:.1f}%"],
                ["Rappel (TPR)", f"{R['recall'] * 100:.1f}%"],
                ["F1", f"{R['f1']:.3f}"],
                ["Spécificité", f"{R['specificity'] * 100:.1f}%"],
                ["Taux de faux positifs", f"{R['fpr'] * 100:.1f}%"],
                ["IC 95 % du rappel (Wilson)",
                 f"{R['recall_ci95'][0] * 100:.0f}–{R['recall_ci95'][1] * 100:.0f}%"],
            ], ["left", "right"]),
        ], style={"display": "flex", "flexDirection": "column", "gap": "18px"})

        # ROC : utilise les courbes déjà calculées dans session_report
        fpr_c, tpr_c = R["fpr_curve"], R["tpr_curve"]
        roc = go.Figure([
            go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                       line=dict(color="#DCDBD4", dash="dot", width=1)),
            go.Scatter(x=fpr_c, y=tpr_c, mode="lines", line=dict(color=ACCENT, width=2)),
            go.Scatter(x=[R["fpr"]], y=[R["recall"]], mode="markers",
                       marker=dict(color=DANGER, size=9)),
        ])
        roc.update_xaxes(title="FPR", range=[0, 1]); roc.update_yaxes(title="TPR", range=[0, 1])

        # Calibration : utilise les données déjà calculées dans session_report
        rel = [(p, o, n) for p, o, n in R["reliability_data"] if n]
        cal = go.Figure([
            go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                       line=dict(color="#DCDBD4", dash="dot", width=1)),
            go.Scatter(x=[p for p, _, _ in rel], y=[o for _, o, _ in rel],
                       mode="lines+markers", line=dict(color=VIOLET, width=2),
                       marker=dict(size=[min(18, 6 + n ** .5) for _, _, n in rel],
                                   color="white", line=dict(color=VIOLET, width=1.6))),
        ])
        cal.update_xaxes(title="score prédit", range=[0, 1])
        cal.update_yaxes(title="fréquence observée", range=[0, 1])

        dec = _table(["Bande", "Taux de fraude", "n"],
                     [[d["bande"], "" if d["taux"] is None else f"{d['taux'] * 100:.0f}%",
                       str(d["n"])] for d in mx.deciles(y, s)],
                     ["left", "right", "right"])

        # Dérive des scores : même mesure que l'onglet Dérive (référence figée, bits)
        mon = _cb_mod._drift
        js_val = mon.value(buffer.get_drift_scores()) if buffer is not None else None
        js_tier = (1 if js_val is None or js_val < mon.threshold
                   else 2 if js_val < 2 * mon.threshold else 3)
        stab_rows = [
            ("PSI des scores (moitié 1 vs 2)", "Σ (fᵢᵦ − fᵢₐ)·ln(fᵢᵦ/fᵢₐ)", R["psi"], mx.tier("psi", R["psi"])),
            ("Statistique KS", "sup |F₁(x) − F₀(x)|", R["ks"], 1 if R["ks"] >= .3 else 2),
            ("Divergence JS (bits, référence figée)", "½·KL(P‖M) + ½·KL(Q‖M)", js_val, js_tier),
            ("Écart de calibration (ECE)", "Σ (nᵢ/n)·|ȳᵢ − p̄ᵢ|", R["ece"], mx.tier("ece", R["ece"])),
        ]
        stab = html.Div([
            html.Div([
                html.Div([html.Div(k, style={"fontSize": "12.5px", "color": "#3F4650"}),
                          html.Div(formula, className="mk-num",
                                   style={"fontSize": "10.5px", "color": INK4})]),
                html.Span("n/d" if v is None else f"{v:.3f}", className="mk-num",
                          style={"fontSize": "12.5px", "fontWeight": 600}),
                html.Span(f"PALIER {t}", className=f"tier tier-{t}"),
            ], style={"display": "grid", "gridTemplateColumns": "1fr 74px 92px",
                      "alignItems": "center", "gap": "12px", "padding": "9px 0",
                      "borderBottom": f"1px solid {LINE}"})
            for k, formula, v, t in stab_rows
        ])
        # Fraîcheur et cumul de la session : la fenêtre des 2 000 dernières décisions se
        # renouvelle en quelques secondes, donc ses valeurs bougent peu ; le cumul et la
        # courbe d'évolution montrent ce qui change vraiment.
        n_lots = len({j["time"] for j in journal})
        t = totals or {}
        s_scored, s_alerts = int(t.get("scored", 0)), int(t.get("alerts", 0))
        s_fraud, s_tp = int(t.get("fraud", 0)), int(t.get("tp", 0))
        session = ""
        if s_scored:
            prec_s = f"{s_tp / s_alerts * 100:.1f} %" if s_alerts else "n/d"
            rec_s = f"{s_tp / s_fraud * 100:.1f} %" if s_fraud else "n/d"
            session = (f" · Depuis le début de la session : {_fmt(s_scored)} décisions, "
                       f"{_fmt(s_alerts)} alertes ({s_alerts / s_scored * 100:.1f} %), "
                       f"précision {prec_s}, rappel {rec_s}.")
        updated = html.Span(
            f"Mis à jour à {_dt.datetime.now().strftime('%H:%M:%S')} · indicateurs ci-dessous "
            f"sur les {len(journal)} dernières décisions étiquetées ({n_lots} lots)." + session,
            style={"fontSize": "12px", "color": INK3, "lineHeight": 1.6})
        return (kpis, conf, _theme(roc, 240), _theme(cal, 240), dec, stab,
                updated, _trend_figure(journal, tau))

    # 7 · journal d'audit (slow-interval  1 Hz max, pas à chaque tick)
    _audit_last: list = [None]   # identifiant de la dernière décision déjà affichée

    @app.callback(Output("audit-kpis", "children"), Output("audit-table", "children"),
                  Input("slow-interval", "n_intervals"),
                  Input("tabs-main", "value"),
                  State("store-journal", "data"), State("store-totals", "data"))
    def on_audit(_slow, tab, journal, totals):
        if tab != "audit":
            raise PreventUpdate
        journal, totals = journal or [], totals or {}
        # Clé = identifiant de la dernière décision (la longueur du journal se fige à
        # 2000) ; à l'ouverture de la vue on redessine toujours.
        last = journal[-1] if journal else None
        key = (last.get("id", len(journal)) if last else None, int(totals.get("scored", 0)))
        if ctx.triggered_id != "tabs-main" and key == _audit_last[0]:
            raise PreventUpdate
        _audit_last[0] = key
        if not journal:
            return [], html.Div("Aucune décision enregistrée dans cette session.",
                                style={"fontSize": "13.5px", "color": INK4})
        alerts = sum(1 for j in journal if j["verdict"] == "FRAUDE")
        scored, t_alerts = int(totals.get("scored", 0)), int(totals.get("alerts", 0))
        kpis = [_kpi("Décisions (session)", _fmt(scored), f"dont {len(journal)} détaillées ci-dessous"),
                _kpi("Alertes fraude (session)", _fmt(t_alerts),
                     color=DANGER if t_alerts else INK),
                _kpi("Taux d'alerte (session)",
                     f"{t_alerts / scored * 100:.0f}%" if scored else "n/d"),
                _kpi("Comptes touchés", str(len({j['acct'] for j in journal})),
                     "dans le journal détaillé")]
        # Limiter à 100 lignes les plus récentes pour éviter un DOM de 2000 lignes
        display = journal[-100:][::-1]
        total   = len(journal)
        rows = [[f"{total - i:03d}", j["time"], j["acct"], f"{j['score']:.3f}",
                 j.get("driver", ""), j["verdict"]]
                for i, j in enumerate(display)]
        tbl = _table(["#", "Heure", "Compte", "Score", "Variable dominante", "Verdict"],
                     rows, ["left", "left", "left", "right", "left", "right"])
        footer = (html.Div(f"… {total - 100} entrées antérieures non affichées",
                           style={"fontSize": "11px", "color": INK4, "marginTop": "8px",
                                  "fontFamily": "var(--mono)"})
                  if total > 100 else None)
        return kpis, html.Div([tbl] + ([footer] if footer else []))

    @app.callback(Output("download-journal", "data"),
                  Input("btn-export-csv", "n_clicks"), State("store-journal", "data"),
                  prevent_initial_call=True)
    def export_csv(n, journal):
        if not n or not journal:
            raise PreventUpdate
        buf = io.StringIO()
        buf.write("n,heure,compte,score,verdict,variable_dominante,label\n")
        for i, j in enumerate(journal, 1):
            buf.write(f"{i},{j['time']},{j['acct']},{j['score']:.6f},"
                      f"{j['verdict']},{j.get('driver', '')},{j.get('label', '')}\n")
        return dcc.send_string(buf.getvalue(), "mkan_journal.csv")

    # 8 · rapport COBAC  gabarit corrigé, imprimable
    @app.callback(Output("tab-content", "children", allow_duplicate=True),
                  Input("btn-symbolic", "n_clicks"),
                  State("store-journal", "data"), State("store-config", "data"),
                  State("store-account-id", "data"), State("store-last-result", "data"),
                  State("store-user", "data"),
                  prevent_initial_call=True)
    def cobac_report(n, journal, cfg, account_id, last_result, user):
        if not n:
            raise PreventUpdate
        journal, cfg = journal or [], cfg or {}
        tau, mode = float(cfg.get("tau", 0.5)), cfg.get("mode", "sim")
        acc = _cb_mod.inspected_account(account_id) or ""
        if buffer is not None and (not acc or buffer.get_window(acc) is None):
            return html.Div("Aucun compte à fenêtre complète pour l'instant : le rapport porte "
                            "sur le compte inspecté, qui doit avoir reçu 10 transactions.",
                            style={"fontSize": "13.5px", "color": INK4})

        # Contributions réelles : lire depuis le cache serveur (_last_result)
        # plutôt que depuis le dcc.Store réduit (qui ne contient plus gate_contribs)
        from dashboard.callbacks import _last_result as _srv_result
        _full = _srv_result or last_result or {}
        gate_contribs = _full.get("gate_contribs") or []
        top_features  = _full.get("top_features")  or []
        last_score    = _full.get("score")

        if gate_contribs:
            try:
                gate_rows_html = html.Table([
                    html.Thead(html.Tr([
                        html.Th("Porte"), html.Th("Contribution (|w·h|)"),
                        html.Th("Rang", style={"textAlign": "right"}),
                    ])),
                    html.Tbody([html.Tr([
                        html.Td(str(g[0]) if isinstance(g, (list, tuple)) else str(g),
                                style={"fontFamily": "var(--mono)", "fontWeight": 600}),
                        html.Td(f"{float(g[1]):.4f}" if isinstance(g, (list, tuple)) and len(g) > 1
                                else ""),
                        html.Td(str(i + 1), style={"textAlign": "right"}),
                    ]) for i, g in enumerate(gate_contribs)]),
                ], className="mk-table")
                gate_section_title = "2 · Contributions des portes · dernière transaction scorée"
                gate_note = (f"Score : {last_score:.4f}" if last_score is not None else "")
            except Exception:
                gate_rows_html = html.Div(
                    "Données de contribution non disponibles pour cette transaction.",
                    style={"fontSize": "12px", "color": INK4})
                gate_section_title = "2 · Contributions des portes"
                gate_note = ""
        else:
            gate_rows_html = html.Div(
                "Aucune transaction scorée dans cette session  soumettez au moins "
                "une fenêtre complète (W=10) pour voir les contributions réelles.",
                style={"fontSize": "12px", "color": INK4, "lineHeight": 1.6})
            gate_section_title = "2 · Contributions des portes"
            gate_note = ""

        # Top features
        top_feat_block = None
        if top_features:
            try:
                from .layout import FEATURE_LABELS
                rows_tf = []
                for i, tf in enumerate(top_features[:5]):
                    if isinstance(tf, (list, tuple)) and len(tf) >= 2:
                        fname = FEATURE_LABELS.get(str(tf[0]), str(tf[0]))
                        rows_tf.append([str(i + 1), fname, f"{float(tf[1]):.4f}"])
                    else:
                        rows_tf.append([str(i + 1), str(tf), ""])
                if rows_tf:
                    top_feat_block = html.Div([
                        html.Div("Variables dominantes (top 5)", className="mk-kicker",
                                 style={"marginTop": "18px"}),
                        _table(["Rang", "Feature", "Importance L1"],
                               rows_tf, ["left", "left", "right"]),
                    ])
            except Exception:
                pass

        y, s = _labelled(journal)
        R = mx.session_report(y, s, tau) if len(y) >= 2 else None
        kpi_rows = ([("AUC", f"{R['auc']:.3f}"), ("KS", f"{R['ks']:.3f}"),
                     ("Brier", f"{R['brier']:.3f}"), ("ECE", f"{R['ece']:.3f}"),
                     ("PSI", f"{R['psi']:.3f}"), ("Décisions", str(R["n"]))]
                    if R else [("Décisions", str(len(journal))), ("Seuil τ", f"{tau:.3f}"),
                               ("Étiquetées", "0")])
        ref = f"MKAN/COBAC/{_dt.date.today().year}-{max(len(journal), 1):04d}"
        digest = hashlib.sha256(f"{ref}|{tau}|{len(journal)}".encode()).hexdigest()[:16]
        analyst = (user.get("name") + "  " + user.get("role", "Analyste")) if user else "Session anonyme"

        return html.Article([
            html.Header([
                html.Div([
                    html.Div("Commission bancaire de l'Afrique centrale", className="mk-kicker"),
                    html.H1("Rapport d'auditabilité du modèle de scoring"),
                    html.Div("Établissement déclarant · MKAN / Mobile Money  zone CEMAC",
                             style={"fontSize": "12.5px", "color": INK3}),
                ]),
                html.Div([html.Div(f"{k} {v}", className="mk-num",
                                   style={"fontSize": "11px", "color": INK3})
                          for k, v in [("Réf.", ref),
                                       ("Date", _dt.date.today().strftime("%d/%m/%Y")),
                                       ("Mode", "LIVE" if mode == "live" else "SIMULÉ"),
                                       ("Seuil τ", f"{tau:.3f}")]],
                         style={"textAlign": "right"}),
            ]),

            html.H2("1 · Objet et périmètre"),
            html.P("Le présent rapport documente les contributions réelles des portes apprises par le "
                   "scoreur MKAN (12 variables, fenêtre glissante W=10, 16 nœuds cachés, "
                   "portes KAN à activations gaussiennes M=8 et Fourier K=2) tel que chargé depuis le checkpoint "
                   "mkan_final.pt. Il vise l'exigence d'explicabilité des décisions automatisées "
                   "de détection de fraude sur les services de paiement mobile en zone CEMAC."),

            html.H2(gate_section_title),
            html.Div(gate_note, className="mk-num",
                     style={"fontSize": "11px", "color": INK4, "marginBottom": "8px"}) if gate_note else None,
            gate_rows_html,
            top_feat_block,

            html.H2("3 · Indicateurs de validation de la session"),
            html.Div([html.Div([html.Span(k, className="mk-kicker"),
                                html.Span(v, className="mk-num",
                                          style={"fontSize": "14px", "fontWeight": 600})],
                               style={"display": "flex", "flexDirection": "column", "gap": "4px",
                                      "padding": "11px 13px", "background": "#FCFCFA"})
                      for k, v in kpi_rows],
                     style={"display": "grid", "gap": "1px", "background": LINE,
                            "border": f"1px solid {LINE}",
                            "gridTemplateColumns": "repeat(auto-fit, minmax(126px, 1fr))"}),

            html.H2("4 · Réserves et limites d'opposabilité"),
            html.P("4.1  Les contributions rapportées sont extraites de la dernière transaction "
                   "scorée dans la session. Elles reflètent l'état réel des poids du modèle pour "
                   "cette séquence et ne valent pas extrapolation hors distribution d'entraînement."),
            html.P("4.2  " + ("Rapport établi en mode LIVE : les décisions horodatées sont "
                               "opposables et le journal est scellé pour dix ans."
                               if mode == "live" else
                               "Rapport établi en mode SIMULÉ sur données synthétiques  sans "
                               "valeur probante, à usage de recette uniquement.")),
            html.P("4.3  Un PSI supérieur à 0.2 ou un ECE supérieur à 0.1 suspend la validité du "
                   "rapport jusqu'au ré-entraînement du modèle."),

            html.Footer([
                html.Div([
                    html.Div("Empreinte du rapport (SHA-256 tronqué)", className="mk-kicker"),
                    html.Div(f"sha256:{digest}…", className="mk-num", style={"fontSize": "12px"}),
                    html.Div(f"Analyste responsable · {analyst}",
                             style={"fontSize": "12px", "color": INK3}),
                ]),
                html.Div(html.Button("Imprimer / PDF", id="btn-print", n_clicks=0,
                                     className="btn btn-primary no-print")),
            ]),
        ], className="mk-report")

    # 9 · Télémétrie extras : dérive JS, seuil τ, latence p50
    @app.callback(
        Output("tele-drift",   "children"),
        Output("tele-tau",     "children"),
        Output("tele-latency", "children"),
        Input("slow-interval", "n_intervals"),
        State("store-config",  "data"),
    )
    def update_tele_extras(_slow, cfg):
        cfg = cfg or {}
        tau = float(cfg.get("tau", 0.5))
        drift_str = ""
        mon = _cb_mod._drift
        if buffer is not None:
            if not mon.ready:
                drift_str = f"calibrage {len(mon.ref)}/{mon.REF_N}" if mon.ref else ""
            else:
                v = mon.value(buffer.get_drift_scores())
                if v is not None:
                    drift_str = f"{v:.4f}" + (" ⚠" if v > mon.threshold else "")
        lat_str = _cb_mod.get_latency_p50()
        return drift_str, f"{tau:.3f}", lat_str

    # 10 · Vue Fenêtres : grille des buffers + heatmap de la fenêtre du compte inspecté
    _heatmap_hash: list = [None]   # [hash précédent]  redessine seulement si données changées
    _MAX_CARDS = 48
    _win_opts: list = [None]

    @app.callback(
        Output("buffers-grid",    "children"),
        Output("graph-heatmap",   "figure"),
        Output("window-summary",  "children"),
        Output("win-account",     "options"),
        Input("slow-interval",    "n_intervals"),
        Input("tabs-main",        "value"),
        Input("win-filter",       "value"),
        Input("win-account",      "value"),
        State("store-account-id", "data"),
    )
    def update_window_view(_slow, tab, fil, chosen, account_id):
        if tab != "window":
            raise PreventUpdate   # inutile de lire des centaines de fenêtres hors de cette vue
        if buffer is None:
            return html.Div("Buffer non initialisé.", style={"color": INK4}), go.Figure(), "", []
        n_active = buffer.count_accounts()
        # On n'interroge que les comptes récemment actifs (≤ 400) : lire le niveau de
        # tous les comptes chaque seconde saturait Redis (mono-thread).
        candidates = list(_cb_mod._recent_accts)
        insp = _cb_mod.inspected_account(account_id) or ""
        if insp and insp not in candidates:
            candidates.append(insp)
        if chosen and chosen not in candidates:
            candidates.append(chosen)
        fill_levels = buffer.get_fill_levels(candidates)
        n_full = sum(1 for a in candidates if fill_levels[a] >= 10)

        keep = {"all": lambda n: True, "full": lambda n: n >= 10,
                "mid": lambda n: 5 <= n < 10, "low": lambda n: n < 5}.get(fil or "all")
        shown = [a for a in candidates if keep(fill_levels[a])]
        shown.sort(key=lambda a: (-fill_levels[a], a))

        # Compte visualisé : choix explicite, sinon compte inspecté, sinon le plus rempli
        acc = chosen or (insp if insp else "") or (shown[0] if shown else "")
        ranked = shown[:_MAX_CARDS]
        if acc and acc not in ranked:
            ranked = [acc] + ranked[:_MAX_CARDS - 1]
        ranked.sort(key=lambda a: (a != acc, -fill_levels[a], a))
        acc_options = [{"label": f"{a}  ({fill_levels[a]}/10)", "value": a}
                       for a in shown[:200]]
        cards = []
        for a in ranked:
            level = fill_levels[a]
            is_active = (a == acc)
            pct = min(level / 10, 1.0)
            bar_color = OK if level >= 10 else ACCENT if level >= 5 else INK4
            cards.append(html.Div([
                html.Div(a[:20], style={
                    "fontFamily": "var(--mono)", "fontSize": "10.5px",
                    "fontWeight": 700 if is_active else 400,
                    "color": ACCENT if is_active else INK3,
                    "overflow": "hidden", "textOverflow": "ellipsis", "whiteSpace": "nowrap",
                }),
                html.Div(html.Div(style={
                    "width": f"{pct*100:.0f}%", "height": "3px",
                    "background": bar_color, "borderRadius": "2px",
                }), style={"background": "#F3F4F6", "borderRadius": "2px",
                           "height": "3px", "margin": "5px 0"}),
                html.Div(f"{level}/10", style={"fontSize": "10px", "color": INK4,
                                               "fontFamily": "var(--mono)"}),
            ], style={
                "padding": "10px 12px", "background": "#fff",
                "border": f"2px solid {ACCENT}" if is_active else f"1px solid {LINE}",
            }))
        if not cards:
            grid_content = html.Div(
                "Aucun compte actif pour l'instant : le flux n'a pas encore livré de "
                "transactions, ou le scoring est en pause.",
                style={"fontSize": "13px", "color": INK4})
        else:
            grid_content = html.Div(cards, style={
                "display": "grid", "gap": "1px",
                "gridTemplateColumns": "repeat(auto-fill, minmax(140px, 1fr))",
                "background": "var(--line-2)", "border": "1px solid var(--line-2)",
            })
        summary = (f"{n_active:,} comptes actifs au total · parmi les {len(candidates)} plus "
                   f"récemment actifs, {n_full} ont une fenêtre complète (scorés) · "
                   f"{len(shown)} correspondent au filtre, {len(ranked)} affichés "
                   f"(compte visualisé en premier, puis les plus remplis)").replace(",", " ")

        window_arr = buffer.get_window(acc) if acc else None
        ylabels = [FEATURE_LABELS.get(f, f) for f in FEATURE_COLS]

        # Pas de « données identiques : pas de redessin » : en revenant sur l'onglet, le
        # graphique est un nouveau composant vide, et un no_update le laissait blanc.
        if window_arr is None:
            fig_heat = go.Figure()
            fig_heat.add_annotation(
                text=("Aucun compte inspecté pour l'instant." if not acc else
                      f"La fenêtre de {acc} n'est pas encore complète (10 transactions requises)."),
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False, font=dict(color=INK4, size=13))
            fig_heat.update_xaxes(visible=False); fig_heat.update_yaxes(visible=False)
            fig_heat.update_layout(height=340, paper_bgcolor="white", plot_bgcolor="white",
                                   margin=dict(t=20, b=20, l=20, r=20))
        else:
            xlabels = [f"t−{9 - i}" if i < 9 else "t (récente)" for i in range(10)]
            # Chaque variable a son propre minimum et maximum sur les 10 transactions :
            # les échelles brutes vont de 0/1 à plusieurs millions et écraseraient tout.
            mn, mx = window_arr.min(axis=0), window_arr.max(axis=0)
            rng = mx - mn
            # ligne constante -> case vide (NaN) : un aplat bleu ressemblerait à une mesure
            z = np.where(rng > 0, (window_arr - mn) / np.where(rng > 0, rng, 1.0), np.nan)
            fig_heat = go.Figure(go.Heatmap(
                z=z.T.tolist(), x=xlabels, y=ylabels, customdata=window_arr.T.tolist(),
                zmin=0, zmax=1,
                colorscale=[[0, "#EFF6FF"], [0.5, "#60A5FA"], [1, "#1E3A8A"]],
                colorbar=dict(thickness=12, len=0.85, tickvals=[0, 1],
                              ticktext=["min", "max"], tickfont=dict(size=10)),
                hovertemplate="<b>%{y}</b><br>%{x}<br>valeur brute : %{customdata:.4g}"
                              "<extra></extra>",
            ))
            _theme(fig_heat, 340)
            fig_heat.update_layout(margin=dict(t=10, b=36, l=130, r=16))
        opts_key = tuple(o["label"] for o in acc_options)
        if opts_key == _win_opts[0]:
            acc_options = no_update      # évite de fermer la liste déroulante ouverte
        else:
            _win_opts[0] = opts_key
        return grid_content, fig_heat, summary, acc_options

    # 11 · Vue Modèle : 12 graphes de splines (un par feature) + tableau normalisation
    @app.callback(
        Output("graph-splines", "children"),
        Output("norm-table",    "children"),
        Input("tabs-main",      "value"),
    )
    def update_model_view(tab):
        if tab != "model":
            raise PreventUpdate
        import torch
        log_feats = {"delta_B_orig", "delta_B_dest", "r1", "r2", "v1h"}

        if inference is None:
            no_model = html.Div("Modèle non chargé.", style={"color": INK4, "fontSize": "13px"})
            return no_model, no_model

        # Cache : le modèle ne change jamais en cours de session → calculé une seule fois
        cache_key = id(inference)
        if cache_key in _spline_cache:
            return _spline_cache[cache_key]

        # ── Tableau de normalisation ──────────────────────────────────────
        norm_rows = []
        for i, f in enumerate(inference.feature_cols):
            norm_rows.append([
                FEATURE_LABELS.get(f, f),
                f"{inference.mu[i]:.4f}",
                f"{inference.std[i]:.4f}",
                "log1p → z" if f in log_feats else "z-score",
            ])
        norm_tbl = _table(
            ["Feature", "μ entraînement", "σ entraînement", "Transformation"],
            norm_rows, ["left", "right", "right", "left"])

        # ── Calcul des courbes splines ────────────────────────────────────
        model     = inference.model
        hidden    = model.hidden_size
        concat_sz = hidden + model.cell.input_size
        x_grid    = torch.linspace(-2.5, 2.5, 60)
        gate_cfg  = [
            ("forget",    ACCENT,  "Oubli f"),
            ("input",     OK,      "Entrée i"),
            ("candidate", WARN,    "Candidat c̃"),
            ("output",    DANGER,  "Sortie o"),
        ]

        # Un dcc.Graph par feature
        spline_graphs = []
        for feat_i, feat in enumerate(inference.feature_cols):
            ci         = hidden + feat_i
            feat_label = FEATURE_LABELS.get(feat, feat)
            traces     = []
            with torch.no_grad():
                for gate_name, color, gate_label in gate_cfg:
                    gate = getattr(model.cell, f"{gate_name}_gate")
                    ys = []
                    for xv in x_grid:
                        x_in = torch.zeros(1, concat_sz)
                        x_in[0, ci] = float(xv)
                        acts = gate.edge_activations(x_in)
                        ys.append(acts[0, ci, :].abs().mean().item())
                    traces.append(go.Scatter(
                        x=x_grid.tolist(), y=ys,
                        name=gate_label,
                        line=dict(color=color, width=1.8),
                        hovertemplate=f"{gate_label}<br>x=%{{x:.2f}} |φ|=%{{y:.4f}}<extra></extra>",
                    ))
            fig = go.Figure(traces)
            fig.update_layout(
                title=dict(text=feat_label, font=dict(size=11, color=INK3), x=0.5),
                height=180,
                margin=dict(t=28, b=28, l=36, r=8),
                paper_bgcolor="white", plot_bgcolor="white",
                font=dict(family="IBM Plex Sans, sans-serif", size=10, color=INK),
                showlegend=(feat_i == 0),
                legend=dict(orientation="h", y=1.28, font=dict(size=9)),
            )
            _tf = dict(family="IBM Plex Mono, monospace", size=8, color=INK3)
            fig.update_xaxes(gridcolor=LINE, zeroline=True, zerolinecolor="#E5E7EB",
                             tickfont=_tf,
                             title=dict(text="x normalisé", font=dict(size=8)))
            fig.update_yaxes(gridcolor=LINE, zeroline=False,
                             tickfont=_tf,
                             title=dict(text="|φ(x)|", font=dict(size=8)))
            spline_graphs.append(
                html.Div(
                    dcc.Graph(figure=fig, config={"displayModeBar": False}),
                    style={"background": "white", "border": f"1px solid {LINE}",
                           "minWidth": 0}
                )
            )

        grid = html.Div(spline_graphs, style={
            "display": "grid",
            "gridTemplateColumns": "repeat(auto-fill, minmax(240px, 1fr))",
            "gap": "1px",
            "background": LINE,
            "border": f"1px solid {LINE}",
        })
        _spline_cache[cache_key] = (grid, norm_tbl)   # mise en cache pour les prochaines visites
        return grid, norm_tbl

    # 12 · Kill switch : suspendre depuis n'importe quel onglet
    @app.callback(
        Output("auto-interval", "disabled", allow_duplicate=True),
        Input("btn-kill",       "n_clicks"),
        prevent_initial_call=True,
    )
    def on_kill(n_clicks):
        if not n_clicks:
            raise PreventUpdate
        return True

    # 13 · Purge de session
    @app.callback(
        Output("store-journal", "data", allow_duplicate=True),
        Output("store-totals",  "data", allow_duplicate=True),
        Output("audit-purge-msg", "children"),
        Input("btn-purge",      "n_clicks"),
        State("store-config",   "data"),
        State("store-totals",   "data"),
        prevent_initial_call=True,
    )
    def on_purge(n_clicks, cfg, totals):
        if not n_clicks:
            raise PreventUpdate
        if (cfg or {}).get("mode") == "live":
            return (no_update, no_update,
                    html.Span("Purge impossible en mode live : le journal est scellé "
                              "(opposable au dossier COBAC).", style={"color": DANGER}))
        n = int((totals or {}).get("scored", 0))
        if buffer is not None:
            buffer.clear_drift()
        _cb_mod._drift.reset()                # nouvelle référence de dérive
        epoch = _cb_mod.new_epoch()           # invalide les ticks déjà en vol
        _cb_mod._last_result = None
        return ([], {"epoch": epoch},
                html.Span(f"Session purgée : {_fmt(n)} décisions effacées, compteurs remis à zéro.",
                          style={"color": OK}))

    app.clientside_callback("function(n){ if(n){ window.print(); } return ''; }",
                            Output("btn-print", "title"), Input("btn-print", "n_clicks"),
                            prevent_initial_call=True)

    # 14 · Connexion
    @app.callback(
        Output("store-user",    "data",     allow_duplicate=True),
        Output("view-root",     "children", allow_duplicate=True),
        Output("login-msg",     "children"),
        Input("btn-login",      "n_clicks"),
        State("login-email",    "value"),
        State("login-password", "value"),
        prevent_initial_call=True,
    )
    def on_login(n, email, password):
        if not n:
            raise PreventUpdate
        if not email or not password:
            return no_update, no_update, "Email et mot de passe requis."
        user = authenticate(email.strip(), password)
        if user is None:
            return no_update, no_update, "Identifiants incorrects."
        return user, _render_profile(user), no_update

    # 15 · Création de compte
    @app.callback(
        Output("store-user",      "data",     allow_duplicate=True),
        Output("view-root",       "children", allow_duplicate=True),
        Output("signup-msg",      "children"),
        Input("btn-signup",       "n_clicks"),
        State("signup-name",      "value"),
        State("signup-email",     "value"),
        State("signup-password",  "value"),
        prevent_initial_call=True,
    )
    def on_signup(n, name, email, password):
        if not n:
            raise PreventUpdate
        if not name or not email or not password:
            return no_update, no_update, "Tous les champs sont obligatoires."
        if len(password) < 6:
            return no_update, no_update, "Mot de passe trop court (min. 6 caractères)."
        user, err = create_user(name.strip(), email.strip(), password)
        if err:
            return no_update, no_update, err
        return user, _render_profile(user), no_update

    # 16 · Déconnexion
    @app.callback(
        Output("store-user",  "data",     allow_duplicate=True),
        Output("view-root",   "children", allow_duplicate=True),
        Input("btn-logout",   "n_clicks"),
        prevent_initial_call=True,
    )
    def on_logout(n):
        if not n:
            raise PreventUpdate
        from .layout import build_view
        return None, build_view("login")

    # 17 · Bouton profil  mise à jour des initiales selon store-user
    @app.callback(
        Output("btn-profile", "children"),
        Input("store-user",   "data"),
    )
    def update_profile_btn(user):
        if not user:
            return "AN"
        return "".join(w[0] for w in (user.get("name") or "AN").split()[:2]).upper()


# ── Étapes d'architecture : figures calculées sur le modèle et le flux réels ──

def _stage_theme(fig: go.Figure) -> go.Figure:
    """Thème commun des figures d'étape : titre lisible, légende en bas si plusieurs séries."""
    _theme(fig, 250)
    named = [t for t in fig.data if getattr(t, "name", None) and t.showlegend is not False]
    if len(fig.data) > 1 and named:
        fig.update_layout(showlegend=True,
                          legend=dict(orientation="h", y=-0.32, x=0, font=dict(size=10)),
                          margin=dict(t=40, b=64, l=52, r=16))
    else:
        fig.update_layout(margin=dict(t=40, b=40, l=52, r=16))
    return fig


def _empty_stage(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
                       showarrow=False, font=dict(color=INK4, size=12))
    fig.update_xaxes(visible=False); fig.update_yaxes(visible=False)
    return _stage_theme(fig)


def _stage_figure(sid: str, inference, buffer) -> go.Figure:
    """Chaque figure est calculée à partir du checkpoint chargé ou du flux en
    cours : aucune courbe illustrative."""
    import torch
    x = np.linspace(-2.5, 2.5, 200)

    def inspected_window():
        acc = _cb_mod.inspected_account("")
        return acc, (buffer.get_window(acc) if (buffer is not None and acc) else None)

    if sid == "input":
        acc, win = inspected_window()
        if win is None:
            return _empty_stage("En attente d'un compte à fenêtre complète (10 transactions)")
        mn, mxv = win.min(axis=0), win.max(axis=0)
        rng = mxv - mn
        z = np.where(rng > 0, (win - mn) / np.where(rng > 0, rng, 1.0), 0.5)
        fig = go.Figure(go.Heatmap(
            z=z.T.tolist(), customdata=win.T.tolist(), showscale=False, zmin=0, zmax=1,
            x=[f"t−{9 - i}" if i < 9 else "t" for i in range(10)],
            y=[FEATURE_LABELS[f] for f in FEATURE_COLS],
            colorscale=[[0, "#F1F0EA"], [1, ACCENT]],
            hovertemplate="<b>%{y}</b> %{x}<br>valeur brute : %{customdata:.4g}<extra></extra>"))
        fig.update_yaxes(tickfont=MONO)
        fig.update_layout(title=dict(text=f"Fenêtre réelle du compte {acc} (échelle propre à "
                                          "chaque ligne)", font=dict(size=11, color=INK3), x=0))
        return _stage_theme(fig)

    if sid == "norm":
        acc, win = inspected_window()
        if win is None:
            return _empty_stage("En attente d'un compte à fenêtre complète (10 transactions)")
        z = inference._normalize(win)[-1]
        lim = 10.0
        clipped = [float(np.clip(v, -lim, lim)) for v in z]
        out_of_range = [abs(float(v)) > lim for v in z]
        fig = go.Figure(go.Bar(
            x=clipped, y=[FEATURE_LABELS[f] for f in inference.feature_cols],
            orientation="h", marker_color=[DANGER if o else ACCENT for o in out_of_range],
            text=[f"{v:.2e} · hors échelle" if o else "" for v, o in zip(z, out_of_range)],
            textposition="outside", textfont=dict(size=9, color=DANGER), cliponaxis=False,
            customdata=[float(v) for v in z],
            hovertemplate="<b>%{y}</b><br>valeur normalisée : %{customdata:.3g}<extra></extra>"))
        fig.update_layout(title=dict(
            text=f"Valeurs réellement fournies au modèle (compte {acc}, transaction la plus "
                 "récente) ; rouge = hors de l'échelle ±10", font=dict(size=11, color=INK3), x=0))
        fig.update_xaxes(title="valeur normalisée (z), tronquée à ±10", range=[-lim * 1.6, lim * 1.6])
        fig.update_yaxes(tickfont=MONO, autorange="reversed")
        return _stage_theme(fig)

    if sid == "cell":
        model, hidden = inference.model, inference.model.hidden_size
        concat = hidden + model.cell.input_size
        lr = _cb_mod._last_result
        var = next((f["label"] for f in (lr or {}).get("top_features", [])
                    if f["label"] in inference.feature_cols), inference.feature_cols[0])
        ci = hidden + inference.feature_cols.index(var)
        colors = {"forget": ACCENT, "input": OK, "candidate": WARN, "output": DANGER}
        names = {"forget": "Oubli", "input": "Entrée", "candidate": "Candidat", "output": "Sortie"}
        traces = []
        with torch.no_grad():
            for g in ("forget", "input", "candidate", "output"):
                gate = getattr(model.cell, f"{g}_gate")
                ys = []
                for xv in x[::4]:
                    xin = torch.zeros(1, concat)
                    xin[0, ci] = float(xv)
                    ys.append(gate.edge_activations(xin)[0, ci, :].abs().mean().item())
                traces.append(go.Scatter(x=x[::4], y=ys, mode="lines", name=names[g],
                                         line=dict(color=colors[g], width=1.8)))
        fig = go.Figure(traces)
        fig.update_layout(title=dict(text=f"Fonctions apprises des 4 portes pour la variable "
                                          f"« {FEATURE_LABELS.get(var, var)} »",
                                     font=dict(size=11, color=INK3), x=0))
        fig.update_xaxes(title="valeur normalisée x"); fig.update_yaxes(title="|φ(x)| moyen")
        return _stage_theme(fig)

    if sid == "basis":
        gate = inference.model.cell.forget_gate
        centers = gate.centers.detach().numpy()
        h = float(gate.h)
        traces = [go.Scatter(x=x, y=np.exp(-0.5 * ((x - c) / h) ** 2), mode="lines",
                             name="Gaussiennes (8)" if i == 0 else None, showlegend=(i == 0),
                             legendgroup="g", line=dict(color=ACCENT, width=1.4))
                  for i, c in enumerate(centers)]
        for k in range(1, gate.K + 1):
            traces.append(go.Scatter(x=x, y=np.cos(k * x), mode="lines",
                                     name="Fourier (cos kx, sin kx)" if k == 1 else None,
                                     showlegend=(k == 1), legendgroup="f",
                                     line=dict(color=VIOLET, width=1.2, dash="dot")))
            traces.append(go.Scatter(x=x, y=np.sin(k * x), mode="lines", showlegend=False,
                                     legendgroup="f", line=dict(color=VIOLET, width=1.2,
                                                                dash="dot")))
        fig = go.Figure(traces)
        fig.update_layout(title=dict(text=f"Les {len(centers)} gaussiennes (centres de "
                                          f"{centers.min():.0f} à {centers.max():.0f}, largeur "
                                          f"h = {h:.3f}) et les harmoniques de Fourier",
                                     font=dict(size=11, color=INK3), x=0))
        fig.update_xaxes(title="valeur normalisée x"); fig.update_yaxes(title="fonction de base")
        return _stage_theme(fig)

    if sid == "head":
        w = inference.model.projection.weight.detach().numpy()[0]
        b = float(inference.model.projection.bias.detach().numpy()[0])
        fig = go.Figure(go.Bar(
            x=[f"h[{i}]" for i in range(len(w))], y=[float(v) for v in w],
            marker_color=[ACCENT if v >= 0 else DANGER for v in w],
            hovertemplate="<b>%{x}</b><br>poids : %{y:.3f}<extra></extra>"))
        fig.update_layout(title=dict(text=f"Poids appris de la projection linéaire "
                                          f"(biais b = {b:.3f})",
                                     font=dict(size=11, color=INK3), x=0))
        fig.update_yaxes(title="poids")
        return _stage_theme(fig)

    # sortie : sigmoïde, seuil τ réel et score courant
    tau = float(inference.threshold)
    z = np.linspace(-8, 8, 200)
    fig = go.Figure([
        go.Scatter(x=z, y=1 / (1 + np.exp(-z)), mode="lines", line=dict(color=INK, width=2),
                   name="σ(z)"),
        go.Scatter(x=[-8, 8], y=[tau, tau], mode="lines", name=f"seuil τ = {tau:.2f}",
                   line=dict(color=DANGER, dash="dot", width=1)),
    ])
    lr = _cb_mod._last_result
    if lr:
        s = min(max(float(lr["score"]), 1e-6), 1 - 1e-6)
        fig.add_trace(go.Scatter(x=[float(np.log(s / (1 - s)))], y=[s], mode="markers",
                                 name=f"score courant {s * 100:.1f} %",
                                 marker=dict(color=ACCENT, size=10)))
    fig.update_xaxes(title="z (sortie linéaire)"); fig.update_yaxes(title="score", range=[0, 1])
    return _stage_theme(fig)
