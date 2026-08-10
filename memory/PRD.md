# PRD – Krypto_Trader (externe Daytrading-Website, Repo-Verbesserungen)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/Krypto_Trader, Branch `fixxxed-pls`, Deployment extern auf Render + MongoDB Atlas/Supabase – soll extern bleiben). Der KI-Trader ist nach Updates schlechter geworden. Verdacht des Traders: Liquidations-Daten/Heatmap liefern falsche Daten, KI-Team-Kosten zu hoch (~2€/Tag Gemini), widersprüchliche Lektionen/Prompts. Vorgabe: saubere, modulare, rückwärtskompatible Änderungen mit Regressionstests, keine Änderung an Nutzer-Workflows.

## User-Entscheidungen
- Ursachenanalyse + Fix + Kostenoptimierung zusammen
- Heatmap/Liquidations-Daten prüfen und bei Bedarf deaktivieren/fixen: JA
- Lektions-Konsolidierungs-System (neueste Trader-Anweisung gewinnt): JA
- Arbeitsweise: Repo hier klonen (/app/krypto_trader), User deployt selbst via Git
- Keine Trade-Historie verfügbar (vom User resettet)

## Architektur (Bestand)
- Backend: FastAPI, `backend/services/ai_engine.py` (Analyse-Loop, Prompt-Assembly), `ai_learning.py`/`ai_lessons.py` (Lektionen), `ai_master_prompt.py` (Governance), `liquidity_data.py` (Multi-Exchange-Liquidität), `ai_providers.py` (Gemini/Groq/OpenRouter/Mistral/Cerebras Modell-Ketten), `ai_roles.py` (KI-Team-Rollen)
- Frontend: React, `AIGovernancePanel.js` u.a.
- Läuft NICHT in dieser Umgebung (externe App); Arbeit erfolgt am Klon in /app/krypto_trader

## Ursachenanalyse (2026-06)
1. **Liquidations-"Heatmap" ist Pseudo-Daten**: `_model_clusters()` erzeugt Cluster rein rechnerisch (Preis ± 1/Hebel) – existieren immer, für jeden Coin; Prompt nannte sie "Magnete → Umkehrpunkte, KEINE Ausbrüche" → systematischer Bias zu Fade-/Sweep-Trades. Wahrscheinlichste Ursache der Verschlechterung.
2. **Widersprüchliche Trader-Lektionen** (Auto-Lev 200x erzwungen vs. Hebel 10 fix; BE bei 30/35/40%) landeten alle gleichzeitig im Prompt; Dedupe erkannte nur ähnliche Titel.
3. **Kosten**: Riesiger Kontext × 3 Analyse-Gruppen × 48 Zyklen/Tag auf Gemini Flash.

## Umgesetzt (2026-06)
- `use_heatmap_data` (Default **false**) + `use_liquidation_data` (Default true) in KI-Config; modellierte Cluster raus aus dem Prompt, echte Daten (L/S-Ratio, OI, Orderbook-Wände, Live-Liqs, eigene Levels) bleiben; ehrliche Kennzeichnung "MODELL-SCHÄTZUNG" wenn aktiviert; irreführende "Magnete"-Anweisung entfernt
- `liquidity_data.get_liquidity_context(..., include_modeled_clusters=True)` rückwärtskompatibel erweitert
- Lektions-Konfliktkonsolidierung in `ai_lessons.py` (`consolidate_conflicts`, Themen: hebel, break_even, cooldown, heatmap_liquidation) – nur gesperrte Trader-Lektionen, neueste gewinnt, ältere bleiben gespeichert aber inaktiv; Hinweis im Prompt; neuer Endpoint `GET /api/ai/lessons/conflicts`
- `lean_prompt` (Default true): Plattform-Wissen + Fremd-Strategie-Parameter-Block aus dem Analyse-Prompt → Token-/Kostenersparnis; Token-Logging pro Gruppen-Analyse
- Frontend: 2 neue Toggles im AIGovernancePanel (echte Liq-Daten / modellierte Heatmap)
- Regressionstests: `backend/tests/test_ai_trader_quality_fixes.py` (14 Tests, alle grün); bestehende Unit-Tests (ai_lessons_merge, ai_governance) weiter grün. E2E-Tests des Repos brauchen laufenden Server (hier nicht ausführbar, vorbestehend).
- Commit lokal in /app/krypto_trader erstellt (User pusht/deployt selbst)

## Backlog / Nächste Aufgaben
- P1: Kosten weiter senken – Empfehlung: Haupt-Analyst auf gemini-3.5-flash-lite oder Groq GPT-OSS 120B (free) umstellen (nur UI-Konfig, kein Code)
- P1: Push zu GitHub durch den User + Deploy auf Render, dann 20-Trade-Beobachtungsfenster
- P2: Lektions-Konflikt-Anzeige im Frontend (Endpoint existiert bereits)
- P2: Analyse-Gruppen-Prompt weiter deduplizieren (Basis-Kontext wird 3× gesendet)
- P2: Echte Liquidations-Heatmap-Quelle evaluieren (z.B. echte OI-Verteilung statt Modell)
