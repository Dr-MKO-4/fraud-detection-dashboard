import { engine } from '../ui/engineStore';
import { FEATURE_ORDER, FEATURE_HELP } from '../model/features';
import { D, M, K, LOG_FEATURES } from '../model/kan';
import { SCENARIOS, SCENARIO_INFO, FRAUD_CONFIG } from '../sim/config';
import { SCENARIO_COLOR } from '../lib/colors';

const STAGES = [
  ['01', 'MoMTSim', 'Simulateur multi-agent : clients, marchands-agents, banques et mules échangent CASH_IN, CASH_OUT, PAYMENT, TRANSFER, DEBIT et REFUND en FCFA, heure par heure. Cinq agents fraudeurs injectent leurs signatures.'],
  ['02', '12 variables', 'Calcul incrémental par compte (port de stream_features.py) : soldes, ratios r1 et r2, vélocité v1h, rupture, remboursements, nouveaux destinataires, fractionnement, marge de mule, nuit.'],
  ['03', 'Scoreur KAN', 'Chaque variable normalisée passe par sa propre fonction φ (gaussiennes + Fourier). Le logit est la somme des φ et du biais, puis une sigmoïde donne le score.'],
  ['04', 'Explication', 'La somme étant additive, chaque φ_i(x_i) est la contribution exacte de la variable. On affiche la courbe φ, le point courant et une forme symbolique approchée.'],
  ['05', 'Audit', 'Chaque décision est horodatée et chaînée dans le journal ; la dérive JS des scores est suivie ; un rapport au gabarit COBAC documente une alerte.'],
] as const;

/** Schéma de la structure additive : 12 entrées → φ_i → Σ → σ → score. */
function KanDiagram() {
  const W = 760;
  const rowH = 30;
  const top = 20;
  const H = top * 2 + rowH * 12;
  const midY = top + rowH * 6 - rowH / 2;
  const glyph = (i: number, x: number, y: number) => {
    const e = engine.scorer.edges[i];
    const N = 40;
    const xs = Array.from({ length: N }, (_, k) => -D + (2 * D * k) / (N - 1));
    const ys = xs.map((v) => e.eval(v));
    const lo = Math.min(...ys, -0.2);
    const hi = Math.max(...ys, 0.2);
    const d = xs.map((v, k) => `${k ? 'L' : 'M'}${(x + ((v + D) / (2 * D)) * 70).toFixed(1)},${(y + 19 - ((ys[k] - lo) / (hi - lo)) * 17).toFixed(1)}`).join(' ');
    return <path d={d} fill="none" stroke="#e2602e" strokeWidth="1.6" />;
  };
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Structure du scoreur : douze variables, chacune transformée par sa fonction φ, sommées avec un biais, puis passées dans une sigmoïde pour donner le score.">
      {FEATURE_ORDER.map((f, i) => {
        const y = top + i * rowH;
        return (
          <g key={f}>
            <rect x="10" y={y} width="150" height="22" rx="5" fill="#202429" stroke="#3a4047" />
            <text x="20" y={y + 15} fill="#c6c3bd" fontSize="11" fontFamily="JetBrains Mono Variable, monospace">
              {f}
            </text>
            <line x1="160" y1={y + 11} x2="250" y2={y + 11} stroke="#3a4047" />
            <text x="205" y={y + 8} fill="#737b84" fontSize="9" textAnchor="middle" fontFamily="JetBrains Mono Variable, monospace">
              {LOG_FEATURES.has(f) ? 'log1p · z' : 'z-score'}
            </text>
            <rect x="250" y={y} width="90" height="22" rx="5" fill="#16181b" stroke="#3a4047" />
            {glyph(i, 260, y + 1)}
            <line x1="340" y1={y + 11} x2="520" y2={midY + 11} stroke="#3a4047" />
          </g>
        );
      })}
      <text x="295" y={top - 6} fill="#9aa1a8" fontSize="10.5" textAnchor="middle">
        φ_i : {M} gaussiennes + Fourier K = {K}
      </text>
      <circle cx="540" cy={midY + 11} r="22" fill="#2a2e32" stroke="#cd4918" strokeWidth="2" />
      <text x="540" y={midY + 17} fill="#eceae6" fontSize="18" textAnchor="middle">
        Σ
      </text>
      <text x="540" y={midY - 20} fill="#9aa1a8" fontSize="10.5" textAnchor="middle">
        + biais b
      </text>
      <line x1="562" y1={midY + 11} x2="610" y2={midY + 11} stroke="#cd4918" strokeWidth="2" />
      <rect x="610" y={midY - 6} width="56" height="34" rx="6" fill="#2a2e32" stroke="#3a4047" />
      <text x="638" y={midY + 16} fill="#eceae6" fontSize="14" textAnchor="middle">
        σ
      </text>
      <line x1="666" y1={midY + 11} x2="696" y2={midY + 11} stroke="#cd4918" strokeWidth="2" />
      <text x="700" y={midY + 8} fill="#eceae6" fontSize="12" fontWeight="600">
        score
      </text>
      <text x="700" y={midY + 24} fill="#9aa1a8" fontSize="10.5">
        ∈ (0, 1)
      </text>
    </svg>
  );
}

