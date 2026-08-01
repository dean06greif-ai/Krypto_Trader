# PRD – Krypto Trader / Daytrading-Website

## Ausgangslage
Produktiv laufende, extern gehostete Daytrading-Website (React + FastAPI + MongoDB).
Basis dieser Iteration: GitHub `dean06greif-ai/Krypto_Trader`, Branch **1.8-ws-patch**
(inhaltlich identisch zu `new-1.8`, nur `.emergent`/`.gitignore` unterscheiden sich).
Grundsatz des Nutzers: Stabilität, Rückwärtskompatibilität und saubere, modulare
Struktur haben Vorrang vor aggressiven Änderungen. Keine Schnelllösungen.

## Architektur (unverändert)
- `backend/server.py` + `backend/routers/*` (FastAPI, alle Routen unter `/api`)
- `backend/services/*` (Scanner, KI-Engine, Backtester, Optimizer, Regime, Liquidität)
- `backend/strategies/*` (eingebaute Strategien + `CustomStrategy` für KI-Strategien)
- `frontend/src/components/*` (Panels), `frontend/src/lib/*` (Helfer)
- MongoDB über `MONGO_URL`/`DB_NAME`; Zeitzone der Anzeige/Logik: **Europe/Berlin**

## Iteration 2026-06 (diese Session)
### 1. Zeitzone durchgängig Europe/Berlin
- Neu `backend/core/timeutil.py`: `now_utc/now_berlin/berlin_date/berlin_minutes/fmt_berlin`.
  Speicherung bleibt UTC-ISO (rückwärtskompatibel), Anzeige/Logik ist Berlin.
- `services/ai_closed_loop.py`: Tages-Quote nutzt Berlin-Datum (vorher UTC).
- `services/ai_engine.py`: Makro-No-Trade-Fenster, Makro-Termine, News-Zeitstempel und
  „Letzte Analyse / Jetzt" im KI-Prompt in deutscher Zeit.
- Neu `frontend/src/lib/time.js` (`fmtDateTime/fmtDate/fmtTime/fmtShort/fmtTimeSec`),
  eingesetzt in AILabPanel, RegimeLab, RegimeChart, RegimeValidation, Optimizer,
  LocalWorkerPanel, DynamicPanel. Naive ISO-Strings werden als UTC gelesen.

### 2. Analysen löschen: ganze Strategie über alle Coins
- `routers/analytics.py`: neuer Scope `strategy`, ausgelagerte Helfer
  `_clear_scope_filter` / `_validate_clear`, neuer Endpunkt
  `POST /api/analytics/clear/preview` (Anzahl betroffener Signale/Trades + Coin-Liste).
- `PerformanceAnalytics.js`: neue Scope-Option „Strategie … – ALLE Coins" plus
  Live-Vorschau im Bestätigungsdialog (`data-testid="clear-preview"`).

### 3. Assets-Fokus im AI-Trading-Panel
- Gruppen-Schnellwahl („Alle Coins/Rohstoffe/Indizes/Forex") entfernt.
  „Alle Assets" und das aktuelle Asset bleiben.

### 4. KI-Verlauf lädt schnell
- Neu `backend/core/indexes.py` (`ensure_indexes`, im Startup): Indizes für
  `ai_chat(ts)`, `ai_chat(role,ts)`, `signals`, `auto_trades`, `analytics_daily` u.a.
- `chat_history` mit Mongo-Projection (kein `_id`-Overhead).
- `AITradingPanel.js`: Modul-Cache `chatHistoryCache` → Verlauf ist beim erneuten
  Öffnen sofort sichtbar, Server-Abruf aktualisiert im Hintergrund.

### 5. Backtest / Parameter-Optimierer für KI-Strategien (Kern-Bugfix)
Ursachen für „überall 0":
  a) `CustomStrategy.DEFAULT_PARAMS` war leer → Optimizer-Suchraum leer.
  b) KI-Indikatornamen (z.B. `close`, `ema9`, `adx14`, `crosses_above`) waren
     unbekannt → jede Regel dauerhaft `False` → 0 Trades.
Lösung:
- Neu `strategies/custom_params.py`: `normalize_definition` (Alias-Vokabular für
  Indikatoren/Operatoren + Meldung nicht auswertbarer Regeln), `build_param_meta`
  (Suchraum aus genutzten Perioden + numerischen Regel-Schwellen, max. 12 Parameter),
  `apply_params`.
- `CustomStrategy`: normalisiert die Definition, baut `DEFAULT_PARAMS`, neue Methode
  `effective_definition(params)`; neue Indikatoren `adx`, `plus_di`, `minus_di`,
  `cci`, `keltner_upper/middle/lower`, `donchian_high/low` (Parität zum Fast-Path).
- `fast_sim.provider_for(strategy, fs, settings, symbol)` als EINE Stelle für den
  Fast-Path-Provider – wendet Strategie-Parameter auch im Backtest an
  (ersetzt 6 duplizierte Blöcke in backtester/optimizer/robustness/parallel_sim/
  dynamic_strategy); Provider-Cache-Key nutzt jetzt die effektive Definition.
- Backtester & Optimizer liefern `result.strategy_warnings`; UI zeigt sie als
  gelben Hinweis (`.bt-rule-warnings`).

### 6. Liquidität: Heatmap + „Liquidity Levels" (X-Ray-Pro-Äquivalent)
- `services/liquidity_data.py` war vorhanden aber nicht verdrahtet → jetzt aktiv.
- Neu `services/liquidity_levels.py` (rein/testbar): Swing-Pivots, unberührte Level,
  EQH/EQL, unverfüllte FVGs, Volumen-Profil (POC/VAH/VAL, HVN/LVN), runde Marken,
  Scoring 0-100, `heatmap()` (Liquidations-Cluster + Volumen + Level → Preis-Buckets).
- Neu `routers/liquidity.py`: `/api/liquidity/context|levels/{symbol}|heatmap/{symbol}|live/{symbol}`
  mit 30-s-Kerzen-Cache.
- Neu `components/LiquidityPanel.js|.css` + Header-Button (`liquidity-button`).
- KI-Kontext: `AIEngine._liquidity_block()` (~2,7 KB) in Chat-, Analyse- und
  Deep-Analyse-Prompt; Config-Schalter `liquidity_enabled`, `liquidity_symbols`.
- Nur freie Quellen (Binance/OKX/Bybit), keine CoinGlass/Hyblock-Keys.

### Tests
- Neu `backend/tests/test_iter_tz_customparams_liquidity.py` (29 Tests: Zeitzone,
  Alias-Normalisierung, Param-Suchraum, Fast-Path==Referenz-Pfad, Liquidity Levels,
  Clear-Scopes).
- Vom Testing-Agent ergänzt: `backend/tests/test_iteration_review.py` (20/20 grün).
- Regression bestehender Suiten: test_strategies, test_refactor_regression,
  test_iter11/12/15/16, test_multicore – alle grün.

## Backlog / nächste Schritte
- P1: Liquidity-Level als Overlay direkt im Haupt-Chart (aktuell eigenes Panel).
- P1: UI-Schalter für `liquidity_enabled`/`liquidity_symbols` im KI-Governance-Panel.
- P2: Optimierung der KI-Regel-Schwellen im Strategie-Labor sichtbar machen
  (Vorher/Nachher-Vergleich je Regel).
- P2: `AITradingPanel.js` in Sub-Komponenten aufteilen (Chat, Fokus-Auswahl).
- P2: Weitere Indikatoren für KI-Regeln (Supertrend, OBV, MFI, Williams %R) –
  aktuell werden sie als „nicht unterstützt" gemeldet statt still ignoriert.
