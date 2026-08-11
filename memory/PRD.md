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

## Umgesetzt Session 4 (11.06.2026) – Abgebrochenes Update fortgesetzt
- FIX KI-Team-Crash ("Etwas ist schiefgelaufen"): ReferenceError `models is not defined`
  in AIScheduleEditor.js (Abbruchstelle des alten Updates). Modellkatalog jetzt zentral in
  frontend/src/lib/aiModels.js (Import in AITradingPanel + AIScheduleEditor)
- Chat-Scroll: beim Zurückwechseln aus KI-Labor/Team/etc. springt der Chat automatisch ans
  NEUESTE Ende (chatVisible-Effect in AITradingPanel)
- Lektionen nummeriert: Badge `.ai-lesson-no` im Lernen-Reiter, Nummer = `no` aus
  LessonStore (identisch mit KI-Prompt-Nummerierung); /api/ai/insights liefert Lektionen
  jetzt aus lesson_store.all() inkl. `no`; superseded explizit no=null
- Modell-Status: Dropdown + Warn-Banner zeigen jetzt "Letzte Ausfälle" mit betroffenem
  Assistenten (Rolle), Ursache (Rate-Limit / Fehler / Prompt zu groß) und Fallback
- Desktop-Layout: Signal-/Regel-Panel ohne eigenen Scrollbereich, ganze Seite scrollt
  (App.css: app-layout min-height, chart-wrap clamp-Höhe, coin-sidebar + right-panel sticky)
- Chart: Tooltip-Text "der Chart bleibt immer live" entfernt; EMA-200-Warmup verifiziert
  (Pixel-Scan: Linie über 97% der Chartbreite)
- Strategie-Vergleich: sc-name mit title-Attribut (voller Text als Tooltip, Ellipsis-Kürzung)
- Bereits vorhanden & verifiziert (kein Re-Fix nötig): Toast-Dedupe (lib/toast.js, 5-min
  Fenster, Wiederholungen → Glocke), Mobile-Notif-Dropdown-Fix, Lektionen per Chat sofort
  aktiv, Liquidations-/Heatmap-Erklärung (gemessene Force-Orders + Schätzungs-Fallback),
  Preis-Leistungs-Modelle + Fallback-Ketten (Groq/Gemini/OpenRouter/Cerebras/Mistral)
- Testing-Agent Iteration 4: Backend 100%, Frontend 100% (read-only gegen Prod-Atlas)

## Umgesetzt Session 5 (11.06.2026) – Tiefen-Audit Berechnungen/Daten-Frische
- Audit-Ergebnis: Indikator-Mathematik korrekt (vec.py EMA/RSI/ATR/ADX Wilder-Standard,
  technical_indicators Referenz, Timeframe-Aggregation bucket-korrekt inkl. drop_partial,
  PnL/Fees/Liq-Preis/Break-Even-Formeln in bitunix_trade & core/utils verifiziert)
- NEU Frische-Wächter (core/scheduler.py): formende Kerze älter als 4 min (Krypto/Exchange)
  -> Preis wird NICHT mehr für SL/TP-Überwachung & Signal-Auswertung verwendet (gedrosseltes
  Warn-Log); Yahoo-Instrumente (Gold/Öl, Handelspausen) ausgenommen
- NEU Lücken-Backfill (core/scheduler.py): fehlen Minuten zwischen Puffer und neuen Kerzen
  (Loop-Stau/Quellen-Ausfall), werden bis 200 Kerzen nachgeladen statt still verloren
  (verhindert EMA-/Aggregations-Verzerrung); alle geschlossenen Kerzen werden übernommen
- Tests reparierbar gemacht: tests/conftest.py lädt backend/.env + frontend/.env;
  24 Testdateien: hartkodiertes "admin"-Passwort -> os.environ (Suite lief vorher mit 401)
  -> Suite jetzt 1115 passed (vorher 963), Rest = Umgebungs-/E2E-Flakes (Cloudflare,
  parallele Jobs, Bitunix-configured-Annahmen), KEINE Rechenfehler gefunden
