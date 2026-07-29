# PRD – Iteration 17: Forex, Indices, Resources + KI-Lektions-Bugfix
Repo: dean06greif-ai/NEW28.07 · Datum: Juni 2026
(Ergänzung zu `/app/memory/PRD.md`)

## Original-Problemstatement
Die Website läuft produktiv und stabil und wird extern gehostet (soll so bleiben).
Verbesserungen sollen sauber, modular und langfristig wartbar in die bestehende
Architektur eingepflegt werden – Stabilität, Rückwärtskompatibilität und klare
Struktur haben Vorrang vor aggressiven Änderungen.

1. Zusätzlich zu Top-10-Krypto und Gold/Silber/Öl sollen **Forex** sowie der
   **Nasdaq 100 (bei Bitunix: QQQUSDT)** und **SPYUSDT** verfügbar sein.
2. Sidebar: Gold/Silber/Öl unter Reiter **Resources**, QQQUSDT+SPYUSDT unter einem
   passenden Oberbegriff, Forex mit passenden Assets.
3. Alle neuen Assets müssen im **Backtester** und im **Strategie-Optimizer**
   auswählbar sein und funktionieren.
4. Bugfix **KI-Trader**: es wurden nur ca. 5 Lektionen gespeichert, obwohl im Setup
   50 eingestellt sind.

User-Vorgaben: Traden ausschließlich über Bitunix, Kursanzeige darf über andere
Quellen laufen; Kategorien heißen Resources / Indices / Forex.

## Architektur-Erweiterung
- **`backend/core/instruments.py`** ist die EINZIGE Quelle der Wahrheit für Assets
  (Symbol, Gruppe, Bitunix-Kontrakt, Live-Quelle, Historien-Quelle, max. Historie).
  Neue Assets = eine Zeile. `core/config.py` re-exportiert die alten Konstanten
  (`TOP_10_COINS`, `OTHER_INSTRUMENTS`, `OTHER_YAHOO`, `ALL_SYMBOLS`) unverändert.
- **`backend/services/history_sources.py`** löst pro Symbol die Historien-Quelle auf:
  Binance (vorwärts, 1000/Request), Bitunix (rückwärts über `endTime`, 200/Request),
  Yahoo (7-Tage-Chunks). `candle_cache` bleibt der einzige Cache-Layer.
- **Frontend**: `src/hooks/useInstruments.js` lädt `/api/coins` einmal und teilt das
  Universum prozessweit; `src/components/AssetPicker.js` rendert gruppierte Chips.
- **Local Worker** enthält jetzt `core/` im Paket (Quellen-Auflösung), Version 1.6.0.

## Asset-Universum (22 Instrumente)
| Gruppe | Assets | Live-Kurse | Historie | Live-Order (Bitunix) |
|---|---|---|---|---|
| TOP 10 COINS | BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, DOT, POL (USDT) | Bitunix→Binance→OKX | Binance (voll) | ja |
| RESOURCES | GOLD, SILVER, OIL | Yahoo (GC=F, SI=F, CL=F) | Bitunix (XAUUSDT, XAGUSDT, CLUSDT) | ja |
| INDICES | QQQUSDT (Nasdaq 100), SPYUSDT (S&P 500) | Bitunix | Bitunix (~110 Tage) | ja |
| FOREX | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD | Yahoo | Yahoo (~30 Tage) | **nein** – Bitunix listet keine FX-Kontrakte → Scanner/Backtest/Optimizer/Paper |

## Umgesetzt (Juni 2026)
- 22 Instrumente in 4 Gruppen; `/api/coins` liefert `coins`, `crypto`, `tradable`, `groups`.
- Historien-Provider mit Paging, Retry, Fortschritt und `HistoryUnavailable`
  (keine stillen Cache-Lücken mehr); quellenspezifische Request-Drosselung.
- `get_candles()` kappt Anfragen auf die real verfügbare Historie je Instrument.
- `market_data.fetch_from()` für Assets, die nur bei einer Börse gelistet sind.
- Forex-Volumen: Spot-FX hat kein Volumen → Aktivitäts-Proxy aus der Kerzen-Spanne
  (identisch in Live-Feed und Historie), sonst blockierten `rel_vol`-Filter jedes Signal.
- Backtester/Optimizer/Local-Worker nutzen `BACKTEST_SYMBOLS` statt `TOP_10_COINS`.
- `bitunix_trade`: SYMBOL_MAP aus instruments; Live-Order ohne Bitunix-Kontrakt wird
  auf Paper heruntergestuft (verhindert Geister-Positionen).
- **Bugfix KI-Trader**: `ai_learning.merge_lessons()` führt Lektionen zusammen statt
  sie zu ersetzen. Root Cause: das LLM liefert pro Lauf 3-5 Lektionen und die alte
  Liste wurde komplett überschrieben → `max_lessons=50` wirkte nie. Prompt/Schema um
  `removed_lessons` erweitert; Titel-Duplikate werden case-insensitiv aktualisiert.
- Frontend: aufklappbare Sidebar-Reiter (localStorage), `AssetPicker` in Backtester
  und Optimizer, `P`-Badge für Assets ohne Bitunix-Kontrakt, dynamische
  Preis-Genauigkeit im Chart (Forex 4-5 Dezimalstellen).
- Tests: `test_instruments_universe.py`, `test_ai_lessons_merge.py`,
  `test_forex_volume_proxy.py`, `test_iter17_new_assets.py` – 45 Tests grün.

## Backlog
### P0
- keine offenen Punkte.

### P1
- Längere Forex-Historie (Yahoo deckt nur ~30 Tage 1m ab): Twelve Data / Alpha Vantage /
  Dukascopy-Import oder 5m/15m-Historie als Fallback für lange Backtests.
- Frontend: beim ersten Laden mehrere `TypeError: Failed to fetch` in der Konsole
  (Race gegen Backend-Ready) – Fetch-Wrapper mit Retry/Guard.
- Test-Schuld im Repo: HTTP-Integrationstests nutzen widersprüchliche Admin-Passwörter
  (`admin`, `admin123`, `Dean06Greif!/Admin`) bzw. senden keinen Authorization-Header.

### P2
- Weitere Bitunix-Perps als Gruppe „Stocks“ (IWM/Russell 2000, NVDA, TSLA, AAPL).
- Handelszeiten-Kalender für Indizes/Forex (Wochenend-/Feiertagslücken im Backtest ausweisen).
- Pip-/Tick-Größen und FX-typische Positionsgrößen (Lots) im Paper-Trading.

## Nächste Schritte
1. Feedback zur Gruppierung und zur Auswahl der Forex-Paare einholen.
2. P1: Forex-Historie verlängern, danach längere FX-Backtest-Zeiträume freigeben.
3. P1: Fetch-Fehler beim initialen Frontend-Load beseitigen.
