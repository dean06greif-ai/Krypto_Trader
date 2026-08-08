# PRD – Krypto Trader (KI Trader Verbesserungen)

## Original-Problemstellung
Bestehende, produktiv laufende externe Daytrading-Website (GitHub: dean06greif-ai/Krypto_Trader, Branch main8.8.1130, Deployment auf Render, extern/ausgelagert – soll so bleiben). Verbesserungen sauber, modular, rückwärtskompatibel in die bestehende Architektur einpflegen:
1. KI Trader Live-Performance schlecht (119 Trades, 42% Winrate, -76 USDT PnL) – Belohnungssystem zum Lernen gewünscht
2. KI-Modell-Auswahl im KI-Team fehlerhaft/kostenintensiv (Rate-Limits, 413- und 404-Fehler)
3. Kill-Switch löst wiederholt aus (6-10 Verlust-Trades trotz Limit 4), KI lernt nicht daraus
4. Marktanalyse-Texte pro Asset abgeschnitten – Detailansicht pro Coin gewünscht

## User-Entscheidungen
- Reihenfolge: erst Modell-Fix, dann Belohnungssystem, dann UI
- Lernen: Reward-Score pro Trade + Lernen aus Backtests/Endlos-Suche/Regime-Lab (beides)
- Günstige Paid-Modelle (Gemini Flash/Flash-Lite) für kritische Rollen erlaubt
- Kill-Switch: Ja, Zwangs-Lernphase vor Wiederaufnahme
- Arbeit direkt am Repo-Code (liegt unter /app/repo), Deployment weiterhin durch den User auf Render

## Architektur (bestehend, unverändert)
- FastAPI-Backend (/app/repo/backend): server.py + routers/ + services/ (KI-Ökosystem: ai_engine, ai_roles, ai_providers, ai_learning, ai_lessons, trade_guard, …)
- React-Frontend (/app/repo/frontend), MongoDB (Atlas in Produktion)
- Lokale Testumgebung: Symlinks /app/backend -> /app/repo/backend, /app/frontend -> /app/repo/frontend, lokale MongoDB (crypto_scanner_local), KEINE Live-/LLM-Keys (bewusst)

## Umgesetzt (08.06.2026 / aktueller Stand August-Iteration)
1. **Modell-Katalog bereinigt** (services/ai_providers.py):
   - Entfernt: groq `qwen/qwen3-32b` (404), groq `llama-3.3-70b-versatile` (deprecated 08/2026), openrouter `deepseek/deepseek-r1:free` + `qwen/qwen3-235b-a22b:free` (nur noch paid), GitHub-Models-Provider komplett (Dienst wird 30.07.2026 abgeschaltet), cerebras `qwen-3-32b`
   - Neu: groq `openai/gpt-oss-120b` + `openai/gpt-oss-20b`
   - MODEL_MIGRATIONS: gespeicherte tote Modelle (Rollen, Fallbacks, Haupt-Modell) werden beim Laden automatisch auf Nachfolger migriert
2. **413-Schutz**: MODEL_MAX_INPUT_CHARS + model_input_limit(); zu große Prompts überspringen Groq-Modelle in der Kette proaktiv; 413-Fehler führen direkt zum nächsten Modell (kein sinnloser Key-Wechsel); 'NoneType'-Crash bei leeren OpenRouter-Antworten gefixt
3. **Rollen-Presets neu** (services/ai_roles.py): kritische Rollen (Analyst, Trade-Manager, Learner) auf günstiges Gemini Flash mit kostenlosen starken Fallbacks (Groq GPT-OSS 120B), 24/7-Rollen auf billigste Modelle
4. **Belohnungssystem** (NEU services/ai_reward.py): Reward-Score pro geschlossenem Trade (PnL%-basiert, Verluste 1.3x, Malus für Sofort-Stop-Out <15min und Verlust trotz Konfidenz >=80%, Bonus für gehaltene Gewinner). Persistiert an auto_trades + ai_decisions (via ai_learning.sync_outcomes). Aggregierte Stats fließen in jeden Lernlauf UND jede Analyse ein; API: /api/ai/insights Feld `reward`; UI: Reward-Zeile im Lern-Panel
5. **Kill-Switch-Zwangs-Lernphase** (services/trade_guard.py): Bei Auslösung startet sofort ein Lernlauf (Fokus Verlust-Serien-Analyse, trigger="kill_switch"); Auto-Trading bleibt blockiert bis Lernlauf fertig (Retry über learning.tick alle 30s; Sicherheitsnetz: max. 6h Blockade nach Mitternacht); state-Felder learning_required/learning_done; manuelles Resume hebt alles auf
6. **Frontend** (AITradingPanel.js/.css): Markt-Analyse-Overview mit "Mehr anzeigen/Weniger anzeigen" (Backend-Limit 1800→4000 Zeichen), Coin-Entscheidungszeilen klickbar → volle asset-spezifische Begründung, Modell-Dropdowns aktualisiert, Reward-Stats im Lern-Panel
7. **Tests**: NEU tests/test_iter_reward_models_killswitch.py (Katalog, Migration, 413, Reward, Kill-Switch-Lernphase, Watchdog-Klassifikation, Skip-Health) + tests/test_review_iter_reward_killswitch.py + tests/test_iter_reward_regime_watchdog.py (E2E vom Testing-Agent); 4 Alt-Tests an neuen Katalog angepasst. Testing-Agent Iteration 1: Backend 6/6, Frontend 11/11 grün; Iteration 2: Backend 6/6, Frontend 100% grün

