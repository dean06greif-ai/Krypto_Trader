import React, { useEffect, useMemo, useState } from 'react';
import { CaretDown, CaretRight, ArrowCounterClockwise } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const BOOL_KEYS = ['require_adx', 'require_efficiency', 'require_range_break',
  'strong_bypass_range', 'use_variance_ratio'];

/**
 * Einstellungen der Regime-Engine v2 (Regression + ADX + Volatilität +
 * Multi-Timeframe + Hysterese). Felder/Erklärungen kommen vom Backend
 * (/api/regime-lab/engine/defaults), damit UI und Engine nie auseinanderlaufen.
 */
export default function RegimeEngineSettings({ engine, setEngine, config, setConfig }) {
  const [defaults, setDefaults] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    fetch(`${API_URL}/api/regime-lab/engine/defaults`).then(r => r.json())
      .then(setDefaults).catch(() => setDefaults(null));
  }, []);

  const meta = useMemo(() => defaults?.meta || [], [defaults]);
  const val = (k) => (config?.[k] !== undefined ? config[k] : defaults?.config?.[k]);
  const set = (k, v) => setConfig({ ...(config || {}), [k]: v });

  const field = (m) => {
    const v = val(m.key);
    if (BOOL_KEYS.includes(m.key)) {
      return (
        <label className="opt-check" key={m.key} title={m.help} style={{ paddingBottom: 0 }}>
          <input type="checkbox" checked={!!v} onChange={e => set(m.key, e.target.checked)}
            data-testid={`engine-cfg-${m.key}`} /> {m.label}
        </label>
      );
    }
    if (m.key === 'horizons_days') {
      return (
        <label className="opt-field" key={m.key} title={m.help} style={{ minWidth: 190 }}>
          {m.label}
          <input value={(v || []).join(', ')}
            onChange={e => set('horizons_days', e.target.value.split(',')
              .map(x => parseFloat(x.trim())).filter(x => !isNaN(x) && x > 0))}
            placeholder="5, 10, 20, 50, 100"
            data-testid="engine-cfg-horizons_days" style={{ width: 150 }} />
        </label>
      );
    }
    if (m.key === 'vol_metric') {
      return (
        <label className="opt-field" key={m.key} title={m.help}>
          {m.label}
          <select value={v || 'atr'} onChange={e => set('vol_metric', e.target.value)}
            data-testid="engine-cfg-vol_metric">
            <option value="atr">ATR %</option>
            <option value="stdev">Realisierte Vola</option>
          </select>
        </label>
      );
    }
    return (
      <label className="opt-field" key={m.key} title={m.help}>
        {m.label}
        <input type="number" step="0.05" value={v ?? ''}
          onChange={e => set(m.key, e.target.value === '' ? undefined : parseFloat(e.target.value))}
          data-testid={`engine-cfg-${m.key}`} style={{ width: 66 }} />
      </label>
    );
  };

  return (
    <div className="rl-engine-box" data-testid="regime-engine-settings">
      <div className="opt-setup" style={{ alignItems: 'center' }}>
        <label className="opt-field" title="v2 = feste Taxonomie aus Regression/ADX/Volatilität (empfohlen) · Cluster = altes K-Means-Verfahren">
          Regime-Engine
          <select value={engine} onChange={e => setEngine(e.target.value)} data-testid="regime-engine-select">
            <option value="v2">v2 · Regression + ADX + Vola (9 Regime)</option>
            <option value="kmeans">Cluster (K-Means, alt)</option>
          </select>
        </label>
        {engine === 'v2' && (
          <button className="opt-chip" onClick={() => setOpen(!open)} data-testid="regime-engine-toggle">
            {open ? <CaretDown size={11} /> : <CaretRight size={11} />} Engine-Feineinstellungen
          </button>
        )}
        {engine === 'v2' && Object.keys(config || {}).length > 0 && (
          <button className="opt-chip" onClick={() => setConfig({})} data-testid="regime-engine-reset">
            <ArrowCounterClockwise size={11} /> Standard
          </button>
        )}
        {engine === 'v2' && (
          <span className="opt-small">
            9 Regime (Trend × Volatilität) + NNFX-Zuordnung · kein Lookahead
          </span>
        )}
      </div>
      {engine === 'v2' && open && (
        <div className="rl-engine-grid" data-testid="regime-engine-fields">
          {meta.map(field)}
          {!meta.length && <span className="opt-small">Lade Engine-Einstellungen…</span>}
        </div>
      )}
    </div>
  );
}
