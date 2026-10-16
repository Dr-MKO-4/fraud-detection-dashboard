/**
 * MoMTSim (version navigateur) : simulation multi-agent d'un service Mobile Money
 * en zone CEMAC, 1 step = 1 heure, montants en FCFA.
 *
 * Logique reprise de MOMTSIM/src/momtsim.py et momtsim_torch.py (TorchFraudInjector) :
 *  - acteurs CLIENT, MERCHANT (dont agents à float), BANK, MULE ;
 *  - modèle « ressort » des probabilités entrée/sortie, adhérence aux contreparties (90 %) ;
 *  - 5 scénarios de fraude avec les formules de la section 3.2 et fraudScenariosConfig.json ;
 *  - convention CASH_IN : nameOrig = client crédité, nameDest = agent ;
 *  - file des transactions différées (REFUND, mule → receveur) et couverture des mules.
 * Population réduite et nombre d'injections piloté par un taux cible réglable.
 */
import { Rng } from './rng';
import { ACTION_PROFILE, FRAUD_CONFIG as CFG, HOURLY_PROFILE, SCENARIOS, type Action, type Scenario } from './config';

export type ActorType = 'CLIENT' | 'MERCHANT' | 'BANK' | 'MULE';

export interface Tx {
  id: number;
  step: number;
  /** secondes écoulées dans le step (0..3599) */
  timeS: number;
  action: Action;
  amount: number;
  nameOrig: string;
  oldBalanceOrig: number;
  newBalanceOrig: number;
  nameDest: string;
  oldBalanceDest: number;
  newBalanceDest: number;
  isFraud: boolean;
  scenario: Scenario | null;
  /** injectée à la demande (bouton) */
  injected: boolean;
}

interface Actor {
  id: string;
  type: ActorType;
  balance: number;
  equilibrium: number;
  mean: Record<string, number>;
  weight: number;
  history: string[];
  highRisk: boolean;
  refundProba: number;
  float0: number;
}

interface Pending {
  step: number;
  action: Action;
  amount: number;
  orig: string;
  dest: string;
  scenario: Scenario | null;
  fraud: boolean;
  injected: boolean;
}

interface FakeCredState {
  dormantUntil: number;
  nLegDone: number;
  nLegTarget: number;
  injected: boolean;
}

interface SmurfNet {
  emitter: string;
  receiver: string;
  mules: string[];
  conscious: Set<string>;
  lastOp: number;
}

export interface SimOptions {
  seed?: number;
  nClients?: number;
  nMerchants?: number;
  nMules?: number;
  nBanks?: number;
  /** transactions légitimes moyennes par step */
  txPerStep?: number;
  /** part cible de transactions frauduleuses (0..0.6) */
  fraudRate?: number;
  /** step de départ (heure 0 = minuit du jour 0) */
  startStep?: number;
}

const LEGIT_OUT: Exclude<Action, 'REFUND' | 'CASH_IN'>[] = ['CASH_OUT', 'PAYMENT', 'TRANSFER', 'DEBIT'];

function lognormal(rng: Rng, mean: number, cv: number): number {
  const s2 = Math.log(1 + cv * cv);
  const mu = Math.log(mean) - s2 / 2;
  return Math.exp(rng.normal(mu, Math.sqrt(s2)));
}

export function commission(amount: number): number {
  let c = 0;
  for (const t of CFG.split_deposit.tariff_grid) if (amount >= t.threshold) c = t.commission;
  return c;
}

/** Fragmentation optimale du Split Deposit (TorchFraudInjector._optimal_fragmentation). */
export function optimalFragmentation(total: number, rng: Rng): number[] {
  let bestK = 1;
  let bestGain = commission(total);
  for (let k = 2; k < 6; k++) {
    const gain = k * commission(total / k);
    if (gain > bestGain) {
      bestGain = gain;
      bestK = k;
    }
  }
  const base = total / bestK;
  const frags: number[] = [];
  let remaining = total;
  for (let j = 0; j < bestK - 1; j++) {
    const eps = rng.uniform(0, CFG.split_deposit.epsilon_max);
    let frag = base + rng.uniform(-eps, eps);
    const maxFrag = remaining - (bestK - 1 - j);
    frag = Math.max(1, Math.min(frag, maxFrag));
    frags.push(frag);
    remaining -= frag;
  }
  frags.push(Math.max(1, remaining));
  return frags;
}

