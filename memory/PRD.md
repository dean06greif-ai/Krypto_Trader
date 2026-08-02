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

## Umgesetzt (18.02.2026 – zweite Iteration)
- **Auto-Fix im Backtester-Hinweis (P0)**: `custom_params.normalize_with_fixes` liefert
  neben Text-Problemen strukturierte Details (`side`, `index`, `field`, `original`, `suggested`,
  `message`). `suggest_indicator_fix / _operator_fix / _value_fix` nutzen difflib + Alias +
  DYN-Bases (`ema200` → `ema_slow`, `crossesabove` → `cross_above` …). `CustomStrategy` behält
  `rule_problem_details`; Backtester emittiert `strategy_warnings_detail`.
  NEU `POST /api/strategies/{id}/auto-fix-rule` (Admin) speichert Ein-Klick-Korrekturen direkt
  in der Custom-Definition (Registry + DB) – vollständig rückwärtskompatibel.
  NEU `frontend/BacktesterWarnings.js` gruppiert Warnungen pro Strategie und blendet
  grüne „Auto-Fix"-Buttons ein, sobald ein Vorschlag existiert.
- **Chart Auto-Scroll bei Symbol-Wechsel (P1)**: `MainChart.js` ruft nach `setData()` explizit
  `priceScale('right').applyOptions({ autoScale: true })` + `timeScale().fitContent()` auf,
  sodass ETH nach BTC (66k → 1800) sofort im Sichtbereich liegt.
- **AI-Trading-Panel Aufräumung (P1)**:
  * Duplikat: `AIScheduleEditor` aus dem MasterPrompt-Reiter (`AIGovernancePanel`) entfernt.
    Der Zeitplan lebt nur noch im KI-Team-Panel.
  * `AITeamSupervisor` (Hauptaufsicht) hängt nun UNTER den Rollen-Karten – so überschneidet
    sich die rechte Text-Spalte nicht mehr mit den Rollen-Karten.
  * Grid-Layout `ai-supervisor-row` mit `minmax(0,1fr)`, `text-overflow: ellipsis`,
    responsiver Umbruch bei <900px.
  * `ai_roles.chain()` zwingt die Rolle „supervisor" IMMER auf das in Setup gewählte
    Haupt-Modell, unabhängig von zufälligen Rollen-Konfigurationen.
- **KI-Asset-Fokus Presets (P1)**: `AITradingPanel` zeigt jetzt zusätzlich zu „Alle Assets" und
  „Nur {aktuelles Asset}" pro Instrument-Gruppe (Crypto/Resources/Indices/Forex) einen
  1-Klick-Preset-Button. Datenquelle: `useInstruments().groups` (identisch zur Sidebar).
- **Master-Prompt Hebel-Slider auf 200x (P2)**: Frontend `AIGovernancePanel` (max=200) +
  Backend-Sanitizing in `ai_master_prompt._sanitize_rules`.
- **Dashboard Desktop-Layout (P1)**: `StrategyTabs.css` erhöht den Cap der Strategie-Bar auf
  `min(38vh, 260px)` + eigener `z-index`, damit der Chart die Strategien nie mehr überdeckt,
  auch bei vielen KI-Kandidaten.
- **Trades Prozent-PnL (P2)**: `AutoTradeModal` (offene Trades) und `DeepAnalytics`
  (Trade-Liste + MiniTradeLoader) zeigen hinter dem PnL zusätzlich `(±X.XX%)` (auf die Marge –
  Backend-Feld `computed.pnl_pct_capital`, siehe `core/utils._enrich_trade`).
- **Liquidations-Heatmap Erklärungen (P2)**: `LiquidityPanel` bekommt einen ausklappbaren
  Legenden-Block („Was bedeutet was?") mit Datenquellen (Binance/OKX/Bybit, keyless),
  Beschreibung aller Werte (Intervall/Preis/OI/Heatmap/Cluster/Levels/Wände) und einem
  Vertrauens-Absatz. Zusätzlich `title="..."` als Hover-Tooltip an jeder Steuerung und Metrik.

## Umgesetzt (02.08.2026 – dritte Iteration)
- **Regel-Vorschau (P1)**: NEU `POST /api/backtest/rule-preview` – Mini-Backtest (Standard
  7 Tage, max. 30) für eine noch NICHT gespeicherte Custom-Definition direkt aus dem
  StrategyBuilder. Nutzt ephemere `CustomStrategy` + Registry-Shim über den normalen
  `bt.run_backtest`-Pfad (params.preview=true unterdrückt Telegram-Meldung, Job wird
  danach aus bt.JOBS entfernt). Frontend: `StrategyBuilder.js` Abschnitt
  „Regel-Vorschau (7 Tage)" mit Symbol-/Timeframe-Auswahl, Ergebnis (Trades/WR/PnL
  je Seite) + rule_problems. E2E getestet (54 Trades in ~2s).
