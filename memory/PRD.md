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

## Kern-Anforderungen (statisch)
1. Bestehende Endpunkte, Datenmodelle und Nutzer-Workflows bleiben unverändert.
2. Kapital (`max_capital`) und Paper/Live-Modus bleiben für jede KI tabu.
3. Jeder neue Hintergrund-Lauf ist einzeln gekapselt – ein Fehler darf den Trading-Loop nie stoppen.
4. Alle Modelle/Provider ausschließlich aus dem bestehenden Katalog, Keys nur aus ENV.
5. Neue Features immer mit Regressionstests.

## Backlog (priorisiert)
- **P0**: Supabase-Tabelle `ai_knowledge` im Projekt anlegen (`backend/scripts/supabase_schema.sql`),
  danach Spiegel-Status im KI-Labor prüfen.
- **P1**: Optuna direkt auf Strategie-Parameter des Optimizers ansetzen (Vorschläge an den Optimizer
  zurückgeben statt nur XGBoost-Hyperparameter); ML-Regime-Zuordnung aus dem Regime-Lab statt
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
