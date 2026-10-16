import { describe, expect, it } from 'vitest';
import { MoMTSim, optimalFragmentation, commission, type Tx } from '../src/sim/momtsim';
import { Rng } from '../src/sim/rng';
import { LiveEngine } from '../src/live/engine';

const run = (seed: number, steps: number) => {
  const s = new MoMTSim({ seed });
  const out: Tx[] = [];
  for (let i = 0; i < steps; i++) out.push(...s.runStep());
  return out;
};

describe('reproductibilité du simulateur', () => {
  it('même graine, même flux (transaction par transaction)', () => {
    const a = run(42, 60);
    const b = run(42, 60);
    expect(a.length).toBeGreaterThan(500);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it('graine différente, flux différent', () => {
    expect(JSON.stringify(run(42, 30))).not.toBe(JSON.stringify(run(43, 30)));
  });

  it('le moteur live complet (variables, scores, journal) est reproductible', () => {
    const e1 = new LiveEngine({ seed: 9, warmup: 40 });
    const e2 = new LiveEngine({ seed: 9, warmup: 40 });
    e1.advance(3600 * 6);
    e2.advance(3600 * 6);
    const strip = (e: LiveEngine) => e.journal.map((j) => [j.txId, j.compte, j.montant, j.score, j.verdict, j.label].join('|'));
    expect(strip(e1)).toEqual(strip(e2));
    expect(e1.nScored).toBeGreaterThan(50);
  });

  it('le générateur à graine est déterministe', () => {
    const r1 = new Rng(5);
    const r2 = new Rng(5);
    for (let i = 0; i < 100; i++) expect(r1.next()).toBe(r2.next());
  });
});

describe('scénarios et taux de fraude', () => {
  it('produit les 5 scénarios et les 6 types de transactions', () => {
    const tx = run(1000, 300);
    const scen = new Set(tx.filter((t) => t.isFraud).map((t) => t.scenario));
    expect([...scen].sort()).toEqual(['ATO', 'FAKE_CRED', 'REFUND', 'SMURFING', 'SPLIT_DEP']);
    const actions = new Set<string>(tx.map((t) => t.action));
    for (const a of ['CASH_IN', 'CASH_OUT', 'DEBIT', 'PAYMENT', 'TRANSFER', 'REFUND']) expect(actions.has(a)).toBe(true);
  });

  it('le taux de fraude réalisé suit la cible réglée', () => {
    const share = (f: number) => {
      const s = new MoMTSim({ seed: 3, fraudRate: f });
      let n = 0;
      let k = 0;
      for (let i = 0; i < 400; i++) {
        for (const t of s.runStep()) {
          n++;
          if (t.isFraud) k++;
        }
      }
      return k / n;
    };
    const low = share(0.05);
    const mid = share(0.21);
    expect(low).toBeLessThan(mid);
    expect(mid).toBeGreaterThan(0.14);
    expect(mid).toBeLessThan(0.28);
    expect(share(0)).toBe(0);
  });

  it('les soldes restent cohérents côté émetteur', () => {
    for (const t of run(77, 80)) {
      if (t.action === 'CASH_IN') expect(t.newBalanceOrig - t.oldBalanceOrig).toBeCloseTo(t.amount, 6);
      else expect(t.oldBalanceOrig - t.newBalanceOrig).toBeCloseTo(t.amount, 6);
      expect(t.newBalanceOrig).toBeGreaterThanOrEqual(0);
    }
  });

  it('Split Deposit : la fragmentation maximise la commission et conserve le total', () => {
    const r = new Rng(1);
    const frags = optimalFragmentation(30000, r);
    expect(frags.length).toBe(5); // 5 × commission(6 000) = 750 > commission(30 000) = 400
    expect(frags.reduce((a, b) => a + b, 0)).toBeCloseTo(30000, 6);
    expect(commission(30000)).toBe(400);
    expect(commission(999)).toBe(0);
  });
});
