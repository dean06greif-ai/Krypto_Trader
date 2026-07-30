import React, { useState, useEffect, useCallback } from 'react';
import { Flask, CheckCircle, XCircle, ArrowCounterClockwise, ChartLine, Ghost } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders } from '../auth';
import './AIGovernance.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const STAGE_LABEL = {
  ghost: 'Ghost-Test (nur Simulation)',
  live_pending: 'wartet auf deine Freigabe',
  paper: 'freigegeben – Paper',
  live: 'freigegeben – Live erlaubt',
  rejected: 'abgelehnt',
};

/** Strategie-Labor: eigene Strategien der KI von Ghost bis Live begleiten. */
export const AIStrategyLabPanel = () => {
  const [candidates, setCandidates] = useState([]);
  const [settings, setSettings] = useState(null);
  const [ghosts, setGhosts] = useState({});
  const [form, setForm] = useState({ name: '', thesis: '', symbols: '' });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/ai/strategies`).then(r => r.json());
      setCandidates(data.candidates || []);
      setSettings(data.status?.settings || null);
    } catch (e) { /* silent */ }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 20000);
    return () => clearInterval(iv);
  }, [load]);

  const saveSettings = async (patch) => {
    setSettings(prev => ({ ...prev, ...patch }));
    try {
      const res = await fetch(`${API_URL}/api/ai/strategies/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(patch),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      setSettings(data.settings);
    } catch (e) { toast.error(e.message); }
  };

  const decide = async (cid, action) => {
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/strategies/${cid}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ action }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      toast.success(`Strategie → ${STAGE_LABEL[data.candidate.stage] || data.candidate.stage}`);
      load();
    } catch (e) { toast.error(e.message); } finally { setBusy(false); }
  };

  const registerTest = async (cid) => {
    try {
      const res = await fetch(`${API_URL}/api/ai/strategies/${cid}/register-test`, {
        method: 'POST', headers: authHeaders(),
      });
      const data = await res.json();
      if (data.status === 'ok') toast.success('Für Backtester/Optimizer registriert');
      else toast.message(data.detail || 'Nicht testbar');
      load();
    } catch (e) { toast.error(e.message); }
  };

  const loadGhosts = async (cid) => {
    if (ghosts[cid]) { setGhosts({ ...ghosts, [cid]: null }); return; }
    try {
      const data = await fetch(`${API_URL}/api/ai/strategies/ghost-trades?candidate_id=${cid}&limit=25`)
        .then(r => r.json());
      setGhosts({ ...ghosts, [cid]: data.ghost_trades || [] });
    } catch (e) { toast.error('Ghost-Trades konnten nicht geladen werden'); }
  };

  const createCandidate = async () => {
    if (!form.name.trim()) { toast.error('Name fehlt'); return; }
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/strategies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          name: form.name,
          thesis: form.thesis,
          symbols: form.symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean),
          source: 'trader',
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Fehler');
      toast.success('Strategie-Vorgabe angelegt – die KI verfolgt sie jetzt zusätzlich');
      setForm({ name: '', thesis: '', symbols: '' });
      load();
    } catch (e) { toast.error(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="gov-panel" data-testid="ai-strategy-lab-panel">
      <div className="gov-head">
        <span className="gov-title"><Flask size={14} weight="fill" /> Strategie-Labor der KI</span>
        <span className="gov-meta">{candidates.length} Kandidaten</span>
      </div>
      <p className="gov-hint">
        Neue Strategien der KI laufen zuerst als <b>Ghost-Trades</b> (reine Simulation ohne Kapital).
        Erst wenn die Schwellen erreicht sind und <b>du freigibst</b>, darf die Strategie handeln.
        News-getriebene und live nachjustierte Trades sind bewusst nicht backtestbar – dort bleibt
        die KI dynamisch.
      </p>

      {settings && (
        <div className="gov-rules">
          <label className="gov-check">
            <input type="checkbox" checked={!!settings.allow_ai_create}
              onChange={e => saveSettings({ allow_ai_create: e.target.checked })}
              data-testid="strategy-allow-ai-create" />
            <span>KI darf neue Strategien vorschlagen</span>
          </label>
          <label>
            <span>Min. Ghost-Trades</span>
            <input type="number" min="3" max="200" value={settings.min_ghost_trades}
              onChange={e => saveSettings({ min_ghost_trades: Number(e.target.value) })}
              data-testid="strategy-min-ghost-trades" />
          </label>
          <label>
            <span>Min. Ghost-Winrate %</span>
            <input type="number" min="30" max="95" value={settings.min_ghost_winrate}
              onChange={e => saveSettings({ min_ghost_winrate: Number(e.target.value) })}
              data-testid="strategy-min-ghost-winrate" />
          </label>
          <label>
            <span>Nach Freigabe</span>
            <select value={settings.promote_to}
              onChange={e => saveSettings({ promote_to: e.target.value })}
              data-testid="strategy-promote-to">
              <option value="paper">Paper handeln</option>
              <option value="live">Live erlaubt</option>
            </select>
          </label>
        </div>
      )}

      <div className="gov-cands">
        {candidates.length === 0 && (
          <div className="gov-empty">Noch keine Kandidaten – die KI legt bei einer neuen Idee
            automatisch einen an, oder du gibst ihr unten selbst eine Strategie vor.</div>
        )}
        {candidates.map(c => {
          const g = c.stats?.ghost || {};
          return (
            <div className={`gov-card stage-${c.stage}`} key={c.id} data-testid={`strategy-card-${c.id}`}>
              <div className="gov-card-head">
                <b>{c.name}</b>
                <span className={`gov-stage stage-${c.stage}`}>{STAGE_LABEL[c.stage] || c.stage}</span>
              </div>
              {c.thesis && <div className="gov-card-text">{c.thesis}</div>}
              {c.rules_text && <div className="gov-card-rules">Regeln: {c.rules_text}</div>}
              {c.learned_from && <div className="gov-card-src">Gelernt von: {c.learned_from}</div>}
              <div className="gov-card-stats">
                <span><Ghost size={12} weight="fill" /> {g.trades || 0} Ghost-Trades</span>
                <span>Winrate <b>{g.win_rate || 0}%</b></span>
                <span>Summe <b>{g.pnl_pct || 0}%</b></span>
                <span>offen {g.open || 0}</span>
                <span>{(c.symbols || []).join(', ') || 'alle Coins'}</span>
              </div>
              <div className="gov-card-actions">
                {c.stage !== 'live' && (
                  <button className="gov-btn primary" disabled={busy}
                    onClick={() => decide(c.id, 'approve')}
                    data-testid={`strategy-approve-${c.id}`}>
                    <CheckCircle size={13} weight="bold" /> Freigeben ({settings?.promote_to || 'paper'})
                  </button>
                )}
                {c.stage !== 'live' && (
                  <button className="gov-btn" disabled={busy}
                    onClick={() => decide(c.id, 'approve_live')}
                    data-testid={`strategy-approve-live-${c.id}`}>
                    Live freigeben
                  </button>
                )}
                {c.stage !== 'rejected' && (
                  <button className="gov-btn danger" disabled={busy}
                    onClick={() => decide(c.id, 'reject')}
                    data-testid={`strategy-reject-${c.id}`}>
                    <XCircle size={13} weight="bold" /> Ablehnen
                  </button>
                )}
                {c.stage === 'rejected' && (
                  <button className="gov-btn" disabled={busy}
                    onClick={() => decide(c.id, 'reset')}
                    data-testid={`strategy-reset-${c.id}`}>
                    <ArrowCounterClockwise size={13} weight="bold" /> Zurück in Ghost
                  </button>
                )}
                <button className="gov-btn" onClick={() => registerTest(c.id)}
                  data-testid={`strategy-register-test-${c.id}`}>
                  <ChartLine size={13} weight="bold" /> Für Backtest registrieren
                </button>
                <button className="gov-btn" onClick={() => loadGhosts(c.id)}
                  data-testid={`strategy-ghosts-${c.id}`}>
                  Ghost-Trades
                </button>
              </div>
              {ghosts[c.id] && (
                <ul className="gov-ghost-list" data-testid={`strategy-ghost-list-${c.id}`}>
                  {ghosts[c.id].length === 0 && <li>(noch keine Ghost-Trades)</li>}
                  {ghosts[c.id].map(t => (
                    <li key={t.id}>
                      {t.symbol} {t.side} @ {t.entry} → {t.status === 'closed'
                        ? `${t.result} (${t.pnl_pct}%)` : 'offen'} · SL {t.sl} / TP {t.tp}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      <div className="gov-head gov-head-sub">
        <span className="gov-title">Eigene Strategie vorgeben</span>
      </div>
      <div className="gov-rules">
        <label>
          <span>Name</span>
          <input type="text" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
            data-testid="strategy-form-name" />
        </label>
        <label>
          <span>Coins (Komma, leer = alle)</span>
          <input type="text" value={form.symbols} onChange={e => setForm({ ...form, symbols: e.target.value })}
            placeholder="BTCUSDT,ETHUSDT" data-testid="strategy-form-symbols" />
        </label>
      </div>
      <textarea className="gov-textarea" rows={4} value={form.thesis}
        placeholder="Was soll die KI zusätzlich verfolgen? (Idee, Regeln, Bedingungen)"
        onChange={e => setForm({ ...form, thesis: e.target.value })}
        data-testid="strategy-form-thesis" />
      <div className="gov-actions">
        <button className="gov-btn primary" onClick={createCandidate} disabled={busy}
          data-testid="strategy-form-submit">Strategie anlegen</button>
      </div>
    </div>
  );
};

export default AIStrategyLabPanel;
