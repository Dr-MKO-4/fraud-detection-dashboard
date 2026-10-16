const REPOS = [
  ['Dr-MKO-4/MKAN', 'https://github.com/Dr-MKO-4/MKAN', 'Le modèle : cellule récurrente T-KAN, fonctions d’arête hybrides gaussiennes + Fourier, régularisation L1 et entropie, extension de grille, élagage et extraction symbolique pour l’audit.'],
  ['Dr-MKO-4/MOMTSIM-KAN', 'https://github.com/Dr-MKO-4/MOMTSIM-KAN', 'Le simulateur : réimplémentation de MoMTSim adaptée aux cinq scénarios de fraude CEMAC, calibration et producteur de flux live (Kafka ou REST).'],
  ['Dr-MKO-4/fraud-detection-dashboard', 'https://github.com/Dr-MKO-4/fraud-detection-dashboard', 'Le dashboard Dash de scoring temps réel : fenêtres glissantes Redis, explications, dérive, journal et rapport COBAC. Cette démo web vit dans sa branche demo-web.'],
] as const;

export function About() {
  return (
    <article className="doc">
      <p className="kicker">À propos</p>
      <h1>MKAN · MoMTSim</h1>
      <p className="lead">
        Détection de fraude Mobile Money en zone CEMAC par réseau de Kolmogorov-Arnold récurrent, simulateur multi-agent MoMTSim et dashboard de scoring temps réel expliqué.
      </p>
      <p>
        Projet de mémoire de Master 2 de <b>Georges Miguel MOUKOKO EKAMBI</b>, ENSPD, Université de Douala, LIDIA Lab.
      </p>

      <h2>Le mémoire</h2>
      <p>
        Le mémoire décrit la méthodologie complète : formalisation des cinq scénarios de fraude, simulation, architecture MKAN, entraînement et audit. Ses résultats sont en cours de recalcul ; c’est pourquoi cette démo n’affiche aucun chiffre de performance. Le code et la documentation sont dans le dépôt{' '}
        <a href="https://github.com/Dr-MKO-4/MKAN" rel="noopener">
          Dr-MKO-4/MKAN
        </a>
        .
      </p>

      <h2>Les dépôts</h2>
      <div className="cards">
        {REPOS.map(([name, url, desc]) => (
          <a className="card" href={url} key={name} rel="noopener">
            <span className="mono">github.com/{name}</span>
            <h3 style={{ marginTop: 6 }}>{name.split('/')[1]}</h3>
            <p>{desc}</p>
          </a>
        ))}
      </div>

      <h2>Ce que fait et ne fait pas cette démo</h2>
      <ul>
        <li>Elle tourne entièrement dans le navigateur, sans serveur : aucune donnée n’est envoyée.</li>
        <li>Le simulateur, les 12 variables, la normalisation, la structure des fonctions d’arête, la divergence de Jensen-Shannon, le journal et le gabarit du rapport reprennent le code du projet.</li>
        <li>Le scoreur est illustratif : une somme de fonctions φ conçues à la main, pas le checkpoint MKAN entraîné ni sa cellule récurrente.</li>
        <li>Les étiquettes du simulateur servent seulement à colorer le flux et au diagramme de fiabilité ; aucun indicateur de performance n’est calculé.</li>
      </ul>
      <p className="help">Pile : React, TypeScript, Vite ; graphiques en Canvas et SVG ; animation par requestAnimationFrame à pas fixe.</p>
    </article>
  );
}
