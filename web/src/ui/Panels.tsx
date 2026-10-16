import { useState } from 'react';
import { engine } from './engineStore';
import { SCENARIOS, SCENARIO_INFO, type Scenario } from '../sim/config';
import { SCENARIO_COLOR } from '../lib/colors';
import { compact, fcfa, int, pct, dec1 } from '../lib/format';
import { simTimeLabel } from '../live/engine';
import { histogram, reliability } from '../live/metrics';
import { downloadText } from '../lib/download';
import { buildCobacReport } from '../lib/report';

// --------------------------------------------------------------- télémétrie
export function Telemetry() {
  const e = engine;
  const rate = e.nScored ? e.nAlerts / e.nScored : 0;
  const js = e.jsSeries.length ? e.jsSeries[e.jsSeries.length - 1].js : null;
  const drift = js !== null && js > e.jsThreshold;
  const items: [string, string, string?][] = [
    ['Débit', `${dec1(e.txRate)} tx/s`],
    ['Transactions', int(e.nScored)],
    ['Alertes', int(e.nAlerts), 'alert'],
    ["Taux d'alerte", pct(rate, 1)],
    ['Montant bloqué', `${compact(e.blocked)} FCFA`],
    ['Heure simulée', simTimeLabel(Math.floor(e.clockS / 3600), e.clockS % 3600)],
    ['Dérive JS (bits)', js === null ? 'calibrage' : `${js.toFixed(3).replace('.', ',')}${drift ? ' ▲' : ''}`, drift ? 'alert' : undefined],
    ['Seuil τ', pct(e.tau)],
  ];
  return (
    <div className="tele" role="status" aria-live="off" aria-label="Compteurs du flux">
      {items.map(([k, v, cls]) => (
        <div key={k}>
          <span className="k">{k}</span>
          <span className={`v ${cls ?? ''}`}>{v}</span>
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------------- contrôles
const SPEEDS = [0.5, 1, 2, 4, 8];

export function Controls() {
  const e = engine;
  const [seed, setSeed] = useState(String(e.seed));
  const [toast, setToast] = useState<string | null>(null);
  const inject = (s: Scenario) => {
    e.inject(s);
    setToast(`${SCENARIO_INFO[s].label} injecté au step suivant ; la première alerte du scénario sera ouverte automatiquement.`);
  };
  return (
    <div className="stack">
      <section className="panel controls" aria-labelledby="ctl-h">
        <p className="kicker" id="ctl-h">
          Flux MoMTSim
        </p>
        <div className="row">
          <button className="btn primary" onClick={() => { e.playing = !e.playing; e.bump(true); }} aria-pressed={!e.playing} style={{ flex: 1 }}>
            {e.playing ? '❚❚ Pause' : '▶ Lecture'}
          </button>
          <button className="btn" onClick={() => { e.advance(3600); e.bump(true); }} title="Avancer d'une heure simulée" aria-label="Avancer d'une heure simulée">
            +1 h
          </button>
        </div>
        <div className="field">
          <span className="lbl" id="speed-l">
            Vitesse <output>×{e.speed}</output>
          </span>
          <div className="seg" role="group" aria-labelledby="speed-l">
            {SPEEDS.map((s) => (
              <button key={s} aria-pressed={e.speed === s} onClick={() => { e.speed = s; e.bump(true); }}>
                ×{s}
              </button>
            ))}
          </div>
          <span className="help" style={{ margin: 0 }}>×1 : une heure simulée toutes les 2 s.</span>
        </div>
        <div className="field">
          <label htmlFor="fr">
            Taux de fraude cible <output htmlFor="fr">{pct(e.sim.fraudRate)}</output>
          </label>
          <input id="fr" type="range" min={0} max={0.5} step={0.01} value={e.sim.fraudRate} onChange={(ev) => e.setFraudRate(Number(ev.target.value))} />
          <span className="help" style={{ margin: 0 }}>Part des transactions frauduleuses injectées (cible du mémoire : 20 à 26 %).</span>
        </div>
        <div className="field">
          <label htmlFor="tau">
            Seuil d’alerte τ <output htmlFor="tau">{pct(e.tau)}</output>
          </label>
          <input id="tau" type="range" min={0.1} max={0.95} step={0.01} value={e.tau} onChange={(ev) => e.setTau(Number(ev.target.value))} />
        </div>
        <div className="field">
          <label htmlFor="seed">Graine</label>
          <div className="row" style={{ flexWrap: 'nowrap' }}>
            <input id="seed" type="number" value={seed} onChange={(ev) => setSeed(ev.target.value)} />
            <button className="btn" onClick={() => { e.reset(Number(seed) || 0); setToast(`Simulation relancée avec la graine ${Number(seed) || 0}.`); }}>
              Relancer
            </button>
          </div>
          <span className="help" style={{ margin: 0 }}>Même graine, même flux : la simulation est reproductible.</span>
        </div>
      </section>
      <section className="panel" aria-labelledby="inj-h">
        <p className="kicker" id="inj-h">
          Injection de scénario
        </p>
        <div className="inject-grid">
          {SCENARIOS.map((s) => (
            <button key={s} className="btn" onClick={() => inject(s)} title={SCENARIO_INFO[s].signature} data-scenario={s}>
              <i style={{ background: SCENARIO_COLOR[s] }} />
              {s}
            </button>
          ))}
        </div>
        {toast && (
          <div className="toast" role="status">
            {toast}
          </div>
        )}
        <p className="help">Chaque bouton exécute l’agent fraudeur correspondant de MoMTSim sur un compte du flux (dormance et délais raccourcis pour FAKE_CRED, REFUND et SMURFING).</p>
      </section>
    </div>
  );
}

// --------------------------------------------------------------- file d'alertes
export function AlertQueue({ selected, onSelect }: { selected: number | null; onSelect: (id: number) => void }) {
  const list = engine.alerts.slice(0, 60);
  return (
    <section className="panel alerts-col" aria-labelledby="aq-h">
      <div className="panel-head">
        <p className="kicker" id="aq-h">
          File d’alertes
        </p>
        <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
          {int(engine.nAlerts)}
        </span>
      </div>
      {list.length === 0 ? (
        <p className="empty">Aucune alerte pour l’instant.</p>
      ) : (
        <ul className="alert-list">
          {list.map((s) => (
            <li key={s.tx.id}>
              <button className="alert-item" aria-current={selected === s.tx.id} onClick={() => onSelect(s.tx.id)} data-alert-id={s.tx.id}>
                <span className="t1">
                  {s.tx.nameOrig} · {s.tx.action} · {compact(s.tx.amount)}
                </span>
                <span className="sc">{Math.round(s.exp.score * 100)}%</span>
                <span className="t2">
                  <span className="mono">{simTimeLabel(s.tx.step, s.tx.timeS)}</span>
                  {s.detected ? (
                    <span className="badge">
                      <i className="sdot" style={{ background: SCENARIO_COLOR[s.detected] }} />
                      {s.detected}
                    </span>
                  ) : (
                    <span className="badge">atypique</span>
                  )}
                  {s.tx.injected && <span className="badge inj">injecté</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="help">Scénario affiché : signature la plus proche, déduite des contributions φ (heuristique de la démo).</p>
    </section>
  );
}

// --------------------------------------------------------------- dérive JS
export function DriftPanel() {
  const e = engine;
  const series = e.jsSeries;
  const Wd = 520;
  const Ht = 170;
  const pl = 40;
  const pb = 22;
  const pt = 10;
  const yMax = Math.max(0.05, e.jsThreshold * 1.6, ...series.map((p) => p.js)) * 1.1;
  const X = (k: number) => pl + (k / Math.max(1, series.length - 1)) * (Wd - pl - 8);
  const Y = (v: number) => pt + (1 - v / yMax) * (Ht - pt - pb);
  const path = series.map((p, k) => `${k ? 'L' : 'M'}${X(k).toFixed(1)},${Y(p.js).toFixed(1)}`).join(' ');
  const hr = histogram(e.refScores, 10);
  const hc = histogram(e.curScores, 10);
  const nr = hr.reduce((a, b) => a + b, 0) || 1;
  const nc = hc.reduce((a, b) => a + b, 0) || 1;
  const hm = Math.max(...hr.map((v) => v / nr), ...hc.map((v) => v / nc), 0.01);
  const last = series.length ? series[series.length - 1].js : null;
  return (
    <div className="two">
      <div>
        <p className="kicker">Divergence de Jensen-Shannon des scores (bits)</p>
        {series.length < 2 ? (
          <p className="empty">Calibrage de la référence : {e.refScores.length}/1000 scores, puis 200 scores courants.</p>
        ) : (
          <svg viewBox={`0 0 ${Wd} ${Ht}`} width="100%" role="img" aria-label={`Dérive JS : dernière valeur ${last?.toFixed(3)} bit, seuil ${e.jsThreshold.toFixed(3)}`}>
            {[0, yMax / 2, yMax].map((v, k) => (
              <g key={k}>
                <line x1={pl} x2={Wd - 8} y1={Y(v)} y2={Y(v)} stroke="#24282d" />
                <text x={pl - 6} y={Y(v) + 3} fontSize="9" fill="#737b84" textAnchor="end" fontFamily="JetBrains Mono Variable, monospace">
                  {v.toFixed(2)}
                </text>
              </g>
            ))}
            <line x1={pl} x2={Wd - 8} y1={Y(e.jsThreshold)} y2={Y(e.jsThreshold)} stroke="#fab219" strokeDasharray="5 4" />
            <text x={Wd - 10} y={Y(e.jsThreshold) - 5} fontSize="9.5" fill="#fab219" textAnchor="end" fontFamily="JetBrains Mono Variable, monospace">
              seuil = 3 × bruit d’échantillonnage
            </text>
            <path d={path} fill="none" stroke="#3987e5" strokeWidth="2" />
            {series.length > 0 && <circle cx={X(series.length - 1)} cy={Y(last!)} r="4" fill="#3987e5" stroke="#1a1d21" strokeWidth="2" />}
            <text x={pl} y={Ht - 5} fontSize="9" fill="#737b84" fontFamily="JetBrains Mono Variable, monospace">
              {simTimeLabel(series[0].step)}
            </text>
            <text x={Wd - 8} y={Ht - 5} fontSize="9" fill="#737b84" textAnchor="end" fontFamily="JetBrains Mono Variable, monospace">
              {simTimeLabel(series[series.length - 1].step)}
            </text>
          </svg>
        )}
        <p className="help">
          Référence figée sur les 1 000 premiers scores du direct, comparée aux 500 derniers (10 classes), comme le dashboard (drift.py, eq. 4.19). Déplacez le curseur du taux de fraude pour provoquer une dérive. Au-delà du seuil, le mémoire déclenche une extension de grille (eq. 4.17-4.18).
        </p>
      </div>
      <div>
        <p className="kicker">Distribution des scores : référence et fenêtre courante</p>
        <svg viewBox="0 0 520 170" width="100%" role="img" aria-label="Histogrammes des scores de référence et courants">
          {hr.map((v, b) => {
            const x = 40 + b * 47;
            const h1 = (v / nr / hm) * 130;
            const h2 = (hc[b] / nc / hm) * 130;
            return (
              <g key={b}>
                <rect x={x} y={148 - h1} width={20} height={Math.max(0.5, h1)} rx="2" fill="#6d7680" />
                <rect x={x + 22} y={148 - h2} width={20} height={Math.max(0.5, h2)} rx="2" fill="#3987e5" />
                <text x={x + 21} y={162} fontSize="9" fill="#737b84" textAnchor="middle" fontFamily="JetBrains Mono Variable, monospace">
                  {b / 10}
                </text>
              </g>
            );
          })}
          <line x1="36" x2="516" y1="148.5" y2="148.5" stroke="#3a4047" />
        </svg>
        <div className="legend">
          <span>
            <i style={{ background: '#6d7680' }} /> référence ({int(e.refScores.length)})
          </span>
          <span>
            <i style={{ background: '#3987e5' }} /> courant ({int(e.curScores.length)})
          </span>
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------- calibration
export function CalibrationPanel() {
  const rel = reliability(engine.calib, 10);
  const S = 220;
  const p = 34;
  const X = (v: number) => p + v * (S - p - 8);
  const Y = (v: number) => 8 + (1 - v) * (S - p - 8);
  const pts = rel.filter((r) => r.n >= 5);
  const maxN = Math.max(1, ...rel.map((r) => r.n));
  return (
    <div className="two">
      <div>
        <p className="kicker">Diagramme de fiabilité (session simulée)</p>
        <svg viewBox={`0 0 ${S} ${S}`} width="100%" style={{ maxWidth: 380 }} role="img" aria-label="Fréquence de fraude observée par classe de score, comparée à la diagonale de calibration parfaite">
          {[0, 0.5, 1].map((v) => (
            <g key={v}>
              <line x1={X(0)} x2={X(1)} y1={Y(v)} y2={Y(v)} stroke="#24282d" />
              <text x={p - 5} y={Y(v) + 3} fontSize="8" fill="#737b84" textAnchor="end" fontFamily="JetBrains Mono Variable, monospace">
                {v}
              </text>
              <text x={X(v)} y={S - 14} fontSize="8" fill="#737b84" textAnchor="middle" fontFamily="JetBrains Mono Variable, monospace">
                {v}
              </text>
            </g>
          ))}
          <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} stroke="#6d7680" strokeDasharray="4 4" />
          <path d={pts.map((r, k) => `${k ? 'L' : 'M'}${X(r.pred).toFixed(1)},${Y(r.obs).toFixed(1)}`).join(' ')} fill="none" stroke="#3987e5" strokeWidth="2" />
          {pts.map((r) => (
            <circle key={r.bin} cx={X(r.pred)} cy={Y(r.obs)} r="4" fill="#3987e5" stroke="#1a1d21" strokeWidth="2">
              <title>{`classe ${r.bin / 10}-${(r.bin + 1) / 10} : score moyen ${r.pred.toFixed(2)}, fraude observée ${r.obs.toFixed(2)}, ${r.n} transactions`}</title>
            </circle>
          ))}
          <text x={X(0.5)} y={S - 2} fontSize="8.5" fill="#9aa1a8" textAnchor="middle">
            score moyen de la classe
          </text>
        </svg>
      </div>
      <div>
        <p className="kicker">Effectif par classe de score</p>
        <svg viewBox="0 0 300 150" width="100%" role="img" aria-label="Nombre de transactions par classe de score">
          {rel.map((r) => {
            const h = Math.sqrt(r.n / maxN) * 110;
            return (
              <g key={r.bin}>
                <rect x={14 + r.bin * 28} y={124 - h} width={24} height={Math.max(0.5, h)} rx="2" fill="#6d7680">
                  <title>{`${r.n} transactions`}</title>
                </rect>
                <text x={26 + r.bin * 28} y={138} fontSize="8" fill="#737b84" textAnchor="middle" fontFamily="JetBrains Mono Variable, monospace">
                  {r.bin / 10}
                </text>
              </g>
            );
          })}
        </svg>
        <p className="help">
          Fréquence de fraude réelle (étiquettes du simulateur) par classe de score, sur les 4 000 dernières décisions. Une courbe sur la diagonale signifie que le score se lit comme une probabilité. Le scoreur de la démo n’est pas calibré : ce panneau illustre la surveillance, il ne mesure pas le modèle du mémoire. Échelle racine carrée pour les effectifs.
        </p>
      </div>
    </div>
  );
}

// --------------------------------------------------------------- journal d'audit
export function AuditPanel() {
  const j = engine.journal;
  const [onlyAlerts, setOnlyAlerts] = useState(true);
  const rows: typeof j = [];
  for (let k = j.length - 1; k >= 0 && rows.length < 100; k--) if (!onlyAlerts || j[k].verdict === 'ALERTE') rows.push(j[k]);
  return (
    <div>
      <div className="panel-head" style={{ flexWrap: 'wrap' }}>
        <p className="kicker">
          Journal d’audit horodaté · {int(j.length)} décisions
        </p>
        <div className="row" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--ink-3)', display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={onlyAlerts} onChange={(e) => setOnlyAlerts(e.target.checked)} /> alertes seulement
          </label>
          <button className="btn small" id="export-csv" onClick={() => downloadText(`mkan_journal_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`, engine.journalCsv(), 'text/csv;charset=utf-8')}>
            Exporter CSV
          </button>
        </div>
      </div>
      <div className="table-scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>#</th>
              <th>Horodatage</th>
              <th>Heure sim.</th>
              <th>Compte</th>
              <th>Action</th>
              <th className="r">Montant</th>
              <th className="r">Score</th>
              <th>Verdict</th>
              <th>Variable dominante</th>
              <th>Scénario</th>
              <th>Empreinte</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.n} className={r.verdict === 'ALERTE' ? 'al' : ''}>
                <td>{r.n}</td>
                <td>{r.horodatage.slice(11, 19)}</td>
                <td>{r.heureSimulee}</td>
                <td>{r.compte}</td>
                <td>{r.action}</td>
                <td className="r">{compact(r.montant)}</td>
                <td className="r">{r.score.toFixed(3)}</td>
                <td>{r.verdict}</td>
                <td>{r.variableDominante}</td>
                <td>{r.scenarioDetecte}</td>
                <td style={{ color: 'var(--ink-4)' }}>{r.empreinte}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="help">Chaque décision est chaînée à la précédente par une empreinte (FNV-1a, démonstration) : modifier une ligne casse toutes les suivantes. L’export CSV contient aussi l’étiquette du simulateur.</p>
    </div>
  );
}

// --------------------------------------------------------------- rapport COBAC
export function ReportPanel({ selectedId }: { selectedId: number | null }) {
  const [busy, setBusy] = useState(false);
  const s = engine.find(selectedId) ?? engine.alerts[0];
  const make = async (print: boolean) => {
    setBusy(true);
    try {
      const html = await buildCobacReport(engine, s);
      if (print) {
        const fr = document.createElement('iframe');
        fr.style.position = 'fixed';
        fr.style.right = '0';
        fr.style.bottom = '0';
        fr.style.width = '0';
        fr.style.height = '0';
        fr.style.border = '0';
        fr.setAttribute('title', 'Rapport COBAC à imprimer');
        document.body.appendChild(fr);
        fr.srcdoc = html;
        fr.onload = () => {
          fr.contentWindow?.focus();
          fr.contentWindow?.print();
          setTimeout(() => fr.remove(), 60000);
        };
      } else {
        downloadText(`rapport_cobac_mkan_demo_${new Date().toISOString().slice(0, 10)}.html`, html, 'text/html;charset=utf-8');
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="two">
      <div>
        <p className="kicker">Rapport d’auditabilité (gabarit COBAC)</p>
        <p className="why" style={{ marginTop: 0 }}>
          Reprend le gabarit du dashboard : objet et périmètre, explication de la décision sélectionnée (contributions φ, formes symboliques et R²), synthèse de session, réserves d’opposabilité, empreinte SHA-256.
        </p>
        <dl className="facts" style={{ maxWidth: 420 }}>
          <dt>Décision documentée</dt>
          <dd>{s ? `#${s.tx.id} · ${s.tx.nameOrig} · ${Math.round(s.exp.score * 100)} %` : 'aucune alerte'}</dd>
          <dt>Décisions de la session</dt>
          <dd>{int(engine.nScored)}</dd>
          <dt>Alertes</dt>
          <dd>{int(engine.nAlerts)}</dd>
          <dt>Montant bloqué</dt>
          <dd>{fcfa(engine.blocked)}</dd>
        </dl>
        <div className="row" style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
          <button className="btn primary" id="export-report" disabled={busy} onClick={() => make(false)}>
            Exporter le rapport (HTML)
          </button>
          <button className="btn" disabled={busy} onClick={() => make(true)}>
            Imprimer / PDF
          </button>
        </div>
      </div>
      <div className="note">
        Rapport établi en mode <b>SIMULÉ</b> sur données synthétiques générées dans le navigateur, par un scoreur illustratif : sans valeur probante, à usage de démonstration uniquement. Il ne contient aucun indicateur de performance du modèle du mémoire.
      </div>
    </div>
  );
}
