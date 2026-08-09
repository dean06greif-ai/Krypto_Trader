# PRD – Krypto_Trader (Daytrading-Plattform, extern deployt auf Render)

## Original-Problem
Bestehende, produktiv laufende Daytrading-Website (GitHub `dean06greif-ai/Krypto_Trader`, Branch `main8.8.1130`).
KI Trader performt live schlecht (42% WR, -76 USDT). Verbesserungen sauber & modular in bestehende Architektur,
Stabilität & Rückwärtskompatibilität vor aggressiven Änderungen. Deployment bleibt extern (Render), User deployt den Code selbst.

## Architektur
- Backend: FastAPI (`backend/server.py`), Services-Schicht (`services/`), Router (`routers/`), MongoDB (motor)
- KI: `ai_providers.py` (Modell-Katalog/Ketten/Health), `ai_roles.py` (KI-Team-Presets), `ai_engine.py` (Analyse-Loop),
  `ai_learning.py` (Lektionen), `trade_guard.py` (Kill-Switch), `notifications.py`
- Frontend: React (CRA/craco), Haupt-Panel `AITradingPanel.js`, recharts vorhanden
- Preview: lokales Mongo statt Produktions-Atlas; Bitunix/Telegram-Keys bewusst nicht gesetzt

## Umgesetzt (09.06.2026 / Iteration "KI-Trader-Verbesserung")
1. **Modell-Katalog-Fix** (`ai_providers.py`): alle Slugs live verifiziert. Entfernt (tot): groq qwen3-32b,
   openrouter deepseek-r1:free & qwen3-235b:free, cerebras llama-3.3-70b & qwen-3-32b, open-mistral-nemo,
   **GitHub Models komplett (Anbieter-Retirement, HTTP 410)**. Neu: gemini-3.6-flash, gemini-3.5-flash-lite,
   groq gpt-oss-120b/20b, qwen3.6-27b, openrouter nemotron-nano-9b-v2:free, ministral-8b-latest, cerebras zai-glm-4.7/gemma-4-31b.
   `MODEL_MIGRATIONS` mappt tote Slugs automatisch (Engine-Config + Rollen beim Laden).
2. **Token-Budget/413-Fix**: `MODEL_INPUT_TOKEN_BUDGET` + gelernte Budgets; zu große Prompts überspringen Modelle
   (Status `skipped_too_large` im Health/KI-Status statt Fehler). NoneType-Guard bei leeren `choices` (OpenRouter).
3. **Ausfall-Meldungen**: Provider zählt erst als "ausgefallen", wenn ALLE Keys (primär + backup) erschöpft sind.
   Groq-Backup-Key: `GROQ_API_KEY_BACKUP` wird automatisch erkannt (generisches `<ENV>_BACKUP`-Schema).
4. **KI-Team-Presets** kostenoptimiert & verifiziert (Learner: gemini-3.1-pro-preview; Trade-Manager: gemini-3.5-flash
   + 2 Fallbacks; Deep-Analyst: nemotron-ultra:free; Research: groq gpt-oss-120b).
5. **Belohnungssystem** (`services/ai_rewards.py`, Collection `ai_rewards`): deterministischer Reward pro geschlossenem
   KI-Trade (PnL-Basis, Win/Loss, Sofort-Stop-Out, Konfidenz-Disziplin, CRV-Bonus). Hook in `bitunix_trade._after_close`.
   Backfill alter Trades. Prompt-Block in jedem Lernlauf (`ai_learning`). API: `GET /api/ai/rewards?days=N`.
6. **Reward-Verlauf + Regime-Reward im Lern-Panel**: `AIRewardPanel.js` (recharts-Kurve, Regime-Tabelle, Trend).
7. **Kill-Switch-Zwangs-Lernphase** (`trade_guard.py`): Kill-Switch setzt `learning_required`, startet sofort
   Lernlauf (trigger="kill_switch", spezieller Prompt-Block); Auto-Trading bleibt auch nach Mitternacht gesperrt,
   bis Lernlauf ok (Retry alle 15 min). Manuelles Resume hebt beides auf. Toggle `forced_learning_enabled`.
8. **Modell-Wächter** (`services/ai_model_watch.py`): wöchentlicher Live-Check aller Slugs gegen Provider-Kataloge,
   Warnung Website+Telegram (Toggle `model_watch`). API: `GET /api/ai/models/watch`, `POST .../run` (admin).
9. **Skip-Anzeige** im KI-Status (Badge + Dropdown + Banner: "übersprungen – Prompt zu groß").
10. **Analyse-Texte pro Coin**: Entscheidungszeilen in der Markt-Analyse aufklappbar (volle Begründung).
11. **Sprung-Pfeil im KI-Verlauf** (`ai-jump-latest-btn`): erscheint beim Hochscrollen, springt zum neuesten Eintrag.
12. **Coin-Freigabe-Filter**: KI analysiert nur Coins mit Trade-Modus paper/live (spart Tokens).
13. Regressionstests: `tests/test_iter_reward_guard_models.py` (15 Tests) + Alt-Suiten grün.

## Backlog / Nächste Iteration (vom User gewünscht, noch NICHT umgesetzt)
- P1: Lektions-Wirkung messen (Winrate vorher/nachher pro Lektion, wirkungslose aussortieren)
- P1: Mobile-Fixes: X-Button in großer Signal-Ansicht abgeschnitten; Dropdowns (Benachrichtigungen/Analyse-Tools) seitlich abgeschnitten
- P1: EMA-200-Linie im Dashboard-Chart abgeschnitten (Jahres-Ansicht)
- P2: Strategie-Anzeige im Dashboard (Desktop) fest statt einzeln scrollbar
- P2: Trade-Hover im Chart: Trade markieren + TP/SL-Linien temporär einblenden
- P2: Liquidations-/Heatmap prüfen + optional als KI-Input
- P2: KI: mehrere partielle TPs, Struktur-basierte TP/SL, Experimente über Ghost-Trades (CRV 3-5 testen)
