import { useMemo, useState } from 'react';
import { engine } from './engineStore';
import type { Scored } from '../live/engine';
import { simTimeLabel } from '../live/engine';
import { FEATURE_ORDER, FEATURE_LABELS, FEATURE_HELP } from '../model/features';
import { D, M, K, formatFormula, LOG_FEATURES } from '../model/kan';
import { SCENARIO_INFO } from '../sim/config';
import { fcfa, featValue, signed, pct } from '../lib/format';
import { justification } from '../lib/explainText';
import { CRITICAL, NEG, POS, SCENARIO_COLOR, scoreColor } from '../lib/colors';

function Gauge({ score, tau }: { score: number; tau: number }) {
  const R = 80;
  const cx = 100;
  const cy = 96;
  const pt = (v: number, r = R) => {
    const a = Math.PI * (1 - v);
    return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
  };
  const arc = (a: number, b: number) => {
    const [x0, y0] = pt(a);
    const [x1, y1] = pt(b);
    return `M ${x0} ${y0} A ${R} ${R} 0 0 1 ${x1} ${y1}`;
  };
  const [tx0, ty0] = pt(tau, R - 12);
  const [tx1, ty1] = pt(tau, R + 12);
  const [nx, ny] = pt(score, R - 22);
  const alert = score >= tau;
  return (
    <svg viewBox="0 0 200 118" width="100%" style={{ maxWidth: 240 }} role="img" aria-label={`Score de risque ${pct(score)}, seuil ${pct(tau)}`}>
      <path d={arc(0, 1)} stroke="#2a2e32" strokeWidth="14" fill="none" strokeLinecap="round" />
      <path d={arc(0, Math.max(0.002, score))} stroke={alert ? CRITICAL : scoreColor(score, tau)} strokeWidth="14" fill="none" strokeLinecap="round" />
      <line x1={tx0} y1={ty0} x2={tx1} y2={ty1} stroke="#eceae6" strokeWidth="2.5" />
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="#eceae6" strokeWidth="2" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="4.5" fill="#eceae6" />
      <text x={cx} y={cy - 26} textAnchor="middle" fill="#eceae6" fontFamily="JetBrains Mono Variable, monospace" fontSize="24" fontWeight="700">
        {Math.round(score * 100)}%
      </text>
      <text x="20" y="114" fill="#737b84" fontSize="9" fontFamily="JetBrains Mono Variable, monospace" textAnchor="middle">0</text>
      <text x="180" y="114" fill="#737b84" fontSize="9" fontFamily="JetBrains Mono Variable, monospace" textAnchor="middle">100</text>
    </svg>
  );
}

function ContribBars({ s, sel, onSel }: { s: Scored; sel: number; onSel: (i: number) => void }) {
  const max = Math.max(1, ...s.exp.contrib.map(Math.abs));
  return (
    <div role="group" aria-label="Contributions φ_i(x_i) par variable">
      {FEATURE_ORDER.map((f, i) => {
        const c = s.exp.contrib[i];
        const wpct = (Math.abs(c) / max) * 50;
        return (
          <button key={f} className="contrib-row" aria-pressed={sel === i} onClick={() => onSel(i)} title={FEATURE_HELP[f]}>
            <span className="n">
              <span className="mono" style={{ fontSize: 11.5 }}>{f}</span>
            </span>
            <svg viewBox="0 0 100 10" preserveAspectRatio="none" width="100%" height="12" aria-hidden="true">
              <rect x="0" y="4.5" width="100" height="1" fill="#2f343a" />
              <rect x="49.8" y="0" width="0.4" height="10" fill="#737b84" />
              <rect x={c >= 0 ? 50 : 50 - wpct} y="1.5" width={Math.max(0.6, wpct)} height="7" rx="1.2" fill={c >= 0 ? POS : NEG} />
            </svg>
            <span className="v">{signed(c)}</span>
          </button>
        );
      })}
      <div className="contrib-row total" aria-hidden="false">
        <span className="n">biais b</span>
        <span />
        <span className="v">{signed(s.exp.bias)}</span>
      </div>
      <div className="contrib-row total" style={{ borderTop: 0, marginTop: 0, paddingTop: 0 }}>
        <span className="n">
          <b>logit = b + Σ φ</b>
        </span>
        <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
          σ(logit) = {pct(s.exp.score, 1)}
        </span>
        <span className="v" style={{ color: 'var(--ink)' }}>
          {signed(s.exp.logit)}
        </span>
      </div>
      <div className="legend">
        <span>
          <i style={{ background: POS }} /> pousse vers « fraude »
        </span>
        <span>
          <i style={{ background: NEG }} /> pousse vers « normal »
        </span>
      </div>
    </div>
  );
}

