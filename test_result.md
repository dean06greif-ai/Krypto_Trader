#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================
user_problem_statement: |
  Bestehende Daytrading-Website (extern gehostet) erweitern, ohne Bestandsfunktionen zu brechen:
  1. Neben Top-10-Krypto und Gold/Silber/Öl sollen auch Forex sowie Nasdaq (QQQUSDT) und SPYUSDT
     handelbar/analysierbar sein.
  2. Sidebar: Gold/Silber/Öl unter Reiter "Resources", QQQ/SPY unter passendem Oberbegriff, Forex mit
     eigenen Assets.
  3. Alle neuen Assets müssen im Backtester und im Strategie-Optimizer wählbar sein und funktionieren.
  4. Bugfix KI-Trader: es wurden nur ~5 Lektionen gespeichert, obwohl im Setup 50 eingestellt sind.

backend:
  - task: "Asset-Universum als zentrale Quelle (core/instruments.py) + Rückwärtskompatibilität core/config.py"
    implemented: true
    working: true
    file: "backend/core/instruments.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "22 Instrumente in 4 Gruppen (TOP 10 COINS, RESOURCES, INDICES, FOREX). Alte Exporte
          (TOP_10_COINS, OTHER_INSTRUMENTS, OTHER_YAHOO, ALL_SYMBOLS) bleiben als Re-Export erhalten.
          Unit-Tests: backend/tests/test_instruments_universe.py (12 Tests, grün)."

  - task: "Historien-Quellen pro Anlageklasse (Binance/Bitunix/Yahoo) inkl. Cache-Integration"
    implemented: true
    working: true
    file: "backend/services/history_sources.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "candle_cache._fetch_range delegiert an history_sources. Verifiziert per API-Backtest:
          QQQUSDT 60 Tage = 85.985 1m-Kerzen ohne Ratelimit-Fehler; EURUSD/GBPUSD/USDJPY/AUDUSD laden
          ~14 Tage; GOLD/SILVER/OIL Historie jetzt über Bitunix-Kontrakte."

  - task: "Live-Kurse für Indizes (Bitunix) und Forex (Yahoo) im Scanner"
    implemented: true
    working: true
    file: "backend/core/instruments.py, backend/services/market_data.py, backend/core/scheduler.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "Scanner läuft über 22 Instrumente; /api/klines/{QQQUSDT,SPYUSDT,EURUSD,USDJPY}
          liefert Kerzen; /api/rule-states enthält alle neuen Symbole."

  - task: "Backtester/Optimizer/Local-Worker akzeptieren alle Assets (BACKTEST_SYMBOLS statt TOP_10_COINS)"
    implemented: true
    working: true
    file: "backend/routers/backtest.py, backend/routers/optimizer.py, backend/routers/local_worker.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "Backtest über EURUSD/GBPUSD/USDJPY/AUDUSD/QQQUSDT/SPYUSDT/GOLD liefert Trades;
          Optimizer-Lauf (params, bollinger_reversion, EURUSD+QQQUSDT) läuft durch."

  - task: "Forex ohne Börsen-Volumen: Aktivitäts-Proxy statt blockierender Volumen-Filter"
    implemented: true
    working: true
    file: "backend/services/history_sources.py, backend/services/market_data.py, backend/services/technical_indicators.py, backend/services/fast_sim.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "Spot-FX liefert volume=0 -> vorher 0 Trades bei allen Strategien mit rel_vol-Filter.
          Jetzt Kerzen-Spanne als Aktivitäts-Proxy (identisch in Live-Feed und Historie).
          Tests: backend/tests/test_forex_volume_proxy.py (6 Tests, grün)."

  - task: "Live-Order-Guard: Instrumente ohne Bitunix-Kontrakt werden als Paper simuliert"
    implemented: true
    working: true
    file: "backend/services/bitunix_trade.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "SYMBOL_MAP kommt aus core.instruments. Bitunix listet keine FX-Kontrakte -> live
          wird auf paper heruntergestuft statt eine Order zu senden (verhindert Geister-Positionen)."

  - task: "Bugfix KI-Trader: nur ~5 Lektionen wurden gespeichert"
    implemented: true
    working: true
    file: "backend/services/ai_learning.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "Root cause: jeder Lernlauf ersetzte die komplette Lektionsliste durch die LLM-Antwort
          (typisch 3-5 Lektionen) -> max_lessons=50 hatte keine Wirkung. Jetzt merge_lessons(): alte
          Lektionen bleiben erhalten, Titel-Duplikate werden aktualisiert, nur ausdrücklich verworfene
          (removed_lessons) entfallen, Limit = max_lessons. Prompt/Schema angepasst.
          Tests: backend/tests/test_ai_lessons_merge.py (6 Tests, grün). /api/ai/insights gibt 12 von 12
          Test-Lektionen zurück; max_lessons=50 persistiert."

frontend:
  - task: "Sidebar mit aufklappbaren Reitern (TOP 10 COINS / RESOURCES / INDICES / FOREX) aus /api/coins"
    implemented: true
    working: true
    file: "frontend/src/components/CoinSidebar.js, frontend/src/hooks/useInstruments.js"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "Per Screenshot geprüft: Gruppen klappen auf/zu (localStorage), QQQ/EURUSD/USDJPY
          Charts laden live, 'P'-Badge markiert Assets ohne Bitunix-Kontrakt."

  - task: "Gruppierte Asset-Auswahl in Backtester und Optimizer (AssetPicker)"
    implemented: true
    working: true
    file: "frontend/src/components/AssetPicker.js, frontend/src/components/Backtester.js, frontend/src/components/Optimizer.js"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "Screenshot: beide Panels zeigen ASSETS mit allen 4 Gruppen; Override-Punkte im
          Optimizer bleiben erhalten."

  - task: "Preis-Genauigkeit im Chart je Instrument (Forex 4-5 Dezimalstellen)"
    implemented: true
    working: true
    file: "frontend/src/components/MainChart.js"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: "Vorher kollabierten FX-Kerzen auf einer 2-Dezimal-Achse. Jetzt dynamisches
          priceFormat (USDJPY/EURUSD sauber dargestellt)."

metadata:
  created_by: "main_agent"
  version: "1.1"
  test_sequence: 0
  run_ui: false

test_plan:
  current_focus:
    - "Backtester/Optimizer mit Forex- und Index-Assets"
    - "KI-Trader Lektions-Persistenz"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
    -agent: "main"
    -message: |
      Hinweis für Tester: Bitunix listet KEINE Forex-Kontrakte (Prüfung gegen
      /api/v1/futures/market/tickers, 732 Symbole). Forex ist daher Scanner-/Backtest-/Optimizer-/
      Paper-fähig, Live-Orders nur für Assets mit Bitunix-Kontrakt (Krypto, GOLD/SILVER/OIL, QQQ, SPY).
      Forex-Historie ist bei Yahoo auf ca. 30 Tage begrenzt, QQQ/SPY auf ca. 110 Tage (Listing-Datum)
      - get_candles() kappt die Anfrage entsprechend.
      Bekannte, VORBESTEHENDE Testschulden im Repo (nicht durch diese Iteration verursacht):
      mehrere HTTP-Integrationstests nutzen widersprüchliche Admin-Passwörter ("admin", "admin123",
      "Dean06Greif!/Admin") bzw. senden gar keinen Authorization-Header und schlagen daher fehl.
      Zusätzlich startet uvicorn --reload beim Schreiben von .pytest_cache neu, was parallele
      HTTP-Tests sporadisch mit ConnectionError abbrechen lässt (mit -p no:cacheprovider stabil).
