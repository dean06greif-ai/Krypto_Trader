# PRD – Daytrading-Website: KI-Trader-Verbesserungen

## Original-Problemstellung
Bestehende, produktiv laufende externe Daytrading-Website (GitHub: regimeUpdates31071509,
React + FastAPI + MongoDB, Multi-LLM-Provider Gemini/Groq/OpenRouter/Mistral über Env-Keys).
Grundsatz: sauber, modular, rückwärtskompatibel in die bestehende Architektur einpflegen,
Regressionstests vor größeren Änderungen.

Gemeldete Probleme / Wünsche (alle vom User priorisiert):
1. KI "schließt" Live-/Paper-Positionen im Chat nur verbal – auf der Website bleiben sie offen.
2. Lektionen sollen datenbasiert validiert werden (genug Trades müssen exakt auf die
   Änderung hinweisen, exakte Wiedererkennung), gleiches Prinzip für Einstellungs-
   Änderungen (TP/CRV/SL etc.). Bei Autonomie=auto keine Popup-Flut für unvalidierte
   Wünsche – KI arbeitet autonom. Direkte Trader-Anweisungen (Chat) gelten SOFORT.
   Lektionen müssen auch im Lernen-Reiter erscheinen, nicht nur im KI-Gedächtnis.
3. KI soll Trades autonom anpassen können (Gewinn sichern, Marge raus etc.);
   Bug: "ADJUST_SL ... FEHLGESCHLAGEN: SL 73.62 liegt auf der falschen Seite des Preises 73.64".
4. Strategie-Reiter: KI als Hilfstool (Feedback, Verbesserungen); Backtester-Fehler
   "Kandidat keine maschinenlesbare Regel" → KI soll Regeln maschinenlesbar übersetzen
   oder ehrlich sagen, wenn nicht backtestbar.

## Architektur (relevant)
- Backend: FastAPI, `services/` (ai_engine, ai_learning, ai_lessons, ai_validation,
  ai_trade_manager, ai_strategy_lab, bitunix_trade …), `routers/` (ai, ai_lab,
  ai_governance …). MongoDB via MONGO_URL. Admin-Auth: JWT (ADMIN_USER/ADMIN_PASSWORD).
- Frontend: React (CRA/Craco), `components/AITradingPanel.js` (Chat, Lernen-Reiter,
  Vorschlags-Strip), `AIStrategyLabPanel.js` (Strategie-Reiter).
- LLM-Keys nur in Produktion (Render EnvVars), lokal keine → LLM-Flows liefern lokal
  saubere "Kein API-Key"-Meldungen (erwartet).

## Umgesetzt (31.07.2026)
1. **Chat-Kommando-Schicht** – NEU `services/ai_chat_commands.py`:
   Keyword-Vorerkennung + LLM-Extraktion → REALE Ausführung über bestehende
   Sicherheits-/Audit-Wege (trade_manager, lesson_store, _handle_config_changes,
   source="user" = sofort, ohne Validierung). Aktionen: Positionen schließen (mit
   DB-Verifikation), Trade-Aktionen, Trade eröffnen, Lektion anlegen/ändern/löschen,
   Einstellungs-Änderung. Echte Ergebnisse werden vor der Chat-Antwort in den
   System-Kontext injiziert – die KI darf nur berichten, was wirklich passierte
   (ai_engine.chat_stream + CHAT_SYSTEM_TEMPLATE).
2. **ADJUST_SL/TP-Clamp** – `ai_trade_manager.clamp_level()`: SL/TP auf falscher
   Kursseite wird automatisch knapp (0.1 %) auf die gültige Seite korrigiert;
   Seiten-Regel im Trade-Manager-Prompt; Anti-Spam: identische Fehlermeldung pro
   Trade+Aktion max. alle 30 min im Chat.
3. **Lektions-Validierung durch Wiedererkennung** – `ai_validation`
   (min_lesson_confirmations, Default 2) + `ai_learning`: neue KI-Lektionen landen als
   Kandidaten in `ai_lesson_candidates` (exaktes Titel-Matching); aktiv erst nach
   mehrfacher Wiedererkennung UND Daten-Gate. Kandidaten stehen im Lernlauf-Prompt
   (exakter Titel!) und im Lernen-Reiter ("Lektions-Kandidaten"). Trader-Lektionen
   (UI + Chat) gelten sofort, locked, im Lernen-Reiter sichtbar (gemeinsame Quelle
   settings/ai_lessons → /api/ai/insights).
4. **Kein Popup-Spam bei Autonomie=auto** – Backend: geparkte config_changes
   (needs_data/needs_confirmation) erzeugen in auto keine Chat-Notiz; Frontend:
   Vorschlags-Strip lädt in auto keine geparkten Vorschläge.
5. **Strategie-Assistent** – `ai_strategy_lab.assist()` + POST /api/ai/strategies/assist:
   Feedback, Vorschläge, geschärfte Beschreibung, maschinenlesbare rule_definition
   (validiert via valid_rule_definition) oder ehrliche backtest_note. Frontend:
   "KI-Hilfe zur Strategie" (Form) + "KI: Backtest-Regeln ableiten" (Kandidaten-Karte,
   apply_rules=true registriert direkt für Backtester); rule_definition wird beim
   Anlegen mitgespeichert. register-test-Fehlermeldung verweist auf den KI-Button.

## Tests
- NEU `tests/test_ai_chat_commands_and_fixes.py` (12 Unit-Tests, pure Funktionen).
- Testing-Agent: `tests/test_iter_chat_cmds_e2e.py` (16 E2E-API-Tests) – 28/28 PASS,
  Frontend-Flows (Login, Lernen-Reiter, Strategie-Labor) verifiziert.
- Bestehende Suite grün bis auf 2 daten-abhängige Alt-Tests (erwarten geseedete
  Produktionsdaten: test_regime_lab test_03, test_settings_persistence test_11).
- Hinweis E2E-Trade-Test: /api/ai/trade/open braucht vorher
  /api/autotrade/strategy/ai_trader/coin/BTCUSDT {enabled:true, mode:'paper'}.

## Credentials
- Admin: Admin / Dean06Greif! (backend/.env lokal; produktiv via Render EnvVars).

## Backlog / offene Punkte
- P1: LLM-abhängige Flows (Chat-Kommandos end-to-end, Lernlauf-Kandidaten-Promotion,
  Strategie-Assistent-Antworten) in Produktion mit echten Keys verifizieren.
- P2: Vorschlags-Historie-Ansicht (auto_applied-Log) im UI.
- P2: Lektions-Kandidaten manuell im UI freigeben/verwerfen können.
- P2: Konsistenz weiterer Response-Signaturen (status ok/success) – teilweise behoben.
