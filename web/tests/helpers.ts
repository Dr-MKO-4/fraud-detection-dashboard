import { LiveEngine, type Scored } from '../src/live/engine';
import type { Scenario } from '../src/sim/config';

/** Injecte un scénario et collecte, heure par heure, les transactions injectées scorées. */
export function runInjection(sc: Scenario, seed = 7, hours = 14) {
  const e = new LiveEngine({ seed, warmup: 130 });
  e.advance(3600 * 3);
  e.inject(sc);
  const injected: Scored[] = [];
  const legit: Scored[] = [];
  const seen = new Set<number>();
  for (let h = 0; h < hours; h++) {
    e.advance(3600);
    for (const s of e.recent) {
      if (seen.has(s.tx.id)) continue;
      seen.add(s.tx.id);
      if (s.tx.injected && s.tx.isFraud) injected.push(s);
      else if (!s.tx.isFraud) legit.push(s);
    }
  }
  return { e, injected, legit };
}
