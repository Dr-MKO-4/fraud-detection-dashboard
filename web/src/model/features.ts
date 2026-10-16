/**
 * Calcul incrémental des 12 variables du modèle, port TypeScript de
 * dashboard/stream_features.py (IncrementalFeatures). Sémantique identique
 * (1 step = 1 heure) ; les fenêtres Redis du dashboard sont remplacées par la
 * mémoire du navigateur.
 *
 *  delta_B_orig  = oldBalanceOrig - newBalanceOrig
 *  delta_B_dest  = newBalanceDest - oldBalanceDest
 *  r1, r2        = amount / (oldBalanceOrig | newBalanceOrig + eps)
 *  flag_anomalie = amount > moyenne + 2·écart-type (ddof=1) des 10 dernières
 *                  transactions du même (compte, action), min 2 observations
 *  flag_nuit     = step % 24 >= 22 ou < 6
 *  v1h           = nb de transactions du compte au même step (ligne incluse)
 *  var_agent_split = variance (ddof=0) des CASH_IN du même (client, agent, step)
 *  rho_rupture   = amount / (moyenne des montants du compte sur les 720 steps
 *                  strictement antérieurs, 0 si aucun, + eps)
 *  rho_refund    = REFUND reçus / (PAYMENT émis + eps) sur [step-720, step]
 *  rho_nouveau   = part de destinataires nouveaux (aucun contact depuis 2160 steps)
 *  delta_commission = dernier TRANSFER reçu (≤ 48 steps avant) - TRANSFER sortant courant
 */
import type { Tx } from '../sim/momtsim';

export const EPS = 1e-6;
export const WINDOW_STEPS = 30 * 24;
export const NEW_DEST_GAP = 90 * 24;
export const ANOMALY_HISTORY = 10;
export const MULE_TOLERANCE_STEPS = 48;

export const FEATURE_ORDER = [
  'delta_B_orig',
  'delta_B_dest',
  'r1',
  'r2',
  'flag_anomalie',
  'delta_commission',
  'var_agent_split',
  'rho_rupture',
  'rho_refund',
  'v1h',
  'flag_nuit',
  'rho_nouveau',
] as const;
export type FeatureName = (typeof FEATURE_ORDER)[number];

/** Libellés du dashboard (layout.py, FEATURE_LABELS). */
export const FEATURE_LABELS: Record<FeatureName, string> = {
  delta_B_orig: 'Δ Solde origine',
  delta_B_dest: 'Δ Solde destination',
  r1: 'Ratio r1',
  r2: 'Ratio r2',
  flag_anomalie: 'Flag anomalie',
  delta_commission: 'Δ Commission',
  var_agent_split: 'Var. agent split',
  rho_rupture: 'ρ Rupture',
  rho_refund: 'ρ Remboursement',
  v1h: 'Vol. 1h',
  flag_nuit: 'Flag nuit',
  rho_nouveau: 'ρ Nouveaux dest.',
};

export const FEATURE_HELP: Record<FeatureName, string> = {
  delta_B_orig: "Variation du solde de l'émetteur (ancien moins nouveau).",
  delta_B_dest: 'Variation du solde du destinataire (nouveau moins ancien).',
  r1: "Montant rapporté au solde de l'émetteur avant opération.",
  r2: "Montant rapporté au solde de l'émetteur après opération.",
  flag_anomalie: 'Montant supérieur à μ + 2σ des 10 dernières opérations du même type.',
  delta_commission: 'Écart entre le dernier virement reçu (48 h) et le virement sortant : marge de la mule.',
  var_agent_split: 'Variance des CASH_IN du même couple client-agent dans l’heure : fractionnement.',
  rho_rupture: 'Montant rapporté à la moyenne du compte sur 30 jours : rupture de comportement.',
  rho_refund: 'Remboursements reçus par paiement émis sur 30 jours.',
  v1h: 'Nombre d’opérations du compte dans la même heure : vélocité.',
  flag_nuit: 'Opération entre 22 h et 6 h.',
  rho_nouveau: 'Part de destinataires jamais contactés depuis 90 jours.',
};

