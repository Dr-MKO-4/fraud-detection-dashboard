/**
 * Scoreur « à la KAN » ILLUSTRATIF.
 *
 *   logit = b + Σ_i φ_i(x̃_i)        score = σ(logit)
 *
 * Chaque φ_i est une fonction d'arête hybride gaussienne + Fourier, de la même
 * forme que MKAN/hybrid_edge.py (eq. 4.13) :
 *
 *   φ(x) = Σ_m w_m · exp(-(x - μ_m)² / (2h²)) + Σ_k [a_k cos(kx) + b_k sin(kx)]
 *
 * avec M = 8 centres fixes répartis sur [-D, D], h = 2D / (M - 1), K = 2.
 * Les poids NE SONT PAS appris : ils sont obtenus par moindres carrés sur des
 * formes cibles écrites à la main (fonction `target` de chaque variable), pour
 * reproduire des allures plausibles. Ce n'est pas le modèle entraîné du mémoire,
 * qui est une cellule récurrente T-KAN (cell.py) à 16 unités cachées.
 *
 * Normalisation reprise de dashboard/inference.py : log1p(|x|) sur les variables
 * à queue lourde, puis z-score (μ, σ choisis à la main), écrêté à [-D, D].
 */
import { FEATURE_ORDER, type FeatureName } from './features';

export const M = 8;
export const K = 2;
export const D = 3;
export const H = (2 * D) / (M - 1);
export const CENTERS = Array.from({ length: M }, (_, i) => -D + (2 * D * i) / (M - 1));
export const LOG_FEATURES = new Set<FeatureName>(['delta_B_orig', 'delta_B_dest', 'r1', 'r2', 'v1h']);

const sig = (x: number) => 1 / (1 + Math.exp(-x));
const gauss = (x: number, mu: number, s: number) => Math.exp(-((x - mu) ** 2) / (2 * s * s));

interface FeatureSpec {
  mu: number;
  std: number;
  /** forme cible écrite à la main sur l'espace normalisé */
  target: (z: number) => number;
}

/** Formes cibles (illustratives), choisies pour être lisibles par un analyste. */
export const SPECS: Record<FeatureName, FeatureSpec> = {
  delta_B_orig: { mu: 9.6, std: 1.5, target: (z) => 0.55 * sig(2.4 * (z - 1.4)) - 0.08 - 0.06 * gauss(z, -0.4, 0.8) },
  delta_B_dest: { mu: 9.6, std: 1.7, target: (z) => 0.3 * sig(2 * (z - 1.6)) - 0.06 },
  r1: { mu: 0.25, std: 0.3, target: (z) => 1.1 * sig(3 * (z - 0.8)) - 0.12 },
  r2: { mu: 0.5, std: 1.5, target: (z) => 1.2 * sig(2.2 * (z - 1.3)) - 0.1 },
  flag_anomalie: { mu: 0.08, std: 0.27, target: (z) => 0.95 * sig(3 * (z - 1.5)) - 0.06 },
  delta_commission: { mu: 0, std: 9000, target: (z) => 2.6 * gauss(z, 1.9, 0.9) * sig(4 * (z - 0.6)) },
  var_agent_split: { mu: 0, std: 2e5, target: (z) => 2.3 * (sig(5 * (z - 0.5)) - sig(-2.5)) },
  rho_rupture: { mu: 1.2, std: 2.0, target: (z) => 2.4 * sig(2.2 * (z - 1.8)) - 0.1 },
  rho_refund: { mu: 0.05, std: 0.2, target: (z) => 2.3 * sig(3 * (z - 1.05)) - 0.05 },
  v1h: { mu: 0.75, std: 0.25, target: (z) => 2.8 * sig(3.2 * (z - 1.9)) - 0.08 },
  flag_nuit: { mu: 0.2, std: 0.4, target: (z) => 0.5 * sig(4 * (z - 0.75)) - 0.05 },
  rho_nouveau: { mu: 0.3, std: 0.2, target: (z) => 0.85 * sig(2.5 * (z - 1.3)) - 0.12 + 0.04 * Math.sin(2 * z) },
};

export const BIAS = -2.9;

/** Fonction d'arête hybride (hybrid_edge.py). */
export class HybridEdge {
  constructor(
    public wGauss: number[],
    public aFourier: number[],
    public bFourier: number[],
  ) {}

