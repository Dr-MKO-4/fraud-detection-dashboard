"""
stream_features.py  Calcul INCRÉMENTAL des 12 variables du modèle.

Reproduit, ligne par ligne et en O(1) amorti, ce que MOMTSIM/src/features.py
(FeatureEngineer) calcule en lot sur tout l'historique. Le calcul en lot est
super-linéaire (3.6 s pour 20 k lignes, 61 s pour 200 k) et inutilisable sur un
flux continu ; ici chaque compte garde un état borné (fenêtres 30 j / 90 j).

Sémantique reprise à l'identique (1 step = 1 heure) :
  delta_B_orig  = oldBalanceOrig - newBalanceOrig
  delta_B_dest  = newBalanceDest - oldBalanceDest
  r1, r2        = amount / (oldBalanceOrig | newBalanceOrig + eps)
  flag_anomalie = amount > moyenne + 2*écart-type (ddof=1) des 10 dernières
                  transactions du même (compte, action), min 2 observations
  flag_nuit     = step % 24 >= 22 ou < 6
  v1h           = nb de transactions du compte au même step (ligne courante incluse)
  var_agent_split = variance (ddof=0) des CASH_IN du même (agent, client, step)
  rho_rupture   = amount / (moyenne des montants du compte sur les 720 steps
                  STRICTEMENT antérieurs au step courant, 0 si aucun + eps)
  rho_refund    = nb REFUND REÇUS par le compte / (nb PAYMENT ÉMIS + eps)
                  sur [step-720, step] (step inclus)
  rho_nouveau   = part de destinataires « nouveaux » (aucun contact du couple
                  (émetteur, destinataire) depuis 2160 steps) sur [step-720, step]
  delta_commission = montant du dernier TRANSFER reçu par le compte (step
                  strictement antérieur, au plus 48 steps avant) - montant du
                  TRANSFER sortant courant ; 0.0 hors TRANSFER sortant apparié
                  (le calcul en lot met NaN, ramené à 0 au prétraitement)

Un step doit être fourni EN ENTIER à update_step() : v1h, var_agent_split,
rho_refund et rho_nouveau incluent les lignes du même step.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

EPS = 1e-6
WINDOW_STEPS = 30 * 24          # 720
NEW_DEST_GAP = 90 * 24          # 2160
ANOMALY_HISTORY = 10
MULE_TOLERANCE_STEPS = 48       # = FeatureEngineer.mule_tolerance_steps

FEATURE_ORDER = [
    "delta_B_orig", "delta_B_dest", "r1", "r2", "flag_anomalie", "delta_commission",
    "var_agent_split", "rho_rupture", "rho_refund", "v1h", "flag_nuit", "rho_nouveau",
]


class _Account:
    """État borné d'un compte émetteur."""
    __slots__ = ("rows", "sum_amount", "n_payment", "sum_new")

    def __init__(self):
        self.rows: deque = deque()      # (step, amount, is_payment, is_new)
        self.sum_amount = 0.0
        self.n_payment = 0
        self.sum_new = 0

    def prune(self, step: int) -> None:
        """Retire les lignes hors de [step - 720, ...]."""
        lo = step - WINDOW_STEPS
        rows = self.rows
        while rows and rows[0][0] < lo:
            _, amt, pay, new = rows.popleft()
            self.sum_amount -= amt
            self.n_payment -= pay
            self.sum_new -= new


