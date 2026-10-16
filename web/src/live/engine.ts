/**
 * Moteur du dashboard live : MoMTSim → variables incrémentales → scoreur KAN
 * illustratif → décision (seuil τ) → journal d'audit, dérive JS et calibration.
 * Aucun état React ici : l'interface lit des instantanés à fréquence réduite.
 */
import { MoMTSim, type Tx } from '../sim/momtsim';
import { SCENARIOS, type Scenario } from '../sim/config';
import { IncrementalFeatures, FEATURE_ORDER, type FeatureName } from '../model/features';
import { KanScorer, type Explanation } from '../model/kan';
import { jsBits, histogram } from './metrics';

export const W = 10;
export const SIM_SECONDS_PER_REAL = 1800; // ×1 : 1 heure simulée toutes les 2 s
export const WARMUP_STEPS = 130;

export interface Scored {
  tx: Tx;
  exp: Explanation;
  revealAt: number;
  /** fenêtre glissante du compte émetteur (≤ 10 dernières, la courante en dernier) */
  window: Scored[];
  detected: Scenario | null;
  affinity: Record<Scenario, number>;
  alert: boolean;
  revealed: boolean;
}

export interface JournalEntry {
  n: number;
  horodatage: string;
  heureSimulee: string;
  txId: number;
  compte: string;
  action: string;
  montant: number;
  score: number;
  verdict: 'ALERTE' | 'NORMALE';
  variableDominante: string;
  scenarioDetecte: string;
  label: string;
  empreinte: string;
}

/** Affinité variable → scénario : sert à nommer le scénario le plus proche d'une alerte. */
const AFFINITY: Record<Scenario, Partial<Record<FeatureName, number>>> = {
  ATO: { v1h: 1, rho_nouveau: 0.9, r1: 0.5, r2: 0.6, delta_B_orig: 0.5, flag_anomalie: 0.3, flag_nuit: 0.3 },
  REFUND: { rho_refund: 2 },
  FAKE_CRED: { rho_rupture: 1.2, r1: 0.6, flag_anomalie: 0.5, rho_nouveau: 0.4 },
  SPLIT_DEP: { var_agent_split: 2, v1h: 0.3 },
  SMURFING: { delta_commission: 2.2, delta_B_orig: 0.2 },
};

export function detectScenario(contrib: number[]): { detected: Scenario | null; affinity: Record<Scenario, number> } {
  const affinity = {} as Record<Scenario, number>;
  let best: Scenario | null = null;
  let bestV = 0.45;
  for (const s of SCENARIOS) {
    let v = 0;
    FEATURE_ORDER.forEach((f, i) => {
      const w = AFFINITY[s][f];
      if (w) v += Math.max(0, contrib[i]) * w;
    });
    affinity[s] = v;
    if (v > bestV) {
      bestV = v;
      best = s;
    }
  }
  return { detected: best, affinity };
}