function PhiGraph({ i, z, raw }: { i: number; z: number; raw: number }) {
  const edge = engine.scorer.edges[i];
  const [hz, setHz] = useState<number | null>(null);
  const [parts, setParts] = useState(false);
  const Wd = 360;
  const Ht = 190;
  const pl = 36;
  const pr = 22;
  const pt = 12;
  const pb = 34;
  const N = 121;
  const data = useMemo(() => {
    const xs = Array.from({ length: N }, (_, k) => -D + (2 * D * k) / (N - 1));
    return xs.map((x) => ({ x, y: edge.eval(x), ...edge.parts(x) }));
  }, [edge]);
  const ys = data.flatMap((d) => (parts ? [d.y, d.gauss, d.fourier] : [d.y]));
  const yMin = Math.min(-0.3, ...ys) - 0.1;
  const yMax = Math.max(0.3, ...ys) + 0.1;
  const X = (x: number) => pl + ((x + D) / (2 * D)) * (Wd - pl - pr);
  const Y = (y: number) => pt + (1 - (y - yMin) / (yMax - yMin)) * (Ht - pt - pb);
  const path = (key: 'y' | 'gauss' | 'fourier') => data.map((d, k) => `${k ? 'L' : 'M'}${X(d.x).toFixed(1)},${Y(d[key]).toFixed(1)}`).join(' ');
  const cur = edge.eval(z);
  const f = FEATURE_ORDER[i];
  const ticks = [-3, -1.5, 0, 1.5, 3];
  const sym = engine.scorer.symbolic(i);
  const hv = hz !== null ? edge.eval(hz) : null;
  return (
    <div className="phi-box">
      <div className="panel-head" style={{ marginBottom: 4 }}>
        <p className="kicker">
          φ<sub>{i + 1}</sub> · {f}
        </p>
        <label style={{ fontSize: 11.5, color: 'var(--ink-3)', display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={parts} onChange={(e) => setParts(e.target.checked)} /> décomposer
        </label>
      </div>
      <svg
        viewBox={`0 0 ${Wd} ${Ht}`}
        width="100%"
        role="img"
        aria-label={`Fonction φ de ${f} sur le domaine normalisé ; point courant x̃ = ${z.toFixed(2)}, φ = ${cur.toFixed(2)}`}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * Wd;
          const x = ((px - pl) / (Wd - pl - pr)) * 2 * D - D;
          setHz(x >= -D && x <= D ? x : null);
        }}
        onMouseLeave={() => setHz(null)}
      >
        {[yMin, 0, yMax].map((v, k) => (
          <g key={k}>
            <line x1={pl} x2={Wd - pr} y1={Y(v)} y2={Y(v)} stroke={v === 0 ? '#3a4047' : '#24282d'} strokeWidth="1" />
            <text x={pl - 5} y={Y(v) + 3} fill="#737b84" fontSize="9" textAnchor="end" fontFamily="JetBrains Mono Variable, monospace">
              {v.toFixed(1)}
            </text>
          </g>
        ))}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={X(t)} x2={X(t)} y1={Ht - pb} y2={Ht - pb + 4} stroke="#3a4047" />
            <text x={X(t)} y={Ht - pb + 14} fill="#737b84" fontSize="9" textAnchor="middle" fontFamily="JetBrains Mono Variable, monospace">
              {t}
            </text>
            <text x={X(t)} y={Ht - pb + 25} fill="#5d646c" fontSize="8.5" textAnchor="middle" fontFamily="JetBrains Mono Variable, monospace">
              {featValue(engine.scorer.denormalize(i, t))}
            </text>
          </g>
        ))}
        {parts && (
          <>
            <path d={path('gauss')} fill="none" stroke="#9085e9" strokeWidth="1.5" strokeDasharray="4 3" />
            <path d={path('fourier')} fill="none" stroke="#199e70" strokeWidth="1.5" strokeDasharray="2 3" />
          </>
        )}
        <path d={path('y')} fill="none" stroke="#eceae6" strokeWidth="2" />
        <line x1={X(z)} x2={X(z)} y1={pt} y2={Ht - pb} stroke={POS} strokeWidth="1" strokeDasharray="3 3" />
        <circle cx={X(z)} cy={Y(cur)} r="5.5" fill={cur >= 0 ? POS : NEG} stroke="#121417" strokeWidth="2" />
        {hz !== null && hv !== null && (
          <g>
            <line x1={X(hz)} x2={X(hz)} y1={pt} y2={Ht - pb} stroke="#737b84" strokeWidth="1" />
            <circle cx={X(hz)} cy={Y(hv)} r="3.5" fill="#eceae6" />
            <text x={Math.min(X(hz) + 6, Wd - 96)} y={pt + 10} fill="#c6c3bd" fontSize="9.5" fontFamily="JetBrains Mono Variable, monospace">
              x̃={hz.toFixed(2)} φ={hv.toFixed(2)}
            </text>
          </g>
        )}
      </svg>
      <div className="legend" style={{ marginTop: 2 }}>
        <span>
          <i style={{ background: '#eceae6', height: 2 }} /> φ apprise (illustrative)
        </span>
        <span>
          <i style={{ background: cur >= 0 ? POS : NEG, borderRadius: '50%' }} /> point courant : {f} = {featValue(raw)} → x̃ = {z.toFixed(2)}, φ = {signed(cur)}
        </span>
        {parts && (
          <>
            <span>
              <i style={{ background: '#9085e9', height: 2 }} /> Σ gaussiennes (M = {M})
            </span>
            <span>
              <i style={{ background: '#199e70', height: 2 }} /> Fourier (K = {K})
            </span>
          </>
        )}
      </div>
      <p className="help" style={{ marginTop: 6 }}>
        Axe horizontal : x̃ = z-score{LOG_FEATURES.has(f) ? ' de log1p(|x|)' : ''}, écrêté à [−{D}, {D}] ; valeur brute correspondante en gris.
      </p>
      <div className="formula" aria-label="Forme symbolique approchée">
        {sym.symbolifiable ? (
          <>
            φ<sub>{i + 1}</sub>(x) ≈ {formatFormula(sym)} <span style={{ color: 'var(--ink-4)' }}>· R² = {sym.r2.toFixed(3)}</span>
          </>
        ) : (
          <>
            non-symbolifiable (numérique) <span style={{ color: 'var(--ink-4)' }}>· meilleure famille {sym.name}, R² = {sym.r2.toFixed(3)} &lt; 0,99</span>
          </>
        )}
      </div>
    </div>
  );
}

