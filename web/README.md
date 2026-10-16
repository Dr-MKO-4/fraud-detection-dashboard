# MKAN · MoMTSim Live, démo web

Démo autonome du projet de mémoire **MKAN · MoMTSim** (Georges Miguel MOUKOKO, M2 ENSPD, Université de Douala, LIDIA Lab) : détection de fraude Mobile Money en zone CEMAC par réseau de Kolmogorov-Arnold récurrent, simulateur multi-agent MoMTSim et dashboard de scoring temps réel expliqué.

> **Démo pédagogique : flux et scoreur simulés dans le navigateur, ce n'est pas le modèle entraîné.**
> Les fonctions φ du scoreur sont illustratives, conçues à la main pour avoir des formes plausibles. Aucun chiffre de performance du mémoire n'est affiché : ses résultats sont en cours de recalcul.

En ligne : https://mko-mkan.vercel.app

## Ce que montre la démo

- **Simulateur MoMTSim (TypeScript)** : clients, marchands-agents, banques et mules ; CASH_IN, CASH_OUT, DEBIT, PAYMENT, TRANSFER, REFUND en FCFA ; 1 step = 1 heure, profil horaire de volume ; modèle « ressort » et adhérence aux contreparties ; les 5 agents fraudeurs ATO, REFUND, FAKE_CRED, SPLIT_DEP, SMURFING avec les paramètres de `fraudScenariosConfig.json` ; taux de fraude réglable ; graine reproductible.
- **12 variables métier en flux** : port ligne à ligne de `dashboard/stream_features.py` (delta_B_orig, delta_B_dest, r1, r2, flag_anomalie, delta_commission, var_agent_split, rho_rupture, rho_refund, v1h, flag_nuit, rho_nouveau) ; fenêtre glissante de 10 transactions par compte en mémoire (Redis remplacé par le navigateur).
- **Scoreur à la KAN** : `logit = b + Σ φ_i(x̃_i)`, `score = σ(logit)` ; chaque φ_i a la forme de `MKAN/hybrid_edge.py` (8 gaussiennes à centres fixes + Fourier d'ordre 2) ; normalisation log1p puis z-score comme `dashboard/inference.py` ; seuil τ réglable.
- **Explicabilité** : barres signées φ_i(x_i) qui somment au logit, mini-graphe de la fonction φ avec le point courant (décomposition gaussienne / Fourier), forme symbolique approchée `c · f(a·x + b) + d` avec R², ou « non-symbolifiable » sous 0,99 comme `MKAN/audit.py`.
- **Dashboard live** : flux qui défile (Canvas), compteurs, file d'alertes avec signature de scénario et justification, dérive de Jensen-Shannon en bits, diagramme de fiabilité, journal d'audit chaîné exportable en CSV, rapport au gabarit COBAC exportable en HTML ou imprimable en PDF, injection de scénarios, lecture/pause, vitesse, taux de fraude.

## Relation avec le dashboard Dash

Le dashboard Python du dépôt fraud-detection-dashboard (`app.py`, `layout.py`, `callbacks*.py`) score le flux réel du producteur MoMTSim (Kafka ou REST) avec le checkpoint MKAN entraîné, des fenêtres Redis et PyTorch. Cette version web reprend sa mise en page (télémétrie, injection de scénarios, fenêtre du compte, contributions, dérive JS, journal, rapport COBAC) mais n'en dépend pas : pas de serveur, pas de modèle, tout est recalculé dans le navigateur pour pouvoir être montré en ligne.

## Lancer en local

```bash
npm install
npm run dev        # http://localhost:5173
npm test           # Vitest
npm run build      # dist/
```

## Architecture

```
src/sim/        rng.ts (graine), config.ts (fraudScenariosConfig), momtsim.ts (agents, scénarios)
src/model/      features.ts (12 variables incrémentales), kan.ts (arêtes hybrides, scoreur, symbolique)
src/live/       engine.ts (flux → variables → score → décision → journal, dérive), metrics.ts (JS, fiabilité)
src/ui/         engineStore.ts (boucle requestAnimationFrame à pas fixe), TxTape, ScoreTimeline (Canvas),
                Explain, Panels (SVG)
src/pages/      Live, Comment ça marche, À propos
tests/          reproductibilité, variables sur cas connus, scores des scénarios injectés, Σ φ = logit
```

La simulation avance par pas fixes de 1/60 s ; React ne relit le moteur qu'au plus 4 fois par seconde, les deux Canvas sont redessinés à chaque image sans re-render. `prefers-reduced-motion` coupe le défilement animé.

## Dépôts liés

- https://github.com/Dr-MKO-4/MKAN
- https://github.com/Dr-MKO-4/MOMTSIM-KAN
- https://github.com/Dr-MKO-4/fraud-detection-dashboard
