import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Play, Trash, ChartScatter, ArrowClockwise, Cloud, Desktop, Gear } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import SafeOverlay from './SafeOverlay';
import LocalWorkerPanel from './LocalWorkerPanel';
import TIMEFRAMES from '../constants/timeframes';
import EquityChart from './EquityChart';
import RegimeChart from './RegimeChart';
import RegimeOptimizePanel from './RegimeOptimizePanel';
import RegimeEngineSettings from './RegimeEngineSettings';
import RegimeValidation from './RegimeValidation';
import DynamicPanel from './DynamicPanel';
import { regimeColor } from '../lib/regimeColors';
import { fmtDate, fmtDateTime } from '../lib/time';
import './RegimeLab.css';

const NNFX_LABELS = { trend: 'NNFX: Trend', range: 'NNFX: Seitwärts', breakout: 'NNFX: Breakout' };

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

const DAY_OPTIONS = [30, 60, 90, 180, 360, 540, 720, 1080, 1440, 1800, 2160, 2880, 3600];
const STATE_KEY = 'regime_lab_ui_v1';
const loadState = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; } catch { return {}; } };

const scopeKey = (scope, symbol) => (scope === 'per_coin' ? `per_coin:${symbol}` : 'combined');

const M = ({ m }) => m ? (
  <span className="opt-small">
    PnL <b className={m.pnl >= 0 ? 'pos' : 'neg'}>{fmt(m.pnl)}</b> · WR <b>{fmt(m.win_rate, 1)}%</b> ·
    Trades <b>{m.trades}</b> · DD <b>{fmt(m.max_drawdown)}</b>
  </span>
) : null;

// ---------------- Regime-Karte (Label, Kennzahlen, behalten, Strategie-Suche) ----------------
function RegimeCard({ analysis, scope, symbol, regime, usage, strategies, jobBlocked, execution, model, onChanged }) {
  const [showOpt, setShowOpt] = useState(false);
  const key = `${scopeKey(scope, symbol)}:${regime.id}`;
  const kept = (analysis.kept || {})[key] !== false;
  const assignment = (analysis.assignments || {})[key];
  const st = regime.stats || {};

  const toggleKeep = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/keep`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ scope, symbol, regime_id: regime.id, keep: !kept }),
    });
    if (r.ok) onChanged(); else toast.error('Speichern fehlgeschlagen');
  };

  const removeAssignment = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/assign`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ scope, symbol, regime_id: regime.id, remove: true }),
    });
    if (r.ok) { toast.success('Zuordnung entfernt'); onChanged(); } else toast.error('Fehlgeschlagen');
  };

  return (
    <div className={`rl-regime-card ${kept ? '' : 'discarded'} ${assignment ? 'assigned' : ''}`}
      style={{ borderLeft: `3px solid ${regimeColor(regime.id, [regime], model)}` }}
      data-testid={`regime-card-${scope}-${regime.id}`}>
      <div className="rl-regime-head">
        <span className="rl-dot" style={{ background: regimeColor(regime.id, [regime], model) }} />
        <label className="opt-check" style={{ paddingBottom: 0 }} title="Verworfene Regime werden bei Strategie-Suche und Zusammenbau übersprungen">
          <input type="checkbox" checked={kept} onChange={toggleKeep}
            data-testid={`regime-keep-${scope}-${regime.id}`} /> behalten
        </label>
        <b style={{ fontSize: 12.5 }}>#{regime.id + 1} {regime.label}</b>
        {regime.nnfx && (
          <span className={`rl-nnfx-tag ${regime.nnfx}`} title="Zuordnung im NNFX-Framework (3 Regime)"
            data-testid={`regime-nnfx-${scope}-${regime.id}`}>
            {NNFX_LABELS[regime.nnfx] || regime.nnfx}
          </span>
        )}
        <span className="opt-small">Anteil <b>{fmt(regime.share_pct, 0)}%</b></span>
        {usage && <span className="opt-small">· <b>{usage.days}</b> Tage in <b>{usage.segments}</b> Abschnitten</span>}
        <span style={{ flex: 1 }} />
        {kept && (
          <button className="opt-chip" onClick={() => setShowOpt(!showOpt)}
            data-testid={`regime-optimize-toggle-${scope}-${regime.id}`}>
            {showOpt ? 'Suche schließen' : (assignment ? 'Neue Strategie suchen' : 'Strategie suchen')}
          </button>
        )}
      </div>
      <div className="rl-regime-stats">
        <span title="Durchschnittliche Bewegung pro Tag in dieser Phase">Trend/Tag <b className={st.trend_pct_per_day >= 0 ? 'pos' : 'neg'}>{fmt(st.trend_pct_per_day, 2)}%</b></span>
        <span title="Verhältnis |Trend| zu Volatilität – unter ~0.45 gilt die Phase als seitwärts">Trendstärke <b>{fmt(st.trend_strength, 2)}</b></span>
        <span title="0 = reines Hin und Her, 1 = gerade Linie">Effizienz <b>{fmt(st.efficiency, 2)}</b></span>
        <span>Volatilität <b>{fmt(st.vol_pct, 2)}%</b></span>
        {st.score !== undefined && (
          <span title="Trend-Score = gewichteter t-Wert der Regression über alle Horizonte (|t|>2 = belegter Trend)">
            Trend-Score <b className={st.score >= 0 ? 'pos' : 'neg'}>{fmt(st.score, 2)}</b>
          </span>
        )}
        {st.adx !== undefined && <span title="Durchschnittlicher ADX in dieser Phase">ADX <b>{fmt(st.adx, 1)}</b></span>}
        {st.agreement !== undefined && (
          <span title="Anteil der Zeit-Horizonte, die dieselbe Richtung zeigen">
            Konsens <b>{fmt(st.agreement * 100, 0)}%</b>
          </span>
        )}
        {st.vol_z !== undefined && <span title="Volatilität gegen die eigene Historie (z-Wert)">Vola z <b>{fmt(st.vol_z, 2)}</b></span>}
        <span>Rel. Volumen <b>{fmt((regime.features || {}).rel_volume, 2)}</b></span>
      </div>
      {assignment && (
        <div className="rl-assign" data-testid={`regime-assignment-${scope}-${regime.id}`}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <b>✓ Bestätigte Strategie</b>
            <span className="opt-small">{assignment.mode === 'params'
              ? (assignment.strategy_name || assignment.strategy_id)
              : `Eigene Regeln (${(assignment.rules || []).length})`}</span>
            <M m={assignment.metrics} />
            {assignment.validation && <span className="opt-small pos">WF-PnL {fmt(assignment.validation.pnl)}</span>}
            <span style={{ flex: 1 }} />
            <button className="opt-chip" onClick={removeAssignment} data-testid={`regime-assignment-remove-${scope}-${regime.id}`}>
              <Trash size={11} /> entfernen
            </button>
          </div>
          {(assignment.rules || []).length > 0 && (
            <div className="opt-params-list" style={{ marginTop: 4, marginBottom: 0 }}>
              {assignment.rules.map((r, i) => <span key={i} className="opt-param-pill">{r}</span>)}
            </div>
          )}
          {Object.keys(assignment.trade_params || {}).length > 0 && (
            <div className="opt-params-list" style={{ marginTop: 4, marginBottom: 0 }}>
              {Object.entries(assignment.trade_params).map(([k, v]) =>
                <span key={k} className="opt-param-pill trade">{k}: <b>{String(v)}</b></span>)}
            </div>
          )}
        </div>
      )}
      {showOpt && kept && (
        <RegimeOptimizePanel analysisId={analysis.id} scope={scope} symbol={symbol}
          regime={regime} strategies={strategies} analysisTf={analysis.timeframe}
          jobBlocked={jobBlocked} execution={execution}
          onAssigned={() => { setShowOpt(false); onChanged(); }} />
      )}
    </div>
  );
}