function WindowStrip({ s }: { s: Scored }) {
  const cells = s.window;
  const pad = Array.from({ length: Math.max(0, 10 - cells.length) });
  return (
    <div>
      <div className="panel-head" style={{ marginBottom: 0, marginTop: 14 }}>
        <p className="kicker">Fenêtre du compte {s.tx.nameOrig}</p>
        <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>
          {cells.length}/10
        </span>
      </div>
      <div className="window-strip" role="list" aria-label="Dix dernières transactions du compte émetteur">
        {pad.map((_, k) => (
          <span key={'p' + k} role="listitem" aria-label="vide" />
        ))}
        {cells.map((c) => (
          <span
            key={c.tx.id}
            role="listitem"
            className={c.tx.id === s.tx.id ? 'cur' : ''}
            title={`#${c.tx.id} ${c.tx.action} ${fcfa(c.tx.amount)} · score ${pct(c.exp.score)}`}
            aria-label={`${c.tx.action}, score ${pct(c.exp.score)}`}
          >
            <i style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: `${Math.max(6, c.exp.score * 100)}%`, background: scoreColor(c.exp.score, engine.tau), opacity: 0.55 }} />
            <b style={{ position: 'relative', fontWeight: 600 }}>{c.tx.action.replace('_', '').slice(0, 4)}</b>
          </span>
        ))}
      </div>
      <p className="help">
        Le modèle du mémoire déroule sa cellule T-KAN sur ces W = 10 transactions ; ce scoreur illustratif ne lit que la dernière, l’historique entrant par les variables (ρ, flag, v1h).
      </p>
    </div>
  );
}

