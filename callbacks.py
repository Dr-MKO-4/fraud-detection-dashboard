"""
callbacks.py  Callbacks principaux du dashboard MKAN v3.0.

Modes :
  - SIMULÉ  : lecture séquentielle d'un dataset featuresLog chargé par l'utilisateur
  - LIVE    : poll d'une API REST ou consommation Kafka (thread serveur)
  - Injection de scénarios : ATO / REMBOURSEMENT / FAUX CRED. / SPLIT / SMURFING / ANOMALIE
"""

from __future__ import annotations

import base64
import datetime
import io
import queue
import threading

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, html, no_update
from dash.exceptions import PreventUpdate

import time as _time
from collections import OrderedDict, deque

from .layout import FEATURE_COLS, SIM_ACCOUNTS, W, chart_help
from . import live_ingest as _live_ingest
from . import figures as _fig

# Imports lourds levés au niveau module  évite dict lookup sys.modules dans les callbacks
from dashboard.metrics    import DriftMonitor       as _DriftMonitor

# Dérive des scores : référence figée sur les premiers scores de la session
_drift = _DriftMonitor()

# ── Palette ──────────────────────────────────────────────────────────────────
INK    = "#17191C"
INK3   = "#6B7280"
INK4   = "#9AA1AB"
ACCENT = "#0F6FB8"
DANGER = "#C2334D"
OK     = "#2F7D5B"
WARN   = "#C9A227"
LINE   = "#EEF0F4"

_BADGE_BASE: dict = {
    "display": "inline-block", "padding": "6px 12px", "color": "#fff",
    "fontFamily": "var(--mono)", "fontSize": "12px",
    "fontWeight": 600, "letterSpacing": ".08em",
}
_BADGE_FRAUDE: dict = {**_BADGE_BASE, "backgroundColor": DANGER}
_BADGE_NORMAL: dict = {**_BADGE_BASE, "backgroundColor": OK}

_latencies: deque = deque(maxlen=200)
# Résultat complet du dernier scoring  côté serveur, évite ~2.5 KB de JSON Dash par tick
_last_result: dict | None = None


def get_latency_p50() -> str:
    if not _latencies:
        return ""
    s = sorted(_latencies)
    return f"{s[len(s)//2]:.0f} ms"

_buffer       = None
_inference    = None
_rng = np.random.default_rng()    # RNG module-level  pas de re-création par appel

# ── Traitement par lot (playback auto) ────────────────────────────────────────
# Le tick auto-interval score tout un `step` d'un coup, en UN seul forward pass
# batché (cf. inference.score_fast_batch)  jamais de contributions détaillées
# synchrones pour tout le lot (jusqu'à 9430 tx/step, ce serait trop lent).
# Seules les transactions à haut risque (score > _EXPLAIN_THRESHOLD) reçoivent
# une explication complète, calculée par un thread séparé (_explain_worker)
# qui ne bloque jamais la lecture, et patchée dans le journal une fois prête.
_EXPLAIN_THRESHOLD = 0.7
_explain_queue: "queue.Queue" = queue.Queue()
_explain_pending: dict[int, dict] = {}   # entry id → {"driver", "contribution"}
_explain_lock = threading.Lock()
_entry_id_counter = 0
_explain_thread_started = False


def _explain_worker() -> None:
    """Thread démon : dépile (entry_id, window) et calcule l'explication
    complète (contributions par porte). Ne touche jamais les stores Dash
    directement  dépose le résultat dans _explain_pending, lu et fusionné
    dans store-journal par le tick suivant (_merge_explanations), ou par le
    callback merge_explanations quand le scoring est en pause."""
    while True:
        entry_id, window = _explain_queue.get()
        try:
            result = _inference.score(window)
            top = result["top_features"][0] if result["top_features"] else None
            driver     = top["label"]        if top else ""
            contrib    = top["contribution"] if top else 0.0
        except Exception:
            continue
        finally:
            _explain_queue.task_done()
        with _explain_lock:
            _explain_pending[entry_id] = {"driver": driver, "contribution": contrib}


def init(buffer, inference) -> None:
    global _buffer, _inference, _explain_thread_started
    _buffer, _inference = buffer, inference
    _live_ingest.set_reset_hook(lambda: (_buffer.flush_all(), _drift.reset()))
    if not _explain_thread_started:
        threading.Thread(target=_explain_worker, daemon=True).start()
        _explain_thread_started = True


# ── Profils de scénarios de fraude ───────────────────────────────────────────
# Chaque feature : (distribution, *params)
# Distributions : lognormal(mu,sigma) | normal(mu,sigma) | beta(a,b)
#                 const(v) | poisson(lam) | bernoulli(p)

# ── Profils de fraude : VARIANTES multiples par scénario ─────────────────────
# Chaque scénario a plusieurs variantes tirées aléatoirement à chaque injection.
# Distributions : lognormal(mu,sigma) | normal(mu,sigma) | beta(a,b)
#                 const(v) | poisson(lam) | bernoulli(p) | uniform(lo,hi)
# La clé "flag_nuit" peut être bernoulli pour refléter que la fraude n'est pas
# toujours nocturne (ATO de jour existe), que rho_rupture n'est pas toujours
# élevé dans le split, etc.

