"""
modele.py  Modèle de scoring courant et changement de modèle à chaud.

Le tableau de bord ne garde pas une référence directe à MKANInference : il garde un ModeleCourant,
qui délègue tout au modèle actuellement chargé. Changer de modèle remplace ce modèle et reconfigure
ce qui en dépend : longueur de fenêtre W (buffer vidé, scoring, vues), vecteur d'entrée du flux,
textes et cartes de la vue Modèle, nom du checkpoint affiché.

Le catalogue des modèles proposés est MKAN/checkpoints/modeles_tableau_de_bord.json : le modèle
historique et, pour chaque finaliste de la règle du 06/11, le modèle de la graine au score perf
médian sur la validation.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DOSSIER = _ROOT / "MKAN" / "checkpoints"
CATALOGUE = DOSSIER / "modeles_tableau_de_bord.json"

_PORTES_GRU = [("#0F6FB8", "Porte reset"), ("#2F7D5B", "Porte update"), ("#C9A227", "Porte candidat")]


def catalogue() -> dict:
    """{'defaut': fichier, 'modeles': [{'fichier', 'libelle', ...}]} ; seuls les fichiers présents."""
    try:
        cat = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cat = {"defaut": "mkan_final.pt",
               "modeles": [{"fichier": "mkan_final.pt", "libelle": "Modèle historique"}]}
    cat["modeles"] = [m for m in cat["modeles"] if (DOSSIER / m["fichier"]).exists()]
    return cat


class ModeleCourant:
    """Délègue tout au modèle chargé ; `actuel` est remplacé par changer()."""

    def __init__(self, inference):
        object.__setattr__(self, "actuel", inference)

    def __getattr__(self, nom):
        return getattr(self.actuel, nom)


# Textes et cartes d'origine (écrits pour le modèle historique), restaurés quand on y revient
_ORIGINE = {}


def _sauver_origine():
    if _ORIGINE:
        return
    from dashboard import layout, callbacks_extra
    _ORIGINE.update(STAGES=list(layout.STAGES), STAGE_TEXT=copy.deepcopy(callbacks_extra.STAGE_TEXT),
                    BASES_MODELE=layout.BASES_MODELE, PORTES_MODELE=list(layout.PORTES_MODELE))


def configurer(inference, buffer) -> None:
    """Applique au tableau de bord ce qui dépend du modèle chargé (au démarrage et à chaque changement)."""
    from dashboard import layout, callbacks, callbacks_extra, live_ingest
    _sauver_origine()
    W = int(inference.W)
    if buffer is not None and getattr(buffer, "W", W) != W:
        buffer.W = W
    # Fenêtre et libellés lus par les vues (reconstruites à chaque navigation)
    layout.W = layout.W_MODELE = W
    layout.CHECKPOINT_NOM = inference.checkpoint_name
    layout.CELLULE_MODELE = {"gru": "GRUKANCell", "lstm": "TKANCell"}.get(inference.cell_type, "TKANCell")
    callbacks.W = W
    callbacks._WINDOW_CELLS_CACHE = [callbacks._window_cells(n) for n in range(W + 1)]
    callbacks_extra.W_MODELE = W
    callbacks_extra._spline_cache.clear()
    callbacks_extra.STAGE_TEXT.clear()
    callbacks_extra.STAGE_TEXT.update(copy.deepcopy(_ORIGINE["STAGE_TEXT"]))
    if inference.format == "campagne":
        d = inference.description()
        layout.BASES_MODELE = d["bases"]
        layout.PORTES_MODELE = _PORTES_GRU if inference.cell_type == "gru" else list(_ORIGINE["PORTES_MODELE"])
        # Liste partagée avec callbacks_extra : modifiée en place
        layout.STAGES[:] = [
            ("input", "Entrée", f"{d['n_variables']} × {d['W']}", "features × W"),
            ("norm", "Normalisation", "log signé + z", "relue dans le checkpoint"),
            ("cell", "Cellule MKAN", f"hidden {d['hidden']}", f"{d['n_portes']} portes KAN"),
            ("basis", "Base", "une par porte", d["bases"]),
            ("head", "Tête", "linéaire", f"{d['hidden']} → 1"),
            ("out", "Sortie", "σ(z) ∈ [0,1]", "seuil τ"),
        ]
        callbacks_extra._textes_campagne(inference)
        live_ingest.set_vector_builder(inference.vector_from_row)
    else:
        layout.BASES_MODELE = _ORIGINE["BASES_MODELE"]
        layout.PORTES_MODELE = list(_ORIGINE["PORTES_MODELE"])
        layout.STAGES[:] = _ORIGINE["STAGES"]
        live_ingest.set_vector_builder(None)


def changer(courant: ModeleCourant, buffer, fichier: str) -> str:
    """Charge `fichier` (catalogue) et le substitue au modèle courant. Renvoie un message d'état."""
    from dashboard.inference import MKANInference
    from dashboard import callbacks
    if fichier == courant.actuel.checkpoint_name:
        return f"Modèle déjà chargé : {fichier}."
    nouveau = MKANInference(DOSSIER / fichier)          # chargé avant de toucher à l'état courant
    ancien = courant.actuel
    with ancien._verrou:                                  # aucun scoring pendant la bascule
        object.__setattr__(courant, "actuel", nouveau)
        if buffer is not None:
            buffer.flush_all()                            # fenêtres de l'ancien modèle : autre W, autres variables
        configurer(nouveau, buffer)
        callbacks._last_result = None
        try:
            callbacks._drift.reset()
        except Exception:
            pass
    return (f"Modèle chargé : {nouveau.description()['resume']}. Les fenêtres repartent de zéro : "
            f"un compte est scoré après {nouveau.W} transactions.")
