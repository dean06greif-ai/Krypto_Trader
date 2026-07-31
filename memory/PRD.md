# Krypto_Trader – KI Trader Verbesserungen

Quelle: https://github.com/dean06greif-ai/Krypto_Trader (Branch `new-3007-21`)
Stack: React (CRA/craco) + FastAPI + MongoDB (Produktion: Render + Atlas, extern gehostet – bleibt so).

## Grundsatz des Auftrags
Die Website läuft produktiv. Verbesserungen werden modular und rückwärtskompatibel in die
bestehende Architektur eingepflegt; Stabilität und saubere Struktur haben Vorrang vor
aggressiven Änderungen. Vor grösseren Änderungen: Architektur analysieren, Risiken
identifizieren, Regressionstests ergänzen.

## Architektur (relevante Teile)
- `backend/services/ai_engine.py` – KI Trader Kern (Analyse, Autonomie, Vorschläge/Proposals)
- `backend/services/ai_roles.py` – KI-Team (Rollen + Modell-Ketten + Fallbacks)
- `backend/services/ai_strategy_lab.py` – Strategie-Labor (Kandidaten, Ghost-Tests, Assist)
- `backend/services/ai_research.py` – Forschungs-Analyst (bewertet Backtests/Optimizer)
- `backend/services/ai_validation.py` – datenbasierte Freigabe (Stichprobe, Bestätigungen, Schrittweite)
- `frontend/src/components/AITradingPanel.js` – KI-Panel mit Tab-Leiste (Setup, Lernen, KI-Team, …)

## Umgesetzt (31.07.2026)
1. **Autonomie-Bug behoben**: Neuer Endpoint `GET /api/ai/proposals/actionable`
   (Server ist die einzige Quelle der Wahrheit). Bei `autonomy=auto` immer `[]` →
   es können keine „Übernehmen/Ablehnen"-Karten mehr aufblitzen.
   Neu `AIEngine.review_parked_proposals()`: geparkte Wünsche (`needs_data`,
   `needs_confirmation`) werden nach jedem Analyse-/Lernlauf erneut gegen die aktuelle
   Datenlage geprüft und automatisch angewendet, sobald die bestehende Validierung
   (MasterPrompt, Stichprobe, Bestätigungen, Schrittweite) sie freigibt.
2. **Wischen statt Scrollbalken**: neuer Hook `frontend/src/hooks/useDragScroll.js`
   (Maus-Drag, Mausrad, Touch nativ, Klick-Unterdrückung beim Ziehen) für Tab-Leiste,
   Vorschlags-Karten und Asset-Chips. Scrollbalken ausgeblendet.
3. **Chat-Vorschläge als Schnellauswahl**: `frontend/src/components/AIQuickPrompts.js` –
   eine Zeile, seitwärts wischbar, „+" für eigene Vorschläge, Verschieben, Löschen,
   Persistenz in `localStorage` (`krypto_ai_quick_prompts`).