### Iteration 2 (gleiche Session)
8. **Reward-Verlaufskurve**: reward_stats liefert `series` (Tages-Score + kumuliert); SVG-Sparkline im Lern-Panel (data-testid ai-reward-chart)
9. **Regime-Reward**: Trades speichern beim Öffnen `market_regime` (Snapshot des Markt-Beobachters); reward_stats aggregiert `by_regime`; fließt in Lern-/Analyse-Prompt ein; UI-Zeile (ai-reward-regimes)
10. **Modell-Wächter** (NEU services/model_watchdog.py): wöchentlicher Ping aller Katalog-Modelle, erkennt tote Slugs (404/decommissioned/paid-only), Website- + Telegram-Warnung; Endpoints POST /api/ai/models/check (admin) + GET /api/ai/models/watchdog; Zusammenfassung in /api/ai/status.model_watchdog; Loop startet in server.py
11. **Skip-Anzeige**: providers_health.skipped (413-Schutz-Übersprünge) + tote Modelle im Health-Badge-Dropdown des KI-Panels
12. **Scroll-Pfeil** im KI-Chat: Button (ai-jump-newest-btn) erscheint beim Hochscrollen, springt zur neuesten Nachricht

### Iteration 3 (gleiche Session)
13. **Lektions-Wirkung**: measure_effectiveness (Winrate vor/nach Einführung je Lektion); wirkungslose KI-Lektionen (Delta <= -5% bei >=12 Trades danach) werden automatisch deaktiviert und aus den Prompts entfernt; Trader-Lektionen nur markiert ("Wirkung fraglich"); UI-Badges + Reaktivieren-Button (PATCH /api/ai/lessons/{id} {active:true})
14. **Mobile-Fixes**: Signal-Alert-Modal X erreichbar (Selektor-Bug .alert-modal-content -> .alert-modal, max-height 90dvh, sticky X); Dropdowns (Benachrichtigungen, Analyse-Tools) auf Mobile position:fixed volle Breite statt seitlich abgeschnitten
15. **Desktop-Dashboard**: Signal-/Strategie-Panel scrollt nicht mehr einzeln – ganze Seite scrollt (Media-Query >=969px, Sidebars sticky, Chart behält ~58vh)
16. **Chart**: EMA-Linien nicht mehr am Rand abgeschnitten (scaleMargins 0.08); Trade-Hover-Kopplung (Punkt-Hover highlightet Badge, Badge-Hover zeigt SL/TP-Linien)
17. **Coin-Filter**: KI Trader analysiert nur noch Coins, die für ihn zum Traden freigeschaltet sind (paper/live) – symbol_trade_enabled + Filter in run_analysis
18. **Keys/Benachrichtigungen**: GROQ_API_KEY_BACKUP unterstützt (User muss ihn in Render-ENV eintragen); KI-Ausfall-Meldungen erst wenn ALLE Keys eines Modells (primär+backup) erschöpft
19. **Multi-TP & Experimente**: KI kann bis zu 2 zusätzliche Teil-TPs setzen (tp_levels), runner auch bei scalp (strukturabhängiger TP); Experimente (experiment:true) laufen immer als Paper/Ghost, zählen nicht in den Reward-Score, Prompt ermutigt zu Tests mit CRV 3-5/Struktur-Zielen; Liquiditäts-Block als optionaler Kontext markiert
- Testing-Agent Iteration 3: Backend 29/29 pytest + 4/4 API, Frontend 100% (Alert-Modal lokal nicht auslösbar -> manuell prüfen bei nächstem Live-Alert)

## Test-Ergebnis / Hinweise
- Volle pytest-Suite: verbleibende Failures ausschließlich umgebungsbedingt (keine LLM-/Bitunix-Keys lokal, alte Tests mit hartkodiertem Passwort "admin", Marktdaten-/Worker-Abhängigkeiten) – keine Regression
- Produktions-Build (yarn build) erfolgreich

## Backlog / Nächste Schritte
- P1: Reward-Score pro Regime auswerten (sobald Regime-ID am Trade gespeichert wird) → schärfere Regime-4-Lektionen
- P1: Reward-Verlauf-Chart im Lern-Panel (Score über Zeit)
- P2: Provider-Health-Ansicht: übersprungene Modelle (413-Schutz) sichtbar machen
- P2: Automatischer wöchentlicher Modell-Katalog-Check (tote Slugs erkennen und melden)
- P2: Kosmetik: React-Warnung <span> in <option> (Analyse-Zeitplan-Select) – nur Dev-Tool-Wrapper, kein Produktionsproblem