class IncrementalFeatures:
    def __init__(self):
        self._acc: dict[str, _Account] = defaultdict(_Account)
        # 10 derniers montants par (compte, action) pour flag_anomalie
        self._hist: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=ANOMALY_HISTORY))
        # dernier contact par couple (émetteur, destinataire)
        self._last_contact: dict[tuple, int] = {}
        # steps des REFUND reçus par compte (fenêtre 720 steps)
        self._refund_in: dict[str, deque] = defaultdict(deque)
        # dernier TRANSFER reçu par compte : (step, montant)
        self._last_in: dict[str, tuple] = {}
        self._last_prune = 0
        self.last_step = -1

    # ------------------------------------------------------------------
    def update_step(self, step: int, rows: list[dict]) -> list[list[float]]:
        """Traite toutes les transactions d'UN step ; retourne les 12 variables
        de chaque ligne, dans l'ordre de `rows` et de FEATURE_ORDER."""
        if not rows:
            return []
        step = int(step)
        self.last_step = max(self.last_step, step)

        # agrégats de step : v1h et var_agent_split ---------------------
        count_by_acct: dict[str, int] = defaultdict(int)
        cash_in: dict[tuple, list] = defaultdict(list)
        for r in rows:
            count_by_acct[r["nameOrig"]] += 1
            if r["action"] == "CASH_IN":
                cash_in[(r["nameOrig"], r["nameDest"])].append(float(r["amount"]))
        var_split: dict[tuple, float] = {}
        for key, vals in cash_in.items():
            m = sum(vals) / len(vals)
            var_split[key] = sum((v - m) ** 2 for v in vals) / len(vals)   # ddof = 0

        # 1) état des comptes concernés, nettoyé, AVANT le step ---------
        touched = {r["nameOrig"] for r in rows}
        baseline: dict[str, tuple] = {}
        for a in touched:
            st = self._acc[a]
            st.prune(step)
            # moyenne des montants des steps strictement antérieurs (fenêtre 720)
            n = len(st.rows)
            mean_prev = (st.sum_amount / n) if n else 0.0
            baseline[a] = (mean_prev, st.n_payment, st.sum_new, n)

        # REFUND reçus : le step courant compte (fenêtre [step-720, step])
        for r in rows:
            if r["action"] == "REFUND":
                self._refund_in[r["nameDest"]].append(step)
        n_refund_in: dict[str, int] = {}
        for a in touched:
            dq = self._refund_in.get(a)
            if dq is None:
                n_refund_in[a] = 0
                continue
            while dq and dq[0] < step - WINDOW_STEPS:
                dq.popleft()
            n_refund_in[a] = len(dq)

        # 2) is_new pour chaque ligne, dans l'ordre, puis mises à jour --
        is_new_row: list[int] = []
        for r in rows:
            key = (r["nameOrig"], r["nameDest"])
            last = self._last_contact.get(key)
            new = 1 if (last is None or (step - last) > NEW_DEST_GAP) else 0
            is_new_row.append(new)
            self._last_contact[key] = step

        step_pay = defaultdict(int)
        step_new = defaultdict(int)
        step_n = defaultdict(int)
        for r, new in zip(rows, is_new_row):
            a = r["nameOrig"]
            step_pay[a] += 1 if r["action"] == "PAYMENT" else 0
            step_new[a] += new
            step_n[a] += 1

        # 3) calcul ligne par ligne ---------------------------------------
        out: list[list[float]] = []
        night = 1.0 if (step % 24 >= 22 or step % 24 < 6) else 0.0
        for r, new in zip(rows, is_new_row):
            a = r["nameOrig"]
            amount = float(r["amount"])
            old_o, new_o = float(r["oldBalanceOrig"]), float(r["newBalanceOrig"])
            old_d, new_d = float(r["oldBalanceDest"]), float(r["newBalanceDest"])

            # flag_anomalie : 10 dernières tx AVANT la courante, même (compte, action)
            hist = self._hist[(a, r["action"])]
            flag = 0.0
            if len(hist) >= 2:
                n = len(hist)
                mean = sum(hist) / n
                var = sum((v - mean) ** 2 for v in hist) / (n - 1)          # ddof = 1
                flag = 1.0 if amount > mean + 2.0 * math.sqrt(var) else 0.0
            hist.append(amount)

            mean_prev, pay0, new0, n0 = baseline[a]
            rho_rupture = amount / (mean_prev + EPS)
            rho_refund = n_refund_in[a] / ((pay0 + step_pay[a]) + EPS)
            rho_nouveau = (new0 + step_new[a]) / max(n0 + step_n[a], 1)

            # delta_commission : seuls les TRANSFER reçus aux steps antérieurs sont
            # dans _last_in (ceux du step courant y sont versés à l'étape 4)
            delta_commission = 0.0
            if r["action"] == "TRANSFER":
                last_in = self._last_in.get(a)
                if last_in is not None and step - last_in[0] <= MULE_TOLERANCE_STEPS:
                    delta_commission = last_in[1] - amount

            out.append([
                old_o - new_o,                                   # delta_B_orig
                new_d - old_d,                                   # delta_B_dest
                amount / (old_o + EPS),                          # r1
                amount / (new_o + EPS),                          # r2
                flag,                                            # flag_anomalie
                delta_commission,
                # le calcul en lot fusionne sur (émetteur, destinataire, step) : la
                # variance des CASH_IN s'applique aussi aux autres lignes de ce triplet
                var_split.get((a, r["nameDest"]), 0.0),
                rho_rupture,
                rho_refund,
                float(count_by_acct[a]),                         # v1h
                night,                                           # flag_nuit
                rho_nouveau,
            ])

        # 4) versement du step dans l'état des comptes -------------------
        for r, new in zip(rows, is_new_row):
            st = self._acc[r["nameOrig"]]
            amt = float(r["amount"])
            pay = 1 if r["action"] == "PAYMENT" else 0
            st.rows.append((step, amt, pay, new))
            st.sum_amount += amt
            st.n_payment += pay
            st.sum_new += new
            if r["action"] == "TRANSFER":
                # à égalité de step, le plus gros montant reçu (même règle que features.py)
                prev = self._last_in.get(r["nameDest"])
                if prev is None or prev[0] != step or amt > prev[1]:
                    self._last_in[r["nameDest"]] = (step, amt)

        # 5) purge périodique de la mémoire ---------------------------------
        if step - self._last_prune >= 240:
            self._last_prune = step
            horizon = step - NEW_DEST_GAP
            self._last_contact = {k: v for k, v in self._last_contact.items() if v >= horizon}
            for a in [a for a, st in self._acc.items() if not st.rows
                      or st.rows[-1][0] < step - WINDOW_STEPS]:
                del self._acc[a]
            for a in [a for a, dq in self._refund_in.items()
                      if not dq or dq[-1] < step - WINDOW_STEPS]:
                del self._refund_in[a]
            self._last_in = {a: v for a, v in self._last_in.items()
                             if step - v[0] <= MULE_TOLERANCE_STEPS}
        return out