export class MoMTSim {
  readonly rng: Rng;
  readonly seed: number;
  step: number;
  fraudRate: number;
  txPerStep: number;
  private nextId = 1;
  readonly actors = new Map<string, Actor>();
  readonly clients: string[] = [];
  readonly merchants: string[] = [];
  readonly agents: string[] = [];
  readonly mules: string[] = [];
  readonly banks: string[] = [];
  private clientWeights: number[] = [];
  private pending: Pending[] = [];
  private fakeCred = new Map<string, FakeCredState>();
  private smurfNets: SmurfNet[] = [];
  private refundFraudster: string;
  private refundVuln: string[] = [];
  private refundIdx = 0;
  private refundCycles = 0;
  private fraudDebt = 0;
  private fakeSeq = 0;
  private forced: Scenario[] = [];
  /** compte à suivre après une injection manuelle */
  lastInjectedFocus: string | null = null;

  constructor(opts: SimOptions = {}) {
    this.seed = opts.seed ?? 1000;
    this.rng = new Rng(this.seed);
    this.step = opts.startStep ?? 0;
    this.fraudRate = opts.fraudRate ?? 0.21;
    this.txPerStep = opts.txPerStep ?? 34;
    const nC = opts.nClients ?? 420;
    const nM = opts.nMerchants ?? 36;
    const nU = opts.nMules ?? 28;
    const nB = opts.nBanks ?? 3;
    const r = this.rng;

    for (let i = 1; i <= nC; i++) {
      const id = `C${i}`;
      const mean: Record<string, number> = {};
      const scale = lognormal(r, 1, 0.55);
      for (const a of Object.keys(ACTION_PROFILE) as (keyof typeof ACTION_PROFILE)[]) {
        mean[a] = ACTION_PROFILE[a].mean * scale;
      }
      const meanGlobal = (mean.CASH_IN + mean.CASH_OUT + mean.PAYMENT + mean.TRANSFER) / 4;
      const a: Actor = {
        id,
        type: 'CLIENT',
        balance: lognormal(r, 2.2 * meanGlobal, 0.8),
        equilibrium: Math.max(1, 2.5 * meanGlobal),
        mean,
        weight: lognormal(r, 1, 0.7),
        history: [],
        highRisk: false,
        refundProba: 0,
        float0: 0,
      };
      this.actors.set(id, a);
      this.clients.push(id);
      this.clientWeights.push(a.weight);
    }
    for (let i = 1; i <= nM; i++) {
      const id = `M${i}`;
      const highRisk = r.next() < 0.3;
      // agentFloatDistribution.csv : 60 % [100k, 500k], 30 % [500k, 2M], 10 % [2M, 10M]
      const u = r.next();
      const float0 = u < 0.6 ? r.uniform(1e5, 5e5) : u < 0.9 ? r.uniform(5e5, 2e6) : r.uniform(2e6, 1e7);
      const a: Actor = {
        id,
        type: 'MERCHANT',
        balance: float0,
        equilibrium: float0,
        mean: {},
        weight: 1,
        history: [],
        highRisk,
        refundProba: r.uniform(0.3, 0.95) * (r.next() < 0.5 ? 1 : 0.2),
        float0,
      };
      this.actors.set(id, a);
      this.merchants.push(id);
      if (highRisk) this.agents.push(id);
    }
    if (this.agents.length === 0) this.agents.push(this.merchants[0]);
    for (let i = 1; i <= nU; i++) {
      const id = `U${i}`;
      this.actors.set(id, {
        id,
        type: 'MULE',
        balance: r.uniform(2000, 20000),
        equilibrium: 10000,
        mean: { PAYMENT: 8000 },
        weight: 0,
        history: [],
        highRisk: false,
        refundProba: 0,
        float0: 0,
      });
      this.mules.push(id);
    }
    for (let i = 1; i <= nB; i++) {
      const id = `B${i}`;
      this.actors.set(id, {
        id,
        type: 'BANK',
        balance: 1e12,
        equilibrium: 1e12,
        mean: {},
        weight: 0,
        history: [],
        highRisk: false,
        refundProba: 0,
        float0: 0,
      });
      this.banks.push(id);
    }

    // Refund Fraud : un fraudeur persistant, marchands vulnérables (p_refund > seuil)
    this.refundFraudster = r.choice(this.clients);
    this.refundVuln = this.merchants.filter((m) => this.actors.get(m)!.refundProba > CFG.refund.p_refund_threshold);

    // Réseaux de smurfing : mules conscientes (U) + inconscientes (clients compromis)
    const sm = CFG.smurfing;
    const shareConscious = sm.pct_conscious / (sm.pct_conscious + sm.pct_unconscious);
    const nNets = Math.max(5, Math.round(nU / 2));
    for (let n = 0; n < nNets; n++) {
      const emitter = r.choice(this.clients);
      let receiver = r.choice(this.clients);
      while (receiver === emitter) receiver = r.choice(this.clients);
      const k = r.integers(sm.n_mules_min, sm.n_mules_max + 1);
      const nConscious = Math.min(Math.round(k * shareConscious), this.mules.length);
      const conscious = r.sample(this.mules, nConscious);
      const victims = r.sample(
        this.clients.filter((c) => c !== emitter && c !== receiver),
        k - nConscious,
      );
      // émetteur approvisionné : fonds à blanchir
      this.actors.get(emitter)!.balance += r.uniform(3e6, 9e6);
      this.smurfNets.push({ emitter, receiver, mules: [...conscious, ...victims], conscious: new Set(conscious), lastOp: -1e9 });
    }

    // Comptes Fake Credentials déjà créés avant le début de la simulation (dormance en cours)
    if (this.fraudRate > 0) for (let i = 0; i < 6; i++) this.createFakeCred(this.step - r.integers(0, 24 * 6), false);
  }