  eval(x: number): number {
    let g = 0;
    for (let m = 0; m < M; m++) g += this.wGauss[m] * Math.exp(-((x - CENTERS[m]) ** 2) / (2 * H * H));
    let f = 0;
    for (let k = 1; k <= K; k++) f += this.aFourier[k - 1] * Math.cos(k * x) + this.bFourier[k - 1] * Math.sin(k * x);
    return g + f;
  }

  /** décomposition pour l'affichage : composante FastKAN et composante Fourier */
  parts(x: number): { gauss: number; fourier: number } {
    let g = 0;
    for (let m = 0; m < M; m++) g += this.wGauss[m] * Math.exp(-((x - CENTERS[m]) ** 2) / (2 * H * H));
    let f = 0;
    for (let k = 1; k <= K; k++) f += this.aFourier[k - 1] * Math.cos(k * x) + this.bFourier[k - 1] * Math.sin(k * x);
    return { gauss: g, fourier: f };
  }
}

function basisRow(x: number): number[] {
  const row: number[] = [];
  for (let m = 0; m < M; m++) row.push(Math.exp(-((x - CENTERS[m]) ** 2) / (2 * H * H)));
  for (let k = 1; k <= K; k++) row.push(Math.cos(k * x));
  for (let k = 1; k <= K; k++) row.push(Math.sin(k * x));
  return row;
}

/** Résout A x = b (Gauss avec pivot partiel). */
export function solve(A: number[][], b: number[]): number[] {
  const n = b.length;
  const a = A.map((r, i) => [...r, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(a[r][c]) > Math.abs(a[p][c])) p = r;
    [a[c], a[p]] = [a[p], a[c]];
    const piv = a[c][c] || 1e-12;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = a[r][c] / piv;
      if (f === 0) continue;
      for (let k = c; k <= n; k++) a[r][k] -= f * a[c][k];
    }
  }
  return a.map((r, i) => r[n] / (a[i][i] || 1e-12));
}

/** Ajuste les poids (w, a, b) d'une arête sur une forme cible, moindres carrés ridge. */
export function fitEdge(target: (z: number) => number, n = 181, ridge = 1e-6, ridgeFourier = 1e-3): HybridEdge {
  const P = M + 2 * K;
  const AtA = Array.from({ length: P }, () => new Array(P).fill(0));
  const Atb = new Array(P).fill(0);
  for (let i = 0; i < n; i++) {
    const x = -D + (2 * D * i) / (n - 1);
    const row = basisRow(x);
    const y = target(x);
    for (let p = 0; p < P; p++) {
      Atb[p] += row[p] * y;
      for (let q = 0; q < P; q++) AtA[p][q] += row[p] * row[q];
    }
  }
  // pénalité plus forte sur Fourier : évite que les deux bases se compensent
  for (let p = 0; p < P; p++) AtA[p][p] += (p < M ? ridge : ridgeFourier) * n;
  const w = solve(AtA, Atb);
  return new HybridEdge(w.slice(0, M), w.slice(M, M + K), w.slice(M + K));
}

// ---------------------------------------------------------------------------
// Régression symbolique approchée (port simplifié de MKAN/symbolic.py) :
// φ(x) ≈ c · f(a·x + b) + d, (a, b) par grille, (c, d) par moindres carrés.

export const SYMBOLIC_LIBRARY: Record<string, (x: number) => number> = {
  x: (x) => x,
  'x^2': (x) => x * x,
  'x^3': (x) => x * x * x,
  'sin(x)': Math.sin,
  'cos(x)': Math.cos,
  'tanh(x)': Math.tanh,
  'sigmoid(x)': sig,
  'softplus(x)': (x) => Math.log1p(Math.exp(Math.min(x, 20))),
  'exp(x)': (x) => Math.exp(Math.min(x, 10)),
  '|x|': Math.abs,
  'sqrt(|x|)': (x) => Math.sqrt(Math.abs(x) + 1e-6),
  gauss: (x) => Math.exp(-x * x),
};

export interface SymbolicFit {
  name: string;
  a: number;
  b: number;
  c: number;
  d: number;
  r2: number;
  symbolifiable: boolean;
}

