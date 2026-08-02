# PRD – Krypto_Trader (Branch rly-2.8)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (extern auf Render deployt, bleibt ausgelagert).
Repo: https://github.com/dean06greif-ai/Krypto_Trader (Branch rly-2.8).
Eine vorherige KI-Session wurde durch fehlende Credits mitten im Schreiben abgebrochen –
der Deploy schlug danach mit Syntax-Fehlern fehl. Auftrag: Abbruch-Schäden reparieren,
fehlende Punkte fortführen, Stabilität & Rückwärtskompatibilität vor aggressiven Änderungen.

## Architektur
- Frontend: React (CRA), lightweight-charts, Phosphor Icons, CSS pro Komponente
- Backend: FastAPI (server.py + routers/ + services/ + core/), MongoDB (Motor)
- Auth: JWT, Admin via ENV `ADMIN_USER`/`ADMIN_PASSWORD` (Default Admin/admin lokal)
- KI: Multi-Provider (Groq, OpenRouter, Cerebras, Gemini …) via services/ai_providers.py
  - OpenRouter-Keys: ENV `OPENROUTER_API_KEY` (+ `OPENROUTER_API_KEY_BACKUP`), Keys: https://openrouter.ai/keys
  - Gratis-Modelle im Katalog: `deepseek/deepseek-r1:free`, `qwen/qwen3-235b-a22b:free`
- Marktdaten: Bitunix/Binance/OKX (Krypto), Yahoo Finance (Gold/Silber/Öl/Forex/Indizes)
- Tests: pytest (backend/tests, xdist -n 2 per pytest.ini), Integrationstests gegen REACT_APP_BACKEND_URL

## User Persona
Einzelner Betreiber (Admin) – Daytrader, der KI-gestützt Strategien baut, backtestet und
(paper/live) automatisiert handelt; Telegram-Benachrichtigungen.

## Kern-Anforderungen (statisch)
- Custom-/KI-Strategien müssen im Backtester vollständig funktionieren (kein 0/0/0)
- Rule-Engine: alle gängigen Indikatoren mit beliebigen Perioden, Zeitfilter, not_in_range, Mathe-Ausdrücke
- Auto-Fix-Vorschläge für nicht auswertbare Regeln (Ein-Klick)
- Kill-Switch (5% Tagesverlust ODER 3 Verlust-Trades → Pause bis Mitternacht UTC), UI-konfigurierbar
- Anti-Stacking: gleiche Richtung+Asset+Timeframe → 30-Min-Cooldown; anderer TF/Hedge immer erlaubt
- Telegram-Toggles: KI-Ausfall, Backtest fertig, Optimizer fertig, Trade auf/zu, Kill-Switch, tägliche Zusammenfassung
- Website-Meldung bei KI-Ausfall (erst wenn auch Backup scheitert → Fallback)

## Umgesetzt (Stand 02.06.2026, diese Session)
### Reparatur der abgebrochenen Session (Deploy-Fix)
- `Header.js`: doppeltes Datei-Ende (`default Header;`) entfernt
- `StrategyBuilder.js`: dupliziertes Datei-Ende nach `export default` entfernt
- `StrategyTabs.css`: @media-Block war MITTEN in `.strategy-tabs-scroll` geschrieben → korrekt hinter die Regel verschoben (Desktop: Chips wrappen, werden nicht mehr vom Chart überdeckt)
- Production-Build (`yarn build`) läuft wieder fehlerfrei durch

### Fortgeführte Restarbeiten
- Max. Hebel im KI-Trade-Manager-Select jetzt bis 200x (AILabPanel; Backend-Clamp 1–200 war schon da)
- LiquidityPanel: ausklappbare Erklärung aller Werte/Einstellungen (Heatmap, Heat-Wert, Tags, Levels, POC/VAH/VAL, Orderbook-Wände, OI, Kaskade) inkl. ehrlicher Vertrauenswürdigkeits-Einordnung (Heatmap = Schätzung, Rest = echte Live-Börsendaten)
- GOLD/SILVER/OIL-Kerzen: Wochenend-Fallback (Yahoo range 1d leer → 5d Retry) in `core/instruments.py`

### Regressionstests / Test-Hygiene
- `/app/memory/test_credentials.md` angelegt (Admin/admin lokal)
- 7 Testdateien: hartkodiertes Produktiv-Passwort → ENV/`test_credentials.md`-Fallback
- `test_new_features.py`: Indikator-Anzahl `==25` → `>=25` (Rule-Engine wurde bewusst auf 35 erweitert)
- Neues Regressionspack vom Testing-Agent: `backend/tests/test_iter_rly28_review.py` (10/10 grün)
- E2E verifiziert: Custom-Strategie mit KI-Syntax (ema(200), rsi(14), hour not_in_range, cross_below low(20)) → Backtest 7 Trades, 71% WR statt 0/0/0

### Bereits von der Vorsession vorhanden (verifiziert, nicht neu gebaut)
Rule-Engine-Erweiterung + KI-Prompt-Härtung, Auto-Fix im Backtester, Regel-Vorschau (7-Tage-Mini-Backtest),
Kill-Switch/Anti-Stacking (trade_guard), Telegram-Toggles + Website-Notifications (notify.py/notifications.py),
DeepSeek R1 free + Qwen3-235B free, Chart-Autoscale bei Asset-Wechsel, Zeitplan nur noch im KI-Team,
Asset-Fokus-Presets (Gruppen wie Sidebar), Trades mit %-PnL, Aufsicht durch Standard-KI.

## Bekannte offene Punkte / Backlog
- P2: `test_regime_engine.py` Seed 86 (0.25/2.0) schlägt fehl – Grenzfall des „reactive detector"
  aus einer früheren Session (8,6% vs. 8,0%-Schwelle); Engine bewusst NICHT angefasst (Stabilität)
- P2: „Failed to fetch"-Konsolen-Races beim Cold Load (Polling vor Backend-Warmup, heilt sich selbst)
- P2: `create_custom_strategy` ohne Pydantic-Validierung (nimmt rohes Dict)
- Umgebungsbedingt (kein Bug): LLM-Tests brauchen Provider-Keys, Local-Worker-Tests brauchen verbundenen Worker

## Nächste Aufgaben
- User pusht selbst via „Save to GitHub" auf rly-2.8 und deployt auf Render
- Optional: Regime-Engine-Grenzfall analysieren, Pydantic-Modelle für Strategie-Endpoints
