# PRD – Krypto Trader (extern deployt auf Render, Repo: dean06greif-ai/Krypto_Trader, Basis Branch 12.8)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (FastAPI + React + MongoDB, extern auf Render deployt – Originalstruktur MUSS erhalten bleiben). Verbesserungen sauber, modular, rückwärtskompatibel in die bestehende Architektur einpflegen:
1. Rate-Limit-Fehlermeldungen zusammenfassen (kein Riesen-Text), aber weiterhin genau zeigen welches Modell bei welchem Assistenten betroffen war.
2. Weitere Backup-API-Keys: Cerebras x2, Mistral x1, OpenRouter x1 – als Backup INNERHALB des bestehenden Modells (wie bisherige _BACKUP-Keys).
3. KI-Trader auf mehrere vielseitige Daytrading-Strategien anlernen (Setups selbst suchen, Strategien bewerten, verbessern/verwerfen, KI-Gedächtnis, inkl. Swing, Hedge; nicht stur immer dasselbe TP-Muster). ML-Training existiert bereits (services/ai_ml_lab.py, sklearn+optuna).
4. Token-Verbrauch der Prompts beachten (sparsam).
5. Schlechte Trades fixen – Beobachtung des Users: immer gleiche Richtung, mehrere Trades in derselben Preiszone.

## Architektur (unverändert, Render-kompatibel)
- backend/ FastAPI (server.py als App-Assembly, routers/, services/, core/), MongoDB via MONGO_URL
- frontend/ React (craco), REACT_APP_BACKEND_URL
- Alle API-Routen mit /api-Präfix

## Implementiert (12.06.2026)
1. **Zusammengefasste KI-Ausfall-Meldungen** (`services/notifications.py`):
   - `_fail_lines`: gruppiert nach Provider+Ursache statt einer Zeile pro Modell, Modelle bleiben einzeln benannt.
   - `notify_model_failure`: sammelt Ausfälle 75s (`MODEL_FAILURE_AGGREGATE_S`) und sendet EINE Glocken-Meldung; `summarize_model_failures()` (rein, testbar) formatiert: "• groq – Rate-Limit erreicht → Analyst: modelA, modelB · Trade-Manager: modelC". Dedupe/Cooldown 15 min pro Provider+Ursache.
2. **Mehrfach-Backup-Keys** (`services/ai_providers.py`): `provider_keys()` liest `<ENV>`, `<ENV>_BACKUP`, `<ENV>_BACKUP2` … `_BACKUP9` in Prio-Reihenfolge; neue `backup_key_counts()`. `backup_keys_info()` bleibt bool (UI-kompatibel).
   - **Render-ENV neu setzen:** CEREBRAS_API_KEY_BACKUP2=csk-kkyk…, CEREBRAS_API_KEY_BACKUP3=csk-ep59…, MISTRAL_API_KEY_BACKUP=rS5GC…, OPENROUTER_API_KEY_BACKUP2=sk-or-v1-d7fa…
3. **Strategie-Playbook** (`services/ai_playbook.py`, neu): 10 Setups (trend_follow, breakout, squeeze_breakout, mean_reversion, range_fade, liquidity_sweep, momentum_news, pullback, swing_trend, hedge) mit kompakten Beschreibungen (tokensparend). Performance-Tracking pro Setup aus ECHTEN geschlossenen KI-Trades (30 Tage, auto_trades.setup), Urteile bewährt/neutral/test/schwach, automatisches SPERREN schwacher Setups (technisch erzwungen in _emit_signal) + Re-Test nach 14 Tagen. Prompt-Block via `_analysis_extra_blocks` (nur Analyse, nicht Review). Decision-Schema um Pflichtfeld "setup" erweitert (beide Prompts, inkl. Regel: SL/TP passend zum Setup statt Standardwerte). Setup wird am Trade gespeichert (`bitunix_trade.py`) und in der Offene-Trades-Übersicht der KI angezeigt. Neuer Endpoint `GET /api/ai/playbook`.
4. **Diversifikations-Guards gegen schlechte Trades** (`ai_playbook.diversification_check`, erzwungen in `ai_engine._diversification_gate` → `_emit_signal`):
   - Richtungs-Guard: max. N gleichzeitig offene KI-Trades in DIESELBE Richtung (config `max_same_direction`, Default 3, 0=aus, Clamp 0..20). Hedges (Gegenrichtung) nie blockiert.
   - Cluster-Guard: Mindestabstand zwischen Entries auf demselben Symbol+Richtung (config `min_entry_distance_pct`, Default 0.5 %, Clamp 0..5).
   - UI-Controls im AITradingPanel-Setup: data-testid `ai-max-same-direction-select`, `ai-min-entry-distance-input`.
5. **Tests**: `tests/test_playbook_backups_notify.py` (12 Tests, grün). Gesamt-Unitsuite: 546 passed; 9 Fehlschläge existieren identisch im unveränderten Original-Repo (pre-existing: test_regime_engine, test_ai_lab, test_ai_team_regression, test_iter_ai_supervisor_auto).

## Testergebnis (Testing-Agent Iteration 11)
Backend 100 %, Frontend 100 %, keine Issues. Config-Persistenz + Clamps verifiziert, UI-Controls end-to-end, Frontend-Regression sauber.

## User Personas
- Admin/Owner (einziger Nutzer): steuert KI-Trader, Paper-/Live-Trading via Bitunix, Telegram-Benachrichtigungen.

## Backlog / Nächste Aufgaben
- P1: Setup als Feature ins ML-Lab (ai_ml_lab FEATURES) aufnehmen, damit das ML-Modell pro Setup lernt.
- P1: Playbook-Statistik im Frontend visualisieren (eigene Karte im KI-Panel, nutzt GET /api/ai/playbook).
- P2: backup_key_counts im /api/ai/status + UI-Anzeige der Anzahl Backups pro Provider.
- P2: Debounce (200–300 ms) für die Config-Autosaves im AITradingPanel.
- P2: Korrelations-Guard (BTC/ETH/SOL als ein Richtungs-Risiko zählen).

## Wichtige Hinweise
- Lokale Preview-.env: lokale Mongo (mongodb://localhost:27017), ADMIN_PASSWORD=KryptoAdmin!2026 (nur Preview; Render behält eigene ENV). Bitunix/Telegram/Gemini-Keys lokal absichtlich leer.
- Pre-existierende 9 Testfehlschläge NICHT als Regression werten.