// ---------------- Zusammenbau + finaler Walk-Forward ----------------
function BuildAndTest({ analysis, scope, symbol, strategies, jobBlocked, execution, onChanged }) {
  const [name, setName] = useState('');
  const [baseStrategy, setBaseStrategy] = useState('');
  const [busy, setBusy] = useState(false);
  const [wfJob, setWfJob] = useState(null);
  const [wfResult, setWfResult] = useState(null);
  const pollRef = useRef(null);
  useEffect(() => () => clearInterval(pollRef.current), []);

  const key = scopeKey(scope, symbol);
  const model = scope === 'per_coin' ? analysis.per_coin?.[symbol]?.model : analysis.combined?.model;
  const regimes = model?.regimes || [];
  const keptRegimes = regimes.filter(r => (analysis.kept || {})[`${key}:${r.id}`] !== false);
  const assignments = Object.keys(analysis.assignments || {}).filter(k => k.startsWith(key + ':'));
  const savedWf = (analysis.walkforward || {})[key];
  const trainPct = analysis.settings?.train_pct ?? 100;
  const hasHoldout = trainPct < 100;
  const needsBase = keptRegimes.some(r => {
    const a = (analysis.assignments || {})[`${key}:${r.id}`];
    return a && !a.definition;
  }) || assignments.some(k => !(analysis.assignments[k] || {}).definition);

  const build = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/build`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ scope, symbol, name: name || undefined, strategy_id: baseStrategy || undefined }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Erstellen fehlgeschlagen'); return; }
      toast.success(`Dynamische Strategie erstellt (${d.regimes.length} Regime) – unter "Dynamische Strategien" im Optimizer verfügbar`);
    } catch { toast.error('Verbindungsfehler'); }
    finally { setBusy(false); }
  };

  const buildNnfx = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/build-nnfx`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ scope, symbol, name: name || undefined }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'NNFX-Aufbau fehlgeschlagen'); return; }
      toast.success(`NNFX-Strategie erstellt: ${Object.keys(d.regime_strategies).length} Regime → `
        + `${Object.keys(d.nnfx_strategies).length} NNFX-Strategien. Jedes Regime hat jetzt eine Zuordnung `
        + '– Feinjustierung je Regime weiterhin über "Strategie suchen".');
      onChanged();
    } catch { toast.error('Verbindungsfehler'); }
    finally { setBusy(false); }
  };

  const runWf = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/walkforward`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ scope, symbol, strategy_id: baseStrategy || undefined, execution }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
      setWfResult(null);
      setWfJob({ id: d.job_id, status: 'running', progress: 0, phase: 'Startet' });
      pollRef.current = setInterval(async () => {
        try {
          const j = await fetch(`${API_URL}/api/regime-lab/status/${d.job_id}`).then(x => x.json());
          setWfJob(j);
          if (j.status !== 'running') {
            clearInterval(pollRef.current);
            if (j.status === 'done') { setWfResult(j.result); onChanged(); }
            else if (j.status === 'error') toast.error(j.error || 'Walk-Forward fehlgeschlagen');
          }
        } catch { /* transient */ }
      }, 1500);
    } catch { toast.error('Verbindungsfehler'); }
  };

  const wf = wfResult || savedWf;
  return (
    <div className="rl-wf-box" data-testid={`regime-build-${scope}`}>
      <div className="opt-section-title">DYNAMISCHE STRATEGIE ZUSAMMENSTELLEN</div>
      <div className="opt-small" style={{ marginBottom: 8 }}>
        {assignments.length} von {keptRegimes.length} behaltenen Regimen haben eine bestätigte Strategie.
        {!hasHoldout && ' Hinweis: Diese Analyse hat keinen Holdout (Training 100%) – für den finalen Walk-Forward-Test eine Analyse mit z.B. 75% Training erstellen.'}
      </div>
      <div className="opt-setup">
        <label className="opt-field">Name
          <input value={name} onChange={e => setName(e.target.value)} placeholder={`Regime-Lab: ${analysis.name}`}
            data-testid={`regime-build-name-${scope}`} style={{ width: 220 }} />
        </label>
        <label className="opt-field" title={needsBase
          ? 'Erforderlich: mindestens ein Regime nutzt eine bestehende Strategie ohne eigene Regeln'
          : 'Optional: Basis-Strategie für Regime ohne Zuordnung'}>
          Basis-Strategie {needsBase ? '(erforderlich)' : '(optional)'}
          <select value={baseStrategy} onChange={e => setBaseStrategy(e.target.value)}
            data-testid={`regime-build-base-${scope}`}>
            <option value="">– automatisch –</option>
            {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <button className="opt-apply" onClick={build} disabled={busy || !assignments.length}
          data-testid={`regime-build-btn-${scope}`}>
          Dynamische Strategie erstellen
        </button>
        <button className="opt-apply" onClick={runWf}
          disabled={!assignments.length || !hasHoldout || wfJob?.status === 'running' || jobBlocked}
          title="Testet die Kombination auf dem unangetasteten Holdout – Klassifikation rückblickend, kein Lookahead"
          data-testid={`regime-wf-btn-${scope}`}>
          <Play size={13} /> Finaler Walk-Forward (Holdout)
        </button>
        <button className="opt-apply" onClick={buildNnfx} disabled={busy || !keptRegimes.length}
          title="NNFX-Framework: die Regime werden auf Trend / Seitwärts / Volatilität gemappt und automatisch mit den drei NNFX-Strategien belegt (danach je Regime optimierbar)"
          data-testid={`regime-nnfx-btn-${scope}`}>
          NNFX-Framework anwenden
        </button>
      </div>
      {wfJob?.status === 'running' && (
        <div className="opt-progress">
          <div className="opt-progress-bar"><div style={{ width: `${wfJob.progress || 0}%`, height: '100%', background: '#00e5a0' }} /></div>
          <div className="opt-progress-text">{wfJob.phase} · {wfJob.progress || 0}%</div>
        </div>
      )}
      {wf && (
        <div data-testid={`regime-wf-result-${scope}`}>
          <div className={`rl-verdict ${wf.verdict?.dynamic_better ? 'good' : 'bad'}`}>
            {wf.verdict?.recommendation}
          </div>
          <div className="opt-compare">
            <div className="opt-card best">
              <div className="opt-card-title">DYNAMISCH (HOLDOUT · {wf.switches} Phasenwechsel)</div>
              <M m={wf.dynamic_test} />
            </div>
            <div className="opt-card">
              <div className="opt-card-title">BESTE EINZELSTRATEGIE STATISCH (BENCHMARK)</div>
              <M m={wf.best_single?.metrics} />
              {wf.best_single && <div className="opt-small" style={{ marginTop: 4 }}>{wf.best_single.label}</div>}
            </div>
          </div>
          {(wf.per_regime || []).length > 0 && (
            <div className="opt-small" style={{ margin: '6px 0' }}>
              Je Regime im Holdout: {wf.per_regime.map(p => (
                <span key={p.regime} className="opt-param-pill" style={{ marginRight: 4 }}>
                  {p.label || `#${p.regime + 1}`}: <b className={p.metrics.pnl >= 0 ? 'pos' : 'neg'}>{fmt(p.metrics.pnl)}</b> ({p.metrics.trades} T.)
                </span>
              ))}
            </div>
          )}
          {wfResult?.points?.length > 0 && (
            <EquityChart points={wfResult.points} title="Equity im Holdout (dynamisch)" />
          )}
        </div>
      )}
    </div>
  );
}

