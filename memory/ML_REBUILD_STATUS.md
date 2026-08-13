# ML Rebuild Status

## Aktueller Stand (Datum: 2026-08-13)
- Aktive Phase: Phase 0 (Fixes vor jedem ML-Training)
- Aktueller Schritt: 0.1–0.5 erledigt + ai_rewards-RCA/-Fix, als Nächstes 0.6
- % geschafft grob: 30%
- Neuer Agent hat übernommen (2. Handover): Repo neu geklont, Umgebung eingerichtet,
  User-.env erneut erhalten und selektiv übernommen (wie unten), Prod-Lesezugriff verifiziert
  (167 auto_trades, ~36.100 ai_decisions).

## Umgebung & echte Daten (Stand 2026-08-13)
- Prod-.env vom User erhalten. Übernommen nach /app/backend/.env: alle LLM-Keys
  (Groq/Gemini/OpenRouter/Mistral/Cerebras + Backups, alle 5 Provider verifiziert aktiv),
  ADMIN_USER/ADMIN_PASSWORD (→ /app/memory/test_credentials.md), PROD_MONGO_URL/PROD_DB_NAME
  (NUR LESEND für Analysen — die laufende Dev-App bleibt auf lokaler MONGO_URL!).
- BEWUSST NICHT übernommen: BITUNIX_API_KEY/SECRET (keine echten Orders aus Dev),
  TELEGRAM_* (kein Spam an echten Chat), SUPABASE_* (KI-Gedächtnis/Lektionen liegt in
  Supabase! Dev darf Prod-Gedächtnis nicht verschmutzen).
- Echte Prod-Zahlen (Skript /app/scripts/prod_db_stats.py):
  DB 128,7 MB Daten / 48,2 MB Storage; auto_trades=167 (alle closed, Zeitraum 21.07.–12.08.,
  davon ai_trader=28); ai_decisions=35.993 (nur 304 mit outcome); signals=2.183 (622 mit result,
  549 unresolved); ai_market_snapshots=20.000 (Cap exakt erreicht, ältester 02.08. → ~10-Tage-
  Fenster bestätigt); ai_rewards=0 (!) — Reward-System hat in Prod NIE etwas aufgezeichnet;
  ghost=27; backtests=278; optimizer_runs=145.
  Konsequenz: ML-Datenbasis real = ~300–650 gelabelte Beispiele, nur 28 echte KI-Trades →
  Datensammel-/Simulations-Strategie ist kritisch, Meta-Modell v1 braucht signals+ghost+Sim.

## Kontext (einmal lesen, dann Abschnitte unten)
Briefing des Users: Hybrid-Architektur (LLM bleibt Entscheider, ML predictet parallel
Erfolgswahrscheinlichkeit; Eskalation Shadow → Sizer → Gate, jede Stufe nur mit expliziter
User-Freigabe; Haupt-Agent hat Gate-vor-Sizer empfohlen, User-Entscheidung offen).
Beehive (Multi-LLM-Rollen) bleibt, Modell wird als Feature geloggt. Scope: alle 22 Instrumente
(Haupt-Agent empfiehlt Krypto-only für Modell v1, User-Entscheidung offen).
Phase-0-Reihenfolge (vom Haupt-Agent umsortiert, vom User-Briefing abweichend begründet:
irreversibler Datenverlust zuerst):
  0.1 Snapshot-Prune zeitbasiert (stoppt Datenvernichtung)
  0.2 entry_market_snapshot am Trade + an der Decision im Entry-Moment
  0.3 Swing-Labeling DB-basiert (statt RAM open_signal_evals + Midnight-Reset)
  0.4 prompt_version + LLM-Modell an jede Entscheidung binden
  0.5 Ergebnis-Wahrheit vereinheitlichen (kanonisch: Vorzeichen realized_pnl inkl. Fees) + Migration
  0.6 /tmp-Candle-Cache → persistenter Pfad (Render; blockiert auf User-Info zu Persistent Disk)
  0.7 Regime-Brücke Welt A (Observer-Heuristik) → Welt B (regime_engine v2): erst Kosten-Nutzen-Analyse
  0.8 Beehive: keine Modell-Änderung nötig (Analyse 2026-08-13: alle Rollen Free-Tier, Presets gut);
      Rest = UI-Kosten-Panel (Backend existiert: ai_token_usage + GET /api/ai/token-usage)
