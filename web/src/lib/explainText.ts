import { FEATURE_ORDER, FEATURE_LABELS } from '../model/features';
import { SCENARIO_INFO } from '../sim/config';
import type { Scored } from '../live/engine';
import { featValue, signed, pct } from './format';

export function topContributions(s: Scored, n = 3) {
  return s.exp.contrib
    .map((c, i) => ({ i, c, name: FEATURE_ORDER[i], label: FEATURE_LABELS[FEATURE_ORDER[i]], raw: s.exp.raw[i] }))
    .sort((a, b) => b.c - a.c)
    .slice(0, n);
}

/** Justification en une phrase, du type exigé par l'audit (explainability.py). */
export function justification(s: Scored, tau: number): string {
  const top = topContributions(s, 3).filter((t) => t.c > 0.05);
  const verdict = s.exp.score >= tau ? `signalée à risque ${pct(s.exp.score)} (≥ τ = ${pct(tau)})` : `jugée normale à ${pct(s.exp.score)} (< τ = ${pct(tau)})`;
  const parts = top.map((t) => `${t.name} = ${featValue(t.raw)} (φ ${signed(t.c)})`);
  const why = parts.length ? ` parce que ${parts.join(', ')}` : ', aucune variable ne pousse nettement vers la fraude';
  const sc = s.detected ? ` Signature la plus proche : ${SCENARIO_INFO[s.detected].label} (${s.detected}).` : s.exp.score >= tau ? ' Aucune signature de scénario ne domine : alerte atypique.' : '';
  return `Transaction ${verdict}${why}.${sc}`;
}