function fnv1a(str: string, seed = 0x811c9dc5): string {
  let h = seed >>> 0;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

export function simTimeLabel(step: number, timeS = 0): string {
  const day = Math.floor(step / 24);
  const h = ((step % 24) + 24) % 24;
  const m = Math.floor(timeS / 60);
  return `J${day} ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

export interface EngineOptions {
  seed?: number;
  fraudRate?: number;
  tau?: number;
  warmup?: number;
}

export class LiveEngine {
  sim!: MoMTSim;
  features!: IncrementalFeatures;
  readonly scorer: KanScorer;
  seed = 1000;
  playing = true;
  speed = 1;
  tau = 0.5;
  clockS = 0;

  queue: Scored[] = [];
  recent: Scored[] = [];
  alerts: Scored[] = [];
  journal: JournalEntry[] = [];
  windows = new Map<string, Scored[]>();
  nScored = 0;
  nAlerts = 0;
  nTruthFraud = 0;
  blocked = 0;
  alertByScenario: Record<string, number> = {};
  txRate = 0;
  private rateWindow: number[] = [];
  private lastHash = '00000000';

  // dérive (MKAN/drift.py, dashboard : référence 1 000 premiers scores, 500 derniers, 10 classes)
  refScores: number[] = [];
  curScores: number[] = [];
  jsSeries: { step: number; js: number }[] = [];
  jsThreshold = 0.01;
  // calibration : (score, étiquette simulateur) des dernières décisions
  calib: { s: number; y: number }[] = [];

  selectedId: number | null = null;
  focusAccount: string | null = null;
  lastInjection: { scenario: Scenario; at: number; account: string | null } | null = null;
  version = 0;
  private listeners = new Set<() => void>();
  private lastNotify = 0;

  constructor(opts: EngineOptions = {}) {
    this.scorer = new KanScorer();
    this.reset(opts.seed ?? 1000, opts.fraudRate ?? 0.21, opts.warmup ?? WARMUP_STEPS);
    if (opts.tau !== undefined) this.tau = opts.tau;
  }

  reset(seed: number, fraudRate = this.sim?.fraudRate ?? 0.21, warmup = WARMUP_STEPS) {
    this.seed = seed;
    this.sim = new MoMTSim({ seed, fraudRate, startStep: 0 });
    this.features = new IncrementalFeatures();
    this.queue = [];
    this.recent = [];
    this.alerts = [];
    this.journal = [];
    this.windows = new Map();
    this.nScored = this.nAlerts = this.nTruthFraud = this.blocked = 0;
    this.alertByScenario = {};
    this.refScores = [];
    this.curScores = [];
    this.jsSeries = [];
    this.calib = [];
    this.selectedId = null;
    this.focusAccount = null;
    this.lastInjection = null;
    this.lastHash = '00000000';
    this.rateWindow = [];
    // historique rejoué avant le direct (run_live.py --warmup-steps)
    for (let i = 0; i < warmup; i++) this.generateStep();
    this.queue = [];
    this.clockS = this.sim.step * 3600;
    this.bump(true);
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getVersion = () => this.version;

  bump(force = false) {
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    if (!force && now - this.lastNotify < 250) return;
    this.lastNotify = now;
    this.version++;
    for (const l of this.listeners) l();
  }

  /** Simule un step, calcule les 12 variables sur le step entier puis score chaque ligne. */
  generateStep(): Scored[] {
    const step = this.sim.step;
    const txs = this.sim.runStep();
    const feats = this.features.updateStep(step, txs);
    const out: Scored[] = [];
    txs.forEach((tx, i) => {
      const exp = this.scorer.explain(feats[i]);
      const { detected, affinity } = detectScenario(exp.contrib);
      let win = this.windows.get(tx.nameOrig);
      if (!win) {
        win = [];
        this.windows.set(tx.nameOrig, win);
      }
      const s: Scored = { tx, exp, revealAt: step * 3600 + tx.timeS, window: [], detected, affinity, alert: false, revealed: false };
      win.push(s);
      if (win.length > W) win.shift();
      s.window = win.slice();
      out.push(s);
    });
    if (this.windows.size > 6000) {
      for (const [k, v] of this.windows) if (v[v.length - 1].tx.step < step - 240) this.windows.delete(k);
    }
    this.queue.push(...out);
    return out;
  }

  inject(s: Scenario) {
    this.sim.inject(s);
    this.lastInjection = { scenario: s, at: this.sim.step, account: null };
    if (!this.playing) {
      // en pause, on avance d'un step pour rendre l'injection visible
      this.advance(3600);
    }
    this.bump(true);
  }

  setTau(t: number) {
    this.tau = t;
    this.bump(true);
  }

  setFraudRate(f: number) {
    this.sim.setFraudRate(f);
    this.bump(true);
  }

  private decide(s: Scored) {
    s.revealed = true;
    s.alert = s.exp.score >= this.tau;
    this.nScored++;
    if (s.tx.isFraud) this.nTruthFraud++;
    const top = s.exp.contrib.reduce((bi, v, i, a) => (v > a[bi] ? i : bi), 0);
    if (s.alert) {
      this.nAlerts++;
      this.blocked += s.tx.amount;
      const key = s.detected ?? 'ATYPIQUE';
      this.alertByScenario[key] = (this.alertByScenario[key] ?? 0) + 1;
      this.alerts.unshift(s);
      if (this.alerts.length > 300) this.alerts.pop();
    }
    this.recent.push(s);
    if (this.recent.length > 800) this.recent.splice(0, this.recent.length - 800);

    const e: Omit<JournalEntry, 'empreinte'> = {
      n: this.nScored,
      horodatage: new Date().toISOString(),
      heureSimulee: simTimeLabel(s.tx.step, s.tx.timeS),
      txId: s.tx.id,
      compte: s.tx.nameOrig,
      action: s.tx.action,
      montant: s.tx.amount,
      score: s.exp.score,
      verdict: s.alert ? 'ALERTE' : 'NORMALE',
      variableDominante: FEATURE_ORDER[top],
      scenarioDetecte: s.alert ? (s.detected ?? 'atypique') : '',
      label: s.tx.isFraud ? (s.tx.scenario ?? 'FRAUDE') : 'LEGIT',
    };
    const empreinte = fnv1a(this.lastHash + JSON.stringify(e));
    this.lastHash = empreinte;
    this.journal.push({ ...e, empreinte });
    if (this.journal.length > 20000) this.journal.splice(0, this.journal.length - 20000);

    // dérive JS et calibration
    if (this.refScores.length < 1000) this.refScores.push(s.exp.score);
    else {
      this.curScores.push(s.exp.score);
      if (this.curScores.length > 500) this.curScores.shift();
    }
    this.calib.push({ s: s.exp.score, y: s.tx.isFraud ? 1 : 0 });
    if (this.calib.length > 4000) this.calib.splice(0, this.calib.length - 4000);

    if (s.tx.injected && s.alert && this.lastInjection && !this.lastInjection.account) {
      this.lastInjection.account = s.tx.nameOrig;
      this.focusAccount = s.tx.nameOrig;
      this.selectedId = s.tx.id;
    }
  }

  private updateDrift() {
    if (this.refScores.length < 1000 || this.curScores.length < 200) return;
    if (this.jsSeries.length === 0) {
      const half = this.refScores.length / 2;
      const noise = jsBits(histogram(this.refScores.slice(0, half), 10), histogram(this.refScores.slice(half), 10));
      this.jsThreshold = Math.max(0.01, 3 * noise);
    }
    const js = jsBits(histogram(this.refScores, 10), histogram(this.curScores, 10));
    this.jsSeries.push({ step: this.sim.step, js });
    if (this.jsSeries.length > 240) this.jsSeries.shift();
  }

  /** Avance l'horloge simulée de dtSim secondes. */
  advance(dtSim: number) {
    const target = this.clockS + dtSim;
    // un step est simulé (en entier) dès que l'horloge atteint son début
    while (this.sim.step * 3600 <= target) {
      this.generateStep();
      this.updateDrift();
    }
    let revealed = 0;
    while (this.queue.length && this.queue[0].revealAt <= target) {
      this.decide(this.queue.shift()!);
      revealed++;
    }
    this.clockS = target;
    return revealed;
  }

  /** Pas de temps réel (s) appelé par la boucle requestAnimationFrame. */
  tick(dtReal: number) {
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
    if (this.playing) {
      const n = this.advance(dtReal * SIM_SECONDS_PER_REAL * this.speed);
      for (let i = 0; i < n; i++) this.rateWindow.push(now);
    }
    while (this.rateWindow.length && now - this.rateWindow[0] > 2000) this.rateWindow.shift();
    this.txRate = this.rateWindow.length / 2;
    this.bump();
  }

  find(id: number | null): Scored | undefined {
    if (id === null) return undefined;
    return this.alerts.find((s) => s.tx.id === id) ?? this.recent.find((s) => s.tx.id === id);
  }

  journalCsv(): string {
    const head = 'n,horodatage,heure_simulee,tx_id,compte,action,montant_fcfa,score,verdict,variable_dominante,scenario_detecte,label_simulateur,empreinte_chainee';
    const rows = this.journal.map((j) =>
      [j.n, j.horodatage, j.heureSimulee, j.txId, j.compte, j.action, j.montant, j.score.toFixed(6), j.verdict, j.variableDominante, j.scenarioDetecte, j.label, j.empreinte].join(','),
    );
    return [head, ...rows].join('\n') + '\n';
  }
}
