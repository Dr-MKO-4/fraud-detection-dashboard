import { describe, expect, it } from 'vitest';
import { IncrementalFeatures, FEATURE_ORDER, EPS, type FeatureRow } from '../src/model/features';

const F = (name: (typeof FEATURE_ORDER)[number]) => FEATURE_ORDER.indexOf(name);
const row = (p: Partial<FeatureRow> & Pick<FeatureRow, 'action' | 'amount' | 'nameOrig' | 'nameDest'>): FeatureRow => ({
  oldBalanceOrig: 100000,
  newBalanceOrig: 100000 - p.amount,
  oldBalanceDest: 0,
  newBalanceDest: p.amount,
  ...p,
});

describe('variables métier (port de stream_features.py)', () => {
  it('soldes, ratios r1 et r2, nuit et vélocité v1h', () => {
    const fe = new IncrementalFeatures();
    // step 23 = 23 h : nuit ; deux opérations du même compte dans l'heure
    const out = fe.updateStep(23, [
      row({ action: 'TRANSFER', amount: 25000, nameOrig: 'C1', nameDest: 'C2' }),
      row({ action: 'PAYMENT', amount: 5000, nameOrig: 'C1', nameDest: 'M1', oldBalanceOrig: 75000, newBalanceOrig: 70000 }),
    ]);
    const x = out[0];
    expect(x[F('delta_B_orig')]).toBe(25000);
    expect(x[F('delta_B_dest')]).toBe(25000);
    expect(x[F('r1')]).toBeCloseTo(0.25, 9);
    expect(x[F('r2')]).toBeCloseTo(25000 / 75000, 9);
    expect(x[F('flag_nuit')]).toBe(1);
    expect(x[F('v1h')]).toBe(2);
    expect(out[1][F('v1h')]).toBe(2);
    // aucun historique strictement antérieur : rupture = montant / eps
    expect(x[F('rho_rupture')]).toBeCloseTo(25000 / EPS, 0);
    // deux destinataires jamais vus sur deux opérations
    expect(x[F('rho_nouveau')]).toBe(1);
    const day = fe.updateStep(24 + 12, [row({ action: 'PAYMENT', amount: 1000, nameOrig: 'C9', nameDest: 'M1' })]);
    expect(day[0][F('flag_nuit')]).toBe(0);
  });

  it('var_agent_split : variance (ddof = 0) des CASH_IN du même couple dans le step', () => {
    const fe = new IncrementalFeatures();
    const out = fe.updateStep(10, [
      row({ action: 'CASH_IN', amount: 1000, nameOrig: 'C1', nameDest: 'M7', oldBalanceOrig: 0, newBalanceOrig: 1000 }),
      row({ action: 'CASH_IN', amount: 3000, nameOrig: 'C1', nameDest: 'M7', oldBalanceOrig: 1000, newBalanceOrig: 4000 }),
      row({ action: 'CASH_IN', amount: 9000, nameOrig: 'C2', nameDest: 'M7', oldBalanceOrig: 0, newBalanceOrig: 9000 }),
    ]);
    expect(out[0][F('var_agent_split')]).toBe(1e6);
    expect(out[1][F('var_agent_split')]).toBe(1e6);
    expect(out[2][F('var_agent_split')]).toBe(0);
    // CASH_IN : le solde de l'émetteur (client crédité) augmente
    expect(out[0][F('delta_B_orig')]).toBe(-1000);
  });

  it('rho_rupture : moyenne des steps strictement antérieurs', () => {
    const fe = new IncrementalFeatures();
    fe.updateStep(1, [row({ action: 'PAYMENT', amount: 1000, nameOrig: 'C1', nameDest: 'M1' })]);
    fe.updateStep(2, [row({ action: 'PAYMENT', amount: 3000, nameOrig: 'C1', nameDest: 'M1' })]);
    const out = fe.updateStep(3, [row({ action: 'TRANSFER', amount: 20000, nameOrig: 'C1', nameDest: 'C5' })]);
    expect(out[0][F('rho_rupture')]).toBeCloseTo(20000 / (2000 + EPS), 6);
    // 2 destinataires nouveaux (M1 au step 1, C5) sur 3 opérations
    expect(out[0][F('rho_nouveau')]).toBeCloseTo(2 / 3, 9);
  });

  it('flag_anomalie : montant > μ + 2σ (ddof = 1) des 10 dernières du même type', () => {
    const fe = new IncrementalFeatures();
    const amounts = [1000, 1100, 900, 1000, 1050];
    amounts.forEach((a, i) => fe.updateStep(i, [row({ action: 'PAYMENT', amount: a, nameOrig: 'C1', nameDest: 'M1' })]));
    const hi = fe.updateStep(10, [row({ action: 'PAYMENT', amount: 5000, nameOrig: 'C1', nameDest: 'M1' })]);
    expect(hi[0][F('flag_anomalie')]).toBe(1);
    const ok = fe.updateStep(11, [row({ action: 'TRANSFER', amount: 5000, nameOrig: 'C1', nameDest: 'C2' })]);
    expect(ok[0][F('flag_anomalie')]).toBe(0); // pas d'historique TRANSFER
  });

  it('rho_refund : REFUND reçus / PAYMENT émis sur 30 jours', () => {
    const fe = new IncrementalFeatures();
    fe.updateStep(1, [row({ action: 'PAYMENT', amount: 3000, nameOrig: 'F1', nameDest: 'M1' })]);
    fe.updateStep(2, [row({ action: 'REFUND', amount: 3000, nameOrig: 'M1', nameDest: 'F1' })]);
    const out = fe.updateStep(3, [row({ action: 'PAYMENT', amount: 3000, nameOrig: 'F1', nameDest: 'M2' })]);
    expect(out[0][F('rho_refund')]).toBeCloseTo(1 / 2, 6);
  });

  it('delta_commission : TRANSFER reçu au plus 48 steps avant, moins le TRANSFER sortant', () => {
    const fe = new IncrementalFeatures();
    fe.updateStep(5, [row({ action: 'TRANSFER', amount: 400000, nameOrig: 'E1', nameDest: 'U1', oldBalanceOrig: 5e6, newBalanceOrig: 4.6e6 })]);
    const out = fe.updateStep(9, [row({ action: 'TRANSFER', amount: 386000, nameOrig: 'U1', nameDest: 'R1', oldBalanceOrig: 400000, newBalanceOrig: 14000 })]);
    expect(out[0][F('delta_commission')]).toBe(14000);
    const late = new IncrementalFeatures();
    late.updateStep(5, [row({ action: 'TRANSFER', amount: 400000, nameOrig: 'E1', nameDest: 'U1' })]);
    const o2 = late.updateStep(5 + 49, [row({ action: 'TRANSFER', amount: 386000, nameOrig: 'U1', nameDest: 'R1' })]);
    expect(o2[0][F('delta_commission')]).toBe(0);
  });
});
