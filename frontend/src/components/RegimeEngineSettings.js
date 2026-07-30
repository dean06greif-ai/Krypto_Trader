import React, { useEffect, useMemo, useState } from 'react';
import { CaretDown, CaretRight, ArrowCounterClockwise } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const BOOL_KEYS = ['require_adx', 'require_efficiency', 'require_range_break',
  'strong_bypass_range', 'use_variance_ratio', 'auto_adapt'];

const MODE_LABELS = {
  3: '3 Regime · Aufwärts / Seitwärts / Abwärts',
  5: '5 Regime · zusätzlich stark / leicht (empfohlen)',
  9: '9 Regime · Trend × Volatilität',
};

const MODE_HINT = {
  3: 'Gröbste Einteilung – die stabilsten, längsten Abschnitte.',
  5: 'Guter Kompromiss: klare Richtung plus Stärke, ohne Regime-Flut.',
  9: 'Feinste Einteilung – nur bei sehr langen Zeiträumen sinnvoll.',
};

// Diese Felder werden bei aktiver automatischer Anpassung aus dem Zeitraum
// berechnet – manuelles Setzen ist möglich (Override), wird aber markiert.
const ADAPTED_KEYS = ['horizons_days', 'min_hold_days', 'confirm_days', 'smooth_days',
  'vol_ref_days', 'vol_window_days', 'vol_smooth_days', 'gate_timeout_days',
  'validate_min_segment_days'];

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
  const userSet = (k) => config?.[k] !== undefined;
  const mode = Number(val('regime_mode') || 5);
  const adapted = !!val('auto_adapt') && val('adapt_profile') !== 'off';
  const profiles = defaults?.adapt_profiles || [];

  const field = (m) => {
    const v = val(m.key);
    if (m.key === 'regime_mode' || m.key === 'adapt_profile') return null;
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
          {m.label}{adapted && !userSet(m.key) ? ' (auto)' : ''}
          <input value={(v || []).join(', ')}
            onChange={e => set('horizons_days', e.target.value.split(',')
              .map(x => parseFloat(x.trim())).filter(x => !isNaN(x) && x > 0))}
            placeholder={adapted ? 'automatisch aus Zeitraum' : '5, 10, 20, 50, 100'}
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
        {m.label}{adapted && ADAPTED_KEYS.includes(m.key) && !userSet(m.key) ? ' (auto)' : ''}
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
            <option value="v2">v2 · Regression + ADX + Vola</option>
            <option value="kmeans">Cluster (K-Means, alt)</option>
          </select>
        </label>
        {engine === 'v2' && (
          <label className="opt-field" title={MODE_HINT[mode]}>
            Anzahl Regime
            <select value={mode} onChange={e => set('regime_mode', Number(e.target.value))}
              data-testid="regime-mode-select">
              {[3, 5, 9].map(m => <option key={m} value={m}>{MODE_LABELS[m]}</option>)}
            </select>
          </label>
        )}
        {engine === 'v2' && (
          <label className="opt-field" title="Fenster/Glättung automatisch aus dem analysierten Zeitraum ableiten. 'auto' prüft alle Profile und nimmt das bestbewertete.">
            Glättung
            <select value={val('adapt_profile') || 'auto'}
              onChange={e => set('adapt_profile', e.target.value)}
              data-testid="regime-adapt-profile-select">
              <option value="auto">auto (bestes Profil wird ermittelt)</option>
              {profiles.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
              <option value="off">aus (feste Werte)</option>
            </select>
          </label>
        )}
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
          <span className="opt-small" data-testid="regime-engine-hint">
            {MODE_HINT[mode]} {adapted
              ? '· Fenster passen sich dem Zeitraum an'
              : '· feste Fenster (keine Anpassung)'} · kein Lookahead
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
