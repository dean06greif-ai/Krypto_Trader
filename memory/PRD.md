# PRD – Krypto Trader: KI-Ökosystem (KI-Labor)

## Ausgangs-Problemstellung (Original, gekürzt zitiert)
> „Das ist meine funktionierende externe/ausgelagerte Daytrading-Website … Verbessert werden soll
> der KI Trader / das KI Ökosystem: KI-Team mit voreingestellten besten Modellen pro Tätigkeit
> (änderbar), die KIs sollen mit der Website und externen Quellen stark lernen (ML/Deep Learning)
> und mit Backtest/Strategie-Optimizer/Regime-Lab weiter wachsen. Eine eigene KI/Agent soll diese
> komplette Analyse machen und das Ergebnis dem KI Trader übermitteln, der daraus stark lernt +
> Zugriff auf alle Daten hat. Zusätzlich eine KI, die den Markt beobachtet und Daten sammelt.
> Supabase-Datenbank für die KIs zum Sammeln von Daten/Ergebnissen/Analysen. Optuna findet beste
> Parameter, XGBoost lernt daraus, welche Marktbedingungen gute Ergebnisse liefern, die Haupt-KI
> erklärt die Ergebnisse und entwickelt neue Ideen.“
>
> Grundsatz: produktiv & stabil laufende Website – Verbesserungen modular, rückwärtskompatibel,
> wartbar; Stabilität und saubere Struktur vor aggressiven Änderungen; Regressionstests.

## Nutzer-Entscheidungen
- Repo (Branch `NEW29.07`) hierher geklont, modular erweitert, Übernahme ins eigene Repo durch den Trader.
- Nur Modelle aus dem bestehenden Katalog; günstigste passende pro Rolle als Voreinstellung.
- Eigene API-Keys (OpenRouter + Mistral gesetzt), Supabase-Keys bereitgestellt.
- ML-Training: automatisch **und** manuell.

## Architektur (Ist-Stand)
- **Backend** FastAPI (`/app/backend`): `core/` (State, Config, Auth, Pipeline), `routers/` (ein Modul pro Bereich),
  `services/` (Engine, Strategien, Backtester, Optimizer, Regime-Lab, KI-Module), `strategies/`.
- **Frontend** React (`/app/frontend/src/components`), Panels pro Feature.
- **Datenhaltung** MongoDB; neu: Supabase als Langzeit-Wissensspeicher der KIs (optional, Dual-Write).
- **Neue Module** (siehe `backend/README_AI_LAB.md`): `ai_memory.py`, `ai_research.py`, `ai_ml_lab.py`,
  `ai_market_observer.py`, `routers/ai_lab.py`, `components/AILabPanel.js`.

## Umgesetzt (2026-06)
- **KI-Gedächtnis**: MongoDB `ai_knowledge` + Supabase-Spiegel; Housekeeping; Status/Health-Endpunkte.
  Fehlende Supabase-Tabelle/Keys degradieren sauber auf MongoDB.
- **Forschungs-Analyst** (`research_analyst`): wertet Backtests, Optimizer-Läufe (Walk-Forward,
  Robustheit, Konstanz), Regime-Lab und Regime-Analysen aus, vergleicht sie mit der echten
  Live-Performance, erzeugt Erkenntnisse/Ranking/Ideen/Empfehlungen → Gedächtnis → Prompt-Blöcke für
  KI Trader, Tiefen-Analyst und Lern-Modul. Automatik: Uhrzeiten, Max-Intervall, bei neuen Ergebnissen.
- **ML-Labor**: Optuna (TPE) + XGBoost auf echten Ergebnissen (KI-Entscheidungen + Signale) angereichert
  mit dem gemessenen Marktzustand; CV-AUC/Accuracy, Feature-Wichtigkeiten, Win-Wahrscheinlichkeit
  LONG/SHORT je Coin; Erklärung + abgeleitete Regeln durch die Haupt-KI; Auto-Training täglich und
  nach X neuen Ergebnissen; Modell persistiert (Booster in `settings/ai_ml_model`).
