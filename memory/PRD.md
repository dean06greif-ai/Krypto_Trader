# PRD – Daytrading-Website (extern/ausgelagert, dynamischeStrat)

## Original-Problemstatement (Juni 2026)
Bestehende, produktiv laufende Daytrading-Website (GitHub: AntonHeinrich05/dynamischeStrat,
Branch 28071303-weiterebugfixes) soll verbessert werden:
- Regime-Erkennung prüfen (wird Seitwärts wirklich seitwärts erkannt?) + Regime am Chart anzeigen
- Übersicht/Funktion: Regime für Timeframe/Zeitraum/Coins suchen & unter der Konfiguration speichern;
  beides: Regime für alle Coins zusammengefasst UND je Coin einzeln; Ähnlichkeit vergleichen
- Regime behalten/verwerfen können
- Strategie-Discovery + Optimierer direkt für EIN ausgewähltes Regime (alle Einstellungen:
  Indikator-Auswahl, Iterationen, Ziel, Min-Trades, Regeln, Trade-Räume, Timeframe)
- Top-5-Ergebnisse, Auswahl bestätigen, Regime für Regime durchgehen, dann dynamische Strategie bauen
- Finale dynamische Strategie per Walk-Forward auf unangetastetem Holdout testen (kein Lookahead,
  identisch zu Live/Paper)
- Später: Walk-Forward-Verbesserung des normalen Optimizers (mehr Zeit, sinnvollere Kombinationen)

Grundsatz des Nutzers: Website läuft produktiv – Änderungen sauber, modular, rückwärtskompatibel,
in bestehende Architektur einfügen, sehr customizable.

## Architektur
- Backend: FastAPI (Port 8001), Router-Module in backend/routers, Services in backend/services,
  MongoDB via MONGO_URL. Marktdaten: Bitunix (ohne API-Key). Admin-Auth: JWT, Passwort "admin".
- Frontend: React (CRA + craco), recharts, Overlay-Panels (Optimizer, Backtester, NEU: Regime-Lab).
- Bestehende Regime-Logik: services/regime.py (K-Means, rückblickende Features, kein Lookahead),
  services/dynamic_strategy.py (Segmente, Discovery/Optimierung je Regime), routers/dynamic.py.

## Umgesetzt (28.07.2026) – Regime-Lab (komplett getestet, Iteration 1+2)
1. Regime-Label-Fix (services/regime.py): Beschriftung jetzt aus ROHEN Feature-Mitteln
   (Trendstärke = |Trend|/Volatilität statt z-Score) → Seitwärts ist wirklich seitwärts.
   Zusätzlich stats je Regime (trend_pct/Tag, vol, Effizienz, Trendstärke).
   relabel_regimes(): Migration alter gespeicherter Modelle beim Laden.
2. services/regime_lab.py: Analyse-Jobs – Regime clustern (kombiniert + je Coin), Segmente
   + komprimierter Kursverlauf gespeichert (Mongo regime_analyses), Coin-Ähnlichkeitsmatrix,
   Holdout via train_pct (Modell nur auf Trainingsteil), Reuse-Helfer (regime_ranges,
   segments_from_ranges – auch für abweichenden Timeframe).
3. services/regime_opt.py: run_regime_optimizer (Discovery/Params/Combo NUR auf Abschnitten
   eines Regimes, Top-5, Walk-Forward innerhalb der Phase, Fallback: beste nicht-validierte
   Kombination wird markiert angeboten) + run_walkforward (Holdout-Test der zusammengestellten
   dynamischen Strategie vs. beste Einzelstrategie, Verdict, Equity-Punkte).
