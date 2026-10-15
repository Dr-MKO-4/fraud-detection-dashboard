# Mise à jour MKAN v2.4.1  thème clair sans-serif + nouvelles vues

## Fichiers à copier

| Copier ce fichier | Vers | Action |
|---|---|---|
| `assets/style.css` | `dashboard/assets/style.css` | **remplace** l'ancien |
| `dashboard/layout.py` | `dashboard/layout.py` | **remplace** l'ancien |
| `dashboard/metrics.py` | `dashboard/metrics.py` | nouveau |
| `dashboard/callbacks_extra.py` | `dashboard/callbacks_extra.py` | nouveau |

`callbacks.py`, `buffer.py` et `inference.py` restent inchangés : tous les
identifiants d'origine (`inp-*`, `btn-submit`, `btn-reset`, `switch-auto`,
`auto-interval`, `graph-gauge`, `graph-contributions`, `tabs-bottom`,
`tab-content`, `btn-symbolic`, `verdict-badge`, `store-last-result`) sont
conservés.

## Deux ajustements dans `app.py`

```python
from dashboard import callbacks, callbacks_extra          # ← ajouter callbacks_extra

app.layout = build_layout()
callbacks.init(buffer, inference)
callbacks.register_callbacks(app)
callbacks_extra.register_extra(app, buffer, inference)     # ← ajouter cette ligne
```

## Un ajustement dans `callbacks.py`

`switch-auto` est devenu une `dcc.Checklist` (le `dbc.Switch` imposait le style
Bootstrap). Deux endroits à adapter :

```python
# callback « toggle_interval »
def toggle_interval(active):
    return "on" not in (active or [])

# callback « on_submit_or_interval » : le State reste State("switch-auto", "value")
if triggered == "auto-interval":
    if "on" not in (auto_on or []):
        raise PreventUpdate
```

Et la fenêtre glissante n'est plus un `dbc.Progress` mais dix segments : remplace
les deux Output `("progress-window", "value")` / `("progress-window", "label")`
par un seul `Output("progress-window", "children")` recevant `f"{level}/{W}"`,
et  si tu veux les segments colorés  un `Output("window-cells", "children")` :

```python
from dash import html
cells = [html.Span(className="on" if i < level else "") for i in range(10)]
```

## Ce que la mise à jour ajoute

1. **Chrome applicatif** : barre de navigation à six vues, bandeau de télémétrie
   (compte, fenêtre, tx, alertes, dernier score, dérive JS, seuil τ, latence).
2. **Vue Fenêtres** : buffers des dix comptes + heatmap 12 × 10 de la fenêtre.
3. **Vue Métriques** : matrice de confusion à τ réglable, ROC/AUC, KS,
   diagramme de fiabilité, ECE, Brier, log-loss, IC de Wilson, analyse par
   décile, PSI et alertes graduées (paliers 1/2/3).
4. **Vue Modèle** : pipeline cliquable en six étapes, chacune illustrée.
5. **Vue Audit** : journal des décisions + export CSV réel.
6. **Vue Environnement** : bascule *simulé / live* avec confirmation, source de
   données, cadence, plafond d'alertes, garde-fous, arrêt d'urgence.
7. **Vue Profil** : identité, habilitation, matrice de droits simulé/live,
   préférences, sessions actives, signature d'audit.
8. **Rapport COBAC** : gabarit corrigé  en-tête réglementaire, périmètre,
   tableau des expressions closes, indicateurs de validation, réserves
   d'opposabilité, empreinte SHA-256, impression PDF (`@media print`).

## Dépendances

`numpy`, `plotly`, `dash ≥ 2.9` (pour `dcc.Download`). `dash-bootstrap-components`
n'est plus requis par `layout.py`.
