/**
 * Indicateurs de dérive et de calibration (portage de dashboard/metrics.py et
 * MKAN/drift.py). Aucun indicateur de performance n'est affiché par la démo.
 */

/** Histogramme sur [0, 1] en `bins` classes (scores). */
export function histogram(values: number[], bins: number): number[] {
  const h = new Array(bins).fill(0);
  for (const v of values) h[Math.min(bins - 1, Math.max(0, Math.floor(v * bins)))]++;
  return h;
}

/** Divergence de Jensen-Shannon en nats (eq. 4.19), bornée par ln 2. */
export function jsDivergence(p: number[], q: number[], eps = 1e-12): number {
  const sp = p.reduce((a, b) => a + b, 0) + eps;
  const sq = q.reduce((a, b) => a + b, 0) + eps;
  let kp = 0;
  let kq = 0;
  for (let i = 0; i < p.length; i++) {
    const pi = p[i] / sp;
    const qi = q[i] / sq;
    const m = 0.5 * (pi + qi);
    kp += pi * Math.log((pi + eps) / (m + eps));
    kq += qi * Math.log((qi + eps) / (m + eps));
  }
  return 0.5 * kp + 0.5 * kq;
}

/** Même mesure en bits, bornée [0, 1] (js_divergence_bits du dashboard). */
export function jsBits(p: number[], q: number[]): number {
  return jsDivergence(p, q) / Math.LN2;
}

/** Diagramme de fiabilité : score moyen, fréquence observée et effectif par classe. */
export function reliability(pairs: { s: number; y: number }[], bins = 10) {
  const sum = new Array(bins).fill(0);
  const pos = new Array(bins).fill(0);
  const n = new Array(bins).fill(0);
  for (const { s, y } of pairs) {
    const b = Math.min(bins - 1, Math.max(0, Math.floor(s * bins)));
    sum[b] += s;
    pos[b] += y;
    n[b]++;
  }
  return n.map((c, b) => ({ bin: b, n: c, pred: c ? sum[b] / c : NaN, obs: c ? pos[b] / c : NaN }));
}
