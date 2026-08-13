# PRD – Crypto Scanner / Daytrading-Website (extern deployt auf Render)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: AntonHeinrich05/ml-implementation-ML_REBUILD_STATUS.md, Branch 0.5) soll verbessert werden, ohne Struktur/Workflows zu brechen (Render-Deploy muss weiter funktionieren). Grundsatz: sauber, modular, rückwärtskompatibel, mit Regressionstests.

## Architektur
- FastAPI-Backend (`/app/backend`, Port 8001, Router in `routers/`, Services in `services/`, Kern in `core/`)
- React-Frontend (`/app/frontend`, CRA/craco, deutschsprachige UI)
- MongoDB (`MONGO_URL`/`DB_NAME` aus env), Supabase-Spiegel fürs KI-Gedächtnis, Telegram-Notify
- Externe Integrationen: Bitunix Futures (Live-Trading), diverse LLM-Provider (Cerebras, Groq, OpenRouter, Gemini, Mistral) mit Failover-Kette
- Bestehende pytest-Suite in `backend/tests/` (teilweise env-abhängig: Failures ohne MISTRAL/GITHUB/GEMINI-Keys sind vorbestehend)

## User-Persona
Einzelner Admin-Trader (deutsch), der die Seite parallel zu manuellem Bitunix-Trading nutzt; KI-Trader läuft autonom.

## Umgesetzt (13.06.2026, „0.6-Verbesserungen")
1. **Unbegrenzte Backup-Keys**: `services/ai_providers.py::_backup_env_names` scannt env dynamisch – `X_API_KEY_BACKUP`, `_BACKUP2`, `_BACKUP3`, … beliebig hoch, numerisch sortiert; gilt für alle Provider (Cerebras, OpenRouter, Groq, …).
2. **Watchdog**: An/Aus-Toggle + Verlauf/Trades-Löschen waren in 0.5 bereits in den Haupteinstellungen vorhanden; übernommene manuelle Bitunix-Positionen heißen jetzt klar **„Manuell (Bitunix)"** (`manual_trade: true`, `strategy_id` bleibt „external" für Rückwärtskompatibilität), inkl. einmaliger DB-Migration alter Trades beim Start; Default `manage_external=false` (Watchdog fasst manuelle Trades nicht an).
3. **PnL-Genauigkeit**: `core/utils.py::_enrich_trade` akzeptiert echte Bitunix-Positionsdaten; `routers/autotrade.py` holt via 10s-Cache (`_live_position_map`) den echten uPnL der Börse für offene Live-Trades (Fix für Gold-Bug: Scanner nutzte Yahoo GC=F statt Bitunix XAUUSDT). Feld `live_pnl_source` = „bitunix"/„scanner".
4. **UI-Cut-Fix**: `.tdc-pnl-pct`/`.tdc-pnl` mit `flex-shrink:0; white-space:nowrap` – Prozentwert in Klammern wird nie mehr abgeschnitten (Coin-Name bekommt Ellipsis).
5. **Glocke↔Blitz-Kopplung**: `StrategyTabs.js` – Glocke kann nur an sein, wenn Auto-Trade aktiv ist; Klick bei Blitz-aus zeigt Info-Toast „Glocke nur möglich, wenn Auto-Trade (Blitz) aktiv ist".
6. **KI-Trader**: neues Modul `services/session_levels.py` (Asia/London/NY Session-H/L + Umverteilungszonen via Volumen-Cluster); Snapshot enthält jetzt 5m-RSI (Headline-RSI = 5m statt 1m-„Gambling"), Session-Levels & Zonen; System-Prompts erweitert (Timeframe-Disziplin, Sweep-/Breakout-Trigger an Session-Levels/Zonen, Konfidenz-Kalibrierung 70–85 für A-Setups gegen Dauer-HOLD); Playbook sperrt schwache Setups weiterhin automatisch.
7. **Regressionstests**: `tests/test_improvements_0_6.py` (13 Tests) + bestehende Watchdog/Backup-Tests grün (44/44 in den Zieldateien).

## Env-Hinweise (lokale Preview vs. Render)
- Lokal: `MONGO_URL=mongodb://localhost:27017`, Admin/admin123; Bitunix-Key vom User maskiert geliefert → `trade_client.configured()==false` lokal (erwartet)
- Auf Render nutzt der User seine echte env (unverändert kompatibel)

## Backlog / Nächste Aufgaben
- P1: Live-Verifikation der Bitunix-uPnL-Anzeige mit echtem API-Key (nur auf Render möglich)
- P1: Beobachten, ob KI-Trader mit neuen Kontexten mehr/bessere Trades macht (Playbook-Statistik)
- P2: Per-Setup-Timeframe-Statistik ins Playbook (welcher TF pro Setup am besten performt)
- P2: Session-Levels optional im Chart einzeichnen
