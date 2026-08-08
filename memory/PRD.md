# PRD – Daytrading-Website (Crypto Scalping Scanner)

## Original-Problemstellung
Bestehende, produktiv laufende externe Daytrading-Website (Repo
`AntonHeinrich05/regimeUpdates31071509`, Branch `new-indikators`, Deployment auf
Render, bleibt extern). Verbesserungen müssen sauber, modular, rückwärts-
kompatibel und mit Regressionstests in die bestehende Architektur integriert
werden.

Gemeldete Bugs (mit Screenshots belegt):
1. ADA-/DOT-Positionen wurden auf Bitunix eröffnet OHNE Stop-Loss und liefen in
   die Liquidation; die Positionen waren auf der Website nicht sichtbar.
2. Häufig fehlgeschlagene Close-Versuche (`Insufficient amount`, 5x-Schleife)
   und SL-Anpassungen (`Please set at least one of TP/Stop Loss`).
3. KI setzte SL von 0.202349 auf 0.0002 (Stop faktisch entfernt).
4. Telegram-Fehler `NoneType - NoneType` bei Signalen ohne Entry/SL/TP.

## Architektur (unverändert)
- Backend: FastAPI (`backend/server.py` = Assembly, `routers/` = Endpoints,
  `core/` = State/Scheduler/Pipeline, `services/` = Fachlogik)
- Frontend: React (CRA/craco), Komponenten in `src/components/`
- DB: MongoDB (`auto_trades`, `signals`, `settings`, ...)
- Live-Trading: Bitunix USDT-M Futures (`services/bitunix_trade.py`)

## User-Entscheidungen
- SL-Fehlschlag nach Eröffnung: mehrfacher Retry, bei endgültigem Fehlschlag
  Position schließen (Notfall-Close).
- Positions-Watchdog gewünscht: fehlende SLs nachziehen + unbekannte Positionen
  auf der Website anzeigen.

## Umgesetzt (07.06.2026 / Iteration „Bug-Report ADA/DOT")
1. **Ghost-Order-Schutz** (`bitunix_trade.py::on_signal`): Bei Exception/Timeout
   nach Order-Absenden wird via `_untracked_qty()` an der Börse nachgeprüft;
   existiert die Position, wird der Trade übernommen statt verworfen. `code 0`
   ohne auffindbare orderId gilt nicht mehr als Ablehnung
   (`_extract_order_id`).
2. **SL-Verifikation nach Eröffnung** (`_ensure_live_sl`, `_position_has_sl`):
   Nach jedem Live-Entry wird der Börsen-SL verifiziert; fehlt er → bis zu 3x
   nachsetzen, sonst Notfall-Close + Telegram-Alarm. Flag
   `sl_exchange_missing` am Trade.
3. **Positions-Watchdog** (`services/position_watchdog.py`, Loop alle 120s):
   prüft ALLE Bitunix-Positionen; unbekannte Positionen werden als
   „Extern (Watchdog)"-Trade übernommen (Website-sichtbar), fehlende SLs
   nachgezogen (lokaler SL oder Notfall-SL `fallback_sl_percent`), nach
   `max_sl_retries` Fehlzyklen Notfall-Close. Endpoints:
   `GET/POST /api/autotrade/watchdog/{status,run,config}`. UI-Karte in
   Einstellungen → Steuerung (`control-watchdog-card`). Telegram-Toggle
   `watchdog`.
4. **Dust-Close-Fix** (`close_live_position`): Börsen-Rest unter dem
   handelbaren Minimum gilt als geschlossen → keine
   `Insufficient amount`-Endlosschleifen mehr. Monitor-Close-Fehler laufen
   zusätzlich durch `_reconcile_close_error` (extern geschlossen → sofort
   verbuchen).