4. **Tab-Reihenfolge**: „Strategien" steht jetzt vor „MasterPrompt".
5. **KI-Team als Vollansicht**: Chatverlauf/Eingabe werden im KI-Team-Tab aus dem DOM
   entfernt (wie bei „Strategien"), Panel füllt die Höhe.
6. **Asset-Fokus** (früher „Coin-Fokus") mit Schnellauswahl: Alle Assets / Alle Coins /
   Alle Rohstoffe / Alle Indizes / Alle Forex / Nur aktuelles Asset.
   **Voreinstellung: alle Assets.**
7. **Strategie-Labor**: Flacker-Bug behoben (`assist` wird vor dem neuen Lauf verworfen,
   Zuordnung über `candidate_id` aus der Antwort). Neuer Button „KI: Verbesserungen".
   Die Strategie-KI ist jetzt die **Rolle `research_analyst`** (dieselbe KI, die die
   Backtest-Daten des Teams auswertet) und bekommt `test_context()`: Backtests und
   Parameter-Optimierungen **dieser** Strategie plus eigene Ghost/Real-Ergebnisse.
   Einschätzungen landen als `assist_history` (max. 5) an der Strategie und im KI-Feed →
   die KI nimmt beim nächsten Aufruf Bezug darauf. Neu: `GET /api/ai/strategies/{cid}/test-data`.
8. **Lektionen bis 100** wählbar (Clamp 3..100).
9. **Fußzeilen-Text** „Auto-Trading pro Coin über das ⚡-Symbol …" entfernt.
10. **Aufsicht über das KI-Team**: `backend/services/ai_supervisor.py` – das Haupt-Modell
    prüft stichprobenweise alle 9 Rollen (Modell, Aktivität, Fehler, echte Ausgaben) und
    empfiehlt bei Bedarf einen Modellwechsel (nur aus dem erlaubten Katalog).
    Endpoints `POST /api/ai/supervisor/review` (Hintergrund-Task) und `GET /api/ai/supervisor`.
    UI: `AITeamSupervisor.js` mit manuellem Button „KI-Team jetzt prüfen", Polling,
    Bericht je Rolle und Ein-Klick-Übernahme des empfohlenen Modells.
11. **Robustheit**: `backend/services/ai_json.py` (`parse_json_lenient`) – tolerantes Lesen
    aller KI-JSON-Antworten (Markdown-Zäune, Kommentare, Trailing-Kommas, abgeschnittene
    Antworten). Verhindert Komplettausfälle einzelner KI-Aufrufe.
12. **Nebenbefund gefixt**: `frontend/src/components/StrategyTabs.css` enthielt einen
    doppelten/kaputten Block – der Frontend-Build brach mit „Unexpected }" ab.

## Tests
- Neu: `backend/tests/test_iter_ai_supervisor_autonomy.py` (Autonomie-Review, actionable,
  max_lessons, Supervisor-Aufbereitung) und `backend/tests/test_iter_ai_json.py`.
- Vom Testing-Agent ergänzt: `backend/tests/test_iter3_ai_api.py`.
- Lauf: `export REACT_APP_BACKEND_URL=<url> ADMIN_USER=Admin ADMIN_PASSWORD='…'`
  → `python -m pytest tests -q`.
- Bekannte, umgebungsbedingte Fehler (nicht durch diese Iteration): Tests, die einen
  echten Analyse-/Lernlauf brauchen (Marktdaten nötig) bzw. länger als 60 s laufen und
  am Preview-Proxy in einen 502 laufen.

## Backlog / nächste Schritte
- P1: Supervisor-Prüfung optional automatisch (z. B. täglich) mit Verlaufs-Historie
  statt nur letztem Bericht.
- P1: Verbesserungs-Vorschläge der Strategie-KI direkt als „übernehmen"-Aktion in die
  Regel-Definition (heute: Text + Verlauf).
- P2: Quick-Prompts serverseitig speichern (heute pro Browser via localStorage).
- P2: Autonomie-Review auch per Cron statt nur nach Analyse-/Lernläufen.
- P2: `<option><span>…</span></option>`-React-Warnung im Frontend bereinigen (kosmetisch,
  bestand vorher schon).

## Umgesetzt (Iteration 5 – UI/Analyse-Fixes, neue Umgebung trader-refine)
1. **Chart-Bug (zusammengezogen)**: `.main-content` nutzt `grid-template-rows: auto minmax(300px,1fr) auto`,
   `.chart-wrap min-height: 296px`, SignalPanel-Höhe an Viewport gekoppelt (`max-height: min(400px, 32vh)`).
   Chart hat dadurch auf JEDEM Gerät eine garantierte Mindesthöhe (verifiziert bei 760px Viewport: 475px).
2. **Scrollleiste neben Plus**: `StrategyTabs.css` – Action-Buttons (⚙/+) absolut rechts in der Bar,
   Chips-Scrollcontainer über volle Breite (padding-right 88px) → Scrollleiste sitzt ganz rechts neben dem Plus.
3. **Analyse „Heute"**: „Letzte Signale" und „KI-Analyse starten" entfernt; Heute-Zahlen bleiben je aktiver Strategie.
4. **Top Coins**: jetzt dauerhaft beste Win-Rates NUR der aktiven Strategie (aus `performance.by_strategy`),
   Anzeige W/L; „Gesamt-Analyse (dauerhaft)" darunter verschoben.
5. **Zeit-Analyse**: Backend `/api/analytics/time-based/{symbol}?strategy_id=` rückwärtskompatibel erweitert um
   `by_hour`, `by_weekday`, `by_combo` (PRE_SIGNAL exkl., Win-Rate aus entschiedenen Signalen).
   Frontend: Dropdown „Gesamt (alle Strategien)"/je Strategie + Tabs Uhrzeiten/Wochentage/Kombi, beste zuerst.
6. **Trades → Performance je Strategie**: Zeilen werden aus den ECHTEN Trades des Coins abgeleitet
   (inkl. KI Trader, keine leeren Strategien mehr).
7. **Top-10-Collapse-Fix**: `CoinSidebar` – erzwungenes Offenhalten der Gruppe des gewählten Coins entfernt.
8. **Login-Bug (Paper-Blitz)**: `GET /api/autotrade/strategy_coin_configs` ist öffentlich (read-only,
   Schreibzugriffe bleiben admin-only) → Blitz-Status auch ohne Login sichtbar. Zusätzlich lädt App.js nach
   erfolgreichem Login `loadAutotrade/loadStrategies/loadControlState/loadPerformance` neu → kein Seiten-Reload nötig.
9. **Settings nur über X**: `SafeOverlay` hat Prop `closeOnOutside` (default true); SettingsPanel und
   StrategyAutoTradeModal schließen nicht mehr bei Klick daneben.
10. **Iteration-4-KI-Features verifiziert** (Testlauf war zuvor abgebrochen): 11/11 Unit-Tests
   (`test_iter_ai_supervisor_auto.py`) + 22/22 neue E2E-Tests (`backend/tests/test_iter5_api.py`) grün –
   Supervisor auto/history/rollback, Quick-Prompts serverseitig, apply-assist. Cleanup (Defaults) erfolgt.

## Offener Backlog-Punkt (vom User vorgeschlagen, noch NICHT umgesetzt)
- Supervisor: bei „schwach"-Bewertung automatisch auf Fallback-Modell umschalten (mit Log & Rückfall) –
  würde 503-Ausfälle einzelner Modelle ohne Zutun abfangen.