SCENARIO_PROFILES: dict[str, list[dict[str, tuple]]] = {
    # ── ATO  Account Takeover ───────────────────────────────────────────────
    # Vidage de compte suite à prise de contrôle. Pas toujours nocturne,
    # montants variables selon le solde cible, parfois combiné à du split.
    "ato": [
        {   # Variante 1 : vidage massif nocturne classique
            "delta_B_orig":     ("lognormal", 3.2,  0.6),
            "delta_B_dest":     ("lognormal", 3.0,  0.5),
            "r1":               ("beta",      8.0,  1.2),
            "r2":               ("beta",      7.0,  1.5),
            "flag_anomalie":    ("const",     1.0),
            "delta_commission": ("normal",    0.09, 0.02),
            "var_agent_split":  ("beta",      1.0,  8.0),
            "rho_rupture":      ("beta",      3.0,  3.5),
            "rho_refund":       ("beta",      0.4,  9.0),
            "v1h":              ("normal",    24.0, 4.0),
            "flag_nuit":        ("const",     1.0),
            "rho_nouveau":      ("beta",      6.0,  2.0),
        },
        {   # Variante 2 : ATO de jour, montants modérés, rho_rupture faible
            "delta_B_orig":     ("lognormal", 2.4,  0.9),
            "delta_B_dest":     ("lognormal", 2.2,  0.8),
            "r1":               ("beta",      6.0,  2.0),
            "r2":               ("beta",      5.0,  2.0),
            "flag_anomalie":    ("bernoulli", 0.6),
            "delta_commission": ("normal",    0.06, 0.03),
            "var_agent_split":  ("beta",      1.5,  6.0),
            "rho_rupture":      ("beta",      1.5,  5.0),
            "rho_refund":       ("beta",      0.6,  8.0),
            "v1h":              ("normal",    11.0, 4.0),
            "flag_nuit":        ("const",     0.0),
            "rho_nouveau":      ("beta",      4.5,  2.5),
        },
        {   # Variante 3 : ATO progressif  montants croissants, split en cascade
            "delta_B_orig":     ("lognormal", 1.8,  1.1),
            "delta_B_dest":     ("lognormal", 1.6,  1.0),
            "r1":               ("beta",      5.0,  2.5),
            "r2":               ("beta",      4.5,  2.5),
            "flag_anomalie":    ("const",     1.0),
            "delta_commission": ("normal",    0.11, 0.04),
            "var_agent_split":  ("beta",      4.0,  3.0),
            "rho_rupture":      ("beta",      4.5,  2.5),
            "rho_refund":       ("beta",      0.3,  9.0),
            "v1h":              ("normal",    18.0, 6.0),
            "flag_nuit":        ("bernoulli", 0.6),
            "rho_nouveau":      ("beta",      7.0,  1.5),
        },
    ],
    # ── REFUND  Fraude au remboursement circulaire ──────────────────────────
    # Exploitation de la procédure de remboursement vers de nouveaux comptes.
    # Faibles montants, transactions multiples, pas nécessairement la nuit.
    "refund": [
        {   # Variante 1 : remboursement pur  rho_refund très élevé, flux circulaire
            "delta_B_orig":     ("lognormal", 0.7,  0.5),
            "delta_B_dest":     ("lognormal", 0.8,  0.4),
            "r1":               ("beta",      1.2,  6.0),
            "r2":               ("beta",      1.0,  7.0),
            "flag_anomalie":    ("const",     0.0),
            "delta_commission": ("normal",    0.02, 0.01),
            "var_agent_split":  ("beta",      0.4,  9.0),
            "rho_rupture":      ("beta",      0.6,  8.0),
            "rho_refund":       ("beta",      8.5,  1.2),
            "v1h":              ("poisson",   2.0),
            "flag_nuit":        ("bernoulli", 0.1),
            "rho_nouveau":      ("beta",      1.8,  5.5),
        },
        {   # Variante 2 : remboursement + nouveaux destinataires (blanchiment)
            "delta_B_orig":     ("lognormal", 1.2,  0.6),
            "delta_B_dest":     ("lognormal", 1.3,  0.5),
            "r1":               ("beta",      2.0,  4.5),
            "r2":               ("beta",      1.8,  5.0),
            "flag_anomalie":    ("bernoulli", 0.25),
            "delta_commission": ("normal",    0.04, 0.02),
            "var_agent_split":  ("beta",      1.0,  7.0),
            "rho_rupture":      ("beta",      1.2,  6.0),
            "rho_refund":       ("beta",      6.5,  1.8),
            "v1h":              ("poisson",   5.0),
            "flag_nuit":        ("bernoulli", 0.3),
            "rho_nouveau":      ("beta",      4.5,  2.5),
        },
        {   # Variante 3 : remboursement à grande vitesse nocturne
            "delta_B_orig":     ("lognormal", 0.9,  0.7),
            "delta_B_dest":     ("lognormal", 1.0,  0.6),
            "r1":               ("beta",      1.5,  5.5),
            "r2":               ("beta",      1.3,  6.0),
            "flag_anomalie":    ("const",     0.0),
            "delta_commission": ("normal",    0.03, 0.015),
            "var_agent_split":  ("beta",      0.6,  8.5),
            "rho_rupture":      ("beta",      0.9,  7.5),
            "rho_refund":       ("beta",      7.8,  1.4),
            "v1h":              ("normal",    19.0, 4.0),
            "flag_nuit":        ("const",     1.0),
            "rho_nouveau":      ("beta",      2.5,  4.5),
        },
    ],
    # ── FAKE_CRED  Fausses accréditations ───────────────────────────────────
    # Phase dormante (transactions normales) puis exfiltration soudaine.
    # Le flag_anomalie n'est pas toujours levé (détection évitée).
    "fake_cred": [
        {   # Variante 1 : exfiltration soudaine avec flag_anomalie
            "delta_B_orig":     ("lognormal", 2.8,  0.5),
            "delta_B_dest":     ("lognormal", 2.5,  0.5),
            "r1":               ("beta",      5.5,  1.8),
            "r2":               ("beta",      4.5,  2.0),
            "flag_anomalie":    ("const",     1.0),
            "delta_commission": ("normal",    0.05, 0.02),
            "var_agent_split":  ("beta",      0.5,  9.0),
            "rho_rupture":      ("beta",      8.0,  1.2),
            "rho_refund":       ("beta",      0.3,  10.0),
            "v1h":              ("poisson",   3.0),
            "flag_nuit":        ("bernoulli", 0.55),
            "rho_nouveau":      ("beta",      3.5,  3.5),
        },
        {   # Variante 2 : fausses cred. discrètes  pas de flag_anomalie, rho_rupture modéré
            "delta_B_orig":     ("lognormal", 2.1,  0.7),
            "delta_B_dest":     ("lognormal", 1.9,  0.6),
            "r1":               ("beta",      4.0,  2.5),
            "r2":               ("beta",      3.5,  2.5),
            "flag_anomalie":    ("const",     0.0),
            "delta_commission": ("normal",    0.03, 0.02),
            "var_agent_split":  ("beta",      0.7,  8.0),
            "rho_rupture":      ("beta",      5.5,  2.5),
            "rho_refund":       ("beta",      0.4,  9.0),
            "v1h":              ("poisson",   5.0),
            "flag_nuit":        ("bernoulli", 0.4),
            "rho_nouveau":      ("beta",      2.5,  4.0),
        },
        {   # Variante 3 : exfiltration combinée à split agent (dissimulation)
            "delta_B_orig":     ("lognormal", 2.3,  0.8),
            "delta_B_dest":     ("lognormal", 2.0,  0.7),
            "r1":               ("beta",      4.5,  2.2),
            "r2":               ("beta",      4.0,  2.3),
            "flag_anomalie":    ("bernoulli", 0.5),
            "delta_commission": ("normal",    0.07, 0.03),
            "var_agent_split":  ("beta",      5.0,  2.5),
            "rho_rupture":      ("beta",      6.5,  2.0),
            "rho_refund":       ("beta",      0.5,  8.0),
            "v1h":              ("normal",    9.0,  3.0),
            "flag_nuit":        ("const",     0.0),
            "rho_nouveau":      ("beta",      4.0,  3.0),
        },
    ],
    # ── SPLIT  Fractionnement de commission agent ────────────────────────────
    # var_agent_split toujours dominant, mais montants et horaires variables.
    # Parfois combiné à du smurfing (v1h élevé).
    "split": [
        {   # Variante 1 : split pur, petits montants, jour
            "delta_B_orig":     ("lognormal", 0.8,  0.5),
            "delta_B_dest":     ("lognormal", 1.0,  0.4),
            "r1":               ("beta",      1.8,  5.5),
            "r2":               ("beta",      1.5,  6.0),
            "flag_anomalie":    ("const",     0.0),
            "delta_commission": ("normal",    0.04, 0.015),
            "var_agent_split":  ("beta",      9.0,  1.0),
            "rho_rupture":      ("beta",      0.7,  8.0),
            "rho_refund":       ("beta",      0.3,  9.5),
            "v1h":              ("poisson",   4.0),
            "flag_nuit":        ("const",     0.0),
            "rho_nouveau":      ("beta",      1.2,  6.5),
        },
        {   # Variante 2 : split avec volume horaire élevé (combiné smurf)
            "delta_B_orig":     ("lognormal", 1.1,  0.6),
            "delta_B_dest":     ("lognormal", 1.3,  0.5),
            "r1":               ("beta",      2.5,  4.5),
            "r2":               ("beta",      2.0,  5.0),
            "flag_anomalie":    ("bernoulli", 0.2),
            "delta_commission": ("normal",    0.06, 0.025),
            "var_agent_split":  ("beta",      7.5,  1.5),
            "rho_rupture":      ("beta",      1.0,  7.0),
            "rho_refund":       ("beta",      0.5,  8.5),
            "v1h":              ("normal",    14.0, 3.5),
            "flag_nuit":        ("bernoulli", 0.35),
            "rho_nouveau":      ("beta",      2.0,  5.0),
        },
        {   # Variante 3 : split + montants plus élevés + delta_commission fort
            "delta_B_orig":     ("lognormal", 1.5,  0.7),
            "delta_B_dest":     ("lognormal", 1.6,  0.6),
            "r1":               ("beta",      3.0,  4.0),
            "r2":               ("beta",      2.5,  4.5),
            "flag_anomalie":    ("const",     0.0),
            "delta_commission": ("normal",    0.09, 0.03),
            "var_agent_split":  ("beta",      8.0,  1.2),
            "rho_rupture":      ("beta",      1.5,  6.0),
            "rho_refund":       ("beta",      0.4,  9.0),
            "v1h":              ("poisson",   8.0),
            "flag_nuit":        ("bernoulli", 0.25),
            "rho_nouveau":      ("beta",      1.8,  5.5),
        },
    ],
    # ── SMURFING  Structuration multi-mules ─────────────────────────────────
    # Fragmentation des montants pour éviter les seuils de déclaration BEAC.
    # rho_nouveau élevé (nouveaux destinataires), v1h élevé, delta_commission variable.
    "smurf": [
        {   # Variante 1 : structuration classique  many small, many new dest.
            "delta_B_orig":     ("lognormal", 1.2,  0.5),
            "delta_B_dest":     ("lognormal", 1.4,  0.4),
            "r1":               ("beta",      2.5,  3.5),
            "r2":               ("beta",      2.0,  3.5),
            "flag_anomalie":    ("const",     0.0),
            "delta_commission": ("normal",    0.13, 0.03),
            "var_agent_split":  ("beta",      5.0,  2.5),
            "rho_rupture":      ("beta",      1.0,  6.0),
            "rho_refund":       ("beta",      0.5,  9.0),
            "v1h":              ("normal",    17.0, 4.0),
            "flag_nuit":        ("bernoulli", 0.4),
            "rho_nouveau":      ("beta",      7.5,  1.5),
        },
        {   # Variante 2 : smurfing nocturne concentré
            "delta_B_orig":     ("lognormal", 0.9,  0.6),
            "delta_B_dest":     ("lognormal", 1.1,  0.5),
            "r1":               ("beta",      2.0,  4.0),
            "r2":               ("beta",      1.8,  4.0),
            "flag_anomalie":    ("bernoulli", 0.15),
            "delta_commission": ("normal",    0.16, 0.04),
            "var_agent_split":  ("beta",      6.5,  2.0),
            "rho_rupture":      ("beta",      1.5,  5.5),
            "rho_refund":       ("beta",      0.4,  9.0),
            "v1h":              ("normal",    22.0, 3.0),
            "flag_nuit":        ("const",     1.0),
            "rho_nouveau":      ("beta",      8.0,  1.2),
        },
        {   # Variante 3 : smurfing de jour, commission basse, moins visible
            "delta_B_orig":     ("lognormal", 1.6,  0.7),
            "delta_B_dest":     ("lognormal", 1.8,  0.6),
            "r1":               ("beta",      3.5,  3.0),
            "r2":               ("beta",      3.0,  3.0),
            "flag_anomalie":    ("const",     0.0),
            "delta_commission": ("normal",    0.08, 0.03),
            "var_agent_split":  ("beta",      4.0,  3.0),
            "rho_rupture":      ("beta",      2.0,  5.0),
            "rho_refund":       ("beta",      0.6,  8.0),
            "v1h":              ("normal",    12.0, 4.0),
            "flag_nuit":        ("const",     0.0),
            "rho_nouveau":      ("beta",      6.5,  2.0),
        },
    ],
    # ── ANOMALIE  Erreur physique / transaction aberrante ────────────────────
    # Vraiment erratique  haute variance, pas de pattern unique.
    # flag_anomalie souvent mais pas toujours, rho_rupture variable.
    "anomaly": [
        {   # Variante 1 : montant anormalement élevé + flag_anomalie
            "delta_B_orig":     ("lognormal", 3.5,  1.2),
            "delta_B_dest":     ("lognormal", 0.5,  1.5),
            "r1":               ("beta",      7.0,  2.0),
            "r2":               ("beta",      1.0,  5.0),
            "flag_anomalie":    ("const",     1.0),
            "delta_commission": ("normal",    0.0,  0.06),
            "var_agent_split":  ("beta",      1.5,  5.0),
            "rho_rupture":      ("beta",      6.0,  1.8),
            "rho_refund":       ("beta",      0.8,  7.0),
            "v1h":              ("normal",    5.0,  4.0),
            "flag_nuit":        ("bernoulli", 0.4),
            "rho_nouveau":      ("beta",      1.5,  5.5),
        },
        {   # Variante 2 : erreur système  montant négatif / nul simulé, delta erratique
            "delta_B_orig":     ("normal",    0.0,  2.5),
            "delta_B_dest":     ("normal",    0.0,  2.5),
            "r1":               ("uniform",   0.0,  10.0),
            "r2":               ("uniform",   0.0,  10.0),
            "flag_anomalie":    ("const",     1.0),
            "delta_commission": ("normal",    0.0,  0.08),
            "var_agent_split":  ("uniform",   0.0,  10.0),
            "rho_rupture":      ("uniform",   0.0,  10.0),
            "rho_refund":       ("uniform",   0.0,  5.0),
            "v1h":              ("uniform",   0.0,  30.0),
            "flag_nuit":        ("bernoulli", 0.5),
            "rho_nouveau":      ("uniform",   0.0,  5.0),
        },
        {   # Variante 3 : anomalie douce  sans flag, rho_rupture modéré
            "delta_B_orig":     ("lognormal", 1.5,  1.8),
            "delta_B_dest":     ("lognormal", 2.0,  1.5),
            "r1":               ("beta",      3.5,  3.5),
            "r2":               ("beta",      2.5,  3.5),
            "flag_anomalie":    ("const",     0.0),
            "delta_commission": ("normal",    0.03, 0.05),
            "var_agent_split":  ("beta",      2.5,  3.5),
            "rho_rupture":      ("beta",      3.5,  3.0),
            "rho_refund":       ("beta",      2.0,  5.0),
            "v1h":              ("normal",    6.0,  4.5),
            "flag_nuit":        ("bernoulli", 0.3),
            "rho_nouveau":      ("beta",      3.0,  4.0),
        },
    ],
}