4. routers/regime_lab.py: /api/regime-lab/* (analyze, status, active, cancel, list, get [mit
   Label-Migration], delete, keep, optimize, assign, build [erzeugt dynamic_strategies-Doc,
   kompatibel mit bestehender Live-Umschaltung], walkforward). Verworfene Regime werden bei
   optimize/build/walkforward übersprungen; Validierung regime_id → 400.
5. Frontend: components/RegimeLab.js (Overlay, Workflow 1-4), RegimeChart.js (Preis + farbige
   Regime-Bänder + Holdout-Linie + Legende), RegimeOptimizePanel.js (alle Optimizer-Einstellungen
   je Regime, Top-5 mit "Für dieses Regime übernehmen"), RegimeLab.css; Header-Button (ChartScatter).

## Umgesetzt (28.07.2026, Teil 2) – Local-Worker-Support fürs Regime-Lab
- Alle Regime-Lab-Jobs (Analyse, Regime-Strategie-Suche, Walk-Forward) laufen wahlweise
  auf dem lokalen Worker: execution:"local" in analyze/optimize/walkforward.
- services/local_exec.py: Job-Typ "regime_lab" (_get_job, apply_result mit serverseitiger
  Persistierung via regime_lab.persist_worker_result), worker_supports_regime_lab(),
  REQUIRED_WORKER_VERSION 1.5.0, Versions-Gate im claim() (alte Worker bekommen keine
  regime_lab-Jobs – wichtig bei gemischter Flotte).
- services/regime_lab.py: persist_analysis/persist_worker_result; run_analysis db-los fähig
  (Worker schickt analysis_doc zurück). services/regime_opt.py: _load_doc (analysis_doc im
  Payload statt Mongo), Persist-Guards, Multi-Core-Pool (_make_seg_pool über parallel_sim,
  aktiv nur bei SIM_WORKERS>1 = lokaler Worker) für Optimierung UND Walk-Forward.
- routers/regime_lab.py: execution-Branch, 503 ohne Worker, 409 mit Update-Hinweis bei
  Worker < 1.5.0; Analyse-Doc (ohne chart) wird in den Job-Payload eingebettet.
- local_worker/worker.py: Version 1.5.0, handle_regime_lab (alle 3 fn), Cancel-Handling.
  Worker-Paket-Download bündelt services/*.py automatisch → Nutzer muss Paket 1x neu laden.
- Frontend RegimeLab.js: Ausführungs-Toggle Cloud/Lokal (mit Online-Punkt, Zahnrad öffnet
  LocalWorkerPanel), gilt für alle Regime-Lab-Jobs, in localStorage persistiert.
- E2E real verifiziert: Test-Worker im Pod (v1.5.0) hat Analyse, Multi-Core-Optimierung
  (8 Kerne) und Walk-Forward lokal gerechnet; Ergebnisse serverseitig persistiert; Jobs
  gingen NICHT an den parallel verbundenen alten Nutzer-Worker (v1.4.1).
- Iteration 3: Regression bestanden; 1 Frontend-Crash (execution nicht in AnalysisDetail
  destrukturiert) gefixt und per Screenshot verifiziert.

## Backlog / Nächste Aufgaben (priorisiert)
- P1: Walk-Forward des normalen Optimizers verbessern: mehr Zeit/Budget beim Indikator-Testen,
  sinnvollere Regel-Kombinationen (z.B. Trend+Volumen-Paare bevorzugen), bessere Ergebnisse
  bei langen Zeiträumen (Nutzer-Wunsch "später, erst Regime-Workflow").
- P1: Dynamische Strategie auf EINEN Coin optimieren (per_coin-Workflow ist im Regime-Lab
  bereits möglich – ggf. Shortcut/Empfehlung im UI).
- P2: Vergleich mehrerer Regime-Analysen (verschiedene Timeframes/Zeiträume nebeneinander).
- P2: Regime-Lab-Ergebnisse in das Lern-Gedächtnis (services/learning.py) einspeisen.
- P2: Lokaler Worker-Support für Regime-Lab-Jobs (aktuell Cloud/sequenziell).

## Test-Status
- iteration_1.json: 2 kritische Bugs gefunden (Label-Migration, kept-Filter) + 1 minor (500 statt 400) → alle gefixt.
- iteration_2.json: 4/4 fokussierte Backend-Tests bestanden, Regression ok. Frontend-Flows in Iteration 1 zu 100% bestanden.
- Demo-Analyse "Test-Analyse" (ra_c8206904, BTC/ETH, 15m, 60d, Training 75%) mit 2 bestätigten
  Strategien + Walk-Forward-Ergebnis ist als Beispiel gespeichert.

---

## Iteration 17 (Juni 2026) – Forex, Indices, Resources + KI-Lektions-Bugfix
Details siehe `/app/memory/PRD_iteration17_assets.md`.

Kurzfassung:
- Asset-Universum von 13 auf 22 Instrumente erweitert; einzige Quelle der Wahrheit ist
  `backend/core/instruments.py`. Gruppen: TOP 10 COINS / RESOURCES (Gold, Silber, Öl) /
  INDICES (QQQUSDT, SPYUSDT) / FOREX (7 Majors).
- `backend/services/history_sources.py` löst die 1m-Historie pro Symbol auf
  (Binance / Bitunix / Yahoo); `candle_cache` bleibt der einzige Cache-Layer.
- Backtester, Optimizer und Local Worker akzeptieren alle Assets (`BACKTEST_SYMBOLS`).
- Bitunix listet keine FX-Kontrakte → Forex ist Scanner/Backtest/Optimizer/Paper-fähig,
  Live-Orders werden dort automatisch auf Paper heruntergestuft.
- Bugfix KI-Trader: `ai_learning.merge_lessons()` führt Lektionen zusammen statt sie pro
  Lauf zu ersetzen – dadurch wirkt `max_lessons` (bis 50) endlich.
- Offene P1-Punkte: längere Forex-Historie (Yahoo = ~30 Tage), Fetch-Fehler beim
  initialen Frontend-Load, widersprüchliche Admin-Passwörter in Alt-Tests.

---

## Iteration 18 (29.06.2026) – Regime-Engine v2 + NNFX-Framework

### Problem (Nutzer)
Regime wurden falsch erkannt: nur 2 Cluster, "leicht abwärts" bei starkem Absturz,
Trends zu spät oder gar nicht, Labels passten nicht zum Chart. Wunsch: mathematisch
belastbare Regime-Erkennung (Regression, ADX, Volatilität, Multi-Timeframe, Hysterese,
Confidence), 9er-Taxonomie + Mapping auf 3 NNFX-Regime, NNFX als echtes Modul mit
3 Strategien, automatische Strategie-Umschaltung (optional mit manueller Bestätigung),
Validierung der Regime, viele Einstellmöglichkeiten, alles im Regime-Lab an einem Ort.

### Umgesetzt
1. `backend/services/regime_features.py` – vektorisierte, rein rückblickende Mathematik:
   rollierende OLS (Steigung, **t-Wert**, R²), Wilder-ADX/DI+/DI-, ATR%, realisierte Vola,
   Kaufman-Effizienz, rollierender z-Wert, Donchian (auch zeitversetzt), Varianz-Verhältnis
   (Lo/MacKinlay), Run-Length. 200k Bars in ~1 s.
2. `backend/services/regime_engine.py` – Engine v2 (feste Taxonomie statt Clustering):
   Trend-Score = gewichteter t-Wert über mehrere Horizonte (Standard 5/10/20/50/100 Tage)
   × Horizont-Konsens × DI-Bestätigung; Vola-Stufe über z-Wert des geglätteten ATR%;
   Range-Filter (Trend-Einstieg nur bei neuem Extrem / Bestätigung des langen Horizonts /
   Timeout / sehr starkem Score); Zustandsautomat mit Hysterese, Bestätigungsdauer,
   Mindesthaltedauer, Confidence-Schwelle. 9 Regime (Trend × Vola) mit festen IDs/Labels
   + NNFX-Mapping (trend/range/breakout). `validate_labels()` prüft jedes Segment gegen
   den echten Kursverlauf (Abschnitt, Sichtfenster, langer Kontext, Vola-Stufe);
   `ideal_labels()` liefert die Rückblick-Sicht (NUR Anzeige/Kontrolle, nie im Backtest).
   ~47 Konfigurations-Keys, jeder mit Label/Erklärung (CONFIG_META) fürs Frontend.
3. `services/regime.py` – `DEFAULT_ENGINE="v2"`, `is_v2()`, Dispatch in `detect_regimes`
   (neu: `engine`, `engine_config`), `classify_series`, `current_regime`, `relabel_regimes`.
   K-Means bleibt als `engine="kmeans"` erhalten (alte gespeicherte Modelle laufen weiter).
4. `services/regime_lab.py` + `routers/regime_lab.py` – Engine-Auswahl je Analyse,
   gespeicherte Validierung/aktuelles Regime/Rückblick-Vergleich je Coin,
   `GET /api/regime-lab/engine/defaults`, `POST /api/regime-lab/{aid}/build-nnfx`
   (idempotent, schreibt zusätzlich Regime-Zuordnungen für den bestehenden Walk-Forward).
5. NNFX-Modul `backend/strategies/nnfx_strategies.py` – NNFX Trend / Mean-Reversion /
   Breakout (14–18 Parameter je Strategie, gemeinsame Signalberechnung für Live und
   vektorisierten Backtest). Neue Indikatoren in `services/vec.py` + `FastSeries`:
   ADX/DI+/DI-, CCI, Keltner, Donchian.
6. Automatische Umschaltung: `dynamic_live.apply_regime_strategies()` aktiviert je Coin die
   Strategie des aktuellen Regimes (Coin-Toggles + Trade-Parameter), `require_confirmation`
   pro Bot erzeugt `pending_switch` statt automatisch zu schalten;
   `POST /api/dynamic/{id}/confirm` / `/dismiss`.
7. Frontend: `RegimeEngineSettings.js` (Engine-Wahl + generische Feineinstellungen),
   `RegimeValidation.js` (Prüfbericht), RegimeLab (aktuelles Regime je Coin, NNFX-Tags,
   Rückblick-Band im Chart, "NNFX-Framework anwenden", Abschnitt 4 mit DynamicPanel),
   DynamicPanel (NNFX-Badge, aktive Strategie je Coin, manuelle Bestätigung, Begründung).
8. Tests: `tests/test_regime_engine.py` (28), `tests/test_nnfx.py` (17),
   `tests/regime_scenarios.py` (11 synthetische Märkte), `scripts/regime_report.py`
   (Diagnose-Tabelle), `tests/test_regime_v2_integration.py` (16 API-Tests, Testing-Agent).
   Realdaten BTC/ETH 12h/720d: 9 Regime, Validierung bestanden, Richtungstreffer 80 %,
   Ø Abschnitt ~12 Tage, Übereinstimmung mit Rückblick-Sicht 82 %.

### Offen / nächste Schritte
- P1: Optimierung der NNFX-**Strategieparameter** je Regime (aktuell optimiert der
  Regime-Optimizer die Trade-Parameter; Strategie-Parameter-Suche wäre der nächste Schritt).
- P1: Regime-Wechsel-Frühwarnung (z. B. Score-Momentum / Wahrscheinlichkeit des Wechsels)
  in Regime-Lab und Live-Panel anzeigen.
- P2: KI-Kommentar zur Regime-Lage (bestehende KI-Anbindung, nur Komfort – Erkennung bleibt
  rein mathematisch).
- P2: Varianz-Verhältnis-Filter auf Realdaten evaluieren (Standard aus).
- P2: Chart je Strategie-Detail (Equity + Regime-Bänder) im Dynamik-Panel.