  get hour(): number {
    return ((this.step % 24) + 24) % 24;
  }

  private actor(id: string): Actor {
    return this.actors.get(id)!;
  }

  setFraudRate(f: number) {
    this.fraudRate = Math.max(0, Math.min(0.6, f));
  }

  /** Demande l'injection immédiate d'un scénario (bouton du dashboard). */
  inject(s: Scenario) {
    this.forced.push(s);
  }

  // ---------------------------------------------------------------------------
  private transfer(origId: string, destId: string, amount: number) {
    const o = this.actor(origId);
    const d = this.actor(destId);
    const oldO = o.balance;
    const oldD = d.balance;
    if (o.balance - amount >= 0) {
      o.balance -= amount;
      d.balance += amount;
    }
    return { oldO, newO: o.balance, oldD, newD: d.balance, ok: oldO !== o.balance || amount === 0 };
  }

  private emit(
    out: Tx[],
    action: Action,
    amount: number,
    orig: string,
    dest: string,
    scenario: Scenario | null,
    fraud: boolean,
    injected: boolean,
    timeS: number,
  ) {
    amount = Math.round(amount);
    if (amount < 1) return false;
    // CASH_IN : l'agent (dest) cède son float au client (orig), convention MoMTSim
    let oldO: number, newO: number, oldD: number, newD: number, ok: boolean;
    if (action === 'CASH_IN') {
      const t = this.transfer(dest, orig, amount);
      oldO = t.oldD;
      newO = t.newD;
      oldD = t.oldO;
      newD = t.newO;
      ok = t.ok;
    } else {
      const t = this.transfer(orig, dest, amount);
      oldO = t.oldO;
      newO = t.newO;
      oldD = t.oldD;
      newD = t.newD;
      ok = t.ok;
    }
    if (!ok) return false;
    out.push({
      id: 0,
      step: this.step,
      timeS: Math.max(0, Math.min(3599, Math.floor(timeS))),
      action,
      amount,
      nameOrig: orig,
      oldBalanceOrig: oldO,
      newBalanceOrig: newO,
      nameDest: dest,
      oldBalanceDest: oldD,
      newBalanceDest: newD,
      isFraud: fraud,
      scenario,
      injected,
    });
    return true;
  }