_SCENARIO_BTN_MAP = {
    "btn-inject-ato":    "ato",
    "btn-inject-refund": "refund",
    "btn-inject-fake":   "fake_cred",
    "btn-inject-split":  "split",
    "btn-inject-smurf":  "smurf",
    "btn-inject-anomaly":"anomaly",
}


def _scenario_features(scenario: str) -> list[float]:
    """
    Tire un vecteur de features depuis une variante aléatoire du scénario.
    Chaque appel peut produire un profil différent (variété réaliste).
    Utilise le RNG module-level _rng  pas de re-création par appel.
    """
    variants = SCENARIO_PROFILES[scenario]
    p        = variants[int(_rng.integers(len(variants)))]   # variante aléatoire
    out = []
    for feat in FEATURE_COLS:
        dist, *params = p[feat]
        if dist == "lognormal":
            out.append(float(abs(_rng.lognormal(*params))))
        elif dist == "normal":
            out.append(float(_rng.normal(*params)))
        elif dist == "beta":
            out.append(float(np.clip(_rng.beta(*params), 0.0, 1.0)))
        elif dist == "const":
            out.append(float(params[0]))
        elif dist == "poisson":
            out.append(float(_rng.poisson(*params)))
        elif dist == "bernoulli":
            out.append(float(_rng.random() < params[0]))
        elif dist == "uniform":
            out.append(float(_rng.uniform(*params)))
        else:
            out.append(0.0)
    return out