Danach: Anti-Overfitting-Pipeline (Purged WF + Embargo, Permutationstests, Monte-Carlo/GBM,
Kalibrierung), Meta-Labeler (XGBoost/LightGBM), UI (Kalibrierungs-Chart = Placebo-Detektor,
Hybrid-Scoreboard, Robustheits-Dashboard, SHAP pro Trade).
Stopp-Metrik fürs Tuning: Brier-Score + ökonomischer Uplift (nicht Kalibrierung allein).
Verbote: Paper/Live/Backtest-Trades mischen, geshuffelte CV, Look-Ahead, Deep Learning <10k Trades.

## Erledigt
- [0.1] Snapshot-Prune zeitbasiert — 2026-08-13 — `services/ai_market_observer.py`:
  Anzahl-Cap (20k, ≈10 Tage Fenster) ersetzt durch SNAPSHOT_RETENTION_DAYS=200 (zeitbasiert)
  + Notbremse MAX_SNAPSHOTS=500k; `core/indexes.py`: neuer Index `snapshots_ts` (ts aufsteigend),
  damit der Prune keinen Collection-Scan macht — getestet: Skript-Test gegen lokale Mongo
  (alte/neue Docs eingefügt, Prune löscht nur >200 Tage; Notbremse geprüft) + Backend-Neustart sauber.
  Hinweis: finaler Retention-Wert hängt an User-Antwort zum Mongo-Tier (200d ≈ 140–160 MB bei 22 Symbolen).
- [0.2] entry_market_snapshot am Trade + Decision — 2026-08-13 —
  `services/ai_market_observer.py`: neue Methode `entry_snapshot(symbol)` (frisch aus Kerzen-Puffer,
  Fallback letzter 15-min-Snapshot); `services/bitunix_trade.py` (~Z.1115): Trade-Doc-Feld
  `entry_market_snapshot` (source=signal_candles|live|last_snapshot, ts, features inkl. regime);
  `services/ai_engine.py` (~Z.1702): jede ai_decision (auch HOLD → kontrafaktischer Kontext!)
  bekommt `entry_market_snapshot` — getestet: /app/tests/test_fix_0_2_entry_snapshot.py
  (4 PASS inkl. echter on_signal-Paper-Trade) + pytest observer/snapshot 9/9.
- [0.3] Swing-Labeling über Tagesgrenzen — 2026-08-13 —
  `core/pipeline.py`: SIGNAL_EVAL_MAX_DAYS=14, evaluate_open_signals mit Expiry statt
  Tagesgrenze, eval-Einträge tragen ts; `core/scheduler.py`: Mitternachts-`clear()` entfernt;
  `server.py`: Rehydrierung lädt unresolved Signale der letzten 14 Tage (vorher: nur heute) —
  getestet: /app/tests/test_fix_0_3_swing_labeling.py (3 PASS: 3d-Swing win, 5d-Swing loss,
  20d expired bleibt None) + pytest-Subset 47 passed (3 Fails pre-existing, via git stash verifiziert).
  Bekannte Restlücke: Preisbewegungen WÄHREND Server-Downtime werden nicht nachträglich
  ausgewertet (kein Kerzen-Backfill) — bewusst vertagt, siehe Tech-Debt.

- [0.4] prompt_version an jede ai_decision — 2026-08-13 —
  `services/ai_master_prompt.py`: `MasterPromptStore.version_hash()` (sha256 über
  text+lesson_policy+normalisierte rules, 10 Hex-Zeichen; inhaltsbasiert → erkennt Reverts,
  umgebungsunabhängig im Gegensatz zur Integer-`version`); `services/ai_engine.py`:
  `ANALYSIS_PROMPT_HASHES` (lean/full, ändern sich automatisch bei Prompt-Code-Änderung) +
  `prompt_version_info(variant)`; `run_analysis` bindet an jede Decision das Dict
  `prompt_version = {analysis, variant, master, master_v, combined}` (combined = ML-Groupby-Key,
  Format "lean-<hash10>+<hash10>") — getestet: /app/tests/test_fix_0_4_prompt_version.py
  (3 PASS: Hash-Determinismus+Revert, lean/full-Struktur, Integration run_analysis mit
  gemocktem LLM → Decision-Doc trägt prompt_version) + pytest -k "master_prompt or prompt"
  49 passed; 5 Fails in test_ai_team_api/regression identisch ohne Änderungen (git stash
  verifiziert, pre-existing/umgebungsbedingt).
