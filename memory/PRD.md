# PRD – Krypto_Trader (externe Daytrading-Website, Branch fixxxed-pls)

## Original-Problem
Der KI-Trader der extern laufenden Website (Render + Mongo Atlas, Repo:
github.com/dean06greif-ai/Krypto_Trader, Branch fixxxed-pls, geklont nach
/app/krypto_trader) wurde nach Updates deutlich schlechter. Verdacht des
Traders: Liquidations-/Heatmap-Daten, widersprüchliche Lektionen, teures
KI-Team (~2€/Tag Gemini). Vorgaben: Struktur der Dateien beibehalten (User
deployt selbst via Git), Stabilität & Rückwärtskompatibilität vor aggressiven
Änderungen, Regressionstests vor größeren Änderungen.

## User-Entscheidungen
- Heatmap/Liq-Daten kritisch prüfen und bei Bedarf per Schalter deaktivieren: JA
- Lektions-Konsolidierung (neueste Trader-Anweisung gewinnt): JA
- Kosten: NUR lean_prompt, Haupt-Modell NICHT wechseln
- Intervall/Calls smart optimieren: JA
- Fix-Vorschlag (Commit 3eaf7e2) war NICHT im Branch → neu implementiert

## Root Cause (bestätigt)
`services/liquidity_data.py::_model_clusters` erzeugt "Liquidations-Cluster"
rein per Formel (Preis ± 1/Hebel − Maintenance-Margin) – sie existieren IMMER,
für JEDEN Coin, an festen Abständen. Der Prompt in
`ai_engine._liquidity_block` nannte sie "Magnete – Umkehrpunkte, KEINE
Ausbrüche" → KI wurde systematisch in Fade-/Sweep-Trades an erfundenen Levels
gelockt.

## Umgesetzt (10.06.2026 / Session 1)
1. **Heatmap-Fix**: Config-Schalter `use_heatmap_data` (Default AUS) und
   `use_liquidation_data` (Default AN – echte Daten: L/S-Ratio, OI,
   Orderbook-Wände, Live-Liquidationen bleiben). Modell-Cluster werden, falls
   aktiviert, ehrlich als "MODELL-SCHÄTZUNG, niemals alleinige
   Trade-Begründung" gekennzeichnet; `_model_clusters` markiert Output mit
   `modelled: true`. Zwei neue Toggles im Governance-Panel
   (AIGovernancePanel.js).
2. **Lektions-Konsolidierung** (`services/ai_lessons.py`): TOPIC_KEYWORDS
   (leverage, break_even, cooldown, news_pause, Titel-Matching),
   `consolidate_conflicts` – neueste locked (Trader-)Lektion eines Themas
   gewinnt, ältere + KI-Lektionen desselben Themas werden `superseded`
   (bleiben gespeichert, fließen nicht in den Prompt). Neuer Endpoint
   `GET /api/ai/lessons/conflicts` (routers/ai_governance.py).
3. **Kosten**: `lean_prompt` (Default AN) entfernt PLATTFORM-WISSEN +
   PARAMETER-DER-ANDEREN-STRATEGIEN aus jedem Analyse-Lauf;
   Token-Schätzung pro Analyse in Log + Feed (`token_estimate`).
   `smart_skip` (Default AN): Gruppen-LLM-Lauf wird übersprungen wenn letzte
   Entscheidung überall HOLD, Preisbewegung < `smart_skip_move_pct` (0.15%),
   keine offene Position, max. 2 Skips in Folge; nie bei manuellen Läufen.
   Toggles in AITradingPanel.js.
4. **Tests**: 27 neue Unit-Tests (tests/test_ai_trader_quality_fixes.py) + 11
   Integrationstests (tests/test_ai_quality_fixes_integration.py, skippen
   sauber ohne laufenden Server, Credentials aus ENV). Regressionslauf: 466
   bestehende Unit-Tests grün; 6 Failures (test_regime_engine,
   test_ai_team_regression, test_iter_ai_supervisor_auto) existieren auch im
   unveränderten Git-Stand (pre-existing). E2E gegen lokalen Server (Port
   8022, lokale Mongo `crypto_scanner_test`, NICHT Produktiv-DB) grün.