# ── Horodateur simulé ─────────────────────────────────────────────────────────

_BASE_DT     = datetime.datetime(2024, 1, 1)
_clock_cache: dict[int, str] = {}   # step → string, évite timedelta+strftime répétés

def _step_to_clock(step) -> str:
    if step is None:
        return ""
    try:
        k = int(step)
        if k not in _clock_cache:
            # 1 step MoMTSim = 1 heure simulée
            dt = _BASE_DT + datetime.timedelta(hours=k)
            _clock_cache[k] = f"J{k // 24 + 1} · {dt.hour:02d}h"
        return _clock_cache[k]
    except Exception:
        return f"step {step}"



def _window_cells(level: int) -> list:
    full = level >= W
    return [
        html.Span(className=("on" if i < level else "") + (" full" if full else ""))
        for i in range(W)
    ]


# Pré-calcul des 11 états possibles (0..W)  évite de recréer les Span à chaque tick
_WINDOW_CELLS_CACHE: list[list] = [_window_cells(lvl) for lvl in range(W + 1)]

# Cache de l'onglet Dérive JS  évite de recalculer toute la série (jusqu'à
# 2000 histogrammes JS) à chaque tick de slow-interval si aucune nouvelle
# transaction n'est arrivée depuis le dernier rendu (indexé sur un compteur
# monotone, pas la longueur : drift:scores se fige à 2000 éléments par LTRIM).
_drift_tab_cache: dict = {"version": -1, "fig": None}


# ── Compte inspecté, compteurs cumulés ───────────────────────────────────────
#
# « Compte inspecté » : le compte dont la fenêtre alimente la jauge, les
# contributions et la vue Fenêtres. C'est le compte saisi par l'utilisateur
# s'il est actif dans le flux ; sinon le dernier compte scoré du cycle (alerté
# ou non) : la jauge suit la cadence de scoring choisie. Un compte saisi ne
# transige que rarement (~0,01 tx/step) : tant qu'il n'est pas dans le lot, sa
# fenêtre et donc son score n'ont pas changé, la jauge reste figée.

_inspect: dict = {"acct": None}


# Agrégats par lot de l'onglet Historique : store-lots (propre à chaque onglet du
# navigateur), écrit par la MÊME réponse que la jauge, donc toujours en phase avec
# elle (réponse jetée par Dash => ni jauge ni point). Avant : regroupement du
# journal par seconde murale (deux ticks à 0,5 s fusionnés), journal plafonné à
# 2000 décisions (~10 lots visibles) et écrasé par merge_explanations.
LOTS_MAX = 60


def _merge_explanations(journal: list) -> bool:
    """Patche dans le journal les explications prêtes (_explain_worker)."""
    with _explain_lock:
        if not _explain_pending:
            return False
        pending = dict(_explain_pending)
        _explain_pending.clear()
    changed = False
    for e in journal:
        info = pending.get(e.get("id"))
        if info is not None:
            e["driver"] = info["driver"]
            e["contribution"] = info["contribution"]
            changed = True
    return changed


_recent_accts: "OrderedDict[str, None]" = OrderedDict()   # comptes des derniers lots (LRU)
_RECENT_MAX = 400


def note_recent(accounts: list[str]) -> None:
    """Retient les comptes les plus récemment actifs (du plus ancien au plus récent).
    La vue Fenêtres n'interroge que ceux-là : interroger tous les comptes (20 000, ou
    davantage avec des reliquats Redis) à chaque seconde saturait Redis, mono-thread."""
    for a in accounts:
        _recent_accts.pop(a, None)
        _recent_accts[a] = None
    while len(_recent_accts) > _RECENT_MAX:
        _recent_accts.popitem(last=False)


_epoch = 0


def new_epoch() -> int:
    """Nouvelle époque de session (purge). Un tick parti avant la purge et qui finit
    après ne doit pas réécrire l'ancien journal : il repère l'écart d'époque."""
    global _epoch
    _epoch += 1
    return _epoch


def inspected_account(typed: str | None) -> str | None:
    """Compte à afficher : celui saisi s'il a une fenêtre, sinon l'automatique."""
    typed = (typed or "").strip()
    if typed and _buffer is not None and _buffer.get_fill_level(typed) > 0:
        return typed
    return _inspect["acct"]


def _bump_totals(totals: dict | None, n_scored: int, n_alerts: int,
                 n_fraud: int, n_tp: int) -> dict:
    """Compteurs cumulés depuis le début de la session.

    Ils remplacent len(journal) : le journal est plafonné à 2000 entrées, sa
    longueur se fige donc et masquait toute progression."""
    t = dict(totals or {})
    t["scored"] = int(t.get("scored", 0)) + n_scored
    t["alerts"] = int(t.get("alerts", 0)) + n_alerts
    t["fraud"]  = int(t.get("fraud", 0))  + n_fraud
    t["tp"]     = int(t.get("tp", 0))     + n_tp
    return t


def _fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


# ── Logique de scoring partagée ───────────────────────────────────────────────

def _gauge(score: float | None, tau: float, acct: str, prev: dict | None, mode: str):
    """Style de la jauge, libellé du compte, bloc verdict, parité de surlignage."""
    prev = prev or {}
    flash = int(prev.get("flash", 0))
    if acct != prev.get("acct"):
        flash = 1 - flash
    hist = _buffer.get_scores(acct, 24) if acct else []
    return (_fig.gauge_state(score, tau, flash=flash), acct or "aucun",
            _fig.gauge_meta(score, tau, mode, hist), flash)


