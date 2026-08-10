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

## Backlog / Nächste Schritte
- P1: Modell-Kosten weiter senken (User wollte Modell vorerst behalten;
  Option: Groq GPT-OSS 120B kostenlos als Haupt-Analyst)
- P1: Nach Deploy 20-30 Trades beobachten, ob Winrate sich erholt
  (Heatmap aus = Trader-Lektion umgesetzt)
- P2: UI-Anzeige der superseded-Lektionen (grau/durchgestrichen) im Panel
- P2: Token-Verbrauch pro Tag im UI aggregieren (Daten liegen im Feed)