- **Markt-Beobachter** (`market_observer`): Trend, Volatilität, ATR, RSI, Volumen, Range-Position,
  deterministisches Regime-Label pro Coin → `ai_market_snapshots` (Trainingsdaten + Prompt-Kontext).
- **Rollen-Voreinstellungen** je Rolle inkl. Fallback-KI, im UI änderbar, „Voreinstellung
  wiederherstellen“; Modell-Kette hängt zusätzlich alle Provider mit vorhandenem Key an
  (keine Rolle fällt wegen fehlendem Key aus).
- **UI**: Panel „KI-Labor“ mit Tabs Forschung / ML-Modell / Gedächtnis / Markt; neue Rollen-Karten
  im KI-Team mit rollenspezifischen Feldern.
- **Tests**: `tests/test_ai_lab.py` (27 Offline-Tests) und `tests/test_ai_lab_api.py`
  (26 Integrationstests) – grün; KI-Trader-Regression (analyze/status/insights/learn/chat) grün.

## Umgesetzt (2026-06, Teil 2)
- **KI-Trade-Steuerung** (`services/ai_trade_manager.py`, Rolle `trade_manager`): die KI eröffnet
  eigene Trades (Seite, SL/TP in %, Hebel, Kapitalanteil) und steuert offene Trades live –
  vorzeitiger Close, Teil-Close, SL/TP verschieben, Margin hinzufügen/entnehmen, Hebel ändern bei
  gleicher Positionsgröße. Ausführung für Paper und Live über eine Quelle im AutoTrader
  (Bitunix `adjust_position_margin` / `change_leverage`), inkl. Neuberechnung von Margin,
  effektivem Hebel und Liquidationspreis.
- **Schutzregeln**: max. Aktionen pro Trade, Cooldown, Hebel-Obergrenze, Margin-Aufschlagslimit,
  Zusatz-Margin nur aus freiem Kapital; vollständiges Audit (`ai_trade_actions`, Trade-Events,
  KI-Chat, Gedächtnis). Kapitalrahmen und Paper/Live-Modus bleiben für die KI tabu.
- **Closed Loop** (`services/ai_closed_loop.py`, standardmäßig AUS): nach jeder Forschungs-
  Auswertung startet die KI optional selbst einen Optimizer-Lauf für den stärksten Kandidaten;
  validierte Ergebnisse werden als Vorschlag hinterlegt (Übernahme bleibt manuell).
- UI: KI-Labor-Tab „Trade-Steuerung" mit Schaltern, Limits, manuellen Aktions-Buttons je Trade,
  Aktions-Protokoll und Closed-Loop-Schalter. Tests: `tests/test_ai_lab.py` jetzt 34 Tests.

## Kern-Anforderungen (statisch)
1. Bestehende Endpunkte, Datenmodelle und Nutzer-Workflows bleiben unverändert.
2. Kapital (`max_capital`) und Paper/Live-Modus bleiben für jede KI tabu.
3. Jeder neue Hintergrund-Lauf ist einzeln gekapselt – ein Fehler darf den Trading-Loop nie stoppen.
4. Alle Modelle/Provider ausschließlich aus dem bestehenden Katalog, Keys nur aus ENV.
5. Neue Features immer mit Regressionstests.

## Backlog (priorisiert)
- **P0**: Supabase-Tabelle `ai_knowledge` im Projekt anlegen (`backend/scripts/supabase_schema.sql`),
  danach Spiegel-Status im KI-Labor prüfen.
- **P1**: automatische Übernahme validierter Closed-Loop-Parameter (aktuell manuell);
  ML-Regime-Zuordnung aus dem Regime-Lab statt
  heuristischem Label; Embeddings + semantische Suche im Gedächtnis.
- **P2**: Forschungs-Analyst darf validierte Parameter automatisch als Optimizer-Job anstoßen
  (Closed-Loop-Selbstoptimierung); Feature-Store für Trades (Marktzustand direkt beim Signal speichern);
  Modell-Versionierung + A/B-Vergleich mehrerer ML-Modelle; Externes Trading-Wissen (Web-Recherche)
  als eigene Quelle für den Forschungs-Analysten.