interface Row {
  step: number;
  amount: number;
  pay: number;
  isNew: number;
}

class Account {
  rows: Row[] = [];
  head = 0;
  sumAmount = 0;
  nPayment = 0;
  sumNew = 0;

  get length() {
    return this.rows.length - this.head;
  }

  prune(step: number) {
    const lo = step - WINDOW_STEPS;
    while (this.head < this.rows.length && this.rows[this.head].step < lo) {
      const r = this.rows[this.head++];
      this.sumAmount -= r.amount;
      this.nPayment -= r.pay;
      this.sumNew -= r.isNew;
    }
    if (this.head > 256 && this.head * 2 > this.rows.length) {
      this.rows = this.rows.slice(this.head);
      this.head = 0;
    }
  }

  last(): Row | undefined {
    return this.length ? this.rows[this.rows.length - 1] : undefined;
  }
}

export type FeatureRow = Pick<
  Tx,
  'action' | 'amount' | 'nameOrig' | 'nameDest' | 'oldBalanceOrig' | 'newBalanceOrig' | 'oldBalanceDest' | 'newBalanceDest'
>;

export class IncrementalFeatures {
  private acc = new Map<string, Account>();
  private hist = new Map<string, number[]>();
  private lastContact = new Map<string, number>();
  private refundIn = new Map<string, number[]>();
  private lastIn = new Map<string, [number, number]>();
  private lastPrune = 0;
  lastStep = -1;

  private account(a: string): Account {
    let st = this.acc.get(a);
    if (!st) {
      st = new Account();
      this.acc.set(a, st);
    }
    return st;
  }