export function Explain({ s }: { s: Scored | undefined }) {
  const [sel, setSel] = useState<number | null>(null);
  if (!s) {
    return (
      <section className="panel" aria-labelledby="explain-h">
        <p className="kicker" id="explain-h">
          Explication d’une décision
        </p>
        <p className="empty">Sélectionnez une alerte dans la file (ou une ligne du flux) pour voir pourquoi elle a été levée : contributions φ par variable, fonction apprise et forme symbolique.</p>
      </section>
    );
  }
  const tau = engine.tau;
  const alert = s.exp.score >= tau;
  const top = s.exp.contrib.reduce((bi, v, k, a) => (v > a[bi] ? k : bi), 0);
  const i = sel ?? top;
  return (
    <section className="panel" aria-labelledby="explain-h" id="explication">
      <div className="explain-title">
        <h2 id="explain-h">
          Transaction #{s.tx.id} · {s.tx.nameOrig} → {s.tx.nameDest}
        </h2>
        <span className={`verdict ${alert ? 'alert' : 'ok'}`}>{alert ? '▲ ALERTE' : '● NORMALE'}</span>
        {s.detected && alert && (
          <span className="badge">
            <i className="sdot" style={{ background: SCENARIO_COLOR[s.detected] }} /> signature {s.detected}
          </span>
        )}
        <span className={`badge ${s.tx.isFraud ? 'crit' : 'ok'}`}>
          simulateur : {s.tx.isFraud ? s.tx.scenario : 'légitime'}
          {s.tx.injected ? ' · injecté' : ''}
        </span>
      </div>
      <div className="explain">
        <div>
          <Gauge score={s.exp.score} tau={tau} />
          <dl className="facts">
            <dt>Heure simulée</dt>
            <dd>{simTimeLabel(s.tx.step, s.tx.timeS)}</dd>
            <dt>Type</dt>
            <dd>{s.tx.action}</dd>
            <dt>Montant</dt>
            <dd>{fcfa(s.tx.amount)}</dd>
            <dt>Solde émetteur</dt>
            <dd>
              {featValue(s.tx.oldBalanceOrig)} → {featValue(s.tx.newBalanceOrig)}
            </dd>
            <dt>Seuil τ</dt>
            <dd>{pct(tau)}</dd>
          </dl>
          <WindowStrip s={s} />
        </div>
        <div>
          <p className="kicker">Contributions φ_i(x_i) au logit</p>
          <ContribBars s={s} sel={i} onSel={setSel} />
          <p className="why">{justification(s, tau)}</p>
          {s.detected && alert && <p className="help">{SCENARIO_INFO[s.detected].signature}</p>}
        </div>
        <div className="phi-col">
          <p className="kicker">
            Fonction apprise · <span style={{ textTransform: 'none' }}>{FEATURE_LABELS[FEATURE_ORDER[i]]}</span>
          </p>
          <PhiGraph key={i} i={i} z={s.exp.z[i]} raw={s.exp.raw[i]} />
          <p className="help">{FEATURE_HELP[FEATURE_ORDER[i]]} Cliquez une barre pour afficher sa fonction φ.</p>
        </div>
      </div>
    </section>
  );
}