  /** Adhérence (stickiness) : 90 % de chances de rejouer un contact connu. */
  private pickCounterparty(a: Actor, pool: readonly string[]): string {
    const r = this.rng;
    if (a.history.length && r.next() < 0.9) return r.choice(a.history);
    const t = r.choice(pool);
    if (!a.history.includes(t) && r.next() < 0.9) {
      a.history.push(t);
      if (a.history.length > 100) a.history.shift();
    }
    return t;
  }

  /** Modèle ressort : la proba d'entrée croît quand le solde passe sous l'équilibre. */
  private springInProb(a: Actor): number {
    const k = 1 / a.equilibrium;
    const force = k * (a.equilibrium - a.balance);
    return Math.max(0.05, Math.min(0.95, 0.5 * (1 + force)));
  }

  // ---------------------------------------------------------------------------
  private legitStep(out: Tx[], nTx: number) {
    const r = this.rng;
    for (let i = 0; i < nTx; i++) {
      const cid = r.weighted(this.clients, this.clientWeights);
      const c = this.actor(cid);
      const t = r.uniform(0, 3600);
      const inbound = r.next() < this.springInProb(c);
      if (inbound) {
        const agent = this.pickCounterparty(c, this.merchants);
        const amt = lognormal(r, c.mean.CASH_IN, ACTION_PROFILE.CASH_IN.cv);
        if (this.actor(agent).balance > amt) this.emit(out, 'CASH_IN', amt, cid, agent, null, false, false, t);
        continue;
      }
      const action = r.weighted(
        LEGIT_OUT,
        LEGIT_OUT.map((a) => ACTION_PROFILE[a].share),
      );
      let amt = lognormal(r, c.mean[action], ACTION_PROFILE[action].cv);
      if (amt > c.balance) amt = c.balance * r.uniform(0.3, 0.9);
      if (amt < 100) continue;
      let dest: string;
      if (action === 'TRANSFER') dest = this.pickCounterparty(c, this.clients);
      else if (action === 'DEBIT') dest = r.choice(this.banks);
      else dest = this.pickCounterparty(c, this.merchants);
      if (dest === cid) continue;
      if (this.emit(out, action, amt, cid, dest, null, false, false, t) && action === 'PAYMENT') {
        // remboursement légitime (legit_refund.p_refund)
        if (r.next() < CFG.legit_refund.p_refund * 0.3) {
          this.pending.push({
            step: this.step + r.integers(CFG.refund.delay_min_hours, CFG.refund.delay_max_hours + 1),
            action: 'REFUND',
            amount: amt,
            orig: dest,
            dest: cid,
            scenario: null,
            fraud: false,
            injected: false,
          });
        }
      }
    }
  }

  private flushPending(out: Tx[]) {
    const keep: Pending[] = [];
    for (const p of this.pending) {
      if (p.step <= this.step) {
        this.emit(out, p.action, p.amount, p.orig, p.dest, p.scenario, p.fraud, p.injected, this.rng.uniform(0, 3600));
      } else keep.push(p);
    }
    this.pending = keep;
  }

  // ---- 3.2.1 ATO ------------------------------------------------------------
  private runATO(out: Tx[], injected: boolean): number {
    const c = CFG.ato;
    const r = this.rng;
    let victim = '';
    for (let tries = 0; tries < 30; tries++) {
      const v = r.choice(this.clients);
      if (this.actor(v).balance >= c.B_min * (injected ? 4 : 1)) {
        victim = v;
        break;
      }
    }
    if (!victim) return 0;
    const B0 = this.actor(victim).balance;
    // injection manuelle : rafale d'au moins 4 transferts pour une signature lisible
    const n = r.integers(injected ? Math.max(4, c.n_min) : c.n_min, c.n_max + 1);
    const mules = r.sample(this.mules, n);
    // Δt_i ~ Exp(1 / (lambda_ato · 60 s)) cumulés à partir de t0
    let t = r.uniform(0, 3600 - 60 * c.lambda_ato * n * 1.5);
    let remaining = B0;
    let count = 0;
    for (const m of mules) {
      if (remaining <= 0) break;
      const frac = r.uniform(c.frag_min, c.frag_max);
      const amount = Math.min(remaining, frac * B0);
      if (amount <= 1) continue;
      if (this.emit(out, 'TRANSFER', amount, victim, m, 'ATO', true, injected, t)) {
        remaining -= amount;
        count++;
      }
      t += r.exponential(c.lambda_ato * 60);
    }
    if (injected && count) this.lastInjectedFocus = victim;
    return count;
  }

