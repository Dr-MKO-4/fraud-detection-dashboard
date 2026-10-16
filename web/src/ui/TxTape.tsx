import { useEffect, useRef, useState } from 'react';
import { engine, addDrawer, reducedMotion } from './engineStore';
import { scoreColor, SCENARIO_COLOR, CRITICAL } from '../lib/colors';
import { compact } from '../lib/format';
import { simTimeLabel, type Scored } from '../live/engine';

const ROW_H = 26;
const ROWS = 22;

/** Flux de transactions qui défile, dessiné sur Canvas (aucun re-render React par transaction). */
export function TxTape({ onSelect }: { onSelect: (id: number) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const rowsRef = useRef<Scored[]>([]);
  const [hover, setHover] = useState<{ x: number; y: number; s: Scored } | null>(null);

  useEffect(() => {
    const cv = ref.current!;
    const ctx = cv.getContext('2d')!;
    let lastId = -1;
    let offset = 0;
    let lastNow = 0;
    let w = 0;
    let dpr = 1;
    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = cv.clientWidth;
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(ROW_H * ROWS * dpr);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(cv);
    const narrow = () => window.innerWidth <= 900;

    const draw = (now: number) => {
      const dt = lastNow ? (now - lastNow) / 1000 : 0;
      lastNow = now;
      const rec = engine.recent;
      const newest = rec.length ? rec[rec.length - 1].tx.id : -1;
      if (newest !== lastId) {
        let k = 0;
        for (let i = rec.length - 1; i >= 0 && rec[i].tx.id > lastId; i--) k++;
        if (lastId >= 0 && !reducedMotion()) offset = Math.min(offset + k * ROW_H, ROW_H * 4);
        lastId = newest;
      }
      offset = reducedMotion() ? 0 : offset * Math.exp(-dt * 9);
      if (offset < 0.3) offset = 0;
      const rows: Scored[] = [];
      for (let i = rec.length - 1; i >= 0 && rows.length < ROWS + 2; i--) rows.push(rec[i]);
      rowsRef.current = rows;

      const H = ROW_H * ROWS;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, H);
      const tau = engine.tau;
      const nar = narrow();
      const colTime = 10;
      const colAct = nar ? 68 : 94;
      const colFlow = nar ? 148 : 196;
      const scoreW = nar ? 40 : 132;
      const colAmt = w - 10 - scoreW - 10;
      ctx.textBaseline = 'middle';
      for (let r = 0; r < rows.length; r++) {
        const s = rows[r];
        const y = r * ROW_H - offset;
        if (y > H || y < -ROW_H) continue;
        const cy = y + ROW_H / 2;
        if (s.alert) {
          ctx.fillStyle = 'rgba(208,59,59,0.12)';
          ctx.fillRect(0, y + 1, w, ROW_H - 2);
          ctx.fillStyle = CRITICAL;
          ctx.fillRect(0, y + 1, 3, ROW_H - 2);
        } else if (r % 2 === 0) {
          ctx.fillStyle = 'rgba(255,255,255,0.018)';
          ctx.fillRect(0, y, w, ROW_H);
        }
        ctx.font = '500 11px "JetBrains Mono Variable", monospace';
        ctx.fillStyle = '#737b84';
        ctx.textAlign = 'left';
        const t = simTimeLabel(s.tx.step, s.tx.timeS);
        ctx.fillText(nar ? t.split(' ')[1] : t, colTime, cy);
        ctx.fillStyle = '#c6c3bd';
        ctx.font = '600 11px "JetBrains Mono Variable", monospace';
        ctx.fillText(s.tx.action, colAct, cy);
        ctx.font = '500 11.5px "JetBrains Mono Variable", monospace';
        ctx.fillStyle = '#eceae6';
        const flow = `${s.tx.nameOrig} → ${s.tx.nameDest}`;
        ctx.fillText(flow, colFlow, cy);
        if (s.tx.isFraud && s.tx.scenario) {
          // étiquette du simulateur : petit losange coloré par scénario
          const fx = colFlow + ctx.measureText(flow).width + 9;
          ctx.fillStyle = SCENARIO_COLOR[s.tx.scenario];
          ctx.beginPath();
          ctx.moveTo(fx, cy - 4);
          ctx.lineTo(fx + 4, cy);
          ctx.lineTo(fx, cy + 4);
          ctx.lineTo(fx - 4, cy);
          ctx.closePath();
          ctx.fill();
          if (s.tx.injected) {
            ctx.font = '600 9.5px "JetBrains Mono Variable", monospace';
            ctx.fillStyle = '#f0a07e';
            ctx.fillText('INJ', fx + 9, cy);
          }
        }
        ctx.textAlign = 'right';
        ctx.font = '500 11.5px "JetBrains Mono Variable", monospace';
        ctx.fillStyle = '#c6c3bd';
        ctx.fillText(compact(s.tx.amount), colAmt, cy);
        if (!nar) {
          const bx = w - 10 - scoreW + 10;
          const bw = scoreW - 52;
          ctx.fillStyle = '#2a2e32';
          ctx.fillRect(bx, cy - 3, bw, 6);
          ctx.fillStyle = scoreColor(s.exp.score, tau);
          ctx.beginPath();
          ctx.roundRect(bx, cy - 3, Math.max(2, bw * s.exp.score), 6, 3);
          ctx.fill();
          // seuil τ
          ctx.fillStyle = '#eceae6';
          ctx.fillRect(bx + bw * tau - 0.5, cy - 6, 1, 12);
          ctx.fillStyle = s.alert ? '#ef9a9a' : '#9aa1a8';
          ctx.font = `${s.alert ? 700 : 500} 11px "JetBrains Mono Variable", monospace`;
          ctx.fillText(`${Math.round(s.exp.score * 100)}%`, w - 10, cy);
        } else {
          ctx.fillStyle = s.alert ? '#ef9a9a' : '#9aa1a8';
          ctx.fillText(`${Math.round(s.exp.score * 100)}%`, w - 10, cy + 0);
        }
      }
      // fondu en bas
      const g = ctx.createLinearGradient(0, H - 40, 0, H);
      g.addColorStop(0, 'rgba(26,29,33,0)');
      g.addColorStop(1, 'rgba(26,29,33,1)');
      ctx.fillStyle = g;
      ctx.fillRect(0, H - 40, w, 40);
    };
    const off = addDrawer(draw);
    return () => {
      off();
      ro.disconnect();
    };
  }, []);

  const rowAt = (clientY: number) => {
    const cv = ref.current!;
    const r = cv.getBoundingClientRect();
    const idx = Math.floor((clientY - r.top) / ROW_H);
    return rowsRef.current[idx];
  };

  return (
    <div className="canvas-wrap" ref={wrap}>
      <canvas
        ref={ref}
        style={{ height: ROW_H * ROWS, cursor: 'pointer' }}
        role="img"
        aria-label="Flux des transactions scorées, la plus récente en haut. Les lignes en rouge sont des alertes ; ouvrez-les depuis la file d'alertes pour une navigation au clavier."
        onClick={(e) => {
          const s = rowAt(e.clientY);
          if (s) onSelect(s.tx.id);
        }}
        onMouseMove={(e) => {
          const s = rowAt(e.clientY);
          const r = wrap.current!.getBoundingClientRect();
          setHover(s ? { x: e.clientX - r.left, y: e.clientY - r.top, s } : null);
        }}
        onMouseLeave={() => setHover(null)}
      />
      {hover && (
        <div className="tooltip" style={{ left: Math.min(Math.max(hover.x, 140), (wrap.current?.clientWidth ?? 600) - 140), top: hover.y }}>
          #{hover.s.tx.id} · {hover.s.tx.action} {compact(hover.s.tx.amount)} FCFA · score {Math.round(hover.s.exp.score * 100)} %
          {hover.s.tx.isFraud ? ` · simulateur : ${hover.s.tx.scenario}` : ' · simulateur : légitime'}
        </div>
      )}
    </div>
  );
}
