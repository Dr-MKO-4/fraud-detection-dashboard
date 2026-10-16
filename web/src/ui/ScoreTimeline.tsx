import { useEffect, useRef } from 'react';
import { engine, addDrawer } from './engineStore';
import { scoreColor } from '../lib/colors';

const HOURS = 8;
const HEIGHT = 150;

/** Nuage des scores des dernières heures simulées, seuil τ en pointillés. */
export function ScoreTimeline() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current!;
    const ctx = cv.getContext('2d')!;
    let w = 0;
    let dpr = 1;
    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = cv.clientWidth;
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(HEIGHT * dpr);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(cv);
    const padL = 34;
    const padR = 8;
    const padT = 8;
    const padB = 20;
    const draw = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, HEIGHT);
      const t1 = engine.clockS;
      const t0 = t1 - HOURS * 3600;
      const pw = w - padL - padR;
      const ph = HEIGHT - padT - padB;
      const X = (t: number) => padL + ((t - t0) / (t1 - t0)) * pw;
      const Y = (s: number) => padT + (1 - s) * ph;
      ctx.font = '500 10px "JetBrains Mono Variable", monospace';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'right';
      for (const v of [0, 0.5, 1]) {
        ctx.strokeStyle = '#2a2e32';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padL, Y(v) + 0.5);
        ctx.lineTo(w - padR, Y(v) + 0.5);
        ctx.stroke();
        ctx.fillStyle = '#737b84';
        ctx.fillText(`${v * 100}%`, padL - 6, Y(v));
      }
      // heures
      ctx.textAlign = 'center';
      const firstH = Math.ceil(t0 / 3600);
      for (let h = firstH; h * 3600 <= t1; h++) {
        const x = X(h * 3600);
        ctx.strokeStyle = 'rgba(255,255,255,0.04)';
        ctx.beginPath();
        ctx.moveTo(x + 0.5, padT);
        ctx.lineTo(x + 0.5, padT + ph);
        ctx.stroke();
        if (h % 2 === 0) {
          ctx.fillStyle = '#737b84';
          ctx.fillText(`${String(((h % 24) + 24) % 24).padStart(2, '0')}h`, x, HEIGHT - 8);
        }
      }
      const tau = engine.tau;
      const rec = engine.recent;
      for (let i = rec.length - 1; i >= 0; i--) {
        const s = rec[i];
        if (s.revealAt < t0) break;
        const x = X(s.revealAt);
        const y = Y(s.exp.score);
        ctx.fillStyle = scoreColor(s.exp.score, tau);
        ctx.beginPath();
        ctx.arc(x, y, s.alert ? 3 : 2, 0, Math.PI * 2);
        ctx.fill();
        if (s.alert) {
          ctx.strokeStyle = '#1a1d21';
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = '#eceae6';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, Y(tau) + 0.5);
      ctx.lineTo(w - padR, Y(tau) + 0.5);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.textAlign = 'left';
      ctx.fillStyle = '#eceae6';
      ctx.fillText(`τ = ${Math.round(tau * 100)} %`, padL + 6, Y(tau) - 8);
    };
    const off = addDrawer(draw);
    return () => {
      off();
      ro.disconnect();
    };
  }, []);
  return (
    <canvas
      ref={ref}
      style={{ height: HEIGHT, width: '100%', display: 'block' }}
      role="img"
      aria-label="Scores de risque des huit dernières heures simulées ; la ligne pointillée marque le seuil d'alerte τ."
    />
  );
}
