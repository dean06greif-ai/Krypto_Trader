# PRD – Krypto_Trader (externes Repo, Branch new-2.8-Ant)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (extern deployed auf Render, GitHub:
dean06greif-ai/Krypto_Trader, Branch `new-2.8-Ant`). Grundsatz: Stabilität,
Rückwärtskompatibilität, saubere modulare Integration, Regressionstests vor größeren Änderungen.

Aufgaben (User):
1. KI-erstellte Custom-Strategien liefern im Backtester immer 0/0/0, weil die Regel-Engine
   deren Vokabular nicht auswerten kann (ema_200, rsi_period, volatility_pct * price,
   time/not_in_range, low_20/high_20 …). Umfassend fixen ohne Bestehendes zu brechen.
2. Top-3-KI-Empfehlung pro Rolle (Hauptmodell, Analyst, Tiefenanalyst …), inkl. Modelle,
   die der User noch nicht hat; Free-Tier-Limits pro Rolle beachten; sinnvolle Defaults einbauen.

## Architektur (relevant)
- Backend FastAPI (`backend/`), Frontend React (`frontend/`), MongoDB Atlas, Render-Deployment.
- Custom-/KI-Strategien: `strategies/custom_strategy.py` (Live-Pfad) +
  `services/fast_sim.py` (vektorisierter Fast-Path) + `strategies/custom_params.py`
  (Normalisierung/Alias/Optimizer-Suchraum). Paritäts-Prinzip Live <-> Fast-Path.
- KI-Schicht: `services/ai_providers.py` (Modell-Katalog, Fallback-Ketten, Keys),
  `services/ai_roles.py` (Rollen-Presets, user_configured wird respektiert),
  `services/ai_strategy_lab.py` (Strategie-Labor, Prompts für rule_definition).

## Umgesetzt (02.06.2026 – erste Iteration)
- NEU `strategies/rule_terms.py`: eine gemeinsame Implementierung für
  * dynamische Indikatoren mit Periode im Namen (ema_200, sma_50, rsi_7, atr_21, cci_30,
    adx_20, high_20/low_20, roc_10, volume_sma_30, rel_volume_30, stoch_k_9 …)
  * Zeit-Terme hour/minute/dow (UTC) inkl. Alias time→hour
  * sichere Mathe-Ausdrücke als Regel-Wert ("atr_pct * price", "recent_low - 2 * atr")
  * Bereichs-Parsing für in_range/not_in_range ([min,max], "8-17", "7..20")
  * RULE_SPEC_PROMPT: exakte Vokabular-Spezifikation für alle KI-Prompts
- `custom_params.py`: Aliase erweitert (volatility_pct→atr_pct, time→hour, *_period→Indikator,
  between→in_range …), Normalisierung validiert Ausdrücke/Bereiche, Optimizer-Suchraum
  für dynamische Indikatoren (rsi_7 bounded), Zeit-/Bereichs-Regeln werden nicht optimiert.
- `custom_strategy.py`: INDICATORS + hour/minute/dow, OPERATORS + ==, !=, in_range,
  not_in_range; _series berechnet dynamische/Zeit-Terme; _eval_rule + _resolve erweitert.
- `fast_sim.py`: FastSeries führt Timestamps mit; get() kennt dyn/time-Terme (inkl.
  Indikator-Cache); _rule_cond unterstützt neue Operatoren + Ausdrucks-Werte. Parität garantiert.
- `ai_strategy_lab.py`: TESTING_NOTE + ASSIST_SYSTEM nutzen RULE_SPEC_PROMPT → KI generiert
  nur noch unterstützte Syntax.
- `ai_providers.py`: OpenRouter-Katalog + deepseek/deepseek-r1:free, qwen/qwen3-235b-a22b:free.
- `ai_roles.py`: Presets – deep_analyst primär deepseek-r1:free (Fallback gemini-3.1-pro),
  research_analyst Fallback deepseek-r1:free. Eigene User-Auswahl bleibt unangetastet.
- Frontend `StrategyBuilder.js`: Labels/Eingaben für neue Operatoren, Bereich als "min-max",
  Wert-Feld akzeptiert Ausdrücke; Array-Werte werden beim Editieren als "a-b" angezeigt.
- Tests: NEU `backend/tests/test_rule_engine_ext.py` (23 Tests: alle Screenshot-Fälle,
  Parität Live<->Fast, Optimizer-Suchraum, Rückwärtskompatibilität). Gesamt 53 Unit-Tests grün;
  Endpoint-Tests benötigen laufenden Server (in dieser Umgebung nicht deployed, Baseline identisch).

## Backlog / Nächste Aufgaben
- P1: Backtester-Hinweisbox könnte pro Regel einen "Auto-Fix"-Vorschlag anzeigen.
- P1: bb_upper_20/keltner_upper_30 mit dynamischer Periode (aktuell nur über indicators-Config).
- P2: Session-Presets (z.B. "nur London/NY") als UI-Shortcut für hour-in_range-Regeln.
- P2: Modell-Empfehlungs-Seite in der UI (Rollen-Presets mit Begründung anzeigen).