- **Multi-Step-Auto-Fix (P1)**: `custom_params.suggest_expr_fix` – zerlegt Mathe-Ausdrücke
  per `rule_terms.normalize_expr` und korrigiert JEDEN unbekannten Term einzeln
  (Alias → dyn. Term → Fuzzy); in `suggest_value_fix` integriert
  (z.B. `recent_low - 2*atr14` → `recent_low - 2 * atr_14`).
- **Kill-Switch / Drawdown-Guard (NEU)**: `services/risk_guard.py` – stoppt NEUE Auto-Trades
  bei >=5% Tagesverlust (vom zugewiesenen Kapital paper+live) ODER >=3 Verlust-Trades in
  Folge (UTC-Tag); Pause bis Mitternacht UTC + Auto-Reset; manueller Reset per UI.
  Hooks: `bitunix_trade.on_signal` (check_can_open) + `_after_close_hooks`
  (evaluate_after_close – auch Liquidation/manual_close). Persistiert in
  settings/_id=risk_guard. Endpoints: GET/POST `/api/risk-guard`, POST
  `/api/risk-guard/reset`. UI: SettingsPanel „Steuerung"-Tab (Kill-Switch- +
  Anti-Stacking-Karten, tripped-Box mit Reset-Button).
- **Anti-Stacking (NEU)**: gleiche Richtung + Asset + Strategie innerhalb Cooldown
  (Default 30 min) blockiert; Hedge/andere Strategien/Timeframes IMMER erlaubt
  (löst „3x Short BTC nacheinander vom KI-Trader").
- **Benachrichtigungs-Schicht (NEU)**: `services/notifier.py` – 8 einzeln schaltbare
  Telegram-Meldungen (Master, KI-Ausfall, Backtest fertig, Optimizer fertig, Trade
  eröffnet, Trade geschlossen, Kill-Switch, Tages-Zusammenfassung), persistiert in
  settings/_id=notifications, 30-min-Dedup. Endpoints GET/POST
  `/api/notifications/settings`. UI: Telegram-Reiter (Toggles).
- **KI-Ausfall-Meldung**: `ai_providers.generate_chain/stream_chain` melden via
  `_notify_fallback`, wenn Primär-Modell (inkl. Backup-Key) scheitert und ein
  Fallback-Modell übernimmt bzw. die Kette komplett scheitert → Website-Banner + Telegram.
- **Website-Banner (NEU)**: `frontend/SiteAlerts.js` (+CSS) – pollt GET `/api/alerts`
  alle 30s, dismissbar (POST `/api/alerts/dismiss/{id}`); in App.js unter dem Header.
  Kill-Switch-Banner wird bei Guard-Reset entfernt und nach Server-Neustart
  wiederhergestellt, solange der Guard aktiv ist.
- **Weitere Hooks**: backtester/optimizer (done → Telegram), bitunix_trade
  (open/close/liq → Telegram + Guard), ai_engine._daily_reset (Tagesbericht → Telegram).
- **Tests**: NEU `backend/tests/test_risk_guard_notifier.py` (10) +
  `test_risk_guard_pipeline.py` (2, on_signal-Verdrahtung). Gesamt 362 unit-passed
  (Baseline 350, +12, keine Regression; 165 Legacy-Endpoint-Failures brauchen weiter
  einen laufenden Server – bekanntes Umgebungs-Thema).
- **DeepSeek**: war bereits integriert (`deepseek/deepseek-r1:free` via OpenRouter) –
  benötigt nur `OPENROUTER_API_KEY` (openrouter.ai/settings/keys) in der Render-env.

## Backlog / Nächste Aufgaben
- P1: Fix-Suggestions auf Regel-Wert-Ebene für komplexe Mathe-Ausdrücke (z. B. `volatility_pct * price`)
  – ERLEDIGT 02.08.2026 (suggest_expr_fix).
- P1: Rule-Preview-Backtest über 7 Tage direkt beim Anlegen einer Regel im StrategyBuilder.
  – ERLEDIGT 02.08.2026 (rule-preview Endpoint + UI).
- P2: `bb_upper_20/keltner_upper_30` mit dynamischer Periode (aktuell nur über indicators-Config).
- P2: Session-Presets (z.B. "nur London/NY") als UI-Shortcut für hour-in_range-Regeln.
- P2: Modell-Empfehlungs-Seite in der UI (Rollen-Presets mit Begründung anzeigen).

## Backlog / Nächste Aufgaben (alt)
- P1: Backtester-Hinweisbox könnte pro Regel einen "Auto-Fix"-Vorschlag anzeigen.
- P1: bb_upper_20/keltner_upper_30 mit dynamischer Periode (aktuell nur über indicators-Config).
- P2: Session-Presets (z.B. "nur London/NY") als UI-Shortcut für hour-in_range-Regeln.
- P2: Modell-Empfehlungs-Seite in der UI (Rollen-Presets mit Begründung anzeigen).