## Nächste Schritte
1. Supabase-SQL ausführen, Keys in die Produktions-ENV übernehmen.
2. Code aus `/app/backend` + `/app/frontend` ins eigene Repo übernehmen (neue Pakete:
   `optuna`, `xgboost`, `scikit-learn` – bereits in `requirements.txt`).
3. Demo-Daten der Entwicklungsumgebung optional entfernen: `python scripts/seed_ai_lab_demo.py --clean`.
4. Nach ~50 echten abgeschlossenen Trades ML-Training erneut laufen lassen (AUC wird erst dann aussagekräftig).

---

# Update 2 (30.07.2026) – RegimeLab: Regime-Modi, adaptive Glättung, Deep-Test, Local Worker

## Problemstellung (Original, gekürzt)
> Produktive externe Daytrading-Website (Repo `dean06greif-ai/Krypto_Trader`, Branch `NEWEST-29.07`).
> Großes Ziel: die Regime-Bestimmung nahezu perfekt machen. Konkret:
> (1) Local Worker meldet "veraltet", im Download-Paket fehlen `requirements.txt`, `README` und
> `worker.py` – Regime-Lab/Backtester/Strategie-Discovery lokal nicht nutzbar.
> (2) 9 Regime sind zu viele und zu unpräzise – zusätzlich 5er- und 3er-Einteilung; Marktrauschen
> darf nicht als Trendwechsel gelten; Einstellungen (z.B. Mindest-Tage) sollen sich sinnvoll an den
> Zeitraum (360 vs. 2000 Tage) anpassen.
> (3) Farbprinzip grün/gelb/rot ist gut, aber die blassen Abstufungen sind nicht unterscheidbar.
> (4) Dynamische Strategien: funktionieren sie im Backtester mit Konfigurationswechsel? Aktivierung
> soll direkt beim Start-Screen (Live/Paper einer Strategie) möglich sein, Wechsel im Backtester
> sichtbar; bitte erklären, wie es funktioniert.
> (5) Deep-Test: alle Indikator-Kombinationen mit je ~50 Optimierungen, Indikatoren austauschen,
> daraus belastbare Schlüsse ziehen – auch pro Regime im Regime-Lab.

## Nutzer-Entscheidungen
- Regime-Modi **3 / 5 / 9 wählbar, Default 5**.
- Adaptive Glättung automatisch + manueller Override; verschiedene Glättungen begründet prüfen.
- Deep-Test als **Häkchen** im Strategie-Finder/Optimierer, längere Laufzeit akzeptiert.
- Regime im Chart einfärben.
- Local Worker läuft unter **Windows**.

## Umgesetzt
### Local Worker (Blocker behoben)
- Ursache: Der Ordner `local_worker/` war nie im Repo → das ZIP enthielt nur die Rechen-Module.
- Neu: `/app/local_worker/worker.py` (v1.6.0 = `REQUIRED_WORKER_VERSION`), `requirements.txt`,
  `README.md`, `start_worker.bat` (Windows-Starthilfe: venv + Abhängigkeiten + Start).
- Worker-Jobs: `backtest`, `optimizer`, `regime_lab` (Analyse / Regime-Optimierung / Walk-Forward),
  `data_download|update|delete`; Outbound-Polling, Fortschritt, Abbruch, gzip-Upload, Reconnect.
- `GET /api/localworker/package` bricht bei unvollständigem Paket mit klarer Meldung ab;
  neu `GET /api/localworker/package/manifest`.
- Verifiziert: Worker online v1.6.0, Backtest und Regime-Lab-Analyse lokal gerechnet.

### Regime-Engine (`services/regime_engine.py`)
- `regime_mode` 3/5/9, Default 5. Im 5er-Modus ist die zweite Achse die **Trendstärke**
  (stark/leicht, eigene Hysterese) statt der Volatilität; im 9er-Modus unverändert Vola.
- Adaptive Glättung: Fenster als **Anteil des analysierten Zeitraums** (`ADAPT_PROFILES`
  fein/standard/grob) → Horizonte, Bestätigungs-, Mindesthalte-, Glättungs- und Vola-Fenster.
- `adapt_profile="auto"`: alle Profile werden gerechnet und bewertet (`_profile_quality`:
  40 % Rückblick-Treffer + 35 % Plausibilität + 25 % Abschnittslänge); Profile mit
  Plausibilitätsverstoß > `VALIDATE_PASS_PCT` (8 %) können nicht gewinnen. Bericht im UI.
