"""
metrics.py  Indicateurs de validation et de dérive du scoreur MKAN.

Aucune dépendance hors numpy. Toutes les fonctions acceptent des scores
continus dans [0, 1] et des étiquettes binaires (0 = légitime, 1 = fraude).

Références d'usage en détection de fraude :
  - KS  : sup |F1(x) − F0(x)|, pouvoir discriminant.
  - PSI : stabilité de population, alerte au-delà de 0.2.
  - ECE : écart de calibration moyen pondéré (10 bacs).
  - JS  : divergence de Jensen-Shannon, bornée dans [0, ln 2].
"""

from __future__ import annotations

import numpy as np

EPS = 1e-9


# ── Matrice de confusion et taux ─────────────────────────────────────────────

def confusion(y_true, scores, tau: float = 0.5) -> dict:
    y = np.asarray(y_true, dtype=int)
    p = (np.asarray(scores, dtype=float) >= tau).astype(int)
    tp = int(((p == 1) & (y == 1)).sum())
    fp = int(((p == 1) & (y == 0)).sum())
    tn = int(((p == 0) & (y == 0)).sum())
    fn = int(((p == 0) & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec  = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": prec,
        "recall": rec,
        "specificity": tn / (tn + fp) if tn + fp else 0.0,
        "fpr": fp / (fp + tn) if fp + tn else 0.0,
        "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
    }


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de confiance de Wilson  robuste sur petits effectifs."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    demi = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - demi), min(1.0, centre + demi))


# ── Discrimination ───────────────────────────────────────────────────────────

def roc_curve(y_true, scores):
    """Retourne (fpr, tpr) par seuil décroissant, sans sklearn."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    order = np.argsort(-s)
    y = y[order]
    tp = np.cumsum(y)
    fp = np.cumsum(1 - y)
    pos, neg = max(int(y.sum()), 1), max(int((1 - y).sum()), 1)
    return np.concatenate([[0.0], fp / neg]), np.concatenate([[0.0], tp / pos])


def roc_auc(y_true, scores) -> float:
    fpr, tpr = roc_curve(y_true, scores)
    return float(np.trapz(tpr, fpr))


def ks_statistic(y_true, scores) -> float:
    fpr, tpr = roc_curve(y_true, scores)
    return float(np.max(np.abs(tpr - fpr)))


# ── Calibration ──────────────────────────────────────────────────────────────

def brier_score(y_true, scores) -> float:
    y = np.asarray(y_true, dtype=float)
    s = np.asarray(scores, dtype=float)
    return float(np.mean((s - y) ** 2))


def log_loss(y_true, scores) -> float:
    y = np.asarray(y_true, dtype=float)
    s = np.clip(np.asarray(scores, dtype=float), EPS, 1 - EPS)
    return float(-np.mean(y * np.log(s) + (1 - y) * np.log(1 - s)))


def reliability(y_true, scores, bins: int = 10):
    """Diagramme de fiabilité : (score moyen, fréquence observée, effectif) par bac."""
    y = np.asarray(y_true, dtype=float)
    s = np.asarray(scores, dtype=float)
    idx = np.clip((s * bins).astype(int), 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        n = int(m.sum())
        out.append((float(s[m].mean()) if n else np.nan,
                    float(y[m].mean()) if n else np.nan, n))
    return out


def expected_calibration_error(y_true, scores, bins: int = 10) -> float:
    rel = reliability(y_true, scores, bins)
    n_tot = sum(n for _, _, n in rel) or 1
    return float(sum((n / n_tot) * abs(obs - pred)
                     for pred, obs, n in rel if n))


# ── Stabilité et dérive ──────────────────────────────────────────────────────

def psi(reference, current, bins: int = 10) -> float:
    """Population Stability Index. > 0.1 à surveiller, > 0.2 dérive significative."""
    ref, cur = np.asarray(reference, float), np.asarray(current, float)
    if len(ref) < 5 or len(cur) < 5:
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    fa = np.clip(np.histogram(ref, edges)[0] / len(ref), 1e-4, None)
    fb = np.clip(np.histogram(cur, edges)[0] / len(cur), 1e-4, None)
    return float(np.sum((fb - fa) * np.log(fb / fa)))


def js_divergence(p, q, bins: int = 20) -> float:
    """Divergence de Jensen-Shannon entre deux échantillons, bornée [0, ln 2]."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    P = np.clip(np.histogram(np.asarray(p, float), edges)[0].astype(float), EPS, None)
    Q = np.clip(np.histogram(np.asarray(q, float), edges)[0].astype(float), EPS, None)
    P /= P.sum(); Q /= Q.sum()
    M = 0.5 * (P + Q)
    kl = lambda a, b: float(np.sum(a * np.log(a / b)))
    return 0.5 * kl(P, M) + 0.5 * kl(Q, M)


def js_divergence_bits(p, q, bins: int = 10) -> float:
    """Divergence de Jensen-Shannon en BITS, bornée [0, 1] (0 = distributions
    identiques, 1 = supports disjoints). Même mesure que js_divergence, en base 2 :
    l'échelle 0-1 est celle que l'on lit spontanément, alors que la version en nats
    plafonne à ln 2 = 0,693."""
    return js_divergence(p, q, bins=bins) / float(np.log(2.0))


class DriftMonitor:
    """Dérive des scores par rapport à une RÉFÉRENCE FIGÉE.

    Trois défauts de l'ancienne mesure sont corrigés :
      - la référence glissait avec la fenêtre (1re moitié contre 2e moitié des 2000
        derniers scores) : elle ne voyait jamais une dérive lente ; ici la référence
        est figée sur les premiers scores de la session ;
      - la comparaison portait sur 20 scores répartis en 20 classes : avec si peu
        d'échantillons la divergence est fortement gonflée même sans aucune dérive ;
        ici la fenêtre courante compte 500 scores sur 10 classes ;
      - le seuil d'alerte (0,05) était fixé à la main ; ici il vaut 3 fois le bruit
        d'échantillonnage MESURÉ sur la référence (divergence entre deux moitiés
        tirées au hasard de la référence elle-même), plancher 0,01.
    """

    REF_N = 1000       # scores figés comme référence
    WINDOW = 500       # scores récents comparés à la référence
    BINS = 10

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.ref: list[float] = []
        self.noise: float | None = None

    @property
    def ready(self) -> bool:
        return self.noise is not None

    def feed(self, scores) -> None:
        """Alimente la référence tant qu'elle n'est pas complète."""
        if self.ready:
            return
        room = self.REF_N - len(self.ref)
        self.ref.extend(float(x) for x in list(scores)[:room])
        if len(self.ref) >= self.REF_N:
            rng = np.random.default_rng(0)
            arr = np.asarray(self.ref)
            half = self.REF_N // 2
            vals = []
            for _ in range(30):
                perm = rng.permutation(arr)
                vals.append(js_divergence_bits(perm[:half], perm[half:], self.BINS))
            self.noise = float(np.mean(vals))

    @property
    def threshold(self) -> float:
        return max(0.01, 3.0 * (self.noise or 0.0))

    def value(self, recent) -> float | None:
        """Divergence (bits) entre la référence et les WINDOW derniers scores."""
        if not self.ready or len(recent) < 50:
            return None
        return js_divergence_bits(self.ref, list(recent)[-self.WINDOW:], self.BINS)

    def series(self, scores, step: int = 25) -> tuple[list[int], list[float]]:
        """Évolution : divergence de chaque fenêtre de WINDOW scores glissante."""
        xs, ys = [], []
        for end in range(max(50, self.WINDOW // 4), len(scores) + 1, step):
            xs.append(end)
            ys.append(js_divergence_bits(self.ref, scores[max(0, end - self.WINDOW):end],
                                         self.BINS))
        return xs, ys


def deciles(y_true, scores):
    """Taux de fraude par bande de score décroissante  le « pourquoi ce seuil »."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    rows = []
    for i in range(10):
        hi, lo = 1 - i / 10, 1 - (i + 1) / 10
        m = (s >= lo) & (s <= hi if i == 0 else s < hi)
        n = int(m.sum())
        rows.append({"bande": f"{hi:.1f}–{lo:.1f}", "n": n,
                     "taux": float(y[m].mean()) if n else None})
    return rows


# ── Alertes graduées ─────────────────────────────────────────────────────────

TIERS = {
    "psi": (0.10, 0.20),
    "js":  (0.03, 0.05),
    "ece": (0.05, 0.10),
}


def tier(name: str, value: float) -> int:
    """1 informatif · 2 investigation · 3 action requise (ré-entraînement / repli)."""
    t2, t3 = TIERS.get(name, (np.inf, np.inf))
    return 3 if value >= t3 else 2 if value >= t2 else 1


def session_report(y_true, scores, tau: float = 0.5) -> dict:
    """
    Bloc d'indicateurs prêt à insérer dans le rapport COBAC.
    roc_curve et reliability sont calculés une seule fois ici et exposés dans
    le dict retourné (clés fpr_curve, tpr_curve, reliability_data) pour que
    les callbacks n'aient pas à les recalculer.
    """
    y, s = np.asarray(y_true, int), np.asarray(scores, float)
    half = len(s) // 2
    p = psi(s[:half], s[half:]) if half >= 5 else 0.0

    # roc_curve une seule fois  réutilisé pour AUC et KS
    fpr_arr, tpr_arr = roc_curve(y, s)
    auc_val = float(np.trapz(tpr_arr, fpr_arr))
    ks_val  = float(np.max(np.abs(tpr_arr - fpr_arr)))

    # reliability une seule fois  réutilisée pour ECE et le graphique calibration
    rel_data = reliability(y, s)
    n_tot = sum(n for _, _, n in rel_data) or 1
    e = float(sum((n / n_tot) * abs(obs - pred)
                  for pred, obs, n in rel_data if n and not np.isnan(obs)))

    return {
        **confusion(y, s, tau),
        "n": int(len(s)), "tau": float(tau),
        "auc": auc_val, "ks": ks_val,
        "brier": brier_score(y, s), "log_loss": log_loss(y, s),
        "ece": e, "psi": p,
        "tier_psi": tier("psi", p), "tier_ece": tier("ece", e),
        "recall_ci95": wilson_interval(int(((s >= tau) & (y == 1)).sum()), int(y.sum())),
        # Courbes pré-calculées exposées aux callbacks
        "fpr_curve":       fpr_arr.tolist(),
        "tpr_curve":       tpr_arr.tolist(),
        "reliability_data": rel_data,
    }