export function fitSymbolic(phi: (x: number) => number, r2Threshold = 0.99): SymbolicFit {
  const N = 121;
  const xs = Array.from({ length: N }, (_, i) => -D + (2 * D * i) / (N - 1));
  const ys = xs.map(phi);
  const yMean = ys.reduce((s, v) => s + v, 0) / N;
  const ssTot = ys.reduce((s, v) => s + (v - yMean) ** 2, 0) + 1e-12;
  let best: SymbolicFit = { name: 'x', a: 1, b: 0, c: 0, d: yMean, r2: -Infinity, symbolifiable: false };
  const aGrid = [-6, -4, -3, -2.5, -2, -1.5, -1, -0.75, -0.5, -0.3, 0.3, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8];
  const bGrid = Array.from({ length: 49 }, (_, i) => -12 + i * 0.5);
  for (const [name, f] of Object.entries(SYMBOLIC_LIBRARY)) {
    for (const a of aGrid) {
      for (const b of bGrid) {
        let sf = 0;
        let sff = 0;
        let sfy = 0;
        let sy = 0;
        const fv = new Array(N);
        let bad = false;
        for (let i = 0; i < N; i++) {
          const v = f(a * xs[i] + b);
          if (!Number.isFinite(v)) {
            bad = true;
            break;
          }
          fv[i] = v;
          sf += v;
          sff += v * v;
          sfy += v * ys[i];
          sy += ys[i];
        }
        if (bad) continue;
        const den = N * sff - sf * sf;
        if (Math.abs(den) < 1e-9) continue;
        const c = (N * sfy - sf * sy) / den;
        const d = (sy - c * sf) / N;
        let ssRes = 0;
        for (let i = 0; i < N; i++) ssRes += (ys[i] - (c * fv[i] + d)) ** 2;
        const r2 = 1 - ssRes / ssTot;
        if (r2 > best.r2) best = { name, a, b, c, d, r2, symbolifiable: false };
      }
    }
  }
  best.symbolifiable = best.r2 >= r2Threshold;
  return best;
}

const TEMPLATES: Record<string, string> = {
  x: '@',
  'x^2': '@²',
  'x^3': '@³',
  'sin(x)': 'sin@',
  'cos(x)': 'cos@',
  'tanh(x)': 'tanh@',
  'sigmoid(x)': 'σ@',
  'softplus(x)': 'softplus@',
  'exp(x)': 'exp@',
  '|x|': '|@|',
  'sqrt(|x|)': '√|@|',
  gauss: 'exp(−@²)',
};

/** format_formula (symbolic.py) : « c · f(a·x + b) + d » */
export function formatFormula(s: SymbolicFit): string {
  const inner = `(${s.a.toFixed(2)}·x ${s.b >= 0 ? '+' : '−'} ${Math.abs(s.b).toFixed(2)})`;
  const body = (TEMPLATES[s.name] ?? s.name).replace('@', inner);
  return `${s.c < 0 ? '−' : ''}${Math.abs(s.c).toFixed(2)} · ${body} ${s.d >= 0 ? '+' : '−'} ${Math.abs(s.d).toFixed(2)}`;
}

// ---------------------------------------------------------------------------

export interface Explanation {
  raw: number[];
  z: number[];
  contrib: number[];
  bias: number;
  logit: number;
  score: number;
}

export class KanScorer {
  readonly edges: HybridEdge[];
  readonly bias = BIAS;
  private symCache: (SymbolicFit | undefined)[] = [];

  constructor() {
    this.edges = FEATURE_ORDER.map((f) => fitEdge(SPECS[f].target));
  }

  /** forme symbolique approchée de φ_i, calculée à la demande puis mise en cache */
  symbolic(i: number): SymbolicFit {
    let s = this.symCache[i];
    if (!s) {
      const e = this.edges[i];
      s = fitSymbolic((x) => e.eval(x));
      this.symCache[i] = s;
    }
    return s;
  }

  normalize(raw: number[]): number[] {
    return FEATURE_ORDER.map((f, i) => {
      let v = raw[i];
      if (LOG_FEATURES.has(f)) v = Math.log1p(Math.abs(v));
      const z = (v - SPECS[f].mu) / SPECS[f].std;
      return Math.max(-D, Math.min(D, Number.isFinite(z) ? z : D));
    });
  }

  explain(raw: number[]): Explanation {
    const z = this.normalize(raw);
    const contrib = z.map((v, i) => this.edges[i].eval(v));
    const logit = this.bias + contrib.reduce((s, v) => s + v, 0);
    return { raw, z, contrib, bias: this.bias, logit, score: sig(logit) };
  }

  /** conversion inverse z → valeur brute (pour graduer les axes des mini-graphes) */
  denormalize(i: number, z: number): number {
    const f = FEATURE_ORDER[i];
    const v = z * SPECS[f].std + SPECS[f].mu;
    return LOG_FEATURES.has(f) ? Math.expm1(Math.max(0, v)) : v;
  }
}

export const sigmoid = sig;