// ---------------- Detail einer Analyse ----------------
function AnalysisDetail({ analysis, strategies, jobBlocked, execution, onChanged }) {
  const scopes = [];
  if (analysis.combined) scopes.push({ id: 'combined', label: 'Alle Coins (kombiniert)' });
  (analysis.symbols || []).forEach(s => {
    if (analysis.per_coin?.[s] && !analysis.per_coin[s].error) {
      scopes.push({ id: `coin:${s}`, label: s.replace('USDT', '') });
    }
  });
  const [tab, setTab] = useState(scopes[0]?.id || 'combined');
  const isCombined = tab === 'combined';
  const symbol = isCombined ? null : tab.slice(5);
  const scope = isCombined ? 'combined' : 'per_coin';
  const model = isCombined ? analysis.combined?.model : analysis.per_coin?.[symbol]?.model;
  const usage = isCombined ? analysis.combined?.usage : analysis.per_coin?.[symbol]?.usage;
  const regimes = model?.regimes || [];
  const trainEnd = (sym) => analysis.bounds?.[sym]?.train_end_ts;
  const perSymbol = isCombined
    ? (analysis.combined?.per_symbol || {})
    : (symbol ? { [symbol]: analysis.per_coin?.[symbol] || {} } : {});
  const validationSummary = isCombined
    ? analysis.combined?.validation
    : (analysis.per_coin?.[symbol]?.validation);
  const engine = model?.engine || 'kmeans';
  const [showIdeal, setShowIdeal] = useState(false);

  const currentBadges = Object.entries(perSymbol)
    .map(([sym, v]) => [sym, v?.current])
    .filter(([, c]) => c && c.regime !== null && c.regime !== undefined);

  return (
    <div data-testid="regime-analysis-detail">
      <div className="opt-small" style={{ margin: '4px 0 8px' }}>
        {analysis.timeframe} · {analysis.days} Tage · Training {analysis.settings?.train_pct}%
        {analysis.settings?.train_pct < 100 && ' (Rest = Holdout für den finalen Walk-Forward)'} ·
        {engine === 'v2'
          ? ` Engine v2 · ${(model?.regime_mode || model?.config?.regime_mode || 9)}-Regime-Modus`
            + ` · Horizonte ${(model?.config?.horizons_days || []).map(d => `${d}d`).join('/')}`
            + (model?.adapt?.profile ? ` · Glättung "${model.adapt.profile}"` : '')
            + ` · Mindesthaltedauer ${fmt(model?.config?.min_hold_days, 1)}d`
            + ` · Trend-Schwelle t=${model?.config?.trend_t} · ADX≥${model?.config?.adx_min}`
          : ` Cluster-Modell · Lookback ${analysis.settings?.lookback_days}d · max. ${analysis.settings?.max_regimes} Regime · Cluster-Qualität ${fmt(model?.silhouette, 2)}`}
      </div>
      {model?.adapt?.report?.candidates?.length > 0 && (
        <div className="rl-sim" data-testid="regime-adapt-report">
          <span className="opt-small" style={{ alignSelf: 'center' }}
            title="Es wurden mehrere Glättungs-Profile berechnet und nach Plausibilität, Rückblick-Übereinstimmung und Abschnittslänge bewertet. Das beste wurde verwendet.">
            Glättungs-Profile geprüft:
          </span>
          {model.adapt.report.candidates.map(c => (
            <span key={c.profile} className="opt-param-pill"
              style={c.profile === model.adapt.profile
                ? { borderColor: 'rgba(0,229,160,0.5)' } : undefined}
              title={`Verstöße ${c.violation_bars_pct}% · Rückblick-Treffer ${c.direction_pct}% · Ø Abschnitt ${c.avg_segment_days}d (Ziel ${c.target_segment_days}d)${c.passes === false ? ' · Plausibilitätsprüfung NICHT bestanden' : ''}`}>
              {c.profile} <b>{fmt(c.quality, 1)}</b>{c.passes === false ? ' ✕' : ''}
            </span>
          ))}
        </div>
      )}
      {currentBadges.length > 0 && (
        <div className="rl-current-row" data-testid="regime-current-row">
          {currentBadges.map(([sym, c]) => (
            <div key={sym} className="rl-current" data-testid={`regime-current-${sym}`}
              title={c.reason || ''}>
              <span className="rl-dot" style={{ background: regimeColor(c.regime, regimes, model) }} />
              <b>{sym.replace('USDT', '')}</b>
              <span className="rl-current-label">{c.label}</span>
              {c.nnfx && <span className={`rl-nnfx-tag ${c.nnfx}`}>{NNFX_LABELS[c.nnfx] || c.nnfx}</span>}
              <span className="opt-small">Sicherheit <b>{fmt(c.confidence, 0)}%</b></span>
              {c.strength && <span className="opt-small">Stärke <b>{c.strength}</b></span>}
              {c.last_switch && (
                <span className="opt-small">seit {fmtDate(c.last_switch)}</span>
              )}
              {c.early_warning?.active && (
                <span className="rl-warn" title={c.early_warning.reason}
                  data-testid={`regime-warn-${sym}`}>
                  Wechsel → {c.early_warning.next_label} · {fmt(c.early_warning.probability_pct, 0)}%
                  {c.early_warning.eta_days !== null && c.early_warning.eta_days !== undefined
                    ? ` · ~${c.early_warning.eta_days} Tage` : ''}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      <RegimeValidation summary={validationSummary} perSymbol={perSymbol} />
      {scopes.length > 1 && (
        <div className="rl-scope-tabs">
          {scopes.map(s => (
            <button key={s.id} className={`opt-chip ${tab === s.id ? 'on' : ''}`}
              onClick={() => setTab(s.id)} data-testid={`regime-scope-tab-${s.id}`}>{s.label}</button>
          ))}
        </div>
      )}
      {isCombined && (analysis.combined?.coin_similarity || []).length > 0 && (
        <div className="rl-sim" data-testid="regime-coin-similarity">
          <span className="opt-small" style={{ alignSelf: 'center' }}
            title="Anteil der Zeit, in der zwei Coins im selben Regime sind – Coins mit hoher Übereinstimmung passen gut in eine gemeinsame dynamische Strategie">
            Regime-Übereinstimmung:
          </span>
          {analysis.combined.coin_similarity.map((s, i) => (
            <span key={i} className="opt-param-pill"
              style={s.agreement_pct >= 70 ? { borderColor: 'rgba(0,229,160,0.4)' } : undefined}>
              {s.a.replace('USDT', '')}↔{s.b.replace('USDT', '')} <b>{fmt(s.agreement_pct, 0)}%</b>
            </span>
          ))}
        </div>
      )}
      {isCombined
        ? (analysis.symbols || []).map(sym => (
          <RegimeChart key={sym} title={sym.replace('USDT', '')}
            prices={analysis.chart?.[sym]}
            segments={analysis.combined?.per_symbol?.[sym]?.segments}
            idealSegments={showIdeal ? analysis.combined?.per_symbol?.[sym]?.ideal?.segments : null}
            regimes={regimes} model={model} trainEndTs={trainEnd(sym)} />
        ))
        : (
          <RegimeChart title={symbol.replace('USDT', '')}
            prices={analysis.chart?.[symbol]}
            segments={analysis.per_coin?.[symbol]?.segments}
            idealSegments={showIdeal ? analysis.per_coin?.[symbol]?.ideal?.segments : null}
            regimes={regimes} model={model} trainEndTs={trainEnd(symbol)} height={240} />
        )}
      {Object.values(perSymbol).some(v => v?.ideal?.segments?.length) && (
        <label className="opt-check" style={{ paddingBottom: 0 }}
          title="Vergleichsband: so lagen die Phasen im Rückblick (mit Zukunftssicht) – nur zur Kontrolle, wird nie für Backtests/Live genutzt">
          <input type="checkbox" checked={showIdeal} onChange={e => setShowIdeal(e.target.checked)}
            data-testid="regime-show-ideal" /> Rückblick-Vergleich einblenden (nur Kontrolle)
        </label>
      )}
      <div className="opt-section-title">REGIME PRÜFEN, BEHALTEN & STRATEGIEN SUCHEN</div>
      {regimes.map(r => (
        <RegimeCard key={r.id} analysis={analysis} scope={scope} symbol={symbol}
          regime={r} usage={usage?.[String(r.id)]} strategies={strategies}
          jobBlocked={jobBlocked} execution={execution} model={model}
          onChanged={onChanged} />
      ))}
      <BuildAndTest analysis={analysis} scope={scope} symbol={symbol}
        strategies={strategies} jobBlocked={jobBlocked} execution={execution}
        onChanged={onChanged} />
    </div>
  );
}

// ---------------- Haupt-Panel ----------------
export default function RegimeLab({ onClose }) {
  const saved = useRef(loadState()).current;
  const [coins, setCoins] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [selCoins, setSelCoins] = useState(saved.selCoins || []);
  const [timeframe, setTimeframe] = useState(saved.timeframe || '15m');
  const [days, setDays] = useState(saved.days ?? 360);
  const [scope, setScope] = useState(saved.scope || 'both');
  const [maxRegimes, setMaxRegimes] = useState(saved.maxRegimes ?? 5);
  const [lookback, setLookback] = useState(saved.lookback ?? 3);
  const [minShare, setMinShare] = useState(saved.minShare ?? 5);
  const [confMin, setConfMin] = useState(saved.confMin ?? 70);
  const [minHold, setMinHold] = useState(saved.minHold ?? 2);
  const [trainPct, setTrainPct] = useState(saved.trainPct ?? 75);
  const [engine, setEngine] = useState(saved.engine || 'v2');
  const [engineConfig, setEngineConfig] = useState(saved.engineConfig || {});
  const [execution, setExecution] = useState(saved.execution || 'cloud');
  const [lwOnline, setLwOnline] = useState(false);
  const [showLW, setShowLW] = useState(false);
  const [name, setName] = useState('');
  const [job, setJob] = useState(null);
  const [analyses, setAnalyses] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify({
        selCoins, timeframe, days, scope, maxRegimes, lookback, minShare, confMin, minHold, trainPct, execution,
        engine, engineConfig,
      }));
    } catch { /* quota */ }
  }, [selCoins, timeframe, days, scope, maxRegimes, lookback, minShare, confMin, minHold, trainPct, execution,
    engine, engineConfig]);

  const loadList = useCallback(() => {
    fetch(`${API_URL}/api/regime-lab/list`).then(r => r.json())
      .then(d => setAnalyses(d.analyses || [])).catch(() => setAnalyses([]));
  }, []);

  const loadDetail = useCallback((aid) => {
    fetch(`${API_URL}/api/regime-lab/${aid}`).then(r => r.json())
      .then(d => setDetail(d.analysis)).catch(() => toast.error('Analyse konnte nicht geladen werden'));
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/api/coins`).then(r => r.json()).then(d => {
      const cs = d.coins || [];
      setCoins(cs);
      setSelCoins(prev => {
        const valid = (prev || []).filter(c => cs.includes(c));
        return valid.length ? valid : cs.slice(0, 4);
      });
    });
    fetch(`${API_URL}/api/strategies`).then(r => r.json()).then(d => setStrategies(d.strategies || []));
    fetch(`${API_URL}/api/regime-lab/active`).then(r => r.json()).then(d => {
      if (d.active) attachPoll(d.active.id, d.active.kind);
    }).catch(() => {});
    loadList();
    const checkLw = () => fetch(`${API_URL}/api/localworker/status`).then(r => r.json())
      .then(d => setLwOnline(!!d.online)).catch(() => setLwOnline(false));
    checkLw();
    const lwIv = setInterval(checkLw, 10000);
    return () => { clearInterval(pollRef.current); clearInterval(lwIv); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { if (selected) loadDetail(selected); }, [selected, loadDetail]);

  const attachPoll = (jobId, kind) => {
    setJob({ id: jobId, kind, status: 'running', progress: 0, phase: 'Läuft...' });
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetch(`${API_URL}/api/regime-lab/status/${jobId}`).then(x => x.json());
        setJob(j);
        if (j.status !== 'running') {
          clearInterval(pollRef.current);
          if (j.status === 'done' && j.kind === 'analysis') {
            toast.success('Regime-Analyse fertig');
            loadList();
            if (j.result?.analysis_id) setSelected(j.result.analysis_id);
          } else if (j.status === 'error') {
            toast.error(j.error || 'Job fehlgeschlagen');
          }
          setTimeout(() => setJob(null), 4000);
        }
      } catch { /* transient */ }
    }, 1500);
  };

  const startAnalysis = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!selCoins.length) { toast.error('Mindestens 1 Coin wählen'); return; }
    if (execution === 'local' && !lwOnline) {
      toast.error('Kein lokaler Worker verbunden – Worker starten oder Cloud wählen');
      return;
    }
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/analyze`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          symbols: selCoins, timeframe, days, scope, name: name || undefined,
          max_regimes: maxRegimes, lookback_days: lookback, min_share_pct: minShare,
          confidence_min: confMin, min_hold_days: minHold, train_pct: trainPct,
          engine, engine_config: engine === 'v2' ? engineConfig : undefined,
          execution,
        }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
      attachPoll(d.job_id, 'analysis');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const cancelJob = async () => {
    if (job?.id) await fetch(`${API_URL}/api/regime-lab/cancel/${job.id}`, { method: 'POST', headers: authHeaders() });
  };

  const removeAnalysis = async (aid, e) => {
    e.stopPropagation();
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-lab/${aid}`, { method: 'DELETE', headers: authHeaders() });
    if (r.ok) {
      toast.success('Analyse gelöscht');
      if (selected === aid) { setSelected(null); setDetail(null); }
      loadList();
    } else toast.error('Löschen fehlgeschlagen');
  };

  const jobBlocked = job?.status === 'running';
  return (
    <SafeOverlay className="opt-overlay" onClose={onClose} testId="regime-lab-overlay">
      <div className="opt-panel" onClick={e => e.stopPropagation()} data-testid="regime-lab-modal">
        <div className="opt-header">
          <h2 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ChartScatter size={20} weight="bold" /> Regime-Lab
          </h2>
          <button className="opt-close" onClick={onClose} data-testid="regime-lab-close"><X size={22} weight="bold" /></button>
        </div>
        <div className="opt-small" style={{ marginBottom: 12 }}>
          Workflow: 1) Regime für eine Konfiguration suchen & speichern → 2) Regime am Chart prüfen,
          unsinnige verwerfen → 3) je Regime mit Discovery/Optimierer eine Strategie suchen & bestätigen →
          4) dynamische Strategie zusammenstellen und auf dem unangetasteten Holdout per Walk-Forward testen (kein Lookahead).
        </div>

        <div className="opt-exec-row" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', margin: '0 0 10px' }}>
          <div className="bt-exec" data-testid="regime-execution-toggle">
            <span className="bt-exec-label">Ausführung</span>
            <button className={`bt-exec-btn ${execution === 'cloud' ? 'on' : ''}`}
              onClick={() => setExecution('cloud')} data-testid="regime-exec-cloud"
              title="Berechnung auf dem Server – für kurze Zeiträume ok">
              <Cloud size={13} weight="bold" /> Cloud
            </button>
            <button className={`bt-exec-btn ${execution === 'local' ? 'on' : ''}`}
              onClick={() => setExecution('local')} data-testid="regime-exec-local"
              title="Berechnung auf deinem PC (lokaler Worker, Multi-Core + lokale Kerzendaten) – empfohlen ab ~1000 Tagen, entlastet die Website. Worker-Version 1.5.0+ nötig.">
              <Desktop size={13} weight="bold" /> Lokal
              <span className={`bt-exec-dot ${lwOnline ? 'on' : ''}`} data-testid="regime-exec-dot" />
            </button>
            <button className="bt-exec-manage" onClick={() => setShowLW(true)}
              title="Lokale Ausführung verwalten: Worker, Einstellungen & Marktdaten"
              data-testid="regime-exec-manage">
              <Gear size={13} weight="bold" />
            </button>
          </div>
          {execution === 'local' && (
            <span className="opt-small">
              Gilt für alle Regime-Lab-Jobs (Analyse, Strategie-Suche, Walk-Forward)
              {!lwOnline && ' · kein Worker verbunden'}
            </span>
          )}
        </div>
        {showLW && <LocalWorkerPanel onClose={() => setShowLW(false)} />}

        <div className="opt-row">
          <div className="opt-label">1 · NEUE REGIME-ANALYSE</div>
          <div className="opt-chips" style={{ marginBottom: 8 }}>
            {coins.map(c => (
              <button key={c} className={`opt-chip ${selCoins.includes(c) ? 'on' : ''}`}
                onClick={() => setSelCoins(selCoins.includes(c) ? selCoins.filter(x => x !== c) : [...selCoins, c])}
                data-testid={`regime-coin-${c}`}>{c.replace('USDT', '')}</button>
            ))}
          </div>
          <RegimeEngineSettings engine={engine} setEngine={setEngine}
            config={engineConfig} setConfig={setEngineConfig}
            calibrateCtx={{ symbols: selCoins, timeframe, days, execution }} />
          <div className="opt-setup">
            <label className="opt-field">Name (optional)
              <input value={name} onChange={e => setName(e.target.value)} style={{ width: 170 }}
                placeholder="z.B. 15m · 360d · Top4" data-testid="regime-name" />
            </label>
            <label className="opt-field">Timeframe
              <select value={timeframe} onChange={e => setTimeframe(e.target.value)} data-testid="regime-tf">
                {TIMEFRAMES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
              </select>
            </label>
            <label className="opt-field">Zeitraum
              <select value={days} onChange={e => setDays(parseInt(e.target.value))} data-testid="regime-days">
                {DAY_OPTIONS.map(d => <option key={d} value={d}>{d} Tage</option>)}
              </select>
            </label>
            <label className="opt-field" title="Kombiniert = ein Modell über alle Coins · Je Coin = eigenes Modell pro Coin · Beides = beide Varianten zum Vergleichen">
              Modell-Umfang
              <select value={scope} onChange={e => setScope(e.target.value)} data-testid="regime-scope">
                <option value="both">Beides (kombiniert + je Coin)</option>
                <option value="combined">Nur kombiniert (alle Coins)</option>
                <option value="per_coin">Nur je Coin einzeln</option>
              </select>
            </label>
            <label className="opt-field" title="Vorderer Anteil für Regime-Clustering & Strategie-Suche; der Rest bleibt unangetastet für den finalen Walk-Forward">
              Training %
              <input type="number" min={50} max={100} value={trainPct}
                onChange={e => setTrainPct(parseInt(e.target.value) || 75)}
                data-testid="regime-trainpct" style={{ width: 55 }} />
            </label>
            {engine === 'kmeans' && (
              <>
                <label className="opt-field" title="Fenster für Trend/Volatilität/Effizienz – größer = trägere, stabilere Regime">
                  Lookback (Tage)
                  <input type="number" min={0.5} max={60} step={0.5} value={lookback}
                    onChange={e => setLookback(parseFloat(e.target.value) || 3)}
                    data-testid="regime-lookback" style={{ width: 55 }} />
                </label>
                <label className="opt-field">Max. Regime
                  <input type="number" min={2} max={10} value={maxRegimes}
                    onChange={e => setMaxRegimes(parseInt(e.target.value) || 5)}
                    data-testid="regime-max" style={{ width: 50 }} />
                </label>
                <label className="opt-field" title="Regime mit kleinerem Anteil werden zusammengelegt">
                  Min. Anteil %
                  <input type="number" min={1} max={30} value={minShare}
                    onChange={e => setMinShare(parseInt(e.target.value) || 5)}
                    data-testid="regime-minshare" style={{ width: 50 }} />
                </label>
              </>
            )}
            <label className="opt-field" title="Umschalten nur bei dieser Sicherheit (Anti-Flattern)">
              Sicherheit %
              <input type="number" min={50} max={95} value={confMin}
                onChange={e => setConfMin(parseInt(e.target.value) || 70)}
                data-testid="regime-confmin" style={{ width: 50 }} />
            </label>
            <label className="opt-field" title="Mindesthaltedauer eines Regimes in Tagen">
              Min. Haltezeit (d)
              <input type="number" min={0.25} max={60} step={0.25} value={minHold}
                onChange={e => setMinHold(parseFloat(e.target.value) || 2)}
                data-testid="regime-minhold" style={{ width: 55 }} />
            </label>
            <button className="opt-run" onClick={startAnalysis} disabled={jobBlocked} data-testid="regime-analyze-btn">
              <Play size={14} weight="fill" /> Regime suchen & speichern
            </button>
          </div>
          {job && (
            <div className="opt-progress">
              <div className="opt-progress-bar"><div style={{ width: `${job.progress || 0}%`, height: '100%', background: '#b388ff' }} /></div>
              <div className="opt-progress-row">
                <div className="opt-progress-text">
                  {job.kind === 'analysis' ? 'Analyse' : job.kind === 'regime_opt' ? 'Regime-Optimierung' : 'Walk-Forward'} ·
                  {' '}{job.phase} · {job.progress || 0}%
                </div>
                {job.status === 'running' && (
                  <button className="opt-cancel-run" onClick={cancelJob} data-testid="regime-job-cancel">Abbrechen</button>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="opt-row">
          <div className="opt-label" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            2 · GESPEICHERTE ANALYSEN
            <button className="opt-chip" style={{ fontSize: 10 }} onClick={loadList} data-testid="regime-list-refresh">
              <ArrowClockwise size={11} />
            </button>
          </div>
          {analyses === null && <div className="opt-small">Lade...</div>}
          {analyses !== null && analyses.length === 0 && (
            <div className="opt-small">Noch keine Analysen – oben Konfiguration wählen und "Regime suchen & speichern" starten.</div>
          )}
          {(analyses || []).map(a => (
            <div key={a.id} className={`rl-analysis-row ${selected === a.id ? 'on' : ''}`}
              onClick={() => setSelected(selected === a.id ? null : a.id)}
              data-testid={`regime-analysis-row-${a.id}`}>
              <b>{a.name}</b>
              <span className="opt-small">{(a.symbols || []).map(s => s.replace('USDT', '')).join(', ')}</span>
              <span className="opt-small">{a.timeframe} · {a.days}d · Training {a.settings?.train_pct}%</span>
              {a.n_regimes_combined > 0 && <span className="opt-small">{a.n_regimes_combined} Regime (kombiniert)</span>}
              {a.n_assignments > 0 && <span className="opt-small pos">{a.n_assignments} Strategie(n) bestätigt</span>}
              {a.has_walkforward && <span className="opt-small pos">WF getestet</span>}
              <span style={{ flex: 1 }} />
              <span className="opt-small">{fmtDateTime(a.created_at)}</span>
              <button className="opt-chip" onClick={(e) => removeAnalysis(a.id, e)} data-testid={`regime-analysis-delete-${a.id}`}>
                <Trash size={11} />
              </button>
            </div>
          ))}
        </div>

        {selected && detail && (
          <div className="opt-row">
            <div className="opt-label">3 · ANALYSE: {detail.name}</div>
            <AnalysisDetail analysis={detail} strategies={strategies}
              jobBlocked={jobBlocked} execution={execution} onChanged={() => loadDetail(selected)} />
          </div>
        )}

        <div className="opt-row">
          <div className="opt-label">4 · DYNAMISCHE STRATEGIEN (LIVE/PAPER)</div>
          <div className="opt-small" style={{ marginBottom: 6 }}>
            Aktuelles Regime je Coin, aktive Strategie, Auto-Umschaltung inkl. optionaler
            manueller Bestätigung und Wechsel-Protokoll – alles an einem Ort.
          </div>
          <DynamicPanel />
        </div>
      </div>
    </SafeOverlay>
  );
}
