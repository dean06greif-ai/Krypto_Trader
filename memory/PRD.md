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

## Iteration 2 – umgesetzt (30.06.2026)
- **Analyse-Zeitplan** (`services/ai_schedule.py`): Intervall je Zeitfenster (Berlin, auch über
  Mitternacht), Presets „Nacht 30 min" / „US-Open 5 min", Standard-Intervall als Rückfall.
  Endpunkte `GET/POST /api/ai/schedule`, UI im MasterPrompt-Panel (`AIScheduleEditor.js`).
  Der Rhythmus steht auch im Analyse-Prompt, damit Stops/Ziele bis zum nächsten Lauf tragen.
- **MasterPrompt regelt jetzt auch Lektionen**: neues Feld `lesson_policy` (Grundregeln für jede
  Lektion) + harte `forbidden_terms`. Beides wird im Lernlauf als bindend eingespielt;
  widersprechende Lektionen werden verworfen und im „Lernen"-Tab als abgelehnt gelistet.
- **Struktur-/Makro-Parameter geschützt** (`ai_validation.MACRO_KEYS`): SL, CRV, Hebel,
  Konfidenz, Trailing usw. brauchen große Stichprobe (25) UND mehrere Bestätigungen derselben
  Richtung (3, Fenster 14 Tage); jede Änderung wird zusätzlich auf kleine Schritte begrenzt
  (`clamp_step`, max. 20 % des Werts + harte Leitplanken). Status `needs_confirmation` mit
  Zähler; der Trader kann geparkte Vorschläge jederzeit selbst freigeben.
- **Makro-Parameter pro KI-Strategie**: Kandidaten haben eigene `macro_params`
  (`POST /api/ai/strategies/{id}/macro`, UI-Formular). Signale des Kandidaten nutzen dessen
  SL/CRV/Hebel/TP1-Anteil (`cfg_overrides` im AutoTrader); die Validierung rechnet mit der
  Stichprobe genau dieser Strategie (Overfitting-Schutz).
- **Limit-/Fallback-Transparenz** (`ai_providers.record_result/health_status`): Banner im
  KI-Panel („Fallback aktiv: provider/model", „Limit erreicht – frei in ca. X min", Backup-Key),
  Endpunkt `GET /api/ai/providers/health`, zusätzlich in `/api/ai/status`.
- **Telegram-Spam-Bremse** (`services/notify_guard.py`): identische Setups (Coin + Strategie +
  Richtung, Preisabweichung < 0.15 %) werden innerhalb der Sperrzeit (Standard 15 min,
  einstellbar) nur einmal gemeldet.
- **Daytrader-Review – zusätzlich eingebaut**: Tages-Verlustlimit und max. Trades/Tag als harte
  MasterPrompt-Regeln (`check_day_rules`, Prüfung vor jedem Signal), Ghost-Trade-Timeout
  (Standard 240 min → `expired`, zählt nicht als Ergebnis), echte Trade-Statistik pro Kandidat
  (`stats.real`), eigener Ghost-Cooldown (Simulationen blockieren echte Signale nicht mehr).
- **Tests**: `tests/test_ai_iter2_governance.py` (19), `tests/test_ai_iter3_api.py`,
  `tests/test_iter4_master_prompt_lesson_policy.py`; Regressionslauf 117/117 grün.

## Backlog
- **P0**: LLM-Provider-Keys in dieser Umgebung fehlen → Analyse-/Lern-/Chat-Läufe und die
  KI-Meinung zu Trader-Änderungen konnten nicht end-to-end geprüft werden (Produktion hat Keys).
- **P1**: Ghost-Auswertung auch bei ausgeschalteter KI-Engine; Kandidaten wirklich löschen
  (statt nur `rejected`).
- **P1**: Meinungs-Einträge (`role='opinion'`) im KI-Feed farblich hervorheben + Filter.
- **P2**: MasterPrompt-Historie im UI anzeigen/zurückrollen; Kandidaten-Backtest direkt aus dem
  Strategie-Labor starten (Job-Verlinkung zum Optimizer).

## Nächste Schritte
1. Live-Verhalten des Close-Fixes am echten Konto beobachten (Events/`live_close_failed`).
2. P1-Punkte umsetzen, sobald der Trader die neuen Panels im Alltag genutzt hat.
