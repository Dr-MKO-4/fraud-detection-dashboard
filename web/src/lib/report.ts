import type { LiveEngine, Scored } from '../live/engine';
import { simTimeLabel } from '../live/engine';
import { FEATURE_ORDER, FEATURE_LABELS } from '../model/features';
import { D, M, K, formatFormula } from '../model/kan';
import { SCENARIO_INFO } from '../sim/config';
import { justification } from './explainText';
import { fcfa, featValue, int, pct, signed } from './format';

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

async function sha256(text: string): Promise<string> {
  try {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193) >>> 0;
    return h.toString(16).padStart(8, '0');
  }
}

function phiSvg(e: LiveEngine, i: number, z: number): string {
  const edge = e.scorer.edges[i];
  const W = 240;
  const H = 110;
  const N = 81;
  const xs = Array.from({ length: N }, (_, k) => -D + (2 * D * k) / (N - 1));
  const ys = xs.map((x) => edge.eval(x));
  const lo = Math.min(-0.3, ...ys) - 0.1;
  const hi = Math.max(0.3, ...ys) + 0.1;
  const X = (x: number) => 6 + ((x + D) / (2 * D)) * (W - 12);
  const Y = (y: number) => 6 + (1 - (y - lo) / (hi - lo)) * (H - 12);
  const d = xs.map((x, k) => `${k ? 'L' : 'M'}${X(x).toFixed(1)},${Y(ys[k]).toFixed(1)}`).join(' ');
  const cy = edge.eval(z);
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="φ de ${FEATURE_ORDER[i]}"><rect width="${W}" height="${H}" fill="#f7f6f3" rx="6"/><line x1="6" x2="${W - 6}" y1="${Y(0)}" y2="${Y(0)}" stroke="#c9c6bf"/><path d="${d}" fill="none" stroke="#2a2e32" stroke-width="2"/><line x1="${X(z)}" x2="${X(z)}" y1="6" y2="${H - 6}" stroke="#cd4918" stroke-dasharray="3 3"/><circle cx="${X(z)}" cy="${Y(cy)}" r="4.5" fill="#cd4918"/></svg>`;
}

export async function buildCobacReport(e: LiveEngine, s: Scored | undefined): Promise<string> {
  const now = new Date();
  const ref = `MKAN-DEMO/COBAC/${now.getFullYear()}-${String(e.nScored).padStart(6, '0')}`;
  const tau = e.tau;
  let decision = '<p>Aucune alerte sélectionnée dans la session.</p>';
  if (s) {
    const max = Math.max(1, ...s.exp.contrib.map(Math.abs));
    const rows = FEATURE_ORDER.map((f, i) => {
      const c = s.exp.contrib[i];
      const w = (Math.abs(c) / max) * 50;
      const sym = e.scorer.symbolic(i);
      const formula = sym.symbolifiable ? `${esc(formatFormula(sym))} <span class="mut">(R² ${sym.r2.toFixed(3)})</span>` : `non-symbolifiable (numérique) <span class="mut">(R² ${sym.r2.toFixed(3)})</span>`;
      return `<tr><td class="mono">${f}</td><td>${esc(FEATURE_LABELS[f])}</td><td class="r mono">${esc(featValue(s.exp.raw[i]))}</td><td class="r mono">${s.exp.z[i].toFixed(2)}</td><td><svg viewBox="0 0 100 10" width="140" height="12" preserveAspectRatio="none"><rect x="49.8" width="0.4" height="10" fill="#8b8f94"/><rect x="${c >= 0 ? 50 : 50 - w}" y="1.5" width="${Math.max(0.6, w)}" height="7" fill="${c >= 0 ? '#cd4918' : '#3987e5'}"/></svg></td><td class="r mono">${signed(c)}</td><td class="mono small">${formula}</td></tr>`;
    }).join('');
    const top = s.exp.contrib
      .map((c, i) => ({ c, i }))
      .sort((a, b) => b.c - a.c)
      .slice(0, 3);
    decision = `
      <table class="kv">
        <tr><th>Transaction</th><td class="mono">#${s.tx.id} · ${esc(s.tx.action)} · ${esc(s.tx.nameOrig)} → ${esc(s.tx.nameDest)}</td></tr>
        <tr><th>Heure simulée</th><td class="mono">${simTimeLabel(s.tx.step, s.tx.timeS)}</td></tr>
        <tr><th>Montant</th><td class="mono">${fcfa(s.tx.amount)}</td></tr>
        <tr><th>Score / seuil</th><td class="mono">${pct(s.exp.score, 1)} / τ = ${pct(tau)} · ${s.exp.score >= tau ? 'ALERTE' : 'NORMALE'}</td></tr>
        <tr><th>Signature la plus proche</th><td>${s.detected ? `${esc(SCENARIO_INFO[s.detected].label)} (${s.detected})` : 'aucune (atypique)'}</td></tr>
        <tr><th>Étiquette du simulateur</th><td>${s.tx.isFraud ? esc(s.tx.scenario ?? 'fraude') : 'légitime'}${s.tx.injected ? ' (injectée)' : ''}</td></tr>
      </table>
      <p class="just">${esc(justification(s, tau))}</p>
      <h3>Décomposition additive du logit : logit = b + Σ φ<sub>i</sub>(x̃<sub>i</sub>) = ${signed(s.exp.bias)} + … = ${signed(s.exp.logit)}</h3>
      <table class="grid"><thead><tr><th>Variable</th><th>Libellé</th><th class="r">Valeur</th><th class="r">x̃</th><th>Contribution</th><th class="r">φ</th><th>Forme symbolique approchée</th></tr></thead><tbody>${rows}</tbody></table>
      <h3>Fonctions φ des trois variables dominantes (point courant en orange)</h3>
      <div class="phis">${top.map((t) => `<figure>${phiSvg(e, t.i, s.exp.z[t.i])}<figcaption class="mono">${FEATURE_ORDER[t.i]} · φ = ${signed(t.c)}</figcaption></figure>`).join('')}</div>`;
  }
  const byScen = Object.entries(e.alertByScenario)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="r mono">${int(v)}</td></tr>`)
    .join('');
  const lastJs = e.jsSeries.length ? e.jsSeries[e.jsSeries.length - 1].js : null;
  const body = `
  <header>
    <div>
      <div class="kicker">Gabarit inspiré des exigences d'explicabilité COBAC · démonstration</div>
      <h1>Rapport d'auditabilité du scoreur de fraude</h1>
      <div class="mut">MKAN · MoMTSim, Mobile Money zone CEMAC · démo pédagogique exécutée dans le navigateur</div>
    </div>
    <div class="meta mono">Réf. ${ref}<br/>Date ${now.toLocaleDateString('fr-FR')} ${now.toLocaleTimeString('fr-FR')}<br/>Mode SIMULÉ<br/>Seuil τ ${pct(tau)}<br/>Graine ${e.seed}</div>
  </header>
  <div class="warn">Démo pédagogique : flux et scoreur simulés dans le navigateur, ce n'est pas le modèle entraîné. Les fonctions φ sont illustratives (conçues à la main) ; ce rapport ne contient aucun indicateur de performance.</div>
  <h2>1 · Objet et périmètre</h2>
  <p>Le présent document illustre le rapport d'explicabilité prévu par le mémoire (section 4.4.7) pour une décision automatisée de détection de fraude. Le scoreur de démonstration a la structure additive d'un réseau de Kolmogorov-Arnold : 12 variables métier, une fonction d'arête hybride par variable (${M} gaussiennes fixes sur [−${D}, ${D}] et Fourier d'ordre ${K}, eq. 4.13), biais, sigmoïde. Le modèle du mémoire est une cellule récurrente T-KAN déroulée sur une fenêtre de W = 10 transactions ; il n'est pas embarqué ici.</p>
  <h2>2 · Explication de la décision documentée</h2>
  ${decision}
  <h2>3 · Synthèse de la session simulée</h2>
  <table class="kv">
    <tr><th>Décisions</th><td class="mono">${int(e.nScored)}</td></tr>
    <tr><th>Alertes levées</th><td class="mono">${int(e.nAlerts)}</td></tr>
    <tr><th>Montant bloqué</th><td class="mono">${fcfa(e.blocked)}</td></tr>
    <tr><th>Taux de fraude cible du simulateur</th><td class="mono">${pct(e.sim.fraudRate)}</td></tr>
    <tr><th>Dérive JS des scores (bits)</th><td class="mono">${lastJs === null ? 'référence en calibrage' : `${lastJs.toFixed(3)} (seuil ${e.jsThreshold.toFixed(3)})`}</td></tr>
  </table>
  ${byScen ? `<h3>Alertes par signature</h3><table class="grid" style="max-width:320px"><tbody>${byScen}</tbody></table>` : ''}
  <h2>4 · Réserves et limites d'opposabilité</h2>
  <p>4.1 Rapport établi en mode SIMULÉ sur données synthétiques (MoMTSim réimplémenté en TypeScript) : sans valeur probante, à usage de démonstration uniquement.</p>
  <p>4.2 Les fonctions φ et les formes symboliques sont celles du scoreur illustratif ; elles ne décrivent pas les poids du checkpoint MKAN. Une arête de R² inférieur à 0,99 est déclarée non-symbolifiable, comme dans le pipeline d'audit (audit.py).</p>
  <p>4.3 Une dérive JS au-delà du seuil suspendrait la validité du rapport jusqu'à l'extension de grille ou au réentraînement du modèle.</p>`;
  const digest = await sha256(body);
  return `<!doctype html><html lang="fr"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/><title>Rapport COBAC · MKAN démo · ${ref}</title><style>
  body{font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;color:#1d2024;background:#fff;margin:0;padding:32px;font-size:13px;line-height:1.55}
  main{max-width:920px;margin:0 auto}
  header{display:flex;justify-content:space-between;gap:24px;border-bottom:3px solid #cd4918;padding-bottom:14px}
  h1{font-size:22px;margin:4px 0}h2{font-size:15px;margin:26px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px}h3{font-size:13px;margin:16px 0 6px}
  .kicker{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:#cd4918;font-weight:700}
  .mut{color:#6b7076}.meta{text-align:right;font-size:11px;color:#4a4f55}
  .mono{font-family:'JetBrains Mono',Consolas,monospace}.small{font-size:10.5px}.r{text-align:right}
  .warn{margin:16px 0;padding:10px 12px;background:#fdf1ec;border-left:4px solid #cd4918;font-size:12.5px}
  table{border-collapse:collapse;width:100%}.kv th{text-align:left;width:220px;color:#4a4f55;font-weight:500;padding:3px 0}.kv td{padding:3px 0}
  .grid th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#6b7076;border-bottom:1px solid #ccc;padding:4px 6px}.grid td{border-bottom:1px solid #eee;padding:4px 6px;vertical-align:middle}
  .just{background:#f5f4f1;padding:10px 12px;border-radius:6px}
  .phis{display:flex;gap:14px;flex-wrap:wrap}figure{margin:0}figcaption{font-size:11px;color:#4a4f55;margin-top:4px}
  footer{margin-top:28px;border-top:1px solid #ddd;padding-top:10px;font-size:11px;color:#4a4f55}
  @media print{body{padding:0}}
  </style></head><body><main>${body}<footer><div>Empreinte du rapport (SHA-256) : <span class="mono">${digest}</span></div><div>Généré par la démo web MKAN · MoMTSim (Georges Miguel MOUKOKO, LIDIA Lab). Analyste : session de démonstration.</div></footer></main></body></html>`;
}
