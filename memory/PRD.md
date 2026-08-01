# PRD – Krypto_Trader Verbesserungen (externe Website, Repo: dean06greif-ai/Krypto_Trader, Branch new-32.07)

## Original-Problemstellung
Bestehende, produktiv laufende deutsche Daytrading-Website (bleibt extern gehostet). Verbesserungen sauber, modular und rückwärtskompatibel in die bestehende Architektur einpflegen:
1. Website rechnet teilweise in UTC → alles auf deutsche Zeit (Europe/Berlin), auch KI-Trader-Analysen/Zeitpläne.
2. Beim Löschen von Analysen: auch alle Analysen EINER Strategie über ALLE Coins löschen können (mit Bestätigungsdialog + Anzahl).
3. Backtester/Parameter-Optimierer funktionierte nicht für vom KI-Trader neu erstellte Strategien (überall "0 0 0").

## Architektur (Bestand)
- React (CRA/craco) Frontend `/frontend`, FastAPI Backend `/backend` (Router in `routers/`, Logik in `services/`, Strategien in `strategies/`), MongoDB.
- Backtester/Optimizer mit Fast-Path (`services/fast_sim.py`, vektorisiert) + Referenzpfad (`simulate_pair` → `strategy.analyze`), Multi-Core via `parallel_sim`.
- KI-Strategie-Labor (`services/ai_strategy_lab.py`): KI/Trader legen Kandidaten mit `rule_definition` an → Registrierung als Custom-Strategie (`strategies/custom_strategy.py`).

## Umgesetzt (01.06/08.2026)
### 1) Europe/Berlin überall
- NEU `frontend/src/lib/time.js` (fmtBerlin/fmtBerlinDate/fmtBerlinTime/fmtBerlinShort) – alle Anzeigen erzwingen Europe/Berlin unabhängig von Browser-TZ.
- Umgestellt: Optimizer.js, DynamicPanel.js, RegimeLab.js, AILabPanel.js, RegimeChart.js, RegimeValidation.js, LocalWorkerPanel.js, AIGovernancePanel.js (roher ISO-Slice), AIStrategyLabPanel.js (roher ISO-Slice).
- Backend: `ai_closed_loop.py` Tages-Quota jetzt Berlin-Datum; `history_sources.py` Fortschritts-Datum Berlin; Backtest-CSV-Export zusätzlich Spalte `time_berlin` (time_utc bleibt für Kompatibilität). KI-Zeitpläne (`ai_schedule`/`ai_engine`) waren bereits Berlin-basiert. Interne Speicherung bleibt UTC-ISO (sauber), Anzeige Berlin.

### 2) Analyse-Löschen: Scope "Strategie über alle Coins"
- `routers/analytics.py`: neuer Scope `strategy` (nur strategy_id, alle Coins); gemeinsame Validierung `_validate_clear_request`; NEU `POST /api/analytics/clear/preview` (zählt betroffene Signale/Trades vor dem Löschen).
- `PerformanceAnalytics.js`: neue Scope-Option (data-testid `clear-scope-strategy`), Bestätigungsdialog zeigt live die Anzahl betroffener Einträge (data-testid `clear-preview-count`).

### 3) Backtest/Optimizer für KI-Strategien (Root Causes + Fix)
- Root Cause A: KI lieferte rule_definitions mit nicht unterstützten Indikatoren/Operatoren ("adx", "crosses_above", "macd_line" …) → Regeln waren immer False → 0 Trades überall.
  - NEU `services/rule_definition.py`: `normalize_rule_definition` (Alias-Mapping + strikte Validierung, klare Fehlermeldungen), `supported_summary()` als eine Quelle für die KI-Prompts.
  - Integriert in `ai_strategy_lab.assist()` (Antwort enthält `rule_issues`) und `register_for_testing()` (ungültige Definitionen → status not_testable mit Begründung statt stiller 0-Ergebnisse). KI-Prompts nennen jetzt die EXAKTE erlaubte Liste.
- Root Cause B: CustomStrategy hatte `DEFAULT_PARAMS = {}` und ignorierte params → Parameter-Optimierer hatte keinen Suchraum und Overrides wirkten nicht.
  - `strategies/custom_strategy.py`: NEU `build_param_space(definition)` (genutzte Indikator-Perioden + numerische Regel-Schwellen als `rule_long_i_value`/`rule_short_i_value`, Format wie Built-ins), `effective_definition(params)` (Overrides ohne Mutation der Original-Definition), `analyze()` honoriert params.
  - `fast_sim.build_custom_provider(strategy, fs, settings, symbol)` – Fast-Path identisch zum Referenzpfad; alle Call-Sites umgestellt (backtester, optimizer, parallel_sim, robustness, dynamic_strategy).
  - Nebeneffekt: „Beste Parameter übernehmen" (strategy_params) wirkt jetzt auch live für Custom-/KI-Strategien.

## Tests
- NEU `backend/tests/test_iter_berlin_clear_ki_backtest.py` (19 Tests: Normalisierung, Param-Space, effective_definition, Fast-/Referenzpfad-Konsistenz, Clear-Scopes, Preview-API).
- Testing-Agent NEU `backend/tests/test_ai_strategy_pipeline_e2e.py` (Pipeline: Kandidat → Registrierung → Backtest 20 Trades → Optimizer best.params). Regression: test_strategies, test_ai_lab, test_iter12_dynamic_optimizer, test_iter16_optimizer_pool_regression, test_iter11_robustness alle grün (insg. 100+ Tests).

## Credentials
- Admin-Login: Admin / admin (Defaults; via ADMIN_USER/ADMIN_PASSWORD env). LLM-/Bitunix-Keys NICHT im Env hinterlegt (User wollte sie als Bild schicken – noch offen).

## Backlog / Nächste Aufgaben
- P1: Env-Keys des Users eintragen (LLM-Provider, Bitunix, Telegram …), sobald geliefert – dann KI-Flows live testen.
- P2: `rule_definition` in GET /api/strategies zusätzlich spiegeln (Konsistenz, Hinweis Testing-Agent).
- P2: React-Warning `<span> in <option>` in einem Select (Bestandscode) beheben.
- P2: Optionale UI-Anzeige der `rule_issues` im Strategie-Labor-Panel.

## Iteration (01.06.2026) – UI-Labels, Chat-Verlauf-Performance
- `AITradingPanel.js`: Asset-Fokus-Schnellwahl heißt jetzt „Alle Assets“ + „Coins / Rohstoffe / Indizes / Forex“ (Wort „Alle“ nur noch beim Gesamt-Button).
- KI-Chat-Verlauf lädt beim Wiederöffnen sofort: Modul-Cache `CHAT_CACHE` (letzter Verlauf sofort sichtbar, Aktualisierung im Hintergrund), Reset beim „Chat leeren“.
- `server.py`: neue Indizes auf `ai_chat` (`ts:-1` und `role+pinned+ts`) – ohne Index sortierte Mongo Atlas die gesamte Collection im RAM (Ursache der Ladezeit).
- Winrate 70 % (Signal-Winrate im Lern-Panel) vs. 27,3 % (Trade-Winrate der Strategie-Gesamtübersicht): kein Bug, unterschiedliche Kennzahlen – auf Wunsch des Users NICHT geändert.
