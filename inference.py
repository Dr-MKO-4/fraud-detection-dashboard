"""
inference.py  MKANInference : charge le checkpoint une seule fois et expose score().

Deux formats de checkpoint :
  - historique (mkan_final.pt, cellule hybride de MKAN/cell.py) : log1p(|x|) sur
    les features à queue lourde puis z-score, comme à son entraînement (train.ipynb) ;
  - campagne (model.pt de MKAN/Etude_benchmarck/campagne_aws/train_run.py : clés
    state_dict, spec, preprocessing) : le prétraitement est RELU depuis le
    checkpoint (preprocessing.json) et appliqué à l'identique de l'entraînement
    (niveau1_harness.build_regime_frame + standardize) : NaN -> 0,
    sign(x)*log1p(|x|) hors colonnes exclues, z-score, écrêtage. Correctif P4 du
    03/11/2026 : le tableau de bord ne recode plus le prétraitement.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np
import torch

# Rendre le package MKAN importable depuis n'importe quel répertoire courant
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_BENCH = _ROOT / "MKAN" / "Etude_benchmarck"

from MKAN.cell import MKANScorer
from MKAN.explainability import explain_transaction

# Features soumises à log1p avant z-score (queue lourde)  format historique seulement
LOG_FEATURES = {"delta_B_orig", "delta_B_dest", "r1", "r2", "v1h"}

# Ordre des 12 variables calculées en flux (dashboard/stream_features.FEATURE_ORDER)
ENGINEERED_ORDER = [
    "delta_B_orig", "delta_B_dest", "r1", "r2", "flag_anomalie", "delta_commission",
    "var_agent_split", "rho_rupture", "rho_refund", "v1h", "flag_nuit", "rho_nouveau",
]
# Colonnes brutes du régime « brut » (niveau1_harness.RAW_NUMERIC_COLS)
RAW_NUMERIC_COLS = ["amount", "oldBalanceOrig", "newBalanceOrig",
                    "oldBalanceDest", "newBalanceDest"]


def _build_campaign_model(spec: dict, input_size: int) -> torch.nn.Module:
    """Même construction que campagne_aws/train_run.build_model."""
    if str(_BENCH) not in sys.path:
        sys.path.insert(0, str(_BENCH))
    from niveau1_benchmark_run import _build_gate_bases
    from lstm_cell import ConfigurableMKANScorer
    from gru_cell import GRUMKANScorer
    gate_bases, gate_kwargs = _build_gate_bases(spec["cell_type"], spec["base_by_gate"])
    cls = ConfigurableMKANScorer if spec["cell_type"] == "lstm" else GRUMKANScorer
    return cls(input_size, spec["hidden_size"], gate_bases=gate_bases,
               gate_kwargs=gate_kwargs or None)


def _explain_generic(model, xw: torch.Tensor, feature_names: list,
                     threshold: float) -> dict:
    """Explication par portes pour toute cellule à GenericKANLayer (TKAN ou GRU) :
    contribution d'une entrée = |phi(x)| moyen sur les nœuds de la porte, moyennée
    sur les W pas (même règle que MKAN.explainability.explain_transaction), captée
    par des hooks sur l'entrée réelle de chaque porte."""
    gates = [(n.replace("_gate", ""), getattr(model.cell, n))
             for n in ("forget_gate", "input_gate", "candidate_gate", "output_gate",
                       "reset_gate", "update_gate")
             if hasattr(model.cell, n)]
    hidden = model.hidden_size
    concat_labels = [f"h[{k}]" for k in range(hidden)] + list(feature_names)
    W = xw.shape[1]
    contribs = {g: np.zeros(len(concat_labels)) for g, _ in gates}
    handles = []

    def _hook(name):
        def fn(layer, inputs, _out):
            edges = layer.edge_activations(inputs[0])
            contribs[name] += edges.abs().mean(dim=-1)[0].numpy() / W
        return fn

    for g, layer in gates:
        handles.append(layer.register_forward_hook(_hook(g)))
    try:
        model.eval()
        with torch.no_grad():
            score = float(model(xw).reshape(-1)[0])
    finally:
        for hd in handles:
            hd.remove()

    glob = sum(contribs.values())
    glob = glob / (glob.sum() + 1e-12)
    order = np.argsort(-glob)
    return {
        "score": score,
        "verdict": "FRAUDE" if score >= threshold else "NORMALE",
        "threshold": threshold,
        "top_features": [{"label": concat_labels[i], "contribution": float(glob[i])}
                         for i in order],
        "gate_contribs": {g: v.tolist() for g, v in contribs.items()},
        "concat_labels": concat_labels,
    }


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
        self.threshold = threshold
        self.checkpoint_name = Path(checkpoint_path).name
        # Un seul fil à la fois dans le modèle : les requêtes Dash et le fil d'explication
        # l'appellent en parallèle, et deux forward simultanés des couches d'ondelettes de la
        # campagne provoquent une violation d'accès mémoire sous Windows (constaté le 09/11).
        self._verrou = threading.RLock()

        if "spec" in ckpt and "preprocessing" in ckpt:
            self._init_campaign(ckpt)
            return
        self.format = "historique"
        self.regime = "traite"
        self.cell_type = "lstm"
        self.gate_names = ["forget", "input", "candidate", "output"]

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

    def _init_campaign(self, ckpt: dict) -> None:
        """Checkpoint de la campagne (model.pt) : modèle reconstruit depuis la spec,
        prétraitement relu tel qu'enregistré à l'entraînement."""
        spec, pp = ckpt["spec"], ckpt["preprocessing"]
        self.format    = "campagne"
        self.spec      = spec
        self.regime    = pp["regime"]
        self.cell_type = spec["cell_type"]
        self.W         = int(spec["W"])
        self.feature_cols: list[str] = list(pp["feature_cols"])
        excluded = set(pp.get("log_excluded_cols", []))
        self._log_idx = [i for i, f in enumerate(self.feature_cols) if f not in excluded]
        self._signed_log = True
        stats = pp["standardize_stats"]
        self.mu  = np.array([stats[f]["mean"] for f in self.feature_cols], dtype=np.float32)
        std = np.array([stats[f]["std"] for f in self.feature_cols], dtype=np.float32)
        # niveau1_harness.standardize remplace un écart-type nul par 1.0 : même règle
        self.std = np.where(std == 0, 1.0, std).astype(np.float32)
        self._degenerate_idx = []
        self._clip = tuple(pp.get("clip", [-4.0, 4.0]))

        input_size = int(ckpt["input_size"])
        self.model = _build_campaign_model(spec, input_size)
        # Les grilles recalées sur les données (fit_grid) sont des buffers du state_dict
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.eval()
        self.gate_names = [n.replace("_gate", "") for n in
                           ("forget_gate", "input_gate", "candidate_gate", "output_gate",
                            "reset_gate", "update_gate") if hasattr(self.model.cell, n)]
        self._jit_forward = None

    def accepts_engineered(self) -> bool:
        """Vrai si le modèle prend les 12 variables calculées en flux (régime traité)."""
        return self.regime == "traite"

    def vector_from_row(self, row: dict, engineered: list[float]) -> list[float]:
        """Vecteur d'entrée d'une transaction, dans l'ordre de self.feature_cols.

        engineered : les 12 variables de stream_features (ordre ENGINEERED_ORDER).
        Régime brut : colonnes brutes de la transaction + indicatrices de `action`,
        comme niveau1_harness.build_regime_frame(regime="brut")."""
        if self.regime == "traite":
            by_name = dict(zip(ENGINEERED_ORDER, engineered))
            return [float(by_name.get(f, 0.0)) for f in self.feature_cols]
        out = []
        for f in self.feature_cols:
            if f.startswith("action_"):
                out.append(1.0 if str(row.get("action")) == f[len("action_"):] else 0.0)
            else:
                v = row.get(f)
                out.append(0.0 if v is None else float(v))
        return out

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
        thr = self.threshold if tau is None else float(tau)
        with self._verrou:
            if self.format == "campagne":
                return _explain_generic(self.model, x_t, self.feature_cols, thr)
            with torch.no_grad():
                result = explain_transaction(self.model, x_t, self.feature_cols, thr)
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
        with self._verrou, torch.no_grad():
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
        if self.format == "campagne":
            w = np.nan_to_num(w, nan=0.0)
            for i in self._log_idx:
                w[..., i] = np.sign(w[..., i]) * np.log1p(np.abs(w[..., i]))
            w = ((w - self.mu) / self.std).clip(*self._clip)
            return w.astype(np.float32)
        for i in self._log_idx:
            w[..., i] = np.log1p(np.abs(w[..., i]))
        w = (w - self.mu) / self.std
        for i in self._degenerate_idx:
            w[..., i] = 0.0
        return w

    def description(self) -> dict:
        """Description du modèle réellement chargé, pour le rapport COBAC et la vue Modèle."""
        noms = {"relukan": "ReLU-KAN", "wavkan_dog": "ondelette DoG", "efficientkan": "B-spline",
                "wavkan_morlet": "ondelette de Morlet", "linear": "linéaire", "hybrid": "hybride gaussienne-Fourier"}
        portes = {"Forget": "oubli", "Input": "entrée", "Candidate": "candidat", "Output": "sortie"}
        if self.cell_type == "gru":
            portes = {"Forget": "reset", "Input": "update", "Candidate": "candidat"}
        hidden = int(getattr(self.model, "hidden_size", 0) or 0)
        if self.format == "campagne":
            bases = self.spec["base_by_gate"]
            par_porte = ", ".join(f"{portes.get(g, g)} : {noms.get(b, b)}" for g, b in bases.items())
            cellule = "GRUKANCell (3 portes)" if self.cell_type == "gru" else "TKANCell (4 portes)"
            regime = "traité (log signé, z-score, écrêtage)" if self.regime == "traite" else "brut"
            return dict(cellule=cellule, n_portes=len(bases), hidden=hidden, W=self.W,
                        n_variables=len(self.feature_cols), bases=par_porte, regime=regime,
                        checkpoint=self.checkpoint_name,
                        resume=f"{cellule}, bases par porte ({par_porte}), {hidden} unités cachées, "
                               f"fenêtre W={self.W}, {len(self.feature_cols)} variables, régime {regime}")
        return dict(cellule="cellule hybride historique (4 portes)", n_portes=4, hidden=hidden, W=self.W,
                    n_variables=len(self.feature_cols), bases="gaussiennes M=8 et Fourier K=2 sur toutes les portes",
                    regime="log1p puis z-score", checkpoint=self.checkpoint_name,
                    resume=f"cellule hybride historique à 4 portes (gaussiennes M=8 et Fourier K=2), "
                           f"{hidden} unités cachées, fenêtre W={self.W}, {len(self.feature_cols)} variables")

    def transform_label(self, feature: str) -> str:
        """Libellé de la transformation appliquée à une variable (tableau de normalisation)."""
        i = self.feature_cols.index(feature)
        if self.format == "campagne":
            return ("sign·log1p → z → écrêtage" if i in self._log_idx
                    else "z → écrêtage")
        return "log1p → z" if i in self._log_idx else "z-score"