5. **Bitunix-Modify-Endpoint korrigiert** (`modify_position_tp_sl`): jetzt
   `POST /api/v1/futures/tpsl/position/modify_order` mit `positionId` +
   `slStopType`/`tpStopType` (vorher falscher Endpoint mit `orderId` →
   „Please set at least one of TP/Stop Loss" bei JEDER SL-Anpassung).
6. **KI-Schutzregel** (`ai_trade_manager.py::apply_action`): KI darf den SL nur
   enger ziehen; risikovergrößernde SL-Moves werden blockiert (manuell bleibt
   erlaubt).
7. **Telegram-Fixes**: `format_signal_message` None-sicher; Pipeline loggt
   Versand-Fehler statt fälschlich „sent".

## Tests
- Neu: `tests/test_position_watchdog.py` (16), `tests/test_bugreport_fixes.py`
  (7), Testing-Agent: `tests/test_watchdog_api_review.py` (10 API-Tests).
- Regression grün: `test_live_close_fix.py`, `test_live_levels_and_cascade.py`,
  `test_ai_trader.py`, `test_refactor_regression.py`, `test_winrate_bug.py`,
  `test_fix_custom_ai_trades.py` (53/53 im Abnahmelauf).
- Hinweis: Für API-Tests `REACT_APP_BACKEND_URL` aus `frontend/.env`
  exportieren; pytest.ini erzwingt `-n 2`, seriell mit `-n 0`.

## Sicherheit / Dev-Umgebung
- Bitunix-Keys sind in Dev ABSICHTLICH nicht gesetzt (keine echten Orders aus
  Tests). Live-Pfade via gemockte Clients getestet. Produktions-Env-Variablen
  (Render) bleiben unverändert nutzbar; keine neuen Pflicht-Variablen.

## Umgesetzt (07.06.2026 / Iteration 2 „KI-Verbesserungen")
1. **Modell-Ausfall-Anzeige**: `ai-health-badge` (⚠ + Anzahl) im KI-Panel-Header
   bei rate-limiteten ODER fehlerhaften Modellen (Timeout etc.); Banner
   `ai-limit-banner` zeigt jetzt auch reine Fehler/Timeouts inkl. Detail.
   Datenquelle: `providers_health` in `GET /api/ai/status` (bestand schon).
2. **Chat-Kontext-Fix „keine Signale"**: neuer Block „HEUTIGE AKTIVITÄT"
   (`ai_engine._today_activity_block`, Europe/Berlin) mit heutigen Signalen und
   eröffneten/geschlossenen Trades im Chat-Kontext (`_context_brief`).
3. **Lektionen-Hygiene**: `ai_lessons.dedupe_lessons` (Fuzzy-Titel-Dedupe,
   am besten validierte bleibt: locked > confirmations > weight > Aktualität;
   locked nie entfernt) – läuft in `merge_lessons` (jeder Lernlauf) und
   `audit_against_master`. Lernlauf-Schema um `contradictory_lessons`
   erweitert: LLM benennt widersprüchliche/doppelte Lektionen, Entfernung
   umgeht das Removal-Gate (nie locked). MasterPrompt-Audit bestand schon.
- Tests: `tests/test_ai_fixes_iter2.py` (8) + bestehende grün (57/57 Abnahme).

## Umgesetzt (07.06.2026 / Iteration 3 „Mobile-Verbesserungen")
1. **Signal-Popup mobil**: Blockierendes AlertModal wird auf ≤640px nicht mehr
   geöffnet (App.js `isMobile`-Gate) – Toast + Sound bleiben; Desktop
   unverändert.
2. **Chart-Touch-Scroll**: `MainChart.js` setzt auf Touch-Geräten
   (`pointer: coarse`) `handleScroll {vertTouchDrag:false, horzTouchDrag:true}`
   + `handleScale {pinch:true,...}` – vertikales Wischen scrollt die Seite,
   horizontal/Pinch bedient den Chart. Recharts (Regime/Equity) bekommen
   `touch-action: pan-y`.
3. **Abgeschnittene Dropdowns**: Optimizer/Regime-Lab/Backtester/Liquidity
   werden auf ≤640px Bottom-Sheets (100vw); `.opt-field select/input` volle
   Breite (mobile.css).
4. **Zeilenumbrüche**: `.chart-subtitle` einzeilig (nowrap) + `.chart-title`
   flex-wrap; `.tdc-strat-line` bricht normal um statt ellipsis; Beschreibungs-
   Klassen mit `overflow-wrap: break-word`.
- Verifiziert durch Testing-Agent iteration_10 (Frontend 100%, inkl.
  Desktop-Regression: zentrierte Panels, Watchdog-/Sync-Karten intakt).

## Backlog / Nächste Aufgaben
- P1: Watchdog-Einstellungen (Intervall, Notfall-SL-%, Retries) im UI editierbar
  machen (Endpoint `POST /api/autotrade/watchdog/config` existiert bereits).
- P1: „SL fehlt"-Warnbadge direkt an der Trade-Karte in „Offene Trades".
- P2: Auto-Leverage-Kappung (Screenshot zeigte 55x mit Liq. sehr nah am Entry).
- P2: KI-Aktion `adjust_sl` zusätzlich mit Max-Distanz-Sanity (z.B. 15% vom Kurs).
- P2: Cerebras-Modellliste aktualisieren (404 `llama-3.3-70b`, `qwen-3-32b` in
  den Render-Logs) + GitHub-Models-Retirement-Fallback prüfen.

## Umgesetzt (07.06.2026 / Iteration 11 „Endlos-Suche + Indikator-Sync + Worker-Fix")
1. **Local-Worker-Download permanent gefixt**: Ursache war, dass `local_worker/`
   (worker.py, requirements.txt, README.md, start_worker.bat/.sh) in KEINEM
   GitHub-Branch existierte – jeder Fork verlor das Paket. Jetzt: Worker v1.9.0
   komplett neu im Repo unter `/app/local_worker/`, `.gitignore`-Whitelist
   (`!local_worker/...`), Boot-Selfcheck in `routers/local_worker.py` (Error-Log
   wenn Dateien fehlen), Regressionstest `tests/test_worker_package.py`.
   Worker-E2E verifiziert (Backtest, Regime-Analyse 300d, Cancel <5s,
   Disconnect/Reconnect). Test-Worker-Installation: `/root/worker_test`.
2. **Gemeinsamer Indikator-Pool** `frontend/src/lib/indicatorPool.js`:
   23 Indikatoren (inkl. aller Etappe-5: Markt-Struktur, BOS, Liquidity Grab,
   Equal Highs/Lows, Support/Widerstand, Trendkanal, Range, EMA 200, FOMC) in
   6 Zweck-Gruppen (Trend / Momentum / Volatilität-Filter / Mean-Reversion /
   Liquidität-Smart-Money / Events) mit Hover-Erklärung je Indikator.
   Genutzt von Optimizer.js UND RegimeOptimizePanel.js (Regime Lab).
   Gruppen-Toggles + Alle-an/aus; beim Deep-Test/Endlos-Suche dürfen alle
   Gruppen gleichzeitig aktiv sein. **Heikin-Ashi entfernt** als
   Discovery-Kandidat (nur geglättete Kerzendarstellung, dupliziert Momentum);
   bestehende Strategien mit ha_color laufen weiter.
   Sync-Test Frontend↔Backend: `test_frontend_pool_matches_backend`.
3. **Endlos-Suche (Optimizer mode="explore")**, `services/deep_explore.py`:
   Hintergrund-Job, zufällige nie-getestete Kombis (frischer Seed je Lauf →
   nie zweimal dasselbe Ergebnis), gewichtete Auswahl nach Trefferquote +
   35% reine Exploration (auch „unbeliebte" Indikatoren kommen dran),
   positive Kombis → Feintuning (iterations) → Walk-Forward auf Holdout
   (Pflicht-Split, mode=single). Champion = Training positiv + Test-PnL > 0 +
   Konsistenz ≥ 40%. Top-5 je Lauf + globale Top-5 über alle Läufe
   (`GET /api/optimizer/explore/best`, Mongo settings/_id=deep_explore_best).
   Sanfter Stop: `POST /api/optimizer/explore/stop/{job_id}` (Bestes bleibt);
   läuft auch über Local Worker (Worker ≥1.9.0, „stop"-Flag in Progress-
   Antwort; Claim-Gate für alte Worker). Stop-Gründe: target_reached /
   stopped_by_user / time_limit / space_exhausted. UI: Modus-Karte, Felder
   Champions-Ziel/Zeitlimit/Training-%, Live-Phase mit Kombis/min, Stop-Button,
   Auswertungs-Block (Statistik, Trefferquote je Indikator, Near-Misses).
   Messung: ~8.400 Kombis/min bei 2 Tagen/5m/1 Coin (Cloud-Pod).
4. **Test-Hygiene**: 5 Testdateien nutzten das hartkodierte Prod-Passwort →
   lesen jetzt env/`/app/memory/test_credentials.md`; Worker-Versions-Asserts
   dynamisch statt hartkodiert; `custom_params` akzeptiert Regel-Alias
   `operator` → `op`.
- Verifiziert: Testing-Agent iteration_11 (Backend 100%, Frontend 100%,
  0 Issues) + pytest seriell grün (worker/optimizer/explore-Suiten).
- Bekannt VOR-bestehend (nicht neu): test_regime_engine synthetische
  Szenarien rot; test_regime_lab-Tests mit hartkodierten Analyse-IDs
  (Daten existieren nur in Prod-DB).

## Nächste Aufgaben (aktualisiert)
- P1: Etappe 6 Paper-Trading (dynamische Regime-Strategie im Paper-Modus).
- P1: Endlos-Suche: globale Top-5 im UI anzeigen + „Übernehmen"-Button.
- P2: Matrix-Frühwarnung am Chart; KI-Features (Übergangs-Matrix als Kontext).