- [0.5] Ergebnis-Wahrheit vereinheitlicht — 2026-08-13 — kanonisch = Vorzeichen
  auto_trades.realized_pnl (inkl. Fees, wird beim Close ohnehin so klassifiziert):
  `services/bitunix_trade.py` `_after_close` (EIN Hook, alle 5 Close-Pfade laufen durch)
  schreibt result kanonisch ans Signal (result_source=trade_pnl, trade_id, result_ts);
  `core/pipeline.py` evaluate_open_signals überschreibt trade-gelabelte Signale nie mehr
  (Update-Filter result_source!=trade_pnl; TP1-Touch-Labels bekommen result_source=tp1_touch;
  update_performance nur noch wenn wirklich gelabelt wurde); `services/ai_learning.py`
  sync_outcomes: Trade-Branch setzt outcome kanonisch (outcome_source=trade_pnl, gewinnt
  immer), tp1-Branch stuft nie zurück. Migration: `backend/scripts/migrate_0_5_result_truth.py`
  (Dry-Run Default, --apply verweigert HART gegen PROD_MONGO_URL; Prod-Migration später auf
  Render ausführen: dort ist MONGO_URL die Prod-DB). PROD-DRY-RUN-ERGEBNIS (echt, nur lesend):
  96 Signale → kanonisch (Flips: 15 win→loss!, 6 loss→win, 27 unlabeled→Label), 28 Decisions
  (7 Flips, 7 neue Labels), Backfill result_source: 656 Signale / 293 Decisions tp1_touch.
  Getestet: /app/tests/test_fix_0_5_result_truth.py (5 PASS inkl. Prod-Schreibschutz).
- [ai_rewards-RCA + Fix] — 2026-08-13 — URSACHE Prod leer: (a) am 13.08. 07:03 UTC wurde
  DELETE /api/ai/rewards ausgeführt (settings.ai_rewards_state.cleared_at gesetzt; Admin-only —
  vermutlich User im Lern-Panel, beim User nachgefragt), alle 28 KI-Trades schlossen 11.–12.08.
  DAVOR, seither kein Close mehr; (b) Alt-Backfill lief nur bei KOMPLETT leerer Collection und
  nie nach cleared_at → Lücken für immer. Hook selbst funktioniert (Dev-Beweis: Close → Reward).
  FIX `services/ai_rewards.py`: backfill_missing() lückenfüllend + idempotent (Dedupe trade_id),
  respektiert cleared_at (nur Trades danach), include_cleared=True hebt Sperre auf und bewertet
  historisch; ensure_backfill = 10-min-Wrapper; _regime_for-Priorität jetzt
  entry_market_snapshot (Entry-Regime, Fix 0.2) > Snapshot<=closed_at > Live-Observer
  (P1 Tech-Debt "Regime zum Close-Zeitpunkt" gelöst). Neuer Endpoint
  POST /api/ai/rewards/backfill?include_cleared=true (require_admin; für Prod: nach Deploy
  einmal aufrufen → bewertet die 28 historischen KI-Trades nach).
  Getestet: /app/tests/test_fix_rewards_backfill.py (4 PASS).

## In Arbeit
- (nichts)

## Als Nächstes
- [0.6] Candle-Cache: User hat KEINE Render Persistent Disk → /tmp bleibt flüchtig;
  Alternativen bewerten (a: Rebuild-on-Boot akzeptieren + Backfill, b: Kerzen komprimiert in
  Mongo persistieren — Achtung Gratis-Tier 512 MB, c: Hybrid). Kosten-Nutzen vor Umbau!
- Prod-Migration 0.5 nach nächstem Render-Deploy: auf Render
  `python scripts/migrate_0_5_result_truth.py` (Dry-Run prüfen) → `--apply`;
  danach POST /api/ai/rewards/backfill?include_cleared=true (28 KI-Trades nachbewerten,
  Reihenfolge wichtig: erst Migration, dann Backfill, damit Rewards die kanonischen
  Ergebnisse nutzen). Nur nach User-Freigabe!