def _process(account_id: str, features: list[float],
             journal: list, tau: float = 0.5,
             true_label: int | None = None, *,
             window: "np.ndarray | None", level: int, prev: dict | None = None):
    """
    Score d'UNE fenêtre (injection de scénario) → 14 valeurs (Outputs du
    callback principal) + (libellé du compte, bloc verdict de la jauge).
    Toujours pleinement expliqué (contributions).
    """
    prog  = f"{level}/{W}"
    cells = _WINDOW_CELLS_CACHE[min(level, W)]

    if window is None:
        g_style, g_acc, g_meta, _ = _gauge(None, tau, account_id, prev, "")
        return (g_style, _fig.contribution_figure([]), prog, cells,
                None, "", {}, no_update, account_id, prog,
                no_update, no_update, no_update, ""), (g_acc, g_meta)

    global _last_result
    _t0    = _time.perf_counter()
    result = _inference.score(window, tau)
    _latencies.append((_time.perf_counter() - _t0) * 1000)
    _last_result = result   # stocké côté serveur, pas sérialisé dans le store Dash

    score   = result["score"]
    verdict = result["verdict"]
    driver  = result["top_features"][0]["label"] if result["top_features"] else ""

    _buffer.push_score(account_id, score)
    _buffer.push_drift_score(score)

    gauge, g_acc, g_meta, flash = _gauge(score, tau, account_id, prev,
                                         "après injection de scénario")
    bar   = _fig.contribution_figure(_fig.contribution_rows(result["top_features"]))
    badge_style = _BADGE_FRAUDE if verdict == "FRAUDE" else _BADGE_NORMAL
    verdict_note = f"injection de scénario · variable dominante {driver}"

    label = true_label if true_label is not None else (1 if verdict == "FRAUDE" else 0)
    entry = {
        "time":    datetime.datetime.now().strftime("%H:%M:%S"),
        "acct":    account_id,
        "score":   round(score, 6),
        "verdict": verdict,
        "driver":  driver,
        "label":   label,
    }
    new_journal = (list(journal or []) + [entry])[-2000:]

    store_result = {
        "acct":         account_id,
        "flash":        flash,
        "score":        score,
        "verdict":      verdict,
        "threshold":    result["threshold"],
        "top_features": result["top_features"],
    }
    return (gauge, bar, prog, cells, store_result,
            verdict, badge_style, new_journal,
            account_id, prog, no_update, no_update, f"{score * 100:.1f}%",
            verdict_note), (g_acc, g_meta)


# ── Traitement du flux live (1 tick = tout ce qui est arrivé depuis le dernier) ─

def _tick_batch(batch_cursor, account_id: str, journal: list, tau: float, totals: dict,
                prev: dict | None = None, lots: list | None = None):
    """
    Score tout ce que le flux live a livré depuis le tick précédent, en un
    seul forward pass batché, puis explique UN compte (le compte inspecté).
    Retourne les 18 valeurs du callback principal + les compteurs cumulés.
    `lots` (copie de store-lots) est complété sur place avec le point de ce lot.
    """
    global _entry_id_counter, _last_result
    lots = [] if lots is None else lots

    accounts, batch_features, batch_meta, step_val = _live_ingest.pop_pending()

    # Échauffement : ces lignes préremplissent les fenêtres sans produire de décision
    if any(m.get("warmup") for m in batch_meta):
        w_acc = [a for a, m in zip(accounts, batch_meta) if m.get("warmup")]
        w_feat = [f for f, m in zip(batch_features, batch_meta) if m.get("warmup")]
        for k in range(0, len(w_acc), 4000):
            _buffer.push_batch(w_acc[k:k + 4000], w_feat[k:k + 4000])
        keep = [i for i, m in enumerate(batch_meta) if not m.get("warmup")]
        accounts = [accounts[i] for i in keep]
        batch_features = [batch_features[i] for i in keep]
        batch_meta = [batch_meta[i] for i in keep]

    n_batch = len(accounts)
    if not n_batch:
        if step_val is None:
            raise PreventUpdate
        out = [no_update] * 21
        out[13] = f"pas {step_val} · rattrapage de l'historique (échauffement)"
        out[15] = _step_to_clock(step_val)
        out[16] = f"pas {step_val} · échauffement"
        return tuple(out)
    new_batch_cursor = int(batch_cursor or 0) + 1

    # État de session périmé (purge survenue pendant ce tick) : on repart de zéro
    if int((totals or {}).get("epoch", 0)) != _epoch:
        journal = []
        totals = {"epoch": _epoch}

    # Vérité terrain de la dernière transaction de chaque compte dans le lot.
    # None si le flux n'en fournit pas (cas d'un vrai flux opérateur).
    truth: dict[str, int | None] = {}
    for a, m in zip(accounts, batch_meta):
        raw = m.get("isFraud")
        truth[a] = None if raw is None else int(bool(raw))

    # 1 seul aller-retour réseau (pipeline) pour tout le lot, quel que soit N.
    complete = _buffer.push_batch(accounts, batch_features)
    note_recent(accounts)

    if complete:
        acc_list = list(complete.keys())
        windows  = np.stack([complete[a] for a in acc_list])          # (M, W, F)
        _t0 = _time.perf_counter()
        scores, is_fraud = _inference.score_fast_batch(windows, tau)   # 1 forward pass
        _latencies.append((_time.perf_counter() - _t0) * 1000)
        _drift.feed(scores)                    # référence de dérive (premiers scores)
    else:
        acc_list, scores, is_fraud = [], np.array([]), np.array([], dtype=bool)

    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    new_entries = []
    n_fraud = n_tp = 0
    for acc, sc, fraud in zip(acc_list, scores, is_fraud):
        _entry_id_counter += 1
        eid = _entry_id_counter
        sc_f = float(sc)
        lab = truth.get(acc)
        n_fraud += lab or 0
        n_tp += int(bool(fraud) and bool(lab))
        new_entries.append({
            "id": eid, "time": now_str, "lot": new_batch_cursor, "acct": acc,
            "score": round(sc_f, 6), "verdict": "FRAUDE" if fraud else "NORMALE",
            "driver": "", "label": lab,
        })
        _buffer.push_score(acc, sc_f)
        _buffer.push_drift_score(sc_f)
        # Au-delà du seuil à risque : explication complète déléguée au thread
        # séparé (_explain_worker), le journal est patché quand elle est prête.
        if sc_f > _EXPLAIN_THRESHOLD:
            _explain_queue.put((eid, complete[acc]))

    n_scored = len(new_entries)
    n_alerts = int(is_fraud.sum()) if len(is_fraud) else 0
    if not int((totals or {}).get("scored", 0)):
        lots.clear()                       # nouvelle session (purge, connexion, époque)
    new_totals = _bump_totals(totals, n_scored, n_alerts, n_fraud, n_tp)
    clock_str  = _step_to_clock(step_val)
    if n_scored:
        labs = [e["label"] for e in new_entries if e["label"] is not None]
        lot = {"lot": new_batch_cursor, "clock": clock_str, "step": step_val,
               "n": n_scored, "alert_pct": 100 * n_alerts / n_scored,
               "fraud_pct": 100 * sum(labs) / len(labs) if labs else None, "tau": tau}
        lots.append(lot)
    cursor_str = f"pas {step_val} · {n_scored}/{n_batch} scorées"

    if not n_scored:
        followed = inspected_account(account_id)
        level = _buffer.get_fill_level(followed) if followed else 0
        return (no_update, no_update, f"{level}/{W}", _WINDOW_CELLS_CACHE[min(level, W)],
                no_update, no_update, no_update, no_update,
                followed or "", f"{level}/{W}", _fmt_int(new_totals["scored"]),
                _fmt_int(new_totals["alerts"]), no_update,
                f"pas {step_val} · aucune fenêtre complète pour l'instant "
                f"(un compte est scoré à partir de {W} transactions)",
                no_update, clock_str, cursor_str, new_batch_cursor, new_totals,
                no_update, no_update)

    # ── Choix du compte inspecté ─────────────────────────────────────────────
    typed = (account_id or "").strip()
    if typed and _buffer.get_fill_level(typed) >= W:
        inspect_acc, auto, why = typed, False, ""
    else:
        inspect_acc, auto = acc_list[-1], True   # ordre d'arrivée : dernier compte scoré
        why = " (choisi automatiquement : dernier compte scoré du lot)"
        _inspect["acct"] = inspect_acc

    rescored = inspect_acc in complete
    pct = n_alerts / n_scored * 100
    batch_note = (f" · dernier lot : {n_alerts} alertes sur {n_scored} comptes scorés"
                  f" ({pct:.0f} % = taux d'alerte, pas un score)")
    level = _buffer.get_fill_level(inspect_acc)
    new_journal = (list(journal or []) + new_entries)[-2000:]
    _merge_explanations(new_journal)   # ici et non dans un 2e écrivain du store (course)
    tele_acc = f"{inspect_acc}{' · auto' if auto else ''}"

    window = complete[inspect_acc] if rescored else None
    if window is None and inspect_acc != (prev or {}).get("acct"):   # compte affiché côté navigateur
        window = _buffer.get_window(inspect_acc)

    if window is None:
        # Jauge figée : le lot garde le score qu'elle affiche toujours
        lot.update(acct=inspect_acc, gauge=(prev or {}).get("score"))
        return (no_update, no_update, f"{level}/{W}", _WINDOW_CELLS_CACHE[min(level, W)],
                no_update, no_update, no_update, new_journal,
                tele_acc, f"{level}/{W}",
                _fmt_int(new_totals["scored"]), _fmt_int(new_totals["alerts"]),
                no_update,
                f"Compte inspecté {inspect_acc}{why} · aucune nouvelle transaction de ce "
                f"compte, score inchangé{batch_note}",
                no_update, clock_str, cursor_str, new_batch_cursor, new_totals,
                no_update, no_update)

    result = _inference.score(window, tau)   # explication complète, 1 compte
    _last_result = result
    lot.update(acct=inspect_acc, gauge=result["score"])

    mode = ("automatique : dernier compte scoré à chaque cycle, alerté ou non" if auto
            else "suivi manuel")
    gauge, g_acc, g_meta, flash = _gauge(result["score"], tau, inspect_acc, prev, mode)
    bar   = _fig.contribution_figure(_fig.contribution_rows(result["top_features"]))
    verdict_text = result["verdict"]
    badge_style  = _BADGE_FRAUDE if verdict_text == "FRAUDE" else _BADGE_NORMAL
    verdict_note = (f"Compte inspecté {inspect_acc}{why}"
                    f" · score max du lot {float(scores.max()) * 100:.1f} %{batch_note}")
    store_result = {"acct": inspect_acc, "flash": flash, "score": result["score"],
                    "verdict": result["verdict"], "threshold": tau,
                    "top_features": result["top_features"]}

    return (gauge, bar, f"{level}/{W}", _WINDOW_CELLS_CACHE[min(level, W)],
            store_result, verdict_text, badge_style, new_journal,
            tele_acc, f"{level}/{W}",
            _fmt_int(new_totals["scored"]), _fmt_int(new_totals["alerts"]),
            f"{result['score'] * 100:.1f}%", verdict_note,
            no_update, clock_str, cursor_str, new_batch_cursor, new_totals,
            g_acc, g_meta)