## Architektur-Notizen
- Backend: FastAPI, routers/ + services/ + core/, Mongo via MONGO_URL
- KI-Engine: services/ai_engine.py (AIEngine, DEFAULT_AI_CONFIG,
  run_analysis mit Gruppen Krypto/Forex/Indizes), Provider-Ketten in
  ai_providers.py, Lektionen in ai_lessons.py (settings/ai_lessons)
- pytest.ini: xdist -n 2 --dist loadscope (nicht ändern)
- backend/.env ist gitignored; lokal für Tests: MONGO_URL=localhost,
  DB_NAME=crypto_scanner_test

## Umgesetzt (10.06.2026 / Session 2)
1. **BUG Watchdog**: Manuelle Bitunix-Positionen (kein lokaler Website-Trade
   bzw. external_adopted/strategy_id=external) werden nur noch sichtbar
   gemacht, aber NIE angefasst (kein SL-Zwang, kein Dust-Close, kein
   Notfall-Close). Neues Setting `manage_external` (Default False, Opt-in für
   altes Verhalten). SettingsPanel-Beschreibung angepasst.
2. **BUG Break-Even**: Exakte Formel inkl. Entry+Exit-Gebühren:
   LONG be=entry*(1+fee)/(1-fee), SHORT be=entry*(1-fee)/(1+fee) – in
   bitunix_trade._be_price UND backtester.be_price. Zusätzlich Bug behoben:
   Exchange-SL wird beim BE-Trigger (crv/profit_pct) nur noch gesynct, wenn er
   den SL verbessert (vorher konnte ein getrailter SL verschlechtert werden).
3. **Zeitzonen**: Backend rechnet komplett über core/timeutil (Europe/Berlin);
   Frontend-Formatierer ohne timeZone gefixt (AIRewardPanel, SettingsPanel).
4. **Echte Heatmap**: _LiqBuffer speichert jetzt Preise (Fenster 4h,
   DIST_WINDOW_SEC); measured_liq_distribution bucketet echte Force-Orders
   nach Seite. KI-Prompt nutzt GEMESSENE LIQUIDATIONEN statt Modell-Formel;
   /api/liquidity/heatmap/{symbol} liefert clusters_measured + clusters_source
   (measured, Fallback model für UI).
5. **Zeitplan-Modell**: Pro Zeitfenster optional eigenes KI-Modell/Provider
   (ai_schedule model/provider, effective_window; Analyst-Kette bekommt
   Fenster-Modell vorangestellt). Dropdown im AIScheduleEditor.
6. **Token-Dashboard**: _track_tokens zählt geschätzte Tokens pro Rolle &
   Berlin-Tag (collection ai_token_usage); GET /api/ai/token-usage; Anzeige im
   Setup-Panel des KI-Traders.
7. **20-Trade-Review**: _check_heatmap_review veröffentlicht nach 20
   geschlossenen KI-Trades seit dem Fix einmalig eine statistische Auswertung
   (ohne LLM-Kosten) in den KI-Feed (role learning, trigger heatmap_review).
8. **UI**: superseded-Lektionen grau/durchgestrichen mit Badge; Live links /
   Paper rechts (AutoTradeModal, AIStrategyLabPanel; Rest war schon korrekt).
9. **Tests**: 27 neue Tests (test_ai_trader_iter2_fixes.py), Watchdog-Tests an
   Sollverhalten angepasst; 497 Unit-Tests grün; Testing-Agent Iteration 2:
   100% bestanden (E2E gegen lokalen Server 8022).

## Backlog / Nächste Schritte
- P1: Modell-Kosten weiter senken (Option: Groq GPT-OSS 120B kostenlos)
- P1: Nach Deploy 20-30 Trades beobachten (20-Trade-Review kommt automatisch)
- P2: Watchdog manage_external-Toggle im UI (Backend-Setting existiert)
- P2: Heatmap-UI: gemessene Verteilung visualisieren (clusters_measured)
