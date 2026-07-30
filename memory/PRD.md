# PRD – Krypto Daytrading Plattform (KI Trader Ausbau)

## Ausgangs-Problemstellung (Original, Juni 2026)
Bestehende, produktiv laufende externe Daytrading-Website (Repo `dean06greif-ai/Krypto_Trader`,
Branch `30.-19`), die verbessert werden soll. Grundsatz: sauber, modular, langfristig wartbar,
Stabilität + Rückwärtskompatibilität vor aggressiven Änderungen; vor größeren Änderungen
Architektur analysieren, Risiken identifizieren, Regressionstests schreiben.

Zu verbessern war der **KI Trader**:
1. **Bug:** Beim manuellen Schließen/Anpassen eines KI-Live-Trades passierte nur in der Website
   etwas – die echte Position auf Bitunix blieb unverändert.
2. **MasterPrompt**, den nur der Trader ändern kann; gilt als oberstes Gebot – keine Lektionen
   und keine Trades dagegen.
3. Nutzer soll Lektionen **löschen und per Stift bearbeiten** können; KI erkennt Trader-Edits.
4. Einstellungsänderungen und neue Lektionen der KI nur bei **ausreichender Datenvalidierung**;
   Trader-Wünsche gelten immer, die KI darf aber widersprechen.
5. KI soll wissen, dass sie **nur eine Strategie von vielen** ist und von den anderen
   Strategien/Parametern lernen.
6. Neue KI-Strategien erst nach **Ghost-/Paper-Tests + manueller Freigabe** live; Backtest/
   Optimizer nutzbar, aber news-getriebene und live nachjustierte Trades sind nicht testbar.

## Architektur (unverändert erweitert)
- **Backend** FastAPI (`/app/backend`): `server.py` (Bootstrap), `routers/` (alle `/api/...`),
  `services/` (Scanner, AutoTrader/Bitunix, KI-Engine + Rollen), `strategies/` (Registry,
  Custom-Strategien), MongoDB via `MONGO_URL`/`DB_NAME`.
- **Frontend** React (`/app/frontend/src`): `App.js`, `components/` (Chart, StrategyTabs,
  AITradingPanel + Sub-Panels).
- **Neu (modular ergänzt, keine bestehenden Flows verändert)**
  - `services/ai_master_prompt.py` – MasterPrompt-Store, Prompt-Injektion, harte Regeln
    (Trade-/Change-/Lesson-Gates, reine Prüf-Funktionen testbar)
  - `services/ai_lessons.py` – Lektionen-Store (ids, `origin`, `locked`), Merge mit
    Trader-Schutz, Prompt-Text mit Herkunfts-Markern
  - `services/ai_validation.py` – Datenbasis-Freigabe für KI-Änderungen/Lektionen
  - `services/ai_strategy_lab.py` – Kandidaten-Pipeline ghost → live_pending → Freigabe →
    paper/live, Ghost-Trade-Auswertung, Registrierung regelbasierter Ideen für Backtester
  - `routers/ai_governance.py` – `/api/ai/master-prompt`, `/api/ai/lessons`,
    `/api/ai/validation`, `/api/ai/strategies*`
  - Frontend: `AIGovernancePanel.js`, `AIStrategyLabPanel.js`, `AIGovernance.css`,
    Lektions-CRUD im Lern-Tab von `AITradingPanel.js`

## Umgesetzt (30.06.2026)
- **Live-Close-Bugfix** (`services/bitunix_trade.py`): gemeinsamer, verifizierter Live-Pfad
  (`close_live_position`, `_verify_flat`, `_resolve_position`, `_live_position_qty`,
  `sync_live_levels`). Reihenfolge jetzt: erst Börse (positionId nachladen, Retry,
  Verifikation über `get_pending_positions`), dann DB. Scheitert die Börse, bleibt der Trade
  offen (`live_close_failed`), der Monitor versucht es erneut (max. 5x), Telegram-Hinweis,
  `POST /api/autotrade/close/{id}` antwortet 502, KI-Aktion wird als „rejected" auditiert.
  `adjust_levels` schiebt SL **und** TP an die Börse (Modify der bestehenden TP/SL-Order,
  Fallback auf neue Order), Teil-Close bricht bei Börsenfehler ab.
- **MasterPrompt** als oberstes Gebot: Textfeld + harte Regeln (max. Hebel, Mindest-Konfidenz,
  erlaubte Richtungen, gesperrte Coins, max. offene KI-Trades, Live-Freigabepflicht), Versionierung
  + Historie, nur per Admin-Token änderbar, in allen KI-Prompts (Analyse, Deep, Chat, Lernen,
  Trade-Manager) als erster Block; Gates blockieren widersprechende Trades, Config-Änderungen
  (`blocked_master`) und Lektionen.
- **Lektionen**: anlegen/bearbeiten/löschen im Lern-Tab; Trader-Lektionen sind `locked`,
  überleben Lernläufe, werden der KI als unveränderlich markiert, zählen nicht gegen das Limit.
- **Daten-Validierung**: KI-Änderungen ohne Mindest-Stichprobe werden als `needs_data` geparkt
  (nie auto-angewendet), neue/verworfene Lektionen brauchen Mindest-Ergebnisse; Trader-Wünsche
  laufen sofort durch und die KI kommentiert sie (`comment_on_user_change` → Chat-Rolle
  `opinion`).
- **Rollen-Bewusstsein**: neuer Prompt-Block „Deine Rolle im System" + Parameter/Timeframes der
  anderen Strategien als Lernmaterial.
- **Strategie-Labor**: KI-Ideen (`new_strategies`) landen automatisch als Ghost-Kandidaten,
  Ghost-Trades werden gegen echte Kurse ausgewertet; Schwellen (Trades/Winrate) → `live_pending`
  → manuelle Freigabe → paper/live; Entscheidungen mit `strategy_candidate_id` werden bis zur
  Freigabe nur simuliert bzw. auf Paper gezwungen (`force_paper`). Regelbasierte Ideen
  (`rule_definition`) werden als Custom-Strategie für Backtester/Optimizer registriert; die
  Nicht-Testbarkeit von News-/diskretionären Trades steht explizit im Prompt.
- **Tests**: `backend/tests/test_ai_governance.py`, `test_live_close_fix.py` (25 Tests),
  `test_ai_governance_api.py` (29 API-Tests, vom Testing-Agent ergänzt),
  `test_ai_learning.py` um Validierungs-/MasterPrompt-Fälle erweitert.

## Backlog
- **P0**: LLM-Provider-Keys in dieser Umgebung fehlen → Analyse-/Lern-/Chat-Läufe und die
  KI-Meinung zu Trader-Änderungen konnten nicht end-to-end geprüft werden (Produktion hat Keys).
- **P1**: Ghost-Trades zusätzlich mit Zeit-Limit/Timeout schließen; Ghost-Auswertung auch bei
  ausgeschalteter KI-Engine; Kandidaten löschen (statt nur `rejected`).
- **P1**: Meinungs-Einträge (`role='opinion'`) im KI-Feed farblich hervorheben + Filter.
- **P2**: MasterPrompt-Historie im UI anzeigen/zurückrollen; Kandidaten-Backtest direkt aus dem
  Strategie-Labor starten (Job-Verlinkung zum Optimizer).

## Nächste Schritte
1. Live-Verhalten des Close-Fixes am echten Konto beobachten (Events/`live_close_failed`).
2. P1-Punkte umsetzen, sobald der Trader die neuen Panels im Alltag genutzt hat.