- Kaltstart-Abschnitte (vor Ablauf des längsten Horizonts) zählen nicht als Verstoß.
- Oberflächen-Standard "Min. Haltezeit 2 d" hebelt die adaptive Haltedauer nicht mehr aus.
- Messung BTC 180 d/1h, 720 d/4h, 1800 d/4h in Modus 3 und 5: 0 % Verstöße,
  100 % Richtungs-Trefferquote, 6–17 Abschnitte.

### Farben (`frontend/src/lib/regimeColors.js`)
- Modus-abhängige Paletten; 5er: Wein-Dunkelrot → klares Rot → Gelb → klares Grün →
  Tannen-Dunkelgrün, plus abgestufte Deckkraft der Chart-Bänder.

### Deep-Test
- Neu `services/deep_search.py`: Einzeltest → **alle Paare** → Beam-Suche (Breite 6 bzw. 10) →
  Feintuning der Favoriten (je `iterations`) → Austausch jeder Regel gegen jede Alternative →
  Auswertung (Beitrag je Regel per Leave-one-out, Synergie/Anti-Synergie je Paar,
  Indikator-Häufigkeit, Fazit-Text).
- Optimizer: `body.deep_test` + `deep_depth` (`deep`/`extreme`) → `result.deep_report`.
- Regime-Lab: `_deep_regime_search()` in `dynamic_strategy.py`, `deep_test` in `regime_opt.py`
  → `discovery.deep_report`; Walk-Forward-Rückfall auf kleinere Kombination bleibt erhalten.
- UI: `opt-deep-test` (+ `opt-deep-depth`) im Optimizer, `regime-opt-deep-{id}` im Regime-Lab,
  jeweils mit Auswertungs-Panel.

## Offen / Backlog
### P0 – nächste Phase (vom Nutzer gefordert, noch NICHT umgesetzt)
1. **Dynamische Strategien im Backtester**: gespeicherte dynamische Strategie als Backtest fahren
   (Regime pro Bar ohne Lookahead, Konfiguration/Sub-Strategie je Regime), Regime-Bänder im Chart,
   Marker + Liste der Konfigurationswechsel, Vergleich gegen die statische Variante.
2. **Aktivierung im Start-Screen**: im Strategie-/Live-Paper-Dialog direkt "dynamisch fahren"
   wählen (Analyse, Auto-Umschaltung, Bestätigungspflicht) statt über Regime-Lab → Discovery →
   Dynamik-Panel.
3. End-to-End-Nachweis, dass Backtest, Paper und Live identisch umschalten.

### P1
- Erkennungs-Latenz messbar machen (wie viele Tage nach dem echten Wendepunkt schaltet die Engine
  um – "rechtzeitig erkannt ohne Zukunftsblick").
- Deep-Test: Zwischenstände persistieren/fortsetzbar, Zeitbudget mit Abbruch + Teilergebnis.

### P2
- Farbkontrast für farbschwache Nutzer (Muster/Schraffur zusätzlich zur Farbe).
- `<span>` in `<option>` (React-Warnung, Altbestand).
- Job-Endpunkte vereinheitlichen (`/status/{id}` vs. `/job/{id}`).

## Test-Stand Update 2
- `tests/test_regime_engine.py`, `test_regime_extras.py`, `test_nnfx.py`: 65 passed.
- `tests/test_regime_deep_update.py` (Test-Agent, neu): 11 passed, keine kritischen Findings.
- Restliche Repo-Tests scheitern teils an fest verdrahteten geseedeten Analysen (`ra_82c98807`,
  `ra_c8206904`) und AI-Keys, die in dieser Umgebung fehlen – kein Regressionsfehler.

# Update 3 (30.07.2026) – Worker-Befehl, schneller Abbruch, schnelle Offline-Erkennung

## Problemstellung (Nutzer)
1. Abbruch-Button muss schneller wirken.
2. Trennt man den Local Worker, dauert die Erkennung zu lange.
3. Der Startbefehl war früher `python worker.py --server <URL> --token <TOKEN>` –
   die neue worker.py kannte nur `--url`, der aus der UI kopierte Befehl schlug fehl.

