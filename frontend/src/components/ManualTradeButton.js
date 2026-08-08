import React, { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Dezenter "+ Trade"-Button im Chart-Header: eröffnet manuell einen Trade
 * auf dem aktuellen Coin. Läuft über die normale Trade-Pipeline
 * (POST /api/ai/trade/open, source=manuell) – Paper/Live folgt der
 * Coin-Einstellung, alle Guards (Kill-Switch, Limits, Kapital) greifen.
 */
export default function ManualTradeButton({ symbol, onOpened }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ side: 'LONG', leverage: 10, sl_pct: 0.8, tpf_pct: 2.0, capital_pct: 50 });
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  if (!isAdmin()) return null;

  const set = (k, v) => setForm(prev => ({ ...prev, [k]: v }));

  const submit = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/trade/open`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          symbol,
          side: form.side,
          leverage: Number(form.leverage) || undefined,
          sl_pct: Number(form.sl_pct) || undefined,
          tpf_pct: Number(form.tpf_pct) || undefined,
          tp1_pct: Number(form.tpf_pct) ? Number(form.tpf_pct) / 2 : undefined,
          capital_pct: Number(form.capital_pct) || undefined,
          reason: 'Manuell aus dem Chart eröffnet',
          source: 'manuell',
        }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== 'ok') {
        throw new Error(data.detail || 'Trade wurde abgelehnt');
      }
      toast.success(`${form.side} ${symbol} eröffnet @ ${data.entry}`);
      setOpen(false);
      onOpened && onOpened();
    } catch (e) { toast.error(e.message); } finally { setBusy(false); }
  };

  return (
    <span className="manual-trade" ref={boxRef}>
      <button
        className="chart-liq-toggle manual"
        onClick={() => setOpen(v => !v)}
        title="Manuell einen Trade auf diesem Coin eröffnen (Paper/Live folgt der Coin-Einstellung)"
        data-testid="manual-trade-btn"
      >
        + TRADE
      </button>
      {open && (
        <div className="manual-trade-pop" data-testid="manual-trade-form">
          <div className="mt-head">Manueller Trade · {symbol}</div>
          <div className="mt-sides">
            <button
              className={`mt-side long ${form.side === 'LONG' ? 'on' : ''}`}
              onClick={() => set('side', 'LONG')}
              data-testid="manual-trade-long"
            >▲ LONG</button>
            <button
              className={`mt-side short ${form.side === 'SHORT' ? 'on' : ''}`}
              onClick={() => set('side', 'SHORT')}
              data-testid="manual-trade-short"
            >▼ SHORT</button>
          </div>
          {[
            ['leverage', 'Hebel', 'x', 1, 100, 1],
            ['sl_pct', 'Stop-Loss', '%', 0.15, 5, 0.05],
            ['tpf_pct', 'Take-Profit', '%', 0.3, 15, 0.1],
            ['capital_pct', 'Kapitalanteil', '%', 5, 100, 5],
          ].map(([key, label, unit, min, max, step]) => (
            <label className="mt-row" key={key}>
              <span>{label}</span>
              <span className="mt-input">
                <input
                  type="number" min={min} max={max} step={step}
                  value={form[key]}
                  onChange={e => set(key, e.target.value)}
                  data-testid={`manual-trade-${key}`}
                />
                <em>{unit}</em>
              </span>
            </label>
          ))}
          <div className="mt-hint">Paper/Live folgt der Coin-Einstellung · alle Schutz-Guards aktiv</div>
          <button
            className={`mt-submit ${form.side === 'LONG' ? 'long' : 'short'}`}
            disabled={busy}
            onClick={submit}
            data-testid="manual-trade-submit"
          >
            {busy ? 'Wird eröffnet…' : `${form.side} eröffnen`}
          </button>
        </div>
      )}
    </span>
  );
}
