import { useState } from 'react';
import { engine, useEngine } from '../ui/engineStore';
import { TxTape } from '../ui/TxTape';
import { ScoreTimeline } from '../ui/ScoreTimeline';
import { Explain } from '../ui/Explain';
import { AlertQueue, AuditPanel, CalibrationPanel, Controls, DriftPanel, ReportPanel, Telemetry } from '../ui/Panels';
import { SCENARIOS } from '../sim/config';
import { SCENARIO_COLOR, CRITICAL } from '../lib/colors';

const TABS = [
  ['drift', 'Dérive JS'],
  ['calib', 'Calibration'],
  ['audit', "Journal d'audit"],
  ['report', 'Rapport COBAC'],
] as const;

export function Live() {
  useEngine();
  const [tab, setTab] = useState<(typeof TABS)[number][0]>('drift');
  const sel = engine.selectedId;
  const select = (id: number) => {
    engine.selectedId = id;
    engine.bump(true);
    if (window.innerWidth < 900) document.getElementById('explication')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const s = engine.find(sel);
  return (
    <>
      <h1 className="sr-only">Dashboard live MKAN · MoMTSim</h1>
      <p className="mobile-flag note">Démo pédagogique : flux et scoreur simulés dans le navigateur, ce n'est pas le modèle entraîné.</p>
      <Telemetry />
      <div className="live-grid">
        <Controls />
        <div className="stack">
          <section className="panel" aria-labelledby="tape-h">
            <div className="panel-head" style={{ flexWrap: 'wrap' }}>
              <p className="kicker" id="tape-h">
                Flux de transactions scorées
              </p>
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
                {engine.playing ? '● EN DIRECT' : '❚❚ EN PAUSE'} · {engine.sim.clients.length} clients · {engine.sim.merchants.length} marchands · {engine.sim.mules.length} mules ·{' '}
                {engine.sim.banks.length} banques
              </span>
            </div>
            <div className="tape-head" aria-hidden="true">
              <span>Heure</span>
              <span>Type</span>
              <span>Émetteur → destinataire</span>
              <span>FCFA</span>
              <span>Score</span>
            </div>
            <TxTape onSelect={select} />
            <div className="legend">
              <span>
                <i style={{ background: CRITICAL }} /> alerte (score ≥ τ)
              </span>
              <span>
                <i style={{ background: '#c9952a' }} /> score proche du seuil
              </span>
              {SCENARIOS.map((sc) => (
                <span key={sc}>
                  <i style={{ background: SCENARIO_COLOR[sc], transform: 'rotate(45deg) scale(.8)' }} /> {sc}
                </span>
              ))}
              <span style={{ color: 'var(--ink-4)' }}>losange : étiquette du simulateur</span>
            </div>
          </section>
          <section className="panel" aria-labelledby="tl-h">
            <p className="kicker" id="tl-h">
              Scores des 8 dernières heures simulées
            </p>
            <ScoreTimeline />
          </section>
        </div>
        <AlertQueue selected={sel} onSelect={select} />
      </div>
      <div style={{ marginTop: 14 }}>
        <Explain key={s?.tx.id ?? 'none'} s={s} />
      </div>
      <section className="panel" style={{ marginTop: 14 }} aria-label="Surveillance et audit">
        <div className="tabs" role="tablist">
          {TABS.map(([id, label]) => (
            <button key={id} role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls={`pane-${id}`} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`pane-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === 'drift' && <DriftPanel />}
          {tab === 'calib' && <CalibrationPanel />}
          {tab === 'audit' && <AuditPanel />}
          {tab === 'report' && <ReportPanel selectedId={sel} />}
        </div>
      </section>
    </>
  );
}