  // ---- 3.2.2 Refund ---------------------------------------------------------
  private runRefund(out: Tx[], injected: boolean): number {
    const c = CFG.refund;
    const r = this.rng;
    if (this.refundIdx >= this.refundVuln.length) {
      // nouveau fraudeur, nouvelle liste de marchands vulnérables
      this.refundFraudster = r.choice(this.clients);
      this.refundVuln = r.sample(this.refundVuln.length ? this.refundVuln : this.merchants, this.merchants.length);
      this.refundIdx = 0;
    }
    const f = this.refundFraudster;
    if (!injected && r.next() < c.ratio_legit) {
      const m = r.choice(this.merchants);
      this.emit(out, 'PAYMENT', Math.max(r.normal(3325, 800), 100), f, m, null, false, false, r.uniform(0, 3600));
      return 0;
    }
    const cycles = injected ? c.k_max : 1;
    let count = 0;
    for (let k = 0; k < cycles; k++) {
      const merchant = this.refundVuln[this.refundIdx] ?? r.choice(this.merchants);
      const amount = Math.max(r.normal(3325, 800), 100);
      if (this.actor(f).balance < amount) this.actor(f).balance += amount * 2;
      if (this.emit(out, 'PAYMENT', amount, f, merchant, 'REFUND', true, injected, r.uniform(0, 3600))) {
        // REFUND différé Δt ~ U(1 h, 48 h) ; injection manuelle : délai court pour la démo
        const delay = injected ? 1 + k : r.integers(c.delay_min_hours, c.delay_max_hours + 1);
        this.pending.push({ step: this.step + delay, action: 'REFUND', amount, orig: merchant, dest: f, scenario: 'REFUND', fraud: true, injected });
        // à l'injection, les paiements suivants du fraudeur arrivent après les premiers remboursements
        if (injected) {
          this.pending.push({ step: this.step + 2 + k, action: 'PAYMENT', amount: Math.max(r.normal(3325, 800), 100), orig: f, dest: merchant, scenario: 'REFUND', fraud: true, injected });
          count++;
        }
        count += 2;
      }
      this.refundCycles++;
      if (this.refundCycles >= c.k_max) {
        this.refundIdx++;
        this.refundCycles = 0;
      }
    }
    if (injected) this.lastInjectedFocus = f;
    return count;
  }

  // ---- 3.2.3 Fake Credentials -------------------------------------------------
  private createFakeCred(fromStep: number, injected: boolean): string | null {
    const c = CFG.fake_credentials;
    const r = this.rng;
    // compte ouvert avec de fausses pièces : nouvel identifiant, sans historique
    const cid = `C${this.clients.length + this.fakeSeq++ + 1001}`;
    const mean: Record<string, number> = { TRANSFER: r.uniform(3000, 9000) };
    this.actors.set(cid, {
      id: cid,
      type: 'CLIENT',
      balance: r.uniform(50000, 85000),
      equilibrium: 50000,
      mean,
      weight: 0,
      history: [],
      highRisk: false,
      refundProba: 0,
      float0: 0,
    });
    const dormanceH = injected ? 4 : r.integers(c.dormance_min_days * 24, c.dormance_max_days * 24);
    this.fakeCred.set(cid, {
      dormantUntil: fromStep + dormanceH,
      nLegDone: 0,
      nLegTarget: r.integers(c.n_leg_min, c.n_leg_max + 1),
      injected,
    });
    return cid;
  }

