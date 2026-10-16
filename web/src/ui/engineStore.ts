import { useSyncExternalStore } from 'react';
import { LiveEngine } from '../live/engine';

/** Moteur unique pour la session : survit aux changements de page. */
export const engine = new LiveEngine({ seed: 1000, fraudRate: 0.21, tau: 0.5 });

type Drawer = (now: number) => void;
const drawers = new Set<Drawer>();
const FIXED_DT = 1 / 60;
let acc = 0;
let last = 0;
let running = false;

function frame(now: number) {
  if (!running) return;
  const dt = last ? Math.min(0.25, (now - last) / 1000) : 0;
  last = now;
  acc += dt;
  let n = 0;
  // pas fixe : la simulation avance par incréments de 1/60 s quelle que soit la cadence d'affichage
  while (acc >= FIXED_DT && n < 16) {
    engine.tick(FIXED_DT);
    acc -= FIXED_DT;
    n++;
  }
  if (n === 16) acc = 0;
  for (const d of drawers) d(now);
  requestAnimationFrame(frame);
}

export function startLoop() {
  if (running) return;
  running = true;
  last = 0;
  requestAnimationFrame(frame);
}

export function addDrawer(d: Drawer) {
  drawers.add(d);
  return () => {
    drawers.delete(d);
  };
}

export function useEngine(): number {
  return useSyncExternalStore(engine.subscribe, engine.getVersion);
}

export const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (typeof window !== 'undefined') {
  (window as unknown as { __mkan: LiveEngine }).__mkan = engine;
}