export function HowItWorks() {
  const c = FRAUD_CONFIG;
  return (
    <article className="doc">
      <p className="kicker">Comment ça marche</p>
      <h1>Du flux Mobile Money à une alerte expliquée</h1>
      <p className="lead">
        Cette démo rejoue, dans le navigateur, la chaîne du mémoire MKAN : simulation du flux par MoMTSim, calcul des variables métier, scoring par un réseau à la Kolmogorov-Arnold, explication de chaque décision et traçabilité pour le régulateur.
      </p>
      <div className="note" style={{ marginTop: 16 }}>
        Démo pédagogique : flux et scoreur simulés dans le navigateur, ce n'est pas le modèle entraîné. Les fonctions φ sont conçues à la main pour avoir des formes plausibles ; aucun résultat ni indicateur de performance du mémoire n'est affiché.
      </div>

      <div className="flow" role="list" aria-label="Étapes de la chaîne">
        {STAGES.map(([no, t, p]) => (
          <div className="stage" key={no} role="listitem">
            <span className="no">{no}</span>
            <h3>{t}</h3>
            <p>{p}</p>
          </div>
        ))}
      </div>

      <h2>1 · MoMTSim : un flux multi-agent</h2>
      <p>
        Le simulateur reprend la logique de MOMTSIM (momtsim.py et l'injecteur de fraudes) avec une population réduite : chaque client choisit d'entrer ou de sortir de l'argent selon un modèle « ressort » autour d'un solde d'équilibre, revient vers un contact connu 9 fois sur 10, et suit le profil horaire de volume des fichiers de paramètres (pic en soirée, creux la nuit). Une heure simulée est un <i>step</i>. Le taux de fraude cible pilote le nombre d'injections, qui suit le volume. Une graine fixe rend le flux reproductible.
      </p>
      <div className="cards">
        {SCENARIOS.map((s) => (
          <div className="card" key={s}>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <i className="sdot" style={{ background: SCENARIO_COLOR[s] }} /> {s}
            </h3>
            <p style={{ color: 'var(--ink-2)', marginBottom: 6 }}>{SCENARIO_INFO[s].label}</p>
            <p>{SCENARIO_INFO[s].signature}</p>
          </div>
        ))}
      </div>
      <p className="help">
        Paramètres de fraudScenariosConfig.json : B_min = {c.ato.B_min} FCFA, λ_ATO = {c.ato.lambda_ato} min, k_max = {c.refund.k_max}, dormance {c.fake_credentials.dormance_min_days} à {c.fake_credentials.dormance_max_days} jours, ε_max = {c.split_deposit.epsilon_max} FCFA, S_seuil = {c.smurfing.S_seuil.toLocaleString('fr-FR')} FCFA, δ = {c.smurfing.delta_min} à {c.smurfing.delta_max}.
      </p>

      <h2>2 · Les 12 variables, calculées en flux</h2>
      <p>
        Chaque step est traité en entier, comme dans le dashboard : la vélocité v1h et la variance de fractionnement incluent les autres opérations de la même heure. Les fenêtres de 30 jours et de 90 jours et les 10 dernières transactions par compte, gardées dans Redis par le dashboard, vivent ici dans la mémoire du navigateur.
      </p>
      <table className="tbl" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Définition</th>
          </tr>
        </thead>
        <tbody>
          {FEATURE_ORDER.map((f) => (
            <tr key={f}>
              <td>{f}</td>
              <td style={{ whiteSpace: 'normal', fontFamily: 'var(--sans)' }}>{FEATURE_HELP[f]}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>3 · Un scoreur additif à la KAN</h2>
      <p>
        Un réseau de Kolmogorov-Arnold place les fonctions apprenables sur les arêtes plutôt que sur les nœuds. Le scoreur de la démo en garde la forme la plus lisible : une arête par variable, dont la fonction est une combinaison de {M} gaussiennes à centres fixes sur [−{D}, {D}] et d'une série de Fourier d'ordre {K}, comme la fonction d'arête hybride du mémoire (eq. 4.13).
      </p>
      <div className="formula" style={{ whiteSpace: 'normal' }}>
        φ(x) = Σ<sub>m</sub> w<sub>m</sub> · exp(−(x − μ<sub>m</sub>)² / 2h²) + Σ<sub>k</sub> [a<sub>k</sub> cos(kx) + b<sub>k</sub> sin(kx)]  ·  logit = b + Σ<sub>i</sub> φ<sub>i</sub>(x̃<sub>i</sub>)  ·  score = σ(logit)
      </div>
      <p className="help">
        où x̃_i est la variable i après log1p(|x|) pour les queues lourdes puis z-score, μ_m les centres, h = 2D/(M − 1) la largeur, w_m, a_k, b_k les poids de l'arête, b le biais et σ la sigmoïde.
      </p>
      <div className="panel" style={{ marginTop: 14 }}>
        <KanDiagram />
      </div>
      <p>
        Le modèle du mémoire va plus loin : ces couches KAN remplacent les quatre portes d'une cellule LSTM (T-KAN), déroulée sur une fenêtre de 10 transactions par compte, avec 16 unités cachées. Les poids de la démo ne sont pas appris : ils sont ajustés par moindres carrés sur des formes cibles écrites à la main.
      </p>

      <h2>4 · Expliquer une alerte</h2>
      <p>
        Parce que le logit est une somme, la contribution de chaque variable est exactement φ_i(x̃_i) : les barres signées de l'explication somment au logit. La courbe φ_i montre ce que l'arête a « appris » sur tout le domaine, et où se situe la transaction. La régression symbolique cherche ensuite une formule c · f(a·x + b) + d dans la bibliothèque du mémoire (sigmoïde, tanh, sinus, puissances…) ; une arête dont le R² reste sous 0,99 est déclarée non-symbolifiable plutôt que forcée dans une formule inexacte.
      </p>

      <h2>5 · Surveiller et tracer</h2>
      <p>
        La divergence de Jensen-Shannon compare la distribution des scores récents à une référence figée : au-delà de trois fois le bruit d'échantillonnage, le mémoire prévoit une extension de grille des fonctions d'arête. Le diagramme de fiabilité confronte les scores aux étiquettes du simulateur. Chaque décision entre dans un journal horodaté et chaîné, exportable en CSV, et une alerte peut être documentée dans un rapport au gabarit COBAC, exportable en HTML ou imprimable en PDF.
      </p>
    </article>
  );
}