  private fakeCredStep(out: Tx[]): number {
    const c = CFG.fake_credentials;
    const r = this.rng;
    let count = 0;
    for (const [cid, st] of [...this.fakeCred]) {
      const a = this.actor(cid);
      if (this.step < st.dormantUntil) {
        const p = st.injected ? 0.95 : 0.1;
        if (st.nLegDone < st.nLegTarget && r.next() < p) {
          const m = r.choice(this.merchants);
          if (this.emit(out, 'PAYMENT', r.uniform(500, c.m_leg_max), cid, m, null, false, st.injected, r.uniform(0, 3600))) st.nLegDone++;
        }
        continue;
      }
      // exfiltration : plafond = min(max(10 × montant moyen, 50 000), solde)
      const plafond = Math.min(Math.max(a.mean.TRANSFER * 10, 50000), a.balance);
      const amount = r.uniform(c.m_exp_ratio_min * plafond, plafond);
      const dest = r.choice(this.clients);
      if (dest !== cid && this.emit(out, 'TRANSFER', amount, cid, dest, 'FAKE_CRED', true, st.injected, r.uniform(0, 3600))) count++;
      this.fakeCred.delete(cid);
    }
    return count;
  }

  // ---- 3.2.4 Split Deposit ---------------------------------------------------
  private runSplit(out: Tx[], injected: boolean): number {
    const r = this.rng;
    const agent = r.choice(this.agents);
    const client = r.choice(this.clients);
    const available = this.actor(agent).balance;
    if (available < 2000) return 0;
    const total = r.uniform(injected ? 20000 : 2000, Math.min(80000, available));
    const frags = optimalFragmentation(total, r);
    const c = CFG.split_deposit;
    const T = r.uniform(c.T_split_min_sec, c.T_split_max_sec);
    const t0 = r.uniform(0, 3600 - T);
    let count = 0;
    frags.forEach((f, i) => {
      const ts = frags.length > 1 ? t0 + (T * i) / (frags.length - 1) : t0;
      if (this.emit(out, 'CASH_IN', f, client, agent, 'SPLIT_DEP', true, injected, ts)) count++;
    });
    if (injected && count) this.lastInjectedFocus = client;
    return count;
  }

  // ---- 3.2.5 Smurfing --------------------------------------------------------
  private runSmurf(out: Tx[], injected: boolean): number {
    const c = CFG.smurfing;
    const r = this.rng;
    const minGap = injected ? 0 : 24;
    const candidates = this.smurfNets.filter((n) => this.step - n.lastOp >= minGap);
    if (!candidates.length) return 0;
    const net = r.choice(candidates);
    net.lastOp = this.step;
    // l'émetteur est réapprovisionné en fonds illicites entre deux opérations
    const em = this.actor(net.emitter);
    if (em.balance < 2.5e6) em.balance += r.uniform(2e6, 6e6);
    const emitterBal = em.balance;
    let S = c.S_seuil;
    let totalX = emitterBal * r.uniform(0.4, 0.8);
    if (totalX < 2 * 0.7 * S || emitterBal < 2 * 0.7 * S) {
      S = emitterBal / (2 * 0.7 * 2);
      totalX = emitterBal * r.uniform(0.4, 0.8);
    }
    if (totalX < 1) return 0;
    const k = r.integers(Math.min(3, net.mules.length), net.mules.length + 1);
    const chosen = r.sample(net.mules, k);
    const raw = chosen.map(() => r.uniform(0.7 * S, 0.99 * S));
    const sum = raw.reduce((a, b) => a + b, 0);
    const scale = Math.min(totalX, chosen.length * 0.99 * S) / sum;
    let count = 0;
    let t = r.uniform(0, 2400);
    chosen.forEach((m, i) => {
      const x = raw[i] * scale;
      if (this.emit(out, 'TRANSFER', x, net.emitter, m, 'SMURFING', true, injected, t)) {
        count++;
        const delta = r.uniform(c.delta_min, c.delta_max);
        const delay = injected ? r.integers(1, 4) : r.integers(c.delay_mule_min_hours, c.delay_mule_max_hours + 1);
        this.pending.push({ step: this.step + delay, action: 'TRANSFER', amount: x * (1 - delta), orig: m, dest: net.receiver, scenario: 'SMURFING', fraud: true, injected });
        count++;
      }
      t += r.uniform(30, 300);
    });
    // couverture des mules conscientes : n_leg paiements légitimes avant la prochaine opération
    for (const m of net.conscious) {
      const nLeg = r.integers(c.n_leg_mule_min, c.n_leg_mule_max + 1);
      for (let j = 0; j < nLeg; j++) {
        const s = this.step + r.integers(1, c.operation_interval_days * 24);
        const amt = Math.max(500, r.normal(8000, 3000));
        this.pending.push({ step: s, action: 'PAYMENT', amount: amt, orig: m, dest: r.choice(this.merchants), scenario: null, fraud: false, injected: false });
      }
    }
    if (injected && count) this.lastInjectedFocus = net.emitter;
    return count;
  }

