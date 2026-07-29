# KI-Ökosystem (KI-Labor) – Erweiterung des KI Traders

Diese Erweiterung baut das bestehende KI-Team modular aus. Bestehende Endpunkte,
Datenstrukturen und Nutzer-Workflows bleiben unverändert; alles Neue liegt in
eigenen Modulen und ist über eigene Endpunkte/Panels erreichbar.

## Neue Bausteine

| Modul | Aufgabe |
|---|---|
| `services/ai_memory.py` | KI-Gedächtnis: austauschbare Speicherschicht. MongoDB (`ai_knowledge`) als Primärspeicher, Supabase als Langzeit-Spiegel (Dual-Write, degradiert sauber ohne Keys). |
| `services/ai_research.py` | Rolle `research_analyst`: wertet Backtests, Optimizer-Läufe (inkl. Walk-Forward/Robustheit), Regime-Lab und Regime-Analysen aus und übergibt die Erkenntnisse an KI Trader, Tiefen-Analyst und Lern-Modul. |
| `services/ai_ml_lab.py` | ML-Labor: Optuna (TPE) sucht Hyperparameter, XGBoost lernt aus echten Ergebnissen, welche Marktbedingungen Gewinne liefern. Die Haupt-KI erklärt das Ergebnis und leitet Regeln ab. |
| `services/ai_market_observer.py` | Rolle `market_observer`: misst laufend Trend, Volatilität, ATR, RSI, Volumen, Range-Position pro Coin (`ai_market_snapshots`) – Trainingsdaten für das ML-Labor. |
| `routers/ai_lab.py` | Endpunkte des KI-Labors. |
| `frontend/src/components/AILabPanel.js` | UI-Panel „KI-Labor“ im KI-Trader-Panel (Forschung / ML-Modell / Gedächtnis / Markt). |

## Datenfluss

```
Backtester / Optimizer / Regime-Lab ─┐
Markt-Beobachter (Snapshots) ────────┤→ Forschungs-Analyst ─┐
Signale / Trades (echte Ergebnisse) ─┴→ ML-Labor (Optuna+XGBoost) ─┤→ KI-Gedächtnis
                                                                   └→ KI Trader (Analyse-Prompt),
                                                                      Lern-Modul, Tiefen-Analyst
```

## Rollen-Voreinstellungen

`services/ai_roles.ROLE_PRESETS` belegt jede Rolle mit einem passenden, günstigen
Modell aus dem bestehenden Katalog. Sobald im UI eine eigene Wahl getroffen wird,
gilt diese dauerhaft (`user_configured`); „Voreinstellung wiederherstellen“ per
`POST /api/ai/roles/{role}/reset`. Ist für die Voreinstellung kein API-Key
gesetzt, hängt die Modell-Kette automatisch alle Provider mit Key als letzte
Fallback-Stufe an – eine Rolle fällt dadurch nie komplett aus.

## Endpunkte

```
GET  /api/ai/lab/status              Gesamtstatus (Forschung, ML, Beobachter, Gedächtnis)
GET  /api/ai/research/report         letzter Forschungsbericht
GET  /api/ai/research/data           aufbereitete Rohdaten-Digests
POST /api/ai/research/run            Forschungs-Auswertung starten            (Admin)
GET  /api/ai/ml/status               Modell-Status
GET  /api/ai/ml/dataset              Datenlage fürs Training
GET  /api/ai/ml/predict?symbol=      Gewinnwahrscheinlichkeit LONG/SHORT
POST /api/ai/ml/train                Optuna+XGBoost trainieren                (Admin)
POST /api/ai/ml/settings             ML-Einstellungen                         (Admin)
GET  /api/ai/observer/status         Markt-Beobachter
GET  /api/ai/observer/snapshots      Markt-Snapshots
POST /api/ai/observer/run            Markt jetzt scannen                      (Admin)
GET  /api/ai/memory/stats?health=1   Gedächtnis-Status (inkl. Supabase-Ping)
GET  /api/ai/memory/entries?kind=    Wissenseinträge
POST /api/ai/roles/{role}/reset      Rolle auf Voreinstellung zurücksetzen    (Admin)
```

## Automatik

* **Markt-Beobachter**: alle `interval_min` Minuten (Standard 15).
* **Forschungs-Analyst**: zu `schedule_times`, spätestens nach `interval_hours`,
  zusätzlich automatisch bei neuen Backtest-/Optimizer-/Regime-Ergebnissen.
* **ML-Labor**: täglich zur konfigurierten Stunde und nach `min_new_results`
  neuen abgeschlossenen Ergebnissen (mindestens 40 Datensätze, je 8 pro Klasse).
* Alle Läufe hängen im bestehenden `ai_engine.run_loop()` und sind einzeln
  gekapselt – ein Fehler kann den Trading-Loop nicht stoppen.

## Konfiguration (ENV)

```
SUPABASE_URL=https://<projekt>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<secret key>
AI_MEMORY_TABLE=ai_knowledge          # optional
```

Supabase-Tabelle einmalig anlegen: `backend/scripts/supabase_schema.sql` im
SQL-Editor ausführen. Ohne Tabelle/Keys arbeitet das Gedächtnis unverändert mit
MongoDB weiter (der Fehler wird im KI-Labor-Panel angezeigt).

## Tests

```
cd backend && python -m pytest tests/test_ai_lab.py -q     # 27 Tests, offline
python scripts/seed_ai_lab_demo.py                         # Demo-Daten (Dev)
python scripts/seed_ai_lab_demo.py --clean                 # Demo-Daten entfernen
```