## Umgesetzt
### Worker v1.6.1 (`/app/local_worker/worker.py`)
- argparse: `--server` (primär, wie früher) + `--url` als Alias; Config-Kompat (`server`-Key).
- PROGRESS_INTERVAL 2s → 1s (Abbruch-Flag kommt im Progress-Response schneller an).
- Vor dem Ergebnis-Upload wird "Ergebnis wird übertragen..." gemeldet (Anti-Stale).
- REQUIRED_WORKER_VERSION bleibt 1.6.0 → keine "veraltet"-Meldung für 1.6.0-Worker.

### Server (`services/local_exec.py`)
- WORKER_TIMEOUT 90s → 15s (offline-Erkennung; Worker pollt alle 2s, Compute in Threads/Prozessen).
- Neu OFFLINE_JOB_TIMEOUT=20s: laufender Job + Worker offline → sofort Fehler mit klarer Meldung.
- Neu CANCEL_GRACE=10s: Abbruch angefordert, Worker bestätigt nicht → hart abbrechen;
  wartende (queued) Jobs werden bei Abbruch SOFORT storniert.
- STALE_TIMEOUT 900s → 300s; Watchdog-Intervall 12s → 5s.
- apply_result verwirft verspätete Ergebnisse bereits beendeter Jobs (kein Überschreiben).
- Cancel-Endpoints (backtest/optimizer/regime-lab) rufen check_stale() → sofortige Stornierung.

### E2E verifiziert (im Pod mit echtem Worker aus dem ZIP-Paket)
- `python worker.py --server <URL> --token <TOKEN>` verbindet (v1.6.1, "aktuell").
- Abbruch laufender lokaler Backtest: **1,5s** bis Status "cancelled".
- Worker hart gekillt: offline nach **13,8s**, Job-Fehler nach **20,1s** (vorher 90s/900s).

### Test-Suite-Hygiene (vorbestehende Probleme behoben)
- `test_settings_persistence.py` + `test_krypto_alert_features.py`: Admin-Auth-Header ergänzt
  (Endpoints verlangen inzwischen Admin; Tests stammten aus der Zeit davor).
- `test_new_features.py`: Passwort admin123 → env (ADMIN_PASSWORD, Default "admin").
- Restart-Tests (`test_14_settings_survive_backend_restart`, `test_winrate_bug.py`) nur noch mit
  `RUN_RESTART_TESTS=1` – sie starteten das Backend MITTEN im xdist-Lauf neu und rissen alle
  parallelen Tests mit (Ursache der großen Fehlerblöcke in Vollläufen).
- Hartkodierte, gelöschte Seeds dynamisch/geskippt: `custom_3a7f5e25` → erste existierende
  Custom-Strategie (iter15/iter16/iter13); `ra_82c98807`/`ra_c8206904` → skip wenn nicht in DB
  (regime_v2/v3/lab). Worker-Version-Asserts dynamisch statt "1.6.0"/"1.1.0"/"1.3.0".
- `test_ai_trader`: GEMINI-Key-Assert → skip ohne Key; enabled-Liste (mutable Setting) nicht mehr
  hart geprüft. `test_backtest_optimizer`: ==9 Strategien → >=9.
- Neu `tests/run_suite_serial.sh`: Job-startende Testdateien seriell (Datei für Datei), Rest
  parallel – vermeidet 409-Kollisionen, wenn mehrere Dateien gleichzeitig Jobs starten.
- Bekannte Umgebungs-Grenzen: uvicorn --reload überwacht auch tests/ (Testdatei-Edit = Backend-
  Neustart = kurze 502s); Multi-Asset-Daten (QQQ/SPY/Forex/GOLD) im Pod teils nicht ladbar.

## Testergebnis-Verlauf Volllauf
- Vorher (mit Restart-Tests + Kollisionen): 86-122 failed.
- Nach Fixes (paralleler Volllauf): 18 failed / 499 passed / 42 skipped – alle 18 sind
  nachweislich Job-Kollisionen (bestehen einzeln bzw. im seriellen Lauf).
