# PRD – Krypto_Trader (Daytrading-Plattform, extern deployt auf Render)

## Original-Problem
Bestehende, produktiv laufende Daytrading-Website (GitHub `dean06greif-ai/Krypto_Trader`,
zuletzt Branch `fixxxed-pls`). KI Trader wurde nach Updates deutlich schlechter; Verdacht:
Liquidations-/Heatmap-Daten, widersprüchliche Lektionen, teures KI-Team (~2€/Tag Gemini).
Vorgaben: bestehende Datei-Struktur beibehalten (User deployt via Save-to-GitHub → Render),
Stabilität & Rückwärtskompatibilität, Regressionstests vor größeren Änderungen.

## WICHTIG: Arbeitsverzeichnis
Die App liegt DIREKT in /app (Standard-Setup): backend/ (server.py als App-Assembly +
services/ + routers/ + core/ + models/ + strategies/ + tests/), frontend/, local_worker/,
tests/. Supervisor: Backend 8001, Frontend 3000, extern via REACT_APP_BACKEND_URL.
NIEMALS in einen Unterordner klonen – Struktur muss fürs Render-Deploy 1:1 stimmen.

## Root Cause KI-Trader (bestätigt, Session 1)
liquidity_data._model_clusters erzeugte "Liquidations-Cluster" rein per Formel
(Preis ± 1/Hebel) – immer vorhanden, für jeden Coin; der Prompt nannte sie "Magnete –
Umkehrpunkte" → KI wurde in Fade-Trades an erfundenen Levels gelockt.

## Umgesetzt Session 1 (10.06.2026)
- Schalter use_heatmap_data (Default AUS) / use_liquidation_data (Default AN, echte Daten
  L/S-Ratio, OI, Wände, Live-Liqs); Toggles im Governance-Panel
- Lektions-Konsolidierung (ai_lessons): TOPIC_KEYWORDS (leverage, break_even, cooldown,
  news_pause; Titel-Matching), neueste locked Trader-Lektion gewinnt, ältere superseded
  (bleiben gespeichert, nicht im Prompt); GET /api/ai/lessons/conflicts;
  LessonStore.all() liefert Flags immer on-the-fly konsolidiert
- Kosten: lean_prompt (Default AN, statische Blöcke raus) + smart_skip (Default AN,
  Gruppen-Lauf überspringen wenn HOLD+ruhig+keine Position, max 2 Skips in Folge) +
  Token-Schätzung im Log/Feed

## Umgesetzt Session 2 (10.06.2026)
- BUG Watchdog: manuelle Bitunix-Positionen (kein lokaler Website-Trade bzw.
  external_adopted) werden nur sichtbar gemacht, NIE angefasst; Setting manage_external
  (Default False, Opt-in altes Verhalten)
- BUG Break-Even: exakt inkl. Entry+Exit-Gebühren: LONG be=entry*(1+fee)/(1-fee),
  SHORT be=entry*(1-fee)/(1+fee) – bitunix_trade._be_price UND backtester.be_price;
  Exchange-SL-Sync beim BE-Trigger nur wenn Verbesserung
- Zeitzonen: Backend komplett core/timeutil (Europe/Berlin); Frontend-Formatierer ohne
  timeZone gefixt (AIRewardPanel, SettingsPanel)
- Echte Heatmap: _LiqBuffer speichert Preise (DIST_WINDOW_SEC=4h);
  measured_liq_distribution bucketet echte Force-Orders; Prompt nutzt GEMESSENE
  LIQUIDATIONEN statt Modell; /api/liquidity/heatmap/{symbol}: clusters_measured +
  clusters_source (Fallback model)
- Zeitplan-Modell: pro Fenster optional model/provider (ai_schedule.effective_window;
  Analyst-Kette bekommt Fenster-Modell vorangestellt); Dropdown im AIScheduleEditor
- Token-Dashboard: _track_tokens pro Rolle & Berlin-Tag (collection ai_token_usage);
  GET /api/ai/token-usage; Anzeige im KI-Setup-Panel
- 20-Trade-Review: _check_heatmap_review postet nach 20 geschlossenen KI-Trades einmalig
  Statistik-Report in den Feed (role learning, trigger heatmap_review, ohne LLM-Kosten)
- UI: superseded-Lektionen grau/durchgestrichen; Live links / Paper rechts
  (AutoTradeModal, AIStrategyLabPanel)

## Umgesetzt Session 3 (10.06.2026) – Struktur-Fix
- BUG: App lag in /app/krypto_trader (nur kaputter Git-Pointer getrackt), /app enthielt
  Template-Backend → Push hätte Struktur zerstört. FIX: komplette Original-Struktur nach
  /app gespiegelt, krypto_trader entfernt, Standard-Setup (Supervisor 8001/3000)
- Header: LIVE-Badge links vom PAPER-Widget (Header.js, live-overlay vor balance-widget)
- Testing-Agent Iteration 3: 100% (Struktur, Endpoints via externe URL, 95 Unit-Tests,
  Frontend-Smoke inkl. KI-Trader-Tab)

## Tests
- Neue Regressionstests: tests/test_ai_trader_quality_fixes.py (27),
  tests/test_ai_trader_iter2_fixes.py (27), test_ai_quality_fixes_integration.py (11 E2E,
  skippt ohne Server), test_position_watchdog.py an neues Sollverhalten angepasst
- Pre-existing Failures (auch im Original-Repo, NICHT von uns): test_regime_engine,
  test_ai_team_regression, test_iter_ai_supervisor_auto, test_iter2_review_e2e,
  test_backtest_optimizer (E2E gegen fremde URLs)

## Backlog / Nächste Schritte
- P1: Modell-Kosten weiter senken (Option: Groq GPT-OSS 120B kostenlos als Analyst)
- P1: Nach Deploy 20-30 Trades beobachten (20-Trade-Review kommt automatisch)
- P2: Watchdog manage_external-Toggle im UI (Backend-Setting existiert)
- P2: Heatmap-UI: gemessene Verteilung im Chart visualisieren (clusters_measured)
- P2: Telegram-Warnung bei ungewöhnlich hohem Token-Verbrauch
