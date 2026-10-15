# MKAN · Dashboard de détection de fraude Mobile Money en temps réel

Ce tableau de bord Dash score en direct un flux de transactions Mobile Money avec **MKAN**. MKAN est un réseau récurrent dont les quatre portes LSTM sont remplacées par des couches de Kolmogorov-Arnold hybrides (bases gaussiennes et Fourier). Chaque décision est **expliquée**, journalisée et exportable sous la forme d'un rapport d'audit au format COBAC.

> Projet réalisé dans le cadre d'un mémoire de Master 2 (ENSPD, Université de Douala, LIDIA Lab), en stage à la BEAC.

---

## Fonctionnement

```
MoMTSim (simulateur)  ──REST / Kafka──►  dashboard
                                          │
                                          ├─ calcul des 12 variables à partir de l'historique de chaque compte
                                          ├─ fenêtre glissante de W = 10 transactions par compte émetteur (Redis ou mémoire)
                                          ├─ scoring de tout le lot en un seul forward pass
                                          └─ explication complète du compte inspecté (contributions des variables)
```

- **Flux brut** : le dashboard reçoit des transactions brutes (`step`, `action`, `amount`, `nameOrig`, `nameDest`, soldes avant et après). Il calcule lui-même les 12 variables du modèle. Aucune variable n'est précalculée par la source.
- **Scoring multi-comptes** : chaque transaction est routée vers la fenêtre de son émetteur. Un compte est scoré dès que sa fenêtre contient 10 transactions.
- **Vérité terrain** : `isFraud`, fourni par le simulateur, ne sert qu'aux métriques. Il n'intervient jamais dans le scoring.

### Les 12 variables

`delta_B_orig` · `delta_B_dest` · `r1` · `r2` · `flag_anomalie` · `delta_commission` · `var_agent_split` · `rho_rupture` · `rho_refund` · `v1h` · `flag_nuit` · `rho_nouveau`

Les cinq variables à queue lourde passent par `log1p(|x|)`. Toutes sont ensuite centrées-réduites avec le μ et le σ du jeu d'entraînement, lus dans le checkpoint.

### Le modèle

| Élément | Valeur |
|---|---|
| Entrée | 10 pas × 12 variables |
| Cellule | TKANCell : 4 portes LSTM, chacune une `HybridKANLayer` |
| Base par arête | M = 8 gaussiennes RBF + K = 2 harmoniques de Fourier |
| État caché | 16 |
| Sortie | `σ(wᵀh_W + b)`, score de risque dans [0, 1], comparé au seuil τ |

---

## Vues

| Vue | Contenu |
|---|---|
| **Scoring live** | Jauge de risque du compte inspecté, contributions des variables, fenêtre glissante, compteurs cumulés, injection de scénarios de fraude |
| **Fenêtres** | Remplissage des fenêtres des comptes actifs et carte de chaleur de la fenêtre du compte inspecté |
| **Métriques** | AUC ROC, KS, Brier, ECE, PSI, divergence de Jensen-Shannon, réglage du seuil τ |
| **Modèle** | Architecture, splines apprises par porte (`|φ(x)|` moyen), paramètres de normalisation |
| **Audit** | Journal des décisions (heure, compte, score, verdict, variable dominante), export CSV, rapport COBAC |
| **Environnement** | Choix de la source (démo MoMTSim, ou flux live en REST ou Kafka), état de la connexion, arrêt d'urgence |
| **Aide** | Documentation intégrée |

### Injection de scénarios

Six boutons ajoutent une transaction synthétique à la fenêtre du compte inspecté, puis le re-scorent. Ils permettent d'observer la réaction du modèle à chaque typologie de fraude :

**ATO** (prise de contrôle de compte) · **Remboursement** circulaire · **Faux crédits** · **Split** (fractionnement chez un agent) · **Smurfing** (structuration multi-mules) · **Anomalie** système.

### Suivi de dérive

Une référence est figée sur les 1 000 premiers scores. Elle est comparée en continu aux 500 derniers par divergence de Jensen-Shannon (10 classes). Le seuil d'alerte vaut 3 fois le bruit d'échantillonnage mesuré.

---

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS : source .venv/bin/activate
pip install -r requirements_dashboard.txt
pip install xgboost scipy       # importés par le paquet MKAN
```

Prérequis facultatifs :
- **Redis** pour les fenêtres glissantes. Sans Redis, utiliser `--no-redis` : les fenêtres sont alors gardées en mémoire.
- **Kafka** comme transport. Sans Kafka, le transport se fait en REST.

Le checkpoint entraîné `MKAN/checkpoints/mkan_final.pt` est nécessaire au scoring.

## Lancement

1. Démarrer le producteur de transactions :
   ```bash
   cd MOMTSIM
   python run_live.py               # ajouter --no-kafka pour le REST seul
   ```
2. Démarrer le dashboard depuis la racine du projet :
   ```bash
   python -m dashboard.app          # http://localhost:8050
   ```

Le scoring démarre automatiquement. Options :

| Option | Effet |
|---|---|
| `--port 8051` | Change le port |
| `--no-redis` | Garde les fenêtres glissantes en mémoire |
| `--debug` | Active le rechargement à chaud |

### Brancher un autre flux

Dans **Environnement → Live**, indiquer :
- **REST** : une URL qui répond à `GET <url>?since_id=N` avec `{"transactions": [...], "last_id": N}` ;
- **Kafka** : les serveurs bootstrap et le topic.

Champs requis par transaction : `step`, `action`, `amount`, `nameOrig`, `nameDest`, `oldBalanceOrig`, `newBalanceOrig`, `oldBalanceDest`, `newBalanceDest`. Le champ `isFraud` est facultatif.

---

## Structure

```
dashboard/
├── app.py              point d'entrée Dash
├── layout.py           mise en page et vues
├── callbacks.py        boucle de scoring (lot → forward pass → explication)
├── callbacks_extra.py  vues secondaires (fenêtres, métriques, audit, environnement)
├── figures.py          figures Plotly (jauge, contributions, …)
├── inference.py        chargement du checkpoint, normalisation, scoring
├── stream_features.py  calcul des 12 variables à partir du flux brut
├── live_ingest.py      transport REST / Kafka
├── buffer.py           fenêtres glissantes Redis (repli : _fallback_buffer.py)
├── metrics.py          AUC, KS, Brier, ECE, PSI, JS
└── users.py            comptes analystes (stockage local)
```

## Limites

- Le modèle actuel a été entraîné sur des données simulées (MoMTSim). Ses performances sur un flux opérateur réel restent à valider.
- `delta_commission` était constante dans le jeu d'entraînement du checkpoint actuel. Elle est donc neutralisée au scoring jusqu'au prochain réentraînement.
- L'authentification des analystes est une démonstration en stockage local. Elle n'est pas faite pour la production.