## Noch nicht getestet (Testing-Backlog)
- (leer)

## Entscheidungen des Users (beantwortet 2026-08-13, 2. Session)
- Phase-0-Fixes (0.1–0.3) sind bereits auf Render deployt ✓
- KEINE Render Persistent Disk vorhanden → 0.6 braucht Alternativlösung (siehe "Als Nächstes")
- Mongo = Gratis-Tier (512 MB) → 200-Tage-Snapshot-Retention aus 0.1 bleibt (≈140–160 MB + Notbremse)
- Eskalations-Design: GATE VOR SIZER ✓
- ML-Modell v1: KRYPTO-ONLY ✓

## Offene Entscheidungen des Users
- Hat der User am 13.08. ~09:03 deutscher Zeit im Lern-Panel "Belohnungsdaten löschen"
  geklickt? (RCA ai_rewards; ändert nichts am Fix, nur zur Bestätigung der Ursache)
- Freigabe für Prod-Migration 0.5 + Reward-Backfill nach nächstem Render-Deploy
  (Reihenfolge siehe "Als Nächstes")

## Bekannte Baustellen / Tech-Debt
- Signal-Evaluation ohne Kerzen-Backfill: Downtime-Lücken labeln falsch/spät — P1,
  sauber lösbar im ML-Dataset-Builder (candle_cache-basiertes Nach-Labeling)
- 3 Ergebnis-Wahrheiten — ERLEDIGT durch 0.5 (2026-08-13); Prod-Daten-Migration steht noch aus
- Swing-Signale bleiben unlabeled — ERLEDIGT durch 0.3 (2026-08-13)
- ML-Labor: geshuffelte StratifiedKFold auf Zeitreihen, MIN_SAMPLES=40, kein Holdout, Platzhalter-Features,
  Modell ohne Versionierung überschrieben (`services/ai_ml_lab.py`) — P0, wird durch Neubau ersetzt
- Zwei Regime-Welten ohne Brücke (Observer classify_regime vs. regime_engine v2) — P1 (=0.7)
- Regime an Rewards zum Close- statt Entry-Zeitpunkt — ERLEDIGT mit ai_rewards-Fix
  (2026-08-13): _regime_for bevorzugt jetzt entry_market_snapshot
- ai_decisions ohne Index (ts, signal_id) — P2, beim nächsten indexes.py-Anfassen mitnehmen
- ai_engine.py 3171-Zeilen-Gott-Objekt — P2, nur bei Gelegenheit entflechten, kein Selbstzweck

## Handover-Notiz
Falls du der neue Agent bist: Lies zuerst diese Datei, dann /app/memory/PRD.md, dann /app/README.md.
Der letzte Schritt war 0.5 (Ergebnis-Wahrheit, getestet) + ai_rewards-RCA/-Fix (getestet).
Der nächste geplante Schritt ist 0.6 (Candle-Cache ohne Persistent Disk) sowie — nach
User-Freigabe — die Prod-Migration 0.5 + Reward-Backfill nach dem nächsten Render-Deploy.
Es gibt keine offenen ungetesteten Änderungen.
PFLICHT-REGEL (User-Wunsch): Nach jedem abgeschlossenen Schritt dem User einen Abschnitt
"🧪 So testest du es selbst" liefern: (1) Preview-URL + Klickpfad im UI oder konkreter API-Aufruf,
(2) was bei Erfolg zu sehen ist, (3) Hinweis, falls nur in Dev und noch nicht auf Render sichtbar.
Wichtig: Diese Umgebung läuft auf FRISCHER lokaler MongoDB; PROD_MONGO_URL/PROD_DB_NAME in
/app/backend/.env sind der NUR-LESEND-Zugang zur echten Render-DB (für Analysen/Migrationstests).
LLM-Keys sind aktiv (alle 5 Provider), KI Trader in Dev bewusst noch enabled=False.
Bitunix-/Telegram-/Supabase-Keys bewusst NICHT gesetzt (siehe Umgebung & echte Daten).
