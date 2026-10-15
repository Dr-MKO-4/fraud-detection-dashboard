"""
inference.py  MKANInference : charge le checkpoint une seule fois et expose score().

Normalisation : log-transform (np.log1p) sur les features à queue lourde,
puis z-score (mu_train, std_train)  dans l'ordre exact de l'entraînement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

# Rendre le package MKAN importable depuis n'importe quel répertoire courant
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from MKAN.cell import MKANScorer
from MKAN.explainability import explain_transaction

# Features soumises à log1p avant z-score (queue lourde)
LOG_FEATURES = {"delta_B_orig", "delta_B_dest", "r1", "r2", "v1h"}


class MKANInference:
    """
    Chargement unique du modèle MKAN + scoring temps réel sur CPU.

    Args:
        checkpoint_path : chemin vers mkan_final.pt
        threshold       : seuil de décision (défaut 0.5)
    """

    def __init__(self, checkpoint_path: str | Path, threshold: float = 0.5):
        ckpt = torch.load(str(checkpoint_path), map_location="cpu",
                          weights_only=False)

        # Les paramètres sont dans ckpt["config"] (pas à la racine)
        cfg = ckpt.get("config", ckpt)   # fallback = racine pour anciens checkpoints

        # ── Hyperparamètres : priorité aux formes réelles des poids ───────
        # w_gauss shape = (concat_size, hidden_size, M)
        # concat_size   = input_size + hidden_size
        state = ckpt["model_state"]
        _wg   = state.get("cell.forget_gate.w_gauss")
        if _wg is not None:
            hidden_size = int(_wg.shape[1])
            input_size  = int(_wg.shape[0]) - hidden_size
            M           = int(_wg.shape[2])
        else:
            hidden_size = int(cfg.get("hidden_size", 16))
            input_size  = int(cfg.get("input_size",  12))
            M           = int(cfg.get("M",           8))
        K = int(cfg.get("K", 2))

        self.feature_cols: list[str] = cfg.get("feature_cols", [
            "delta_B_orig", "delta_B_dest", "r1", "r2",
            "flag_anomalie", "delta_commission", "var_agent_split",
            "rho_rupture", "rho_refund", "v1h", "flag_nuit", "rho_nouveau",
        ])
        self.W         = int(cfg.get("W", 10))
        self.threshold = threshold

        # ── Normalisation ─────────────────────────────────────────────────
        mu_raw  = cfg.get("mu_train",  {})
        std_raw = cfg.get("std_train", {})

        if isinstance(mu_raw, dict):
            self.mu  = np.array([mu_raw.get(f, 0.0)  for f in self.feature_cols],
                                dtype=np.float32)
            self.std = np.array([std_raw.get(f, 1.0) for f in self.feature_cols],
                                dtype=np.float32)
        else:
            self.mu  = np.asarray(mu_raw,  dtype=np.float32)
            self.std = np.asarray(std_raw, dtype=np.float32)

        # Une variable dont l'écart-type d'entraînement est dégénéré était
        # constante dans le jeu d'entraînement : le modèle n'a appris aucune
        # variation dessus. La normaliser reviendrait à diviser par ~0 et à
        # écraser toutes les autres entrées avec un signal jamais appris ; on
        # la neutralise (contribution nulle) jusqu'au prochain réentraînement.
        # Cas concret : delta_commission (std_train = 1e-06), corrigée depuis
        # dans MOMTSIM/src/features.py mais absente des données d'entraînement
        # du checkpoint courant.
        self._degenerate_idx = [i for i, s in enumerate(self.std) if s < 1e-5]
        if self._degenerate_idx:
            _names = ", ".join(self.feature_cols[i] for i in self._degenerate_idx)
            print(f"[WARN] Variables constantes à l'entraînement, neutralisées "
                  f"au scoring : {_names}")
        self.std = np.where(self.std < 1e-8, 1.0, self.std)
        self._log_idx = [i for i, f in enumerate(self.feature_cols)
                         if f in LOG_FEATURES]

        # ── Modèle ────────────────────────────────────────────────────────
        self.model = MKANScorer(input_size=input_size,
                                hidden_size=hidden_size,
                                M=M, K=K)
        self.model.load_state_dict(ckpt["model_state"])
        self.model.eval()

        # JIT trace du forward pur (sans explication)  utilisé si besoin d'un scoring rapide
        # sans explain_transaction. Fallback silencieux si le trace échoue (ex. DirectML).
        try:
            _dummy = torch.zeros(1, self.W, input_size)
            self._jit_forward = torch.jit.trace(self.model, _dummy)
            self._jit_forward.eval()
        except Exception:
            self._jit_forward = None

    # ── API publique ──────────────────────────────────────────────────────────

    def score(self, window: np.ndarray, tau: float | None = None) -> dict:
        """
        Calcule le score + explication pour une fenêtre (W, F).

        Args:
            tau : seuil de décision à appliquer (curseur de l'interface). À défaut de
                  valeur transmise, retombe sur self.threshold (calibration du modèle).

        Returns:
            dict : score, verdict, threshold, top_features, gate_contribs, concat_labels
        """
        x = self._normalize(window)
        x_t = torch.from_numpy(x).unsqueeze(0)   # (1, W, F)
        with torch.no_grad():
            result = explain_transaction(self.model, x_t, self.feature_cols,
                                         self.threshold if tau is None else float(tau))
        return result

    def score_fast_batch(self, windows: np.ndarray,
                         tau: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """
        Score un lot de fenêtres en UN seul forward pass (dimension batch native
        du modèle, cf. MKANScorer.forward : x_window (batch, W, F) → score (batch,)).
        Réservé au traitement par lot temporel (un `step` = potentiellement des
        centaines/milliers de comptes)  boucler un forward pass par compte
        serait bien plus lent qu'un seul appel batché.

        Args:
            windows : (N, W, F)  une fenêtre pleine par compte du lot.

        Returns:
            scores   : (N,) float
            verdicts : (N,) bool  True = FRAUDE (score >= threshold)
        """
        x = self._normalize(windows)
        x_t = torch.from_numpy(x)
        fwd = self._jit_forward if self._jit_forward is not None else self.model
        with torch.no_grad():
            # forward() renvoie déjà (batch,)  pas de squeeze ici : sur un lot
            # de taille 1, squeeze(-1) supprimerait la dimension batch elle-même.
            scores = fwd(x_t).numpy()
        return scores, scores >= (self.threshold if tau is None else float(tau))

    # ── interne ───────────────────────────────────────────────────────────────

    def _normalize(self, window: np.ndarray) -> np.ndarray:
        """Accepte (W, F) pour une fenêtre unique ou (N, W, F) pour un lot
        self.mu/self.std ont la forme (F,) et se diffusent sur la dernière
        dimension dans les deux cas."""
        w = window.copy().astype(np.float32)
        for i in self._log_idx:
            w[..., i] = np.log1p(np.abs(w[..., i]))
        w = (w - self.mu) / self.std
        for i in self._degenerate_idx:
            w[..., i] = 0.0
        return w