# ── Enregistrement des callbacks ──────────────────────────────────────────────

def register_callbacks(app) -> None:

    # ── 1 · Callback principal : tick d'intervalle ou injection de scénario ───
    # auto-interval traite tout un LOT temporel (un `step`) d'un coup, en un
    # seul forward pass batché  les injections de scénario
    # restent transaction-par-transaction, toujours pleinement expliqués.
    @app.callback(
        Output("graph-gauge",          "style"),
        Output("graph-contributions",  "figure"),
        Output("progress-window",      "children"),
        Output("window-cells",         "children"),
        Output("store-last-result",    "data"),
        Output("verdict-badge",        "children"),
        Output("verdict-badge",        "style"),
        Output("store-journal",        "data"),
        Output("tele-account",         "children"),
        Output("tele-window",          "children"),
        Output("tele-count",           "children"),
        Output("tele-alerts",          "children"),
        Output("tele-last",            "children"),
        Output("verdict-note",         "children"),
        Output("store-cursor",         "data"),
        Output("tele-clock",           "children"),
        Output("tele-cursor",          "children"),
        Output("store-batch-cursor",   "data"),
        Output("store-totals",         "data"),
        Output("gauge-acc",            "children"),
        Output("gauge-meta",           "children"),
        Output("store-lots",           "data"),
        Input("auto-interval",         "n_intervals"),
        Input("btn-inject-ato",        "n_clicks"),
        Input("btn-inject-refund",     "n_clicks"),
        Input("btn-inject-fake",       "n_clicks"),
        Input("btn-inject-split",      "n_clicks"),
        Input("btn-inject-smurf",      "n_clicks"),
        Input("btn-inject-anomaly",    "n_clicks"),
        State("store-cursor",          "data"),
        State("store-batch-cursor",    "data"),
        State("store-account-id",      "data"),
        State("store-journal",         "data"),
        State("store-config",          "data"),
        State("store-totals",          "data"),
        State("store-last-result",     "data"),
        State("store-lots",            "data"),
        prevent_initial_call=True,
    )
    def on_tick_or_inject(n_intervals,
                          n_ato, n_refund, n_fake, n_split, n_smurf, n_anomaly,
                          cursor, batch_cursor, account_id, journal, cfg, totals,
                          last_result, store_lots):
        triggered  = ctx.triggered_id
        tau        = float((cfg or {}).get("tau", 0.5))
        cursor     = int(cursor or 0)
        journal    = journal or []
        account_id = str(account_id or "").strip()   # "" = compte inspecté automatiquement

        old_lots = store_lots or []
        new_lots = list(old_lots)
        lots_out = lambda: new_lots[-LOTS_MAX:] if new_lots != old_lots else no_update

        if triggered == "auto-interval":
            out = _tick_batch(batch_cursor, account_id, journal, tau, totals,
                              prev=last_result, lots=new_lots)
            return (*out, lots_out())

        if triggered not in _SCENARIO_BTN_MAP:
            raise PreventUpdate

        # Injection de scénario : un vecteur synthétisé est ajouté à la fenêtre du
        # compte inspecté (le compte saisi, sinon l'automatique), puis re-scoré.
        acc = inspected_account(account_id)
        if not acc:
            raise PreventUpdate   # aucun compte actif pour l'instant
        features   = _scenario_features(_SCENARIO_BTN_MAP[triggered])
        new_cursor = cursor + 1
        window     = _buffer.push(acc, features)
        level      = _buffer.get_fill_level(acc)

        proc, gauge_extra = _process(acc, features, journal, tau, 1, window=window,
                                     level=level, prev=last_result)
        proc = list(proc)
        verdict = proc[5]
        if window is not None:   # point « injection » dans l'onglet Historique
            new_lots.append({"lot": None, "inject": _SCENARIO_BTN_MAP[triggered],
                             "clock": "injection", "step": None, "n": 1,
                             "alert_pct": None, "fraud_pct": None, "tau": tau,
                             "acct": acc, "gauge": proc[4]["score"]})
        new_totals = (_bump_totals(totals, 1, int(verdict == "FRAUDE"), 1,
                                   int(verdict == "FRAUDE"))
                      if window is not None else (totals or {}))
        proc[10] = _fmt_int(new_totals.get("scored", 0))       # tele-count
        proc[11] = _fmt_int(new_totals.get("alerts", 0))       # tele-alerts
        return (*proc, new_cursor, "", f"injection {new_cursor}", no_update, new_totals,
                *gauge_extra, lots_out())

    # ── 2 · Cadence de scoring → intervalle ─────────────────────────────────────
    @app.callback(
        Output("auto-interval", "interval"),
        Input("radio-speed",    "value"),
    )
    def on_speed_change(speed_val):
        return int(speed_val or 1000)

    # ── 3 · Suspendre / Reprendre le scoring ──────────────────────────────────
    @app.callback(
        Output("auto-interval", "disabled"),
        Input("btn-play",       "n_clicks"),
        Input("btn-pause",      "n_clicks"),
        prevent_initial_call=True,
    )
    def on_playpause(n_play, n_pause):
        return ctx.triggered_id == "btn-pause"

    # ── 4bis · Statut affiché dans l'en-tête (calculé d'un seul endroit) ──────
    @app.callback(
        Output("stream-dot",   "className"),
        Output("stream-label", "children"),
        Input("store-live-state", "data"),
        Input("auto-interval",    "disabled"),
    )
    def update_stream_status(live_state, paused):
        if not (live_state or {}).get("connected"):
            return "mk-dot mk-dot--paused", "FLUX DÉCONNECTÉ"
        if paused:
            return "mk-dot mk-dot--paused", "SCORING EN PAUSE"
        return "mk-dot mk-dot--live", "SCORING EN COURS"

    # ── 4ter · Sync inp-account-id → store-account-id (persistance inter-vues) ─
    @app.callback(
        Output("store-account-id", "data"),
        Input("inp-account-id",    "value"),
        prevent_initial_call=True,
    )
    def on_account_change(value):
        return str(value or "").strip()   # vide = compte inspecté automatiquement

    # ── 5 · Flux live : état, transport (kafka / rest) et mode ────────────────
    # IMPORTANT : Dash ne déclenche pas un callback dont une entrée n'existe pas dans
    # la page. `radio-source` n'existe que dans l'écran Environnement ; le voyant de
    # connexion ne doit donc dépendre que de composants toujours présents
    # (slow-interval, store-config, store-live-state), sinon il reste figé sur sa
    # valeur initiale « déconnecté » tant qu'on n'a pas ouvert cet écran.
    _chosen = {"source": "rest"}      # transport choisi par l'utilisateur (mémoire serveur)

    def _sync_source(mode: str) -> None:
        """Aligne la connexion sur le mode : démo = MoMTSim, live = saisie manuelle."""
        state = _live_ingest.stats()
        source = _chosen["source"]
        if mode == "sim":
            if (state["source"] != source or state["manual"]
                    or state["endpoint"] != _live_ingest.demo_label(source)):
                _live_ingest.start(source)
        elif state["endpoint"] and not state["manual"]:
            _live_ingest.disconnect()          # passage en live : on coupe le flux de démo

    @app.callback(
        Output("store-live-state", "data"),
        Input("slow-interval",     "n_intervals"),
    )
    def publish_live_state(_slow):
        return _live_ingest.stats()

    @app.callback(
        Output("live-status",      "children"),
        Input("store-live-state",  "data"),
        Input("tabs-main",         "value"),
        State("store-config",      "data"),
    )
    def live_status_text(state, tab, cfg):
        if tab != "scoring":
            raise PreventUpdate               # le panneau n'existe que dans cet écran
        state = state or {}
        mode = (cfg or {}).get("mode", "sim")
        if mode == "live" and not state.get("endpoint"):
            return "Mode live : aucune source connectée (onglet Environnement)"
        if state.get("connected"):
            return f"✓ {(state.get('source') or '').upper()} · {state.get('received', 0):,} transactions reçues".replace(",", " ")
        return (f"✗ {(state.get('source') or 'source').upper()} déconnecté · "
                f"{state.get('error') or 'en attente de la source'}")

    @app.callback(
        Output("store-live-state", "data", allow_duplicate=True),
        Input("radio-source",      "value"),
        State("store-config",      "data"),
        prevent_initial_call=True,
    )
    def on_source_change(source, cfg):
        _chosen["source"] = source or "rest"
        _sync_source((cfg or {}).get("mode", "sim"))
        return _live_ingest.stats()

    @app.callback(
        Output("store-live-state", "data", allow_duplicate=True),
        Input("store-config",      "data"),
        prevent_initial_call=True,
    )
    def on_mode_change(cfg):
        _sync_source((cfg or {}).get("mode", "sim"))
        return _live_ingest.stats()

    # ── 5c · Fusion des explications asynchrones (score > 70%) dans le journal ─
    # Scoring en cours : la fusion se fait dans le tick (_tick_batch). Ce callback,
    # second écrivain de store-journal, renvoyait sinon une copie lue AVANT le tick
    # en vol et effaçait les lots que celui-ci venait d'ajouter. Il ne sert donc
    # qu'en pause, quand aucun tick n'écrit le journal.
    @app.callback(
        Output("store-journal", "data", allow_duplicate=True),
        Input("slow-interval",  "n_intervals"),
        State("store-journal",  "data"),
        State("auto-interval",  "disabled"),
        prevent_initial_call=True,
    )
    def merge_explanations(_slow, journal, paused):
        if not paused:
            raise PreventUpdate
        journal = journal or []
        if not _merge_explanations(journal):
            raise PreventUpdate
        return journal

    # ── 9 · Contenu des onglets secondaires ─────────────────────────────────────
    # Découplé de store-last-result (hot-path)  rafraîchi au plus à 1 Hz via slow-interval
    @app.callback(
        Output("tab-content",      "children"),
        Output("div-btn-symbolic", "style"),
        Input("tabs-bottom",       "value"),
        Input("slow-interval",     "n_intervals"),
        Input("store-lots",        "data"),     # écrit dans la même réponse que la jauge
        State("store-account-id",  "data"),
        State("store-config",      "data"),
        prevent_initial_call=True,
    )
    def update_tab(tab, _slow, store_lots, account_id, cfg):
        tau_cur = float((cfg or {}).get("tau", 0.5))
        from dash import dcc as _dcc
        btn_hidden = {"display": "none"}
        btn_show   = {"display": "block"}
        acc        = inspected_account(account_id) or ""
        if ctx.triggered_id == "store-lots" and tab != "tab-history":
            raise PreventUpdate

        if tab == "tab-history":
            # Un point = un lot, c.-à-d. les décisions d'un tick, celui qui a mis la
            # jauge à jour (store-lots, écrit dans la même réponse que la jauge).
            lots = list(store_lots or [])
            if len(lots) < 2:
                return _waiting("Pas encore assez de décisions. Un compte n'est scoré qu'après "
                                f"{W} transactions : patientez quelques instants."), btn_hidden
            # Abscisse = rang de l'événement : un lot du flux ou une injection
            xs = list(range(1, len(lots) + 1))
            info = [(f"injection {l['inject']} · compte {l['acct']}" if l.get("inject") else
                     f"lot {l['lot']} · {l['clock']} · {l['n']} comptes scorés"
                     + (f" · jauge : {l['acct']}" if l.get("acct") else ""))
                    for l in lots]
            alert_pct = [l["alert_pct"] for l in lots]
            # vérité terrain : absente d'un vrai flux opérateur (courbe alors omise)
            fraud_pct = [l["fraud_pct"] for l in lots]
            has_truth = any(v is not None for v in fraud_pct)
            # score affiché par la jauge à l'issue de chaque événement (même valeur)
            gauge_pct = [None if l.get("gauge") is None else 100 * l["gauge"] for l in lots]
            # τ en vigueur au moment de chaque décision (le curseur peut avoir bougé)
            tau_pct = [100 * float(l.get("tau", tau_cur)) for l in lots]
            traces = [
                go.Scatter(x=xs, y=alert_pct, mode="lines+markers", connectgaps=True,
                           name="Alertes de MKAN (lot)", line=dict(color=ACCENT, width=2),
                           marker=dict(size=5), customdata=info,
                           hovertemplate="%{customdata}<br>alertes MKAN : %{y:.0f} %<extra></extra>"),
                go.Scatter(x=xs, y=gauge_pct, mode="lines+markers", connectgaps=True,
                           name="Score de la jauge (1 compte)",
                           line=dict(color=INK4, width=1, dash="dot"),
                           marker=dict(size=[11 if l.get("inject") else 6 for l in lots],
                                       symbol=["star" if l.get("inject") else "circle"
                                               for l in lots],
                                       color=[INK4 if g is None else
                                              (DANGER if g >= t else OK)
                                              for g, t in zip(gauge_pct, tau_pct)]),
                           customdata=info,
                           hovertemplate="%{customdata}<br>score de la jauge : %{y:.1f} %<extra></extra>"),
            ]
            if has_truth:
                traces.insert(0, go.Scatter(
                    x=xs, y=fraud_pct, mode="lines", name="Fraude réelle (lot)",
                    connectgaps=True,
                    line=dict(color=INK3, width=2, dash="dash"), customdata=info,
                    hovertemplate="%{customdata}<br>fraude réelle : %{y:.0f} %<extra></extra>"))
            traces.append(go.Scatter(
                x=xs, y=tau_pct, mode="lines", name="Seuil τ", line_shape="hv",
                line=dict(color=DANGER, width=1, dash="dot"), opacity=.6,
                hovertemplate="seuil τ : %{y:.0f} %<extra></extra>"))
            fig = go.Figure(traces)
            last = lots[-1]
            fig.update_layout(
                yaxis=dict(title="%", range=[0, 100], gridcolor=LINE, ticksuffix=" %"),
                xaxis=dict(title=("Événement (le dernier, à droite, est celui de la jauge : "
                                  + (f"injection sur {last['acct']}" if last.get("inject")
                                     else last["clock"]) + ")"),
                           gridcolor=LINE, nticks=8),
                legend=dict(orientation="h", y=1.15, x=0),
                height=240, margin=dict(t=30, b=44, l=56, r=20),
                paper_bgcolor="white", plot_bgcolor="white",
                font=dict(family="IBM Plex Sans, sans-serif", size=11, color=INK),
            )
            help_ = chart_help(
                "Pour chaque lot de transactions scoré (un point), le pourcentage de comptes "
                "que MKAN a mis en alerte" + (", comparé au pourcentage de comptes réellement "
                "frauduleux (la vérité, connue du simulateur)" if has_truth else
                " (ce flux ne fournit pas de vérité terrain : pas de courbe de fraude réelle)")
                + ". Les points pointillés reprennent le score affiché par la jauge après "
                "chaque lot : celui d'UN compte, le compte inspecté (étoile = injection de "
                "scénario). Le dernier point est toujours la valeur actuelle de la jauge.",
                "Les courbes de lot devraient se suivre. Alertes nettement au-dessus de la "
                "fraude réelle : trop de fausses alertes. En dessous : des fraudes manquées. "
                "Un écart qui dure indique un problème (données qui ne ressemblent plus à "
                "l'entraînement, seuil τ mal placé). Le score de la jauge, lui, porte sur un "
                "seul compte : il peut être bas dans un lot très alerté, et inversement.",
                [(ACCENT, "Alertes de MKAN"), (INK3, "Fraude réelle"),
                 (INK4, "Score de la jauge"), (DANGER, "Seuil τ")])
            return html.Div([_dcc.Graph(figure=fig, config={"displayModeBar": False}),
                             help_]), btn_hidden

        if tab == "tab-drift":
            drift = _buffer.get_drift_scores()
            if not _drift.ready:
                return _waiting(
                    f"Calibrage de la référence : {len(_drift.ref)} / {_drift.REF_N} scores "
                    "recueillis. La dérive se mesure par rapport aux premiers scores de la "
                    "session, figés comme référence."), btn_hidden
            # Sauter le recalcul si aucun nouveau score n'est arrivé depuis le dernier rendu
            version = _buffer.get_drift_version()
            if version == _drift_tab_cache["version"]:
                fig = _drift_tab_cache["fig"]
            else:
                xs, ys = _drift.series(drift)
                thr = _drift.threshold
                fig = go.Figure([
                    go.Scatter(x=xs, y=ys, mode="lines", name="Divergence JS",
                               line=dict(color=ACCENT, width=2),
                               hovertemplate="score n°%{x}<br>divergence : %{y:.4f} bit<extra></extra>"),
                    go.Scatter(x=[xs[0], xs[-1]] if xs else [], y=[thr, thr], mode="lines",
                               name=f"Seuil d'alerte ({thr:.3f})",
                               line=dict(color=DANGER, width=1.5, dash="dot")),
                ])
                fig.update_layout(
                    height=240, margin=dict(t=10, b=70, l=56, r=20),
                    yaxis=dict(title="Divergence JS (bits)", rangemode="tozero", gridcolor=LINE),
                    xaxis=dict(title="Rang du dernier score de la fenêtre (les plus récents à droite)",
                               gridcolor=LINE),
                    legend=dict(orientation="h", y=-0.35, x=0),
                    paper_bgcolor="white", plot_bgcolor="white",
                    font=dict(family="IBM Plex Sans, sans-serif", size=11, color=INK))
                _drift_tab_cache["version"], _drift_tab_cache["fig"] = version, fig
            help_ = chart_help(
                "À quel point la distribution des 500 derniers scores s'éloigne de celle des "
                f"{_drift.REF_N} premiers scores de la session, figés comme référence "
                "(divergence de Jensen-Shannon en bits : 0 = identiques, 1 = totalement "
                "différentes).",
                f"Sous le seuil : le comportement du modèle est stable. Le seuil "
                f"({_drift.threshold:.3f}) n'est pas arbitraire : c'est 3 fois le bruit "
                f"d'échantillonnage ({(_drift.noise or 0):.4f}), mesuré en comparant deux moitiés "
                "tirées au hasard de la référence elle-même. Au-dessus, le profil des scores a "
                "changé au-delà de ce que le hasard explique : nouveau type de fraude, "
                "population différente ou phase du simulateur. À revoir avant de se fier au "
                "modèle.",
                [(ACCENT, "Divergence JS"), (DANGER, "Seuil d'alerte")])
            return html.Div([_dcc.Graph(figure=fig, config={"displayModeBar": False}),
                             help_]), btn_hidden

        if tab == "tab-symbolic":
            # Le rafraîchissement à 1 Hz ne doit pas écraser le rapport généré : on
            # n'affiche l'invite que lorsque l'utilisateur ouvre l'onglet.
            if ctx.triggered_id == "slow-interval":
                raise PreventUpdate
            return _waiting(
                "Cliquez sur « Générer le rapport COBAC » pour lancer l'extraction "
                "symbolique (~5-30 s selon la complexité du modèle)."
            ), btn_show

        return no_update, btn_hidden


def _waiting(msg: str) -> html.P:
    return html.P(msg, style={"fontSize": "13.5px", "color": INK4,
                               "lineHeight": 1.65, "maxWidth": "58ch"})
