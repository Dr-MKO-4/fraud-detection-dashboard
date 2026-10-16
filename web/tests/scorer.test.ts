import { describe, expect, it } from 'vitest';
import { KanScorer, HybridEdge, fitEdge, fitSymbolic, CENTERS, H, M, K, D, sigmoid } from '../src/model/kan';
import { Rng } from '../src/sim/rng';
import { SCENARIOS } from '../src/sim/config';
import { jsBits, histogram } from '../src/live/metrics';
import { runInjection } from './helpers';

const scorer = new KanScorer();

describe('scoreur à la KAN', () => {
  it("fonction d'arête hybride : gaussiennes fixes et Fourier (hybrid_edge.py)", () => {
    expect(CENTERS.length).toBe(M);
    expect(CENTERS[0]).toBe(-D);
    expect(CENTERS[M - 1]).toBeCloseTo(D, 12);
    expect(H).toBeCloseTo((2 * D) / (M - 1), 12);
    const e = new HybridEdge([0, 0, 0, 1, 0, 0, 0, 0], [0.5, 0], [0, 0.25]);
    const x = 0.3;
    const expected = Math.exp(-((x - CENTERS[3]) ** 2) / (2 * H * H)) + 0.5 * Math.cos(x) + 0.25 * Math.sin(2 * x);
    expect(e.eval(x)).toBeCloseTo(expected, 12);
    expect(e.wGauss.length + e.aFourier.length + e.bFourier.length).toBe(M + 2 * K);
  });

  it('les contributions somment au logit, et σ(logit) = score', () => {
    const r = new Rng(3);
    for (let n = 0; n < 200; n++) {
      const raw = [
        r.normal(0, 5e4),
        r.normal(0, 5e4),
        r.uniform(0, 3),
        r.uniform(0, 50),
        r.next() < 0.1 ? 1 : 0,
        r.normal(0, 2e4),
        r.uniform(0, 5e6),
        r.uniform(0, 40),
        r.uniform(0, 2),
        r.integers(1, 9),
        r.next() < 0.3 ? 1 : 0,
        r.next(),
      ];
      const x = scorer.explain(raw);
      const sum = x.bias + x.contrib.reduce((a, b) => a + b, 0);
      expect(x.logit).toBeCloseTo(sum, 10);
      expect(x.score).toBeCloseTo(sigmoid(x.logit), 12);
      expect(x.contrib.length).toBe(12);
    }
  });

  it('normalisation : log1p(|x|) puis z-score, écrêtage à [−D, D]', () => {
    const z = scorer.normalize([1e12, 0, 0, 0, 0, 0, 0, 1e9, 0, 1, 0, 0]);
    expect(z[0]).toBe(D);
    expect(z[7]).toBe(D);
    for (const v of z) expect(Math.abs(v)).toBeLessThanOrEqual(D);
  });

  it('les moindres carrés reproduisent une forme cible', () => {
    const target = (z: number) => 1.2 / (1 + Math.exp(-2 * (z - 1)));
    const e = fitEdge(target);
    for (let z = -2.5; z <= 2.5; z += 0.5) expect(Math.abs(e.eval(z) - target(z))).toBeLessThan(0.03);
  });

  it('la régression symbolique retrouve une sigmoïde (R² ≥ 0,99)', () => {
    const f = fitSymbolic((x) => 2 * sigmoid(3 * x - 1.5) - 0.2);
    expect(['sigmoid(x)', 'tanh(x)']).toContain(f.name);
    expect(f.r2).toBeGreaterThan(0.99);
    expect(f.symbolifiable).toBe(true);
  });

  it('divergence de Jensen-Shannon en bits : nulle si identiques, 1 si disjointes', () => {
    expect(jsBits([1, 2, 3], [1, 2, 3])).toBeCloseTo(0, 9);
    expect(jsBits([1, 0], [0, 1])).toBeCloseTo(1, 6);
    expect(histogram([0, 0.05, 0.99, 1], 10)).toEqual([2, 0, 0, 0, 0, 0, 0, 0, 0, 2]);
  });
});

describe("le score augmente sur les transactions d'un scénario injecté", () => {
  for (const sc of SCENARIOS) {
    it(sc, () => {
      const { injected, legit, e } = runInjection(sc, 7);
      expect(injected.length).toBeGreaterThan(0);
      const mean = (a: { exp: { score: number } }[]) => a.reduce((s, x) => s + x.exp.score, 0) / a.length;
      const top = Math.max(...injected.map((s) => s.exp.score));
      expect(mean(injected)).toBeGreaterThan(2.5 * mean(legit));
      expect(top).toBeGreaterThanOrEqual(0.5);
      // la première alerte du scénario injecté est ouverte automatiquement
      expect(e.focusAccount).not.toBeNull();
    });
  }
});
