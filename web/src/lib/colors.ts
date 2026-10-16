import type { Scenario } from '../sim/config';

export const SCENARIO_COLOR: Record<Scenario, string> = {
  ATO: '#3987e5',
  REFUND: '#d95926',
  FAKE_CRED: '#199e70',
  SPLIT_DEP: '#c98500',
  SMURFING: '#d55181',
};

export const CRITICAL = '#d03b3b';
export const POS = '#d95926';
export const NEG = '#3987e5';

/** Couleur d'un score : gris-bleu sous le seuil, ambre à l'approche, rouge (critique) au-delà. */
export function scoreColor(score: number, tau: number): string {
  if (score >= tau) return CRITICAL;
  const t = Math.max(0, Math.min(1, score / Math.max(tau, 1e-6)));
  // #4b5560 → #fab219 (warning) sur la dernière moitié
  const u = Math.max(0, (t - 0.45) / 0.55);
  const a = [0x4b, 0x55, 0x60];
  const b = [0xc9, 0x95, 0x2a];
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * u * u));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
