import React, { useCallback, useEffect, useState } from 'react';
import { ShieldCheck, ArrowsClockwise, CheckCircle } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const VERDICT_CLASS = {
  gut: 'ok',
  'auffällig': 'warn',
  schwach: 'bad',
  inaktiv: 'idle',
};

const ACTION_LABEL = {
  keine: 'kein Handlungsbedarf',
  modell_wechseln: 'Modellwechsel empfohlen',
  einstellungen_pruefen: 'Einstellungen prüfen',
  deaktivieren: 'Rolle deaktivieren',
};

/**
 * Aufsicht des Haupt-Modells über das KI-Team: manuell startbare
 * Stichproben-Prüfung aller Rollen + Ergebnis-Bericht. Empfohlene
 * Modellwechsel übernimmt der Trader per Klick (`onApplyModel`).
 */
const AITeamSupervisor = ({ roleLabels = {}, onApplyModel }) => {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/ai/supervisor`).then(r => r.json());
      setState(data && typeof data === 'object' ? data : null);
      return data;
    } catch (e) { return null; }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Läuft eine Prüfung, alle 4s nachfragen bis der Bericht steht
  useEffect(() => {
    if (!busy && !state?.running) return undefined;
    const t = setInterval(async () => {
      const data = await load();
      if (data && !data.running) {
        setBusy(false);
        if (data.last_error) toast.error(data.last_error);
        else if (data.report) {
          toast.success(`Team-Prüfung fertig: ${data.report.roles?.length || 0} Rollen bewertet`);
        }
      }
    }, 4000);
    return () => clearInterval(t);
  }, [busy, state?.running, load]);

  const runReview = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/supervisor/review`, {
        method: 'POST', headers: authHeaders(),
      });
      const data = await res.json();
      if (!res.ok || (data.status !== 'started' && data.status !== 'busy')) {
        toast.error(data.detail || 'Team-Prüfung fehlgeschlagen');
        setBusy(false);
        return;
      }
      toast.message('Das Haupt-Modell prüft jetzt das KI-Team – das dauert ein bis zwei Minuten.');
      load();
    } catch (e) { toast.error('Verbindungsfehler'); setBusy(false); }
  };

  const report = state?.report;
  const fmt = (ts) => {
    try {
      return new Date(ts).toLocaleString('de-DE', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
        timeZone: 'Europe/Berlin',
      });
    } catch { return ''; }
  };

  return (
    <div className="ai-supervisor" data-testid="ai-supervisor-panel">
      <div className="ai-supervisor-head">
        <span className="ai-supervisor-title">
          <ShieldCheck size={14} weight="fill" /> Aufsicht des Haupt-Modells
          {report?.ts ? ` · zuletzt ${fmt(report.ts)}` : ''}
          {report?.model ? ` · ${report.model}` : ''}
        </span>
        <button className="ai-action-btn" onClick={runReview}
          disabled={busy || state?.running} data-testid="ai-supervisor-run-btn">
          <ArrowsClockwise size={13} weight="bold" className={busy || state?.running ? 'spin' : ''} />
          {busy || state?.running ? 'Prüft…' : 'KI-Team jetzt prüfen'}
        </button>
      </div>
      <div className="ai-team-hint">
        Das Haupt-Modell prüft stichprobenweise die Ausgaben jeder Rolle: arbeitet sie
        zuverlässig, ist die Qualität ausreichend oder sollte das Modell gewechselt werden?
        Angewendet wird nichts automatisch – du entscheidest.
      </div>
      {state?.last_error && (
        <div className="ai-warning" data-testid="ai-supervisor-error">⚠ {state.last_error}</div>
      )}
      {report?.summary && (
        <div className="ai-supervisor-summary" data-testid="ai-supervisor-summary">
          {report.summary}
        </div>
      )}
      {(report?.roles || []).length > 0 && (
        <div className="ai-supervisor-rows">
          {report.roles.map(r => (
            <div className={`ai-supervisor-row ${VERDICT_CLASS[r.verdict] || 'ok'}`}
              key={r.role} data-testid={`ai-supervisor-row-${r.role}`}>
              <span className="ai-sup-role">{roleLabels[r.role] || r.role}</span>
              <span className={`ai-sup-verdict ${VERDICT_CLASS[r.verdict] || 'ok'}`}>
                {r.verdict} · {r.score}
              </span>
              <span className="ai-sup-reason">{r.reason}</span>
              <span className="ai-sup-action">{ACTION_LABEL[r.action] || r.action}</span>
              {r.suggested_model && (
                <button className="ai-sup-apply"
                  onClick={() => onApplyModel && onApplyModel(r.role, {
                    provider: r.suggested_provider, model: r.suggested_model,
                  })}
                  title={`Modell ${r.suggested_provider}/${r.suggested_model} für diese Rolle übernehmen`}
                  data-testid={`ai-supervisor-apply-${r.role}`}>
                  <CheckCircle size={12} weight="bold" /> {r.suggested_model}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {(report?.recommendations || []).length > 0 && (
        <ul className="ai-lesson-list" data-testid="ai-supervisor-recommendations">
          {report.recommendations.map((rec, i) => <li key={i}>{rec}</li>)}
        </ul>
      )}
      {!report && (
        <div className="ai-learn-empty">
          Noch keine Prüfung gelaufen – starte sie über „KI-Team jetzt prüfen“.
        </div>
      )}
    </div>
  );
};

export default AITeamSupervisor;