- .gitignore: !local_worker/ Whitelist wiederhergestellt (Render-Deploy-Schutz, Test grün)
- Neue Regressionstests: tests/test_scanner_freshness.py (7 Tests: Backfill + Staleness)
- Hinweis: Groq/OpenRouter-Backup-Key lieferte im Test 402 quota/payment -> Budget prüfen

## Umgesetzt Session 6 (11.06.2026) – User-Wünsche + Ausfall-Diagnose
- Watchdog: enabled-Toggle im Settings-UI (komplett aus = keine Prüfzyklen/Eingriffe;
  /watchdog/run liefert dann skipped), POST /api/autotrade/watchdog/clear (Admin) löscht
  Status-Report + 'Extern (Watchdog)'-Trades (Confirm im UI, watchdog-clear-btn)
- Watchdog fasst manuelle Positionen NIE an: _find_local (Position-ID-Match zuerst) +
  Misch-Schutz (Börsen-Menge > Website-Menge*1.05 => enthält manuellen Anteil => skip)
- Strategie-Vergleich: strategy_id 'external' ausgeschlossen (auch open_counts)
- KI-Trader Live-Daten: _snapshot nutzt formende Kerze (<4 min) als Preis statt letzter
  Schlusskerze
- Benachrichtigungen: popped-Flag (Popup nur 1× insgesamt, POST /api/notifications/popped),
  source+meta (Rolle/Modell/Ursache/Detail/Fallback) an website_notify; Glocke scrollbar
  (max-height 60vh), zeigt Quelle+Uhrzeit+Meta; GET limit bis 200
- KI-Komplettausfall (Telegram+Glocke): notify_ai_failure mit failures-Details pro Modell
  (Ursache + Fehlertext, Markdown-sanitized, Berlin-Zeit); generate_chain sammelt
  failure_details und nutzt echte Rolle statt 'KI-Anfrage'
- ML-Labor & Forschung resetbar: POST /api/ai/ml/reset, /api/ai/research/reset (Admin,
  Confirm-Buttons im KI-Labor-Panel)
- Belohnungssystem löschbar: DELETE /api/ai/rewards (Backfill-Sperre via
  settings/ai_rewards_state.cleared_at), Trash-Button im AIRewardPanel (Confirm)
- Zurückgestellte Lektions-Wünsche: skipped_items (id/title/detail/reason/approvable/ts)
  in settings/ai_lessons; POST /api/ai/lessons/skipped/approve (=> locked Lektion) und
  /delete; UI Haken+Mülleimer im Lernen-Reiter (ai-skip-approve-*/ai-skip-delete-*)
- Strategie-Kandidaten: Duplikat-Block in create_candidate (Name case-insensitive) +
  dedupe_candidates (beim Start, 5 Duplikate entfernt) + POST /api/ai/strategies/dedupe
- Telegram 'Bot Connected' max 1×/24h (settings/telegram_state.last_connect_msg_at) –
  Render-Neustarts spammen nicht mehr
- Lokal: ADMIN_PASSWORD=TestAdmin2026! (nur Preview, Original war maskiert übermittelt)
- Testing-Agent Iteration 7: Backend 32/32, Frontend 100%
  (tests/test_iter7_watchdog_lessons_notify.py – seriell ausführen, -o addopts='')

## Backlog / Nächste Schritte
- P1: Groq-Prompt>Budget-Skips: Prompt-Größe nur nach Rücksprache optimieren (User will
  keine Qualitätsverluste; Modelle ggf. selbst anpassen)
- P1: Modell-Kosten weiter senken (Option: Groq GPT-OSS 120B kostenlos als Analyst)
- P1: Nach Deploy 20-30 Trades beobachten (20-Trade-Review kommt automatisch)
- P2: Heatmap-UI: gemessene Verteilung im Chart visualisieren (clusters_measured)