  /** Traite toutes les transactions d'UN step ; retourne les 12 variables par ligne. */
  updateStep(step: number, rows: FeatureRow[]): number[][] {
    if (!rows.length) return [];
    this.lastStep = Math.max(this.lastStep, step);

    // --- agrégats de step : v1h et var_agent_split
    const countByAcct = new Map<string, number>();
    const cashIn = new Map<string, number[]>();
    for (const r of rows) {
      countByAcct.set(r.nameOrig, (countByAcct.get(r.nameOrig) ?? 0) + 1);
      if (r.action === 'CASH_IN') {
        const k = r.nameOrig + '|' + r.nameDest;
        const l = cashIn.get(k);
        if (l) l.push(r.amount);
        else cashIn.set(k, [r.amount]);
      }
    }
    const varSplit = new Map<string, number>();
    for (const [k, vals] of cashIn) {
      const m = vals.reduce((s, v) => s + v, 0) / vals.length;
      varSplit.set(k, vals.reduce((s, v) => s + (v - m) ** 2, 0) / vals.length);
    }

    // --- 1) état des comptes AVANT le step
    const touched = new Set(rows.map((r) => r.nameOrig));
    const baseline = new Map<string, [number, number, number, number]>();
    for (const a of touched) {
      const st = this.account(a);
      st.prune(step);
      const n = st.length;
      baseline.set(a, [n ? st.sumAmount / n : 0, st.nPayment, st.sumNew, n]);
    }
    for (const r of rows) {
      if (r.action === 'REFUND') {
        const l = this.refundIn.get(r.nameDest);
        if (l) l.push(step);
        else this.refundIn.set(r.nameDest, [step]);
      }
    }
    const nRefundIn = new Map<string, number>();
    for (const a of touched) {
      const dq = this.refundIn.get(a);
      if (!dq) {
        nRefundIn.set(a, 0);
        continue;
      }
      while (dq.length && dq[0] < step - WINDOW_STEPS) dq.shift();
      nRefundIn.set(a, dq.length);
    }

    // --- 2) is_new par ligne, puis mise à jour des contacts
    const isNewRow: number[] = [];
    for (const r of rows) {
      const key = r.nameOrig + '|' + r.nameDest;
      const last = this.lastContact.get(key);
      isNewRow.push(last === undefined || step - last > NEW_DEST_GAP ? 1 : 0);
      this.lastContact.set(key, step);
    }
    const stepPay = new Map<string, number>();
    const stepNew = new Map<string, number>();
    const stepN = new Map<string, number>();
    rows.forEach((r, i) => {
      const a = r.nameOrig;
      stepPay.set(a, (stepPay.get(a) ?? 0) + (r.action === 'PAYMENT' ? 1 : 0));
      stepNew.set(a, (stepNew.get(a) ?? 0) + isNewRow[i]);
      stepN.set(a, (stepN.get(a) ?? 0) + 1);
    });

    // --- 3) calcul ligne par ligne
    const out: number[][] = [];
    const h = ((step % 24) + 24) % 24;
    const night = h >= 22 || h < 6 ? 1 : 0;
    for (const r of rows) {
      const a = r.nameOrig;
      const amount = r.amount;
      const hk = a + '|' + r.action;
      let hist = this.hist.get(hk);
      if (!hist) {
        hist = [];
        this.hist.set(hk, hist);
      }
      let flag = 0;
      if (hist.length >= 2) {
        const n = hist.length;
        const mean = hist.reduce((s, v) => s + v, 0) / n;
        const v = hist.reduce((s, x) => s + (x - mean) ** 2, 0) / (n - 1);
        flag = amount > mean + 2 * Math.sqrt(v) ? 1 : 0;
      }
      hist.push(amount);
      if (hist.length > ANOMALY_HISTORY) hist.shift();

      const [meanPrev, pay0, new0, n0] = baseline.get(a)!;
      const rhoRupture = amount / (meanPrev + EPS);
      const rhoRefund = nRefundIn.get(a)! / (pay0 + (stepPay.get(a) ?? 0) + EPS);
      const rhoNouveau = (new0 + (stepNew.get(a) ?? 0)) / Math.max(n0 + (stepN.get(a) ?? 0), 1);

      let deltaCommission = 0;
      if (r.action === 'TRANSFER') {
        const li = this.lastIn.get(a);
        if (li && step - li[0] <= MULE_TOLERANCE_STEPS) deltaCommission = li[1] - amount;
      }

      out.push([
        r.oldBalanceOrig - r.newBalanceOrig,
        r.newBalanceDest - r.oldBalanceDest,
        amount / (r.oldBalanceOrig + EPS),
        amount / (r.newBalanceOrig + EPS),
        flag,
        deltaCommission,
        varSplit.get(a + '|' + r.nameDest) ?? 0,
        rhoRupture,
        rhoRefund,
        countByAcct.get(a)!,
        night,
        rhoNouveau,
      ]);
    }

    // --- 4) versement du step dans l'état des comptes
    rows.forEach((r, i) => {
      const st = this.account(r.nameOrig);
      const pay = r.action === 'PAYMENT' ? 1 : 0;
      st.rows.push({ step, amount: r.amount, pay, isNew: isNewRow[i] });
      st.sumAmount += r.amount;
      st.nPayment += pay;
      st.sumNew += isNewRow[i];
      if (r.action === 'TRANSFER') {
        const prev = this.lastIn.get(r.nameDest);
        if (!prev || prev[0] !== step || r.amount > prev[1]) this.lastIn.set(r.nameDest, [step, r.amount]);
      }
    });

    // --- 5) purge périodique
    if (step - this.lastPrune >= 240) {
      this.lastPrune = step;
      const horizon = step - NEW_DEST_GAP;
      for (const [k, v] of this.lastContact) if (v < horizon) this.lastContact.delete(k);
      for (const [a, st] of this.acc) {
        const l = st.last();
        if (!l || l.step < step - WINDOW_STEPS) this.acc.delete(a);
      }
      for (const [a, dq] of this.refundIn) if (!dq.length || dq[dq.length - 1] < step - WINDOW_STEPS) this.refundIn.delete(a);
      for (const [a, v] of this.lastIn) if (step - v[0] > MULE_TOLERANCE_STEPS) this.lastIn.delete(a);
    }
    return out;
  }
}