  private runScenario(s: Scenario, out: Tx[], injected: boolean): number {
    switch (s) {
      case 'ATO':
        return this.runATO(out, injected);
      case 'REFUND':
        return this.runRefund(out, injected);
      case 'FAKE_CRED': {
        if (injected) {
          const cid = this.createFakeCred(this.step, true);
          this.lastInjectedFocus = cid;
          return 1;
        }
        return this.createFakeCred(this.step, false) ? 1 : 0;
      }
      case 'SPLIT_DEP':
        return this.runSplit(out, injected);
      case 'SMURFING':
        return this.runSmurf(out, injected);
    }
  }

  /** Volume légitime attendu au step courant (profil horaire, plancher nocturne). */
  expectedLegit(step = this.step): number {
    const h = ((step % 24) + 24) % 24;
    const p = Math.max(HOURLY_PROFILE[h], 0.012);
    return this.txPerStep * 24 * p * 0.93;
  }

  /** Simule un step complet (1 heure) et retourne ses transactions triées dans le temps. */
  runStep(): Tx[] {
    const r = this.rng;
    const out: Tx[] = [];
    const lambda = this.expectedLegit();
    this.legitStep(out, r.poisson(lambda));
    this.flushPending(out);
    this.fakeCredStep(out);
    let flushedFraud = 0;
    for (const t of out) if (t.isFraud) flushedFraud++;
    const nLegit = out.length - flushedFraud;

    // nombre d'injections : suit le volume (volume_follow) pour viser le taux cible ;
    // les transactions différées (REFUND, mule → receveur) sont décomptées à leur arrivée
    const f = this.fraudRate;
    this.fraudDebt += (f / Math.max(1e-6, 1 - f)) * nLegit - flushedFraud;
    const night = this.hour >= 22 || this.hour < 6;
    // ATO plus fréquent la nuit (signature nocturne), parts égales en moyenne sur la journée
    const weights = SCENARIOS.map((s) => (s === 'ATO' ? (night ? 2.2 : 0.6) : 1));
    let guard = 0;
    while (f > 0 && this.fraudDebt > 0 && guard++ < 40) {
      const s = r.weighted(SCENARIOS, weights);
      const before = out.length;
      this.runScenario(s, out, false);
      let produced = 0;
      for (let i = before; i < out.length; i++) if (out[i].isFraud) produced++;
      this.fraudDebt -= s === 'FAKE_CRED' ? 1 : Math.max(produced, 0.5);
    }
    if (f === 0) this.fraudDebt = 0;
    this.fraudDebt = Math.max(-50, Math.min(50, this.fraudDebt));
    for (const s of this.forced.splice(0)) this.runScenario(s, out, true);

    // rebalancement du float des agents toutes les 24 h
    if (this.hour === 0) {
      for (const m of this.merchants) {
        const a = this.actor(m);
        if (a.balance < a.float0) a.balance = a.float0;
      }
    }

    out.sort((a, b) => a.timeS - b.timeS);
    for (const tx of out) tx.id = this.nextId++;
    this.step++;
    return out;
  }
}
