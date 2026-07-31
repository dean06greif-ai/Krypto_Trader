"""
AI Trading Engine ("KI Trader")
- Periodically sends multi-timeframe market snapshots + crypto news + user chat
  directives to a configurable LLM (Gemini, Groq, OpenRouter/Grok, Mistral).
- The LLM returns structured trade decisions (LONG/SHORT/HOLD + confidence +
  SL/TP suggestions + reasoning). Actionable decisions are emitted as signals
  through the normal signal/auto-trade pipeline (strategy_id "ai_trader").
- Provides a multi-turn chat so the user can give the AI instructions
  ("achte auf BTC-Support bei 60k") that flow into the next analysis.

Provider (alle kostenlos in ihren Free-Tiers, deploybar auf Render):
  - Google Gemini      -> GEMINI_API_KEY  (google-genai SDK)
  - Groq (Llama, Qwen) -> GROQ_API_KEY    (OpenAI-kompatibel)
  - OpenRouter (Grok, DeepSeek, Llama Free) -> OPENROUTER_API_KEY
  - Mistral            -> MISTRAL_API_KEY (OpenAI-kompatibel)

Der Fallback bei Rate-Limit bleibt innerhalb des ausgewählten Providers.
"""
import os
import json
import re
import time
import uuid
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable
try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python <3.9 fallback (nicht relevant für Render, aber safe)
    from backports.zoneinfo import ZoneInfo  # type: ignore

from dotenv import load_dotenv
load_dotenv()

from services.timeframes import aggregate_candles
from services.technical_indicators import TechnicalIndicators
from services.news_feed import news_feed
from services import macro_context
from services.ai_knowledge import PLATFORM_KNOWLEDGE, tunable_spec_text, validate_changes
from services.ai_master_prompt import master_prompt
from services.ai_strategy_lab import strategy_lab
from services import ai_schedule
from services import ai_validation
from services.ai_validation import validation_gate
from services import ai_providers
from services.ai_roles import role_manager

logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

SUMMARY_SYSTEM = (
    "Du bist der 'KI Trader'. Fasse den abgelaufenen Trading-Tag prägnant auf Deutsch zusammen. "
    "Antworte AUSSCHLIESSLICH mit reinem Text (kein JSON, kein Markdown-Codeblock). "
    "Struktur (kompakt, max. 12 Zeilen):\n"
    "• Tages-Marktüberblick (2-3 Sätze)\n"
    "• Wichtigste Eckdaten: Anzahl Analysen, ausgelöste Signale, Trade-Entscheidungen (LONG/SHORT/HOLD)\n"
    "• Trader-Direktiven (die vom Nutzer selbst definierten Anweisungen, die die Handelsentscheidungen aktuell steuern)\n"
    "• Aktive Konfiguration (Provider/Modell, Intervall, Min. Konfidenz, Cooldown)\n"
    "Sei nüchtern und ohne Floskeln. Nutze ausschließlich die übergebenen Fakten."
)

DEFAULT_AI_CONFIG = {
    "enabled": False,
    "interval_min": 10,
    # Zeitplan der regelmäßigen Analyse: Fenster mit eigenem Intervall,
    # z.B. nachts alle 30 min, 15-18 Uhr alle 5 min (services/ai_schedule.py).
    "schedule": [],
    "min_confidence": 65,
    "provider": "gemini",
    "model": "gemini-3.5-flash",
    "news_enabled": True,
    # Externer Makro-Kontext (Key-Levels, Funding/OI, Makro-Kalender, DXY/Yield,
    # BTC-Dominanz, Trump/Truth-Social) — pro Analyse-Zyklus über get_macro_context().
    "macro_enabled": True,
    # Coins, für die pro Zyklus Key-Levels + Funding/OI geholt werden (kompakt ~2 KB).
    "macro_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "cooldown_min": 45,
    # Max. gleichzeitig offene KI-Trader-Trades pro Coin (1–5). Default 1 =
    # bisheriges Verhalten (strikt ein Trade pro Coin). Nur der KI-Trader nutzt
    # dieses Limit; alle anderen Strategien bleiben bei strikt 1 Trade pro Coin.
    "max_trades_per_coin": 1,
    # Einstellungs-Autonomie: darf die KI ihre Trade-Settings ändern?
    # off = nie | suggest = Vorschläge, Trader bestätigt | auto = sofort anwenden
    "autonomy": "suggest",
    # Selbst-Lernen aus Signal-/Trade-Ergebnissen
    "learning_enabled": True,
    "learn_on_trade_close": True,
    "learning_lookback_days": 14,
    "max_lessons": 10,
    # KI-berechnete SL/TP-Levels direkt für die Order nutzen (statt Coin-Trade-Settings)
    "use_ai_levels": False,
}

# Kataloge, Keys (inkl. Backup-Keys) & Modell-Gewichte leben zentral in
# services/ai_providers.py – hier nur Aliase für Rückwärtskompatibilität.
ALLOWED_MODELS = ai_providers.ALLOWED_MODELS
OPENAI_COMPAT_PROVIDERS = ai_providers.OPENAI_COMPAT_PROVIDERS
FALLBACK_ORDER = ai_providers.FALLBACK_ORDER

DEEP_ANALYSIS_SYSTEM = (
    "Du bist der 'Tiefen-Analyst' im KI-Team einer Krypto-Daytrading-Plattform. "
    "Du erstellst eine SEHR gründliche Marktanalyse (kein direkter Trade-Auftrag): "
    "Makro-Lage, Schlüssel-Levels, Szenarien pro Coin, Risiken, konkrete Empfehlungen "
    "für den regulären Analysten (der periodisch tradet). Nutze ALLE übergebenen Daten "
    "inkl. Wirtschaftskalender, News-Wächter-Ereignisse und Performance der anderen "
    "Strategien der Plattform. Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown:\n"
    '{"report": "8-15 Sätze tiefe Marktanalyse auf Deutsch", '
    '"outlook": [{"symbol": "BTCUSDT", "bias": "bullish|bearish|neutral", '
    '"key_levels": "kompakt", "szenario": "1-2 Sätze"}], '
    '"risks": ["Risiko 1", "Risiko 2"], '
    '"recommendations": ["konkrete Empfehlung für den Analysten"]}'
)

ANALYSIS_SYSTEM = (
    "Du bist ein erfahrener Krypto-Daytrading-Analyst und triffst eigenständige "
    "Trading-Entscheidungen für ein automatisiertes System. Du bekommst Multi-Timeframe-"
    "Marktdaten, aktuelle News-Schlagzeilen, offene Positionen und Anweisungen des Traders. "
    "Sei diszipliniert: Trade NUR bei klarer Edge, sonst HOLD. Sei ehrlich mit der Konfidenz. "
    "Berücksichtige Anweisungen des Traders IMMER mit höchster Priorität. "
    "Der MASTERPROMPT des Traders steht über allem – auch über deinen Lektionen. "
    "Du bist EINE Strategie von vielen auf dieser Plattform: dein Auftrag ist, den Markt "
    "perfekt zu kennen, passende Strategien anzuwenden oder neu zu entwickeln und dabei von "
    "den anderen Strategien und deren Parametern zu lernen. "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown, exakt in diesem Schema:\n"
    '{"market_overview": "2-4 Sätze Marktlage auf Deutsch", '
    '"decisions": [{"symbol": "BTCUSDT", "action": "LONG|SHORT|HOLD", '
    '"confidence": 0-100, "sl_pct": 0.2-3.0, "tp1_pct": 0.3-4.0, "tpf_pct": 0.5-8.0, '
    '"news_impact": "positive|negative|neutral", "strategy_candidate_id": null, '
    '"reasoning": "1-2 Sätze auf Deutsch"}], '
    '"new_strategies": [{"name": "...", "thesis": "...", "rules_text": "...", '
    '"symbols": ["BTCUSDT"], "learned_from": "..."}], '
    '"config_changes": [{"symbol": "BTCUSDT", "changes": {"leverage": 8}, "reason": "kurz"}]}\n'
    "Regeln: sl_pct/tp1_pct/tpf_pct sind Prozent-Abstände vom aktuellen Preis. "
    "tp1_pct > sl_pct (CRV mind. 1.2), tpf_pct > tp1_pct. Für JEDES übergebene Symbol genau eine Entscheidung. "
    "strategy_candidate_id nur setzen, wenn die Entscheidung zu einem Strategie-Kandidaten aus "
    "deinem Strategie-Labor gehört (Kandidaten in der Ghost-Phase werden automatisch nur "
    "simuliert). new_strategies nur bei einer wirklich neuen, begründeten Idee (sonst leere Liste). "
    "config_changes ist optional und NUR erlaubt, wenn der Prompt-Abschnitt EINSTELLUNGS-AUTONOMIE aktiv ist – "
    "sonst leere Liste. Nutze deine Performance-Statistik und gelernten Lektionen aktiv für bessere Entscheidungen."
)

OPINION_SYSTEM = (
    "Du bist der 'KI Trader'. Der Trader hat eine Änderung an deinen Vorgaben vorgenommen. "
    "Sie gilt sofort – du kannst sie nicht blockieren. Sage aber ehrlich und datenbasiert deine "
    "Meinung: stimmt sie mit deiner Erfahrung überein, oder hast du einen Einwand? "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown:\n"
    '{"stance": "zustimmung|einwand|neutral", "comment": "2-4 Sätze auf Deutsch", '
    '"risk": "kurz, falls Risiko – sonst leer"}'
)

CHAT_SYSTEM_TEMPLATE = (
    "Du bist der 'KI Trader' – die integrierte Trading-KI einer Krypto-Daytrading-Plattform. "
    "Du analysierst periodisch alle Coins (Multi-Timeframe + News) und kannst automatisch Trades auslösen. "
    "Der Nutzer chattet hier mit dir, um dir Anweisungen zu geben (z.B. 'achte auf BTC-Support bei 60k', "
    "'sei heute defensiv', 'keine Shorts auf SOL'). Alle Nutzer-Nachrichten fließen automatisch als "
    "Direktiven in deine nächste Analyse ein – bestätige das, wenn dir jemand eine Anweisung gibt. "
    "Antworte kompakt, präzise und auf Deutsch. Nutze die Live-Daten unten für fundierte Antworten. "
    "Erfinde keine Zahlen.\n"
    "WICHTIG – ECHTE AKTIONEN: Gibt der Trader eine ausführbare Anweisung (Positionen schließen, "
    "Trade anpassen, Lektion anlegen/ändern/löschen, Einstellung ändern), führt das System sie REAL "
    "aus, BEVOR du antwortest. Die echten Ergebnisse stehen dann im Block 'SOEBEN REAL AUSGEFÜHRTE "
    "AKTIONEN'. Berichte EXAKT diese Ergebnisse. Behaupte NIEMALS, etwas geschlossen oder geändert "
    "zu haben, das dort nicht mit ✅ gelistet ist – fehlt der Block, wurde NICHTS ausgeführt: sage "
    "das ehrlich und bitte um eine präzisere Anweisung.\n\n"
    "=== AKTUELLER KONTEXT ===\n{context}\n\n"
    "=== BISHERIGER CHAT-VERLAUF ===\n{history}"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_rate_limit_error(err: Exception) -> bool:
    """True wenn Gemini 429 / RESOURCE_EXHAUSTED / Quota-Fehler wirft."""
    s = str(err).lower()
    return any(k in s for k in ("429", "resource_exhausted", "quota", "rate limit", "ratelimit"))


class AIEngine:
    def __init__(self):
        self.config = dict(DEFAULT_AI_CONFIG)
        self.db = None
        self.scanner = None
        self.signal_cb: Optional[Callable] = None
        self.toggle_check: Optional[Callable] = None
        self.symbols: List[str] = []
        self.decisions: Dict[str, Dict] = {}
        self.last_run: Optional[str] = None
        self.next_run: Optional[str] = None
        self.active_window: Optional[str] = None
        self._day_risk_cache: Dict = {}
        self.last_error: Optional[str] = None
        self.running = False
        self._analyzing = False
        self._next_due = 0.0
        self._last_signal_ts: Dict[str, float] = {}
        self._last_ghost_ts: Dict[str, float] = {}
        # Modell, das aktuell benutzt wird (nach Fallback ggf. abweichend von cfg.model)
        self._effective_model: Optional[str] = None
        self._effective_provider: Optional[str] = None
        # Deep-Analysis-Scheduling: Slot ("HH:MM") -> Berlin-Datum des letzten Laufs
        self._deep_ran: Dict[str, str] = {}
        self.deep_last: Optional[str] = None
        self.deep_last_error: Optional[str] = None
        # Housekeeping-State (Europe/Berlin) – wird in settings/ai_trader_housekeeping persistiert.
        # Hour-Key im Format "YYYYMMDDHH", Date-Key "YYYY-MM-DD".
        self._last_cleanup_hour: Optional[str] = None
        self._last_reset_date: Optional[str] = None
        self._housekeeping_lock = asyncio.Lock()
        # Retry-Backoff für den täglichen Reset. Zählt Fehlversuche pro anstehendem
        # Vortag, damit ein LLM-/DB-Ausfall den Reset nicht dauerhaft verhindert –
        # aber auch nicht die Engine in einer Endlosschleife blockiert.
        self._reset_retry_day: Optional[str] = None
        self._reset_retry_count: int = 0
        # Lern-Modul (wird in setup() initialisiert, braucht db)
        self.learning = None

    @property
    def key(self) -> Optional[str]:
        """API-Key des aktuell konfigurierten Providers (primärer Key).

        Fällt auf den Key eines beliebigen konfigurierten Providers zurück –
        die Modell-Kette (services/ai_roles.chain) nutzt ohnehin alle Provider
        mit Key als letzte Fallback-Stufe. So blockiert eine Voreinstellung für
        einen Provider ohne Key den Betrieb nicht."""
        direct = ai_providers.primary_key(self.config.get("provider", "gemini"))
        if direct:
            return direct
        for prov, has_key in ai_providers.available_providers().items():
            if has_key:
                return ai_providers.primary_key(prov)
        return None

    @staticmethod
    def _provider_key(provider: str) -> Optional[str]:
        return ai_providers.primary_key(provider)

    def _available_providers(self) -> Dict[str, bool]:
        """True, wenn für den Provider ein API-Key gesetzt ist."""
        return ai_providers.available_providers()

    def setup(self, db, scanner, signal_cb, toggle_check, symbols: List[str]):
        self.db = db
        self.scanner = scanner
        self.signal_cb = signal_cb
        self.toggle_check = toggle_check
        self.symbols = symbols
        from services.ai_learning import AILearning  # lazy: vermeidet Zyklen
        self.learning = AILearning(self)

    # ---------------- config ----------------
    async def load_config(self):
        doc = await self.db.settings.find_one({"_id": "ai_trader_config"})
        if doc:
            doc.pop("_id", None)
            for k in DEFAULT_AI_CONFIG:
                if k in doc:
                    self.config[k] = doc[k]
            # Migration: unbekannten Provider oder ungültiges Modell -> Default (Gemini Flash)
            prov = self.config.get("provider")
            mod = self.config.get("model")
            if prov not in ALLOWED_MODELS or mod not in ALLOWED_MODELS.get(prov, []):
                self.config["provider"] = "gemini"
                self.config["model"] = "gemini-3.5-flash"
                await self.db.settings.update_one(
                    {"_id": "ai_trader_config"},
                    {"$set": {"provider": "gemini", "model": "gemini-3.5-flash"}},
                    upsert=True,
                )
        else:
            await self.db.settings.insert_one({"_id": "ai_trader_config", **self.config})
        # load last decisions for continuity after restart
        try:
            rows = await self.db.ai_decisions.find().sort("ts", -1).limit(60).to_list(60)
            for r in rows:
                sym = r.get("symbol")
                if sym and sym not in self.decisions:
                    r.pop("_id", None)
                    self.decisions[sym] = r
        except Exception:
            pass
        # Housekeeping-Marker laden. Beim allerersten Start werden sie mit dem
        # aktuellen Berlin-Zeitstempel initialisiert, damit weder Cleanup noch
        # Reset direkt nach dem Boot feuern (sondern erst zur nächsten vollen
        # Stunde bzw. zum nächsten 00:00 Uhr Berlin).
        try:
            hk = await self.db.settings.find_one({"_id": "ai_trader_housekeeping"})
            now_berlin = datetime.now(BERLIN_TZ)
            if hk:
                self._last_cleanup_hour = hk.get("last_cleanup_hour")
                self._last_reset_date = hk.get("last_reset_date")
            if not self._last_cleanup_hour:
                self._last_cleanup_hour = now_berlin.strftime("%Y%m%d%H")
            if not self._last_reset_date:
                self._last_reset_date = now_berlin.strftime("%Y-%m-%d")
            await self.db.settings.update_one(
                {"_id": "ai_trader_housekeeping"},
                {"$set": {
                    "last_cleanup_hour": self._last_cleanup_hour,
                    "last_reset_date": self._last_reset_date,
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"AI housekeeping init failed: {e}")
        if self.learning:
            await self.learning.load_state()
        # KI-Team-Rollen (Modelle, Handelszeiten, Fallback-KI) laden
        try:
            await role_manager.load(self.db)
        except Exception as e:
            logger.warning(f"AI roles load failed: {e}")

    async def update_config(self, updates: Dict) -> Dict:
        was_enabled = self.config.get("enabled")
        if "enabled" in updates:
            self.config["enabled"] = bool(updates["enabled"])
        if "interval_min" in updates:
            self.config["interval_min"] = max(2, min(120, int(updates["interval_min"])))
        if "schedule" in updates:
            self.config["schedule"] = ai_schedule.normalize_schedule(updates["schedule"])
        if "min_confidence" in updates:
            self.config["min_confidence"] = max(0, min(100, int(updates["min_confidence"])))
        if "cooldown_min" in updates:
            self.config["cooldown_min"] = max(0, min(720, int(updates["cooldown_min"])))
        if "max_trades_per_coin" in updates:
            self.config["max_trades_per_coin"] = max(1, min(5, int(updates["max_trades_per_coin"])))
        if "news_enabled" in updates:
            self.config["news_enabled"] = bool(updates["news_enabled"])
        if "macro_enabled" in updates:
            self.config["macro_enabled"] = bool(updates["macro_enabled"])
        if "macro_symbols" in updates and isinstance(updates["macro_symbols"], list):
            syms = [str(s).upper() for s in updates["macro_symbols"] if str(s).strip()]
            self.config["macro_symbols"] = syms[:6] or macro_context.DEFAULT_SYMBOLS
        if "autonomy" in updates and updates["autonomy"] in ("off", "suggest", "auto"):
            self.config["autonomy"] = updates["autonomy"]
        if "learning_enabled" in updates:
            self.config["learning_enabled"] = bool(updates["learning_enabled"])
        if "learn_on_trade_close" in updates:
            self.config["learn_on_trade_close"] = bool(updates["learn_on_trade_close"])
        if "learning_lookback_days" in updates:
            self.config["learning_lookback_days"] = max(3, min(90, int(updates["learning_lookback_days"])))
        if "max_lessons" in updates:
            self.config["max_lessons"] = max(3, min(100, int(updates["max_lessons"])))
        if "use_ai_levels" in updates:
            self.config["use_ai_levels"] = bool(updates["use_ai_levels"])
        if "provider" in updates and "model" in updates:
            prov, mod = updates["provider"], updates["model"]
            if prov in ALLOWED_MODELS and mod in ALLOWED_MODELS[prov]:
                self.config["provider"], self.config["model"] = prov, mod
                # Wechselt der Nutzer das Modell manuell, reset des Fallback-States.
                self._effective_model = None
        elif "model" in updates:
            mod = updates["model"]
            # Finde Provider automatisch anhand des Modells
            for prov, models in ALLOWED_MODELS.items():
                if mod in models:
                    self.config["model"] = mod
                    self.config["provider"] = prov
                    self._effective_model = None
                    break
        await self.db.settings.update_one({"_id": "ai_trader_config"},
                                          {"$set": dict(self.config)}, upsert=True)
        if self.config.get("enabled") and not was_enabled:
            self._next_due = 0  # run analysis immediately after enabling
        elif "schedule" in updates or "interval_min" in updates:
            # Neues (kürzeres) Intervall soll sofort greifen, nicht erst nach dem
            # alten Wartefenster.
            interval = max(1, self.current_interval()[0]) * 60
            self._next_due = min(self._next_due, time.time() + interval)
        return dict(self.config)

    # ---------------- market context ----------------
    def _snapshot(self, symbol: str) -> Optional[Dict]:
        candles = self.scanner.candle_buffer.get(symbol, [])
        if len(candles) < 60:
            return None
        ti = TechnicalIndicators
        price = candles[-1]["close"]
        lines = []
        rsi_1m = 0
        for tf in ("1m", "15m", "1h"):
            agg = candles if tf == "1m" else aggregate_candles(candles, tf, drop_partial=True)
            if len(agg) < 20:
                continue
            cl = [c["close"] for c in agg][-120:]
            rsi_arr = ti.calculate_rsi(cl, 14)
            rsi = rsi_arr[-1] if rsi_arr and rsi_arr[-1] is not None else 50
            if tf == "1m":
                rsi_1m = rsi
            ema20 = ti.calculate_ema(cl, 20)[-1]
            ema50 = ti.calculate_ema(cl, 50)[-1] if len(cl) >= 50 else None
            trend = "aufwärts" if (ema50 and ema20 > ema50) else ("abwärts" if ema50 else "unklar")
            chg = (cl[-1] - cl[0]) / cl[0] * 100 if cl[0] else 0
            hi = max(c["high"] for c in agg[-60:])
            lo = min(c["low"] for c in agg[-60:])
            lines.append(f"{tf}: RSI {rsi:.0f}, Trend {trend}, Δ{chg:+.2f}%, Range {lo:g}-{hi:g}")
        try:
            atr = ti.calculate_atr(candles, 14)[-1] or 0
            vols = [c.get("volume", 0) for c in candles]
            v_recent = sum(vols[-5:]) / 5
            v_base = (sum(vols[-60:]) / 60) or 1
            lines.append(f"ATR(1m) {atr / price * 100:.3f}% | Volumen x{v_recent / v_base:.2f}")
        except Exception:
            pass
        return {"symbol": symbol, "price": price, "rsi": round(rsi_1m, 1),
                "text": f"{symbol}: Preis {price:g} | " + " | ".join(lines)}

    async def _user_directives(self, limit: int = 15) -> str:
        rows = await self.db.ai_chat.find({"role": "user"}).sort("ts", -1).limit(limit).to_list(limit)
        rows.reverse()
        if not rows:
            return "(keine)"
        return "\n".join(f"- [{r.get('ts', '')[:16]}] {r.get('text', '')}" for r in rows)

    def _resolve_coins(self, coins) -> List[str]:
        """Normalisiert den Coin-Filter aus dem Chat.

        Leer / None / enthält "ALL" => alle bekannten Symbole. Sonst nur die
        angeforderten Symbole (Reihenfolge von self.symbols beibehalten,
        unbekannte ignorieren)."""
        if not coins:
            return list(self.symbols)
        wanted = {str(c).upper() for c in coins}
        if "ALL" in wanted or "ALLE" in wanted:
            return list(self.symbols)
        filtered = [s for s in self.symbols if s.upper() in wanted]
        return filtered or list(self.symbols)

    async def _open_trades_text(self, allowed: Optional[List[str]] = None) -> str:
        """Text-Übersicht aller offener Trades (jede Strategie, Paper + Live).

        Wenn `allowed` gesetzt ist, werden nur die passenden Symbole detailliert
        gezeigt – die übrigen offenen Positionen erscheinen als kompakte Zeile,
        damit die KI weiß, dass sie existieren (kein Blindflug bei Fokus-Chats).
        """
        rows = await self.db.auto_trades.find({"status": "open"}).to_list(200)
        if not rows:
            return "(keine offenen Positionen)"

        def _age(iso: str) -> str:
            try:
                dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
                mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
                if mins < 60:
                    return f"{mins}m"
                if mins < 60 * 24:
                    return f"{mins // 60}h{mins % 60:02d}m"
                return f"{mins // 1440}d{(mins % 1440) // 60}h"
            except (ValueError, TypeError):
                return "?"

        def _fmt(t: Dict) -> str:
            flags = []
            if t.get("tp1_hit"):
                flags.append("TP1✓")
            if t.get("breakeven_moved"):
                flags.append("BE")
            if t.get("profit_secured"):
                flags.append("Profit-Lock")
            if t.get("liquidated"):
                flags.append("LIQ")
            flag_txt = f" [{' '.join(flags)}]" if flags else ""
            strat = t.get("strategy_name") or t.get("strategy_id") or "?"
            qty_rem = t.get("qty_remaining", t.get("qty"))
            pnl = t.get("realized_pnl")
            pnl_txt = f", realPnL {pnl:+.2f}USDT" if isinstance(pnl, (int, float)) else ""
            return (
                f"- id={t.get('id')} {t.get('symbol')} {t.get('side')} "
                f"[{t.get('mode')}/{strat}] Entry {t.get('entry')} "
                f"SL {t.get('sl')} TP1 {t.get('tp1')} TPf {t.get('tpf')} "
                f"Hebel {t.get('leverage')}x Qty {qty_rem}/{t.get('qty')}"
                f"{pnl_txt} Alter {_age(t.get('opened_at'))}{flag_txt}"
            )

        if allowed is None:
            focus = rows
            others: List[Dict] = []
        else:
            allow = {s.upper() for s in allowed}
            focus = [t for t in rows if str(t.get("symbol", "")).upper() in allow]
            others = [t for t in rows if str(t.get("symbol", "")).upper() not in allow]

        lines = [_fmt(t) for t in focus] if focus else ["(keine offenen Positionen im Fokus)"]
        if others:
            paper = sum(1 for t in others if t.get("mode") == "paper")
            live = sum(1 for t in others if t.get("mode") == "live")
            syms = sorted({str(t.get("symbol", "")) for t in others})
            lines.append(
                f"(WEITERE offene Positionen außerhalb des Fokus: {len(others)} "
                f"[paper {paper}, live {live}] auf {', '.join(syms)})"
            )
        return "\n".join(lines)

    async def _context_brief(self, coins=None) -> str:
        cadence = ai_schedule.schedule_text(self.config.get("schedule"),
                                            self.config.get("interval_min", 10))
        parts = [master_prompt.prompt_block(),
                 self._role_context_block(),
                 f"=== DEIN ANALYSE-RHYTHMUS ===\nDu wirst nach diesem Zeitplan aufgerufen: "
                 f"{cadence}. Plane Stops/Ziele so, dass sie bis zum nächsten Aufruf "
                 f"tragfähig sind – aktuell in {self.current_interval()[0]} Minuten.",
                 "PLATTFORM-WISSEN (was diese Website macht – dein Grundverständnis):\n"
                 + PLATFORM_KNOWLEDGE]
        # Letzte Tages-Zusammenfassung als KI-Gedächtnis ganz oben einfügen.
        try:
            last_sum = await self.db.ai_chat.find_one(
                {"role": "summary"}, sort=[("ts", -1)],
            )
            if last_sum and last_sum.get("text"):
                parts.append(
                    f"TAGES-ZUSAMMENFASSUNG ({last_sum.get('day', '')}) – merken & berücksichtigen:\n"
                    + str(last_sum["text"])[:1500]
                )
        except Exception:
            pass
        selected = self._resolve_coins(coins)
        is_all = len(selected) == len(self.symbols)
        allow = {s.upper() for s in selected}

        focus = "ALLE COINS" if is_all else ", ".join(s.replace("USDT", "") for s in selected)
        parts.append(
            "FOKUS-COINS: " + focus + "\n"
            "(Der Nutzer hat den Chat auf diese Coins eingegrenzt – beziehe dich "
            "ausschließlich auf ihre Marktdaten, KI-Strategien, Signale und Trades. "
            "Ignoriere alle anderen Assets, außer der Nutzer fragt ausdrücklich danach.)"
        )

        snaps = []
        for s in selected:
            snap = self._snapshot(s)
            if snap:
                snaps.append(snap["text"])
        parts.append("MARKTDATEN:\n" + ("\n".join(snaps) if snaps else "(noch keine Daten)"))
        if self.config.get("news_enabled"):
            news = await news_feed.get_headlines(8)
            if news:
                parts.append("NEWS:\n" + "\n".join(f"- {n['title']} ({n['source']})" for n in news))
        try:
            macro = await self._macro_block()
            if macro:
                parts.append(macro)
        except Exception:
            pass
        if self.decisions:
            dec = [f"- {s}: {d.get('action')} ({d.get('confidence')}%) – {d.get('reasoning', '')[:120]}"
                   for s, d in self.decisions.items() if s.upper() in allow]
            if dec:
                parts.append("LETZTE KI-ENTSCHEIDUNGEN:\n" + "\n".join(dec))
        parts.append("OFFENE POSITIONEN:\n" + await self._open_trades_text(selected))
        try:
            if self.learning:
                parts.append("DEINE PERFORMANCE (Signale + Live/Paper-Trades):\n"
                             + await self.learning.performance_text())
                parts.append("DEINE GELERNTEN LEKTIONEN:\n" + await self.learning.lessons_text())
        except Exception:
            pass
        try:
            parts.append("PERFORMANCE DER ANDEREN STRATEGIEN (letzte 14 Tage):\n"
                         + await self._strategy_performance_text())
        except Exception:
            pass
        try:
            parts.append(await strategy_lab.context_text())
        except Exception:
            pass
        try:
            pend = await self.db.ai_proposals.count_documents({"status": "pending"})
            if pend:
                parts.append(f"OFFENE EINSTELLUNGS-VORSCHLÄGE: {pend} "
                             "(warten im Panel auf Bestätigung des Traders)")
        except Exception:
            pass
        cfg = self.config
        parts.append(f"ENGINE: {'AKTIV' if cfg['enabled'] else 'AUS'} | Analyse alle {cfg['interval_min']} min | "
                     f"Min. Konfidenz {cfg['min_confidence']}% | Modell {cfg['provider']}/{cfg['model']} | "
                     f"Autonomie: {cfg.get('autonomy', 'suggest')} | Lernen: "
                     f"{'an' if cfg.get('learning_enabled', True) else 'aus'} | "
                     f"Letzte Analyse: {self.last_run or 'noch keine'}")
        return "\n\n".join(parts)

    # ---------------- analysis ----------------
    async def _ai_coin_settings_text(self) -> str:
        """Aktuelle KI-Trader Trade-Einstellungen pro Coin (für Prompt & Self-Tuning)."""
        from core.defaults import DEFAULT_STRATEGY_COIN_CFG
        docs = await self.db.strategy_coin_configs.find(
            {"_id": {"$regex": "^ai_trader_"}}).to_list(100)
        saved = {d["_id"].replace("ai_trader_", "", 1): d.get("config", {}) for d in docs}
        lines = []
        for sym in self.symbols:
            c = {**DEFAULT_STRATEGY_COIN_CFG, **saved.get(sym, {})}
            sl_desc = {"structure": f"Struktur(Lookback {c.get('sl_lookback')})",
                       "fixed": f"fest {c.get('sl_fixed_percent')}%",
                       "atr": f"ATR x{c.get('atr_sl_multiplier', 1.2)}"}.get(
                           c.get("sl_mode"), str(c.get("sl_mode")))
            lev = (f"auto (max {c.get('auto_lev_max')}x)" if c.get("auto_leverage_enabled")
                   else f"{c.get('leverage')}x")
            lines.append(
                f"{sym} [{c.get('mode', 'off')}]: Hebel {lev}, SL {sl_desc}, "
                f"TP1 CRV {c.get('tp1_crv')} ({c.get('tp1_close_percent')}% Teilverkauf), "
                f"TP-Full CRV {c.get('tp_full_crv')}, BE {c.get('be_mode')}, "
                f"Profit-Secure {'an' if c.get('profit_secure_enabled') else 'aus'}")
        return "\n".join(lines)

    async def _macro_block(self) -> str:
        """Externer Makro-Kontext (get_macro_context) als kompakter Text-Block für die KI.

        Deckt die 4 vom Trader gewünschten Quellen ab (Key-Levels, Funding/OI,
        Makro-Kalender mit UTC-No-Trade-Fenstern, DXY/Yield/BTC-Dominanz) plus
        Trump/Truth-Social. Fällt lautlos aus, wenn eine Quelle nicht erreichbar ist.
        """
        if not self.config.get("macro_enabled", True):
            return ""
        try:
            syms = self.config.get("macro_symbols") or macro_context.DEFAULT_SYMBOLS
            ctx = await macro_context.get_macro_context(symbols=list(syms))
        except Exception as e:
            logger.warning(f"macro context failed: {e}")
            return ""

        lines = ["=== EXTERNER MAKRO-KONTEXT (live, alle ~10 min · get_macro_context) ==="]

        mr = ctx.get("market_regime") or {}
        if mr:
            dxy = mr.get("dxy") or {}
            y10 = mr.get("us10y_yield") or {}
            lines.append(
                "MARKT-REGIME: "
                f"BTC-Dominanz {mr.get('btc_dominance_pct', '?')}% | "
                f"DXY {dxy.get('value', '?')} ({dxy.get('chg_pct', '?')}%) | "
                f"US10Y {y10.get('value', '?')}% ({y10.get('chg_pct', '?')}%) | "
                f"Bias: {mr.get('risk_bias', 'neutral')}"
            )

        cal = ctx.get("macro_calendar") or {}
        ntw = cal.get("no_trade_windows_utc") or []
        if ntw:
            lines.append("⛔ NO-TRADE-FENSTER (UTC, High-Impact – NICHT traden, Lektion 16):")
            for w in ntw[:5]:
                lines.append(f"  - {w.get('event')}: {w.get('start_utc')} → {w.get('end_utc')}")
        upcoming = cal.get("upcoming") or []
        if upcoming:
            nxt = [f"{u.get('event')} ({u.get('importance')}) {u.get('time_utc')}"
                   for u in upcoming[:4]]
            lines.append("MAKRO-TERMINE (UTC): " + " | ".join(nxt))

        fo = ctx.get("funding_oi") or {}
        for sym, f in fo.items():
            lines.append(
                f"FUNDING/OI {sym}: rate {f.get('funding_rate', '?')} "
                f"(ann. {f.get('funding_annualized_pct', '?')}%), "
                f"OI-Δ 15m {f.get('oi_delta_15m_pct', '?')}% / 1h {f.get('oi_delta_1h_pct', '?')}% / "
                f"4h {f.get('oi_delta_4h_pct', '?')}% → {f.get('squeeze_bias', '?')}"
            )

        kl = ctx.get("key_levels") or {}
        for sym, tfs in kl.items():
            for tf, lv in tfs.items():
                sup = ", ".join(str(x) for x in (lv.get("support") or [])[:3]) or "-"
                res = ", ".join(str(x) for x in (lv.get("resistance") or [])[:3]) or "-"
                lines.append(
                    f"KEY-LEVELS {sym} {tf}: Support [{sup}] | Resistance [{res}] | "
                    f"POC {lv.get('poc')} VAH {lv.get('vah')} VAL {lv.get('val')}"
                )

        trump = ctx.get("trump_truth_social") or {}
        posts = trump.get("latest") or []
        if posts:
            flag = "⚠️ MARKTRELEVANT" if trump.get("market_relevant") else "keine klare Marktrelevanz"
            lines.append(f"TRUMP / TRUTH SOCIAL ({flag}):")
            for p in posts[:3]:
                kw = f" [{', '.join(p.get('market_keywords', []))}]" if p.get("market_keywords") else ""
                lines.append(f"  - [{p.get('time_utc', '')[:16]}]{kw} {p.get('text', '')[:160]}")

        lines.append(
            "NUTZUNG: Setze SL/TP an die Key-Levels (POC/VAH/VAL & Support/Resistance). "
            "Beachte Funding/OI für Squeeze-/Trend-Nachhaltigkeit. Handle NICHT in No-Trade-Fenstern. "
            "Berücksichtige DXY/Yield/Dominanz für Bias & Risiko-Budget."
        )
        return "\n".join(lines)

    async def _strategy_performance_text(self, days: int = 14) -> str:
        """Leserechte auf die anderen Strategien der Website: Winrate der Signale
        + PnL der geschlossenen Trades pro Strategie – als Lern-Kontext für die KI."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            sig_rows = await self.db.signals.aggregate([
                {"$match": {"timestamp": {"$gte": cutoff},
                            "signal_class": {"$ne": "PRE_SIGNAL"}}},
                {"$group": {"_id": "$strategy_id", "total": {"$sum": 1},
                            "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                            "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}}}},
                {"$sort": {"total": -1}},
            ]).to_list(50)
            trade_rows = await self.db.auto_trades.aggregate([
                {"$match": {"status": "closed", "opened_at": {"$gte": cutoff}}},
                {"$group": {"_id": "$strategy_id", "trades": {"$sum": 1},
                            "pnl": {"$sum": "$realized_pnl"},
                            "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}}}},
            ]).to_list(50)
        except Exception as e:
            return f"(Strategie-Performance nicht verfügbar: {str(e)[:80]})"
        trades_by = {t["_id"]: t for t in trade_rows}
        lines = []
        seen = set()
        for r in sig_rows:
            sid = r["_id"] or "unknown"
            seen.add(sid)
            dec = r["wins"] + r["losses"]
            wr = f"{round(r['wins'] / dec * 100)}%" if dec else "–"
            t = trades_by.get(sid)
            tr_txt = ""
            if t and t.get("trades"):
                twr = round(t["wins"] / t["trades"] * 100)
                tr_txt = f" | Trades: {t['trades']}, PnL {float(t.get('pnl') or 0):+.2f} USDT, Winrate {twr}%"
            me = " (DU SELBST)" if sid == "ai_trader" else ""
            lines.append(f"- {sid}{me}: {r['total']} Signale, Winrate {wr}{tr_txt}")
        for sid, t in trades_by.items():
            if sid in seen or not sid:
                continue
            twr = round(t["wins"] / t["trades"] * 100) if t["trades"] else 0
            lines.append(f"- {sid}: Trades: {t['trades']}, PnL {float(t.get('pnl') or 0):+.2f} USDT, Winrate {twr}%")
        return "\n".join(lines) or "(noch keine Strategie-Daten)"

    async def _deep_report_block(self) -> str:
        """Letzte Tiefenanalyse als Kontext-Block für die regulären Analysen."""
        try:
            doc = await self.db.settings.find_one({"_id": "ai_deep_report"})
        except Exception:
            return ""
        if not doc or not doc.get("report"):
            return ""
        lines = [f"=== LETZTE TIEFENANALYSE ({str(doc.get('ts', ''))[:16]} · "
                 f"{doc.get('model', '?')}, Gewicht {doc.get('weight_label', '?')}) ==="]
        lines.append(str(doc["report"])[:1500])
        recs = doc.get("recommendations") or []
        if recs:
            lines.append("EMPFEHLUNGEN DES TIEFEN-ANALYSTEN (stark gewichten):")
            lines.extend(f"- {str(r)[:200]}" for r in recs[:6])
        return "\n".join(lines)


    async def _other_strategy_params_text(self, limit: int = 14) -> str:
        """Parameter & Timeframes der ANDEREN Strategien – die KI soll daraus lernen."""
        try:
            from strategies.registry import registry as strategy_registry
            metas = strategy_registry.list_all()
        except Exception as e:
            return f"(Strategie-Parameter nicht verfügbar: {str(e)[:80]})"
        settings = getattr(self.scanner, "settings", {}) or {}
        params_by = settings.get("strategy_params", {}) or {}
        tfs = settings.get("strategy_timeframes", {}) or {}
        enabled = set(settings.get("enabled_strategies", []) or [])
        lines = []
        for m in metas[:limit]:
            sid = m.get("id")
            if sid == "ai_trader":
                continue
            p = params_by.get(sid) or m.get("default_params") or {}
            p_txt = ", ".join(f"{k}={v}" for k, v in list(p.items())[:8]) or "Standard-Parameter"
            lines.append(f"- {sid} ({m.get('name')}) [{tfs.get(sid, m.get('timeframe', '1m'))}, "
                         f"{'aktiv' if sid in enabled else 'inaktiv'}]: {p_txt}")
        return "\n".join(lines) or "(keine weiteren Strategien)"

    def _role_context_block(self) -> str:
        return (
            "=== DEINE ROLLE IM SYSTEM (wichtig) ===\n"
            "Du bist EINE Strategie von vielen auf dieser Plattform (strategy_id 'ai_trader'). "
            "Die anderen Strategien laufen parallel und unabhängig weiter – du ersetzt sie nicht "
            "und konkurrierst nicht mit ihnen. Dein Auftrag:\n"
            "1. Den Markt so gut wie möglich verstehen (Struktur, Regime, Liquidität, News).\n"
            "2. Für die aktuelle Lage die passende Strategie WÄHLEN oder eine neue ENTWICKELN – "
            "du bist nicht an ein festes Regelwerk gebunden und darfst dynamisch bleiben.\n"
            "3. Von den anderen Strategien und ihren Parametern LERNEN: was funktioniert in "
            "welchem Marktzustand, welche SL/TP-Logik und Timeframes tragen sich, was scheitert.\n"
            "4. Neue Ideen zuerst im Strategie-Labor testen (Ghost/Paper), nie ungetestet live.\n"
            "5. News-getriebene und live nachjustierte Trades sind Teil deiner Stärke – sie sind "
            "aber NICHT backtestbar; bewerte sie separat von regelbasierten Tests."
        )

    async def _analysis_extra_blocks(self) -> str:
        """MasterPrompt, Rolle, Plattform-Wissen, Performance, Lektionen, Settings,
        Strategie-Labor, Validierung + Autonomie-Regeln."""
        parts = [master_prompt.prompt_block(),
                 self._role_context_block(),
                 f"=== PLATTFORM-WISSEN ===\n{PLATFORM_KNOWLEDGE}"]
        macro = await self._macro_block()
        if macro:
            parts.append(macro)
        try:
            if self.learning:
                parts.append("=== DEINE BISHERIGE PERFORMANCE (echte Ergebnisse) ===\n"
                             + await self.learning.performance_text())
                parts.append("=== DEINE GELERNTEN LEKTIONEN (aus echten Ergebnissen – befolgen!) ===\n"
                             + await self.learning.lessons_text())
        except Exception as e:
            logger.warning(f"AI learning blocks failed: {e}")
        try:
            parts.append("=== DEINE AKTUELLEN TRADE-EINSTELLUNGEN (KI Trader, pro Coin) ===\n"
                         + await self._ai_coin_settings_text())
        except Exception:
            pass
        try:
            parts.append(f"=== PERFORMANCE DER ANDEREN STRATEGIEN (letzte 14 Tage – lerne daraus) ===\n"
                         + await self._strategy_performance_text())
        except Exception:
            pass
        try:
            parts.append("=== PARAMETER DER ANDEREN STRATEGIEN (Vorbilder für eigene Ideen) ===\n"
                         + await self._other_strategy_params_text())
        except Exception:
            pass
        try:
            parts.append(await strategy_lab.context_text())
        except Exception as e:
            logger.warning(f"AI strategy lab block failed: {e}")
        try:
            parts.append(validation_gate.prompt_block())
        except Exception:
            pass
        try:
            deep = await self._deep_report_block()
            if deep:
                parts.append(deep)
        except Exception:
            pass
        # KI-Ökosystem: Forschungs-Analyst, ML-Labor, Markt-Beobachter, Gedächtnis
        try:
            from services.ai_research import research_analyst
            research = await research_analyst.context_text()
            if research:
                parts.append(research)
        except Exception as e:
            logger.warning(f"AI research block failed: {e}")
        try:
            from services.ai_ml_lab import ml_lab
            ml = await ml_lab.context_text()
            if ml:
                parts.append(ml)
        except Exception as e:
            logger.warning(f"AI ml block failed: {e}")
        try:
            from services.ai_market_observer import market_observer
            obs = await market_observer.context_text()
            if obs:
                parts.append(obs)
        except Exception as e:
            logger.warning(f"AI observer block failed: {e}")
        try:
            from services.ai_memory import memory
            mem = await memory.context_text(kinds=["idea", "ml_finding"], per_kind=3)
            if mem:
                parts.append("=== KI-GEDÄCHTNIS (jüngstes Team-Wissen) ===\n" + mem)
        except Exception as e:
            logger.warning(f"AI memory block failed: {e}")
        try:
            from services.ai_news_watcher import news_watcher
            nw = await news_watcher.context_text()
            if nw:
                parts.append(nw)
        except Exception:
            pass
        autonomy = self.config.get("autonomy", "suggest")
        if autonomy in ("suggest", "auto"):
            mode_txt = ("Deine Änderungen werden SOFORT automatisch übernommen – sei entsprechend konservativ."
                        if autonomy == "auto" else
                        "Deine Änderungen werden dem Trader als Vorschlag angezeigt und erst nach seiner Bestätigung übernommen.")
            parts.append(
                "=== EINSTELLUNGS-AUTONOMIE (AKTIV) ===\n"
                f"Du darfst deine eigenen Trade-Einstellungen anpassen. {mode_txt}\n"
                "Nutze das optionale JSON-Feld \"config_changes\" (max. 5 Einträge, NUR bei klarem, "
                "datenbasiertem Grund – nicht bei jeder Analyse):\n"
                '[{"symbol": "BTCUSDT", "changes": {"leverage": 8, "sl_fixed_percent": 1.2}, "reason": "kurze Begründung"}]\n'
                'Für Engine-Einstellungen (min_confidence, cooldown_min) nutze "symbol": "ENGINE".\n'
                "STRENG VERBOTEN: max_capital / investierter Betrag / mode (paper/live) – NIE ändern oder vorschlagen.\n"
                + tunable_spec_text())
        else:
            parts.append("=== EINSTELLUNGS-AUTONOMIE (AUS) ===\nGib KEINE config_changes zurück (leere Liste).")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_json(text: str) -> Dict:
        text = re.sub(r"```(json)?", "", text).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("Keine JSON-Antwort der KI")
        return json.loads(text[start:end + 1])

    def is_fresh(self, decision: Optional[Dict]) -> bool:
        if not decision or not decision.get("ts"):
            return False
        try:
            ts = datetime.fromisoformat(decision["ts"].replace("Z", "+00:00"))
            max_age = max(self.config.get("interval_min", 10) * 2.5, 20)
            return (datetime.now(timezone.utc) - ts) < timedelta(minutes=max_age)
        except Exception:
            return False

    async def generate_for_role(self, role: str, prompt: str, system: str,
                                temperature: float = 0.4,
                                json_mode: bool = True) -> tuple[str, str, str]:
        """Generierung über die Modell-Kette einer KI-Team-Rolle.

        Kette = Rollen-Modell (bzw. Haupt-Modell) + Provider-Fallbacks +
        Rollen-Fallback-KI; pro Provider werden primärer + Backup-Key probiert.
        Rückgabe: (text, provider, model)."""
        chain = role_manager.chain(role, self.config)
        text, provider, model = await ai_providers.generate_chain(
            chain, prompt, system, temperature=temperature, json_mode=json_mode)
        self._effective_model = model
        self._effective_provider = provider
        if model != self.config.get("model"):
            logger.info(f"AI [{role}]: nutzt {provider}/{model} (Haupt-Modell: {self.config.get('model')})")
        return text, provider, model

    async def _generate_json(self, prompt: str, system: str,
                             role: str = "analyst") -> tuple[str, str]:
        """JSON-Generierung für eine Rolle. Gibt (raw_text, effektives_model) zurück."""
        text, _provider, model = await self.generate_for_role(role, prompt, system)
        return text, model

    async def run_analysis(self, manual: bool = False) -> Dict:
        if self._analyzing:
            return {"status": "busy", "detail": "Analyse läuft bereits"}
        if not self.key:
            self.last_error = f"API-Key für Provider '{self.config.get('provider')}' fehlt (Render EnvVars setzen)"
            return {"status": "error", "detail": self.last_error}
        self._analyzing = True
        try:
            symbols = [s for s in self.symbols
                       if (not self.toggle_check or self.toggle_check("ai_trader", s))
                       and len(self.scanner.candle_buffer.get(s, [])) >= 60]
            if not symbols:
                return {"status": "error", "detail": "Keine Coins mit ausreichend Kursdaten"}
            snaps = {s: self._snapshot(s) for s in symbols}
            snaps = {s: v for s, v in snaps.items() if v}

            news_block = "(News deaktiviert)"
            if self.config.get("news_enabled"):
                news = await news_feed.get_headlines(18)
                news_block = "\n".join(f"- {n['title']} ({n['source']})" for n in news) or "(keine News verfügbar)"

            directives = await self._user_directives()
            open_trades = await self._open_trades_text()
            extra_blocks = await self._analysis_extra_blocks()
            berlin = self.scanner.berlin_now().strftime("%d.%m.%Y %H:%M")

            prompt = (
                f"Zeit (Berlin): {berlin}\n\n"
                f"{extra_blocks}\n\n"
                f"=== MARKTDATEN (Multi-Timeframe) ===\n" +
                "\n".join(v["text"] for v in snaps.values()) +
                f"\n\n=== AKTUELLE NEWS ===\n{news_block}\n\n"
                f"=== ANWEISUNGEN DES TRADERS (höchste Priorität) ===\n{directives}\n\n"
                f"=== OFFENE POSITIONEN ===\n{open_trades}\n\n"
                f"Analysiere jedes Symbol ({', '.join(snaps.keys())}) und gib deine Entscheidungen als JSON zurück."
            )

            raw, model_used = await self._generate_json(prompt, ANALYSIS_SYSTEM)
            data = self._parse_json(raw)

            now = _now_iso()
            emitted = []
            stored = []
            for d in data.get("decisions", []):
                sym = d.get("symbol")
                if sym not in snaps:
                    continue
                action = str(d.get("action", "HOLD")).upper()
                if action not in ("LONG", "SHORT", "HOLD"):
                    action = "HOLD"
                dec = {
                    "id": str(uuid.uuid4()),
                    "symbol": sym,
                    "action": action,
                    "confidence": max(0, min(100, int(d.get("confidence", 0) or 0))),
                    "sl_pct": float(d.get("sl_pct", 0.6) or 0.6),
                    "tp1_pct": float(d.get("tp1_pct", 0.9) or 0.9),
                    "tpf_pct": float(d.get("tpf_pct", 1.8) or 1.8),
                    "news_impact": d.get("news_impact", "neutral"),
                    "reasoning": str(d.get("reasoning", ""))[:500],
                    "strategy_candidate_id": str(d.get("strategy_candidate_id") or "") or None,
                    "price": snaps[sym]["price"],
                    "rsi": snaps[sym]["rsi"],
                    "ts": now,
                    "signaled": False,
                    "model": model_used,
                    "model_weight": ai_providers.model_weight(model_used),
                }
                self.decisions[sym] = dec
                stored.append(dec)
                if (action in ("LONG", "SHORT")
                        and dec["confidence"] >= self.config["min_confidence"]
                        and self.scanner.is_trading_session("ai_trader")):
                    ok = await self._emit_signal(dec)
                    if ok:
                        dec["signaled"] = True
                        emitted.append(f"{sym} {action}")
            if stored:
                await self.db.ai_decisions.insert_many([dict(x) for x in stored])

            # Neue Strategie-Ideen der KI -> Strategie-Labor (Ghost-Phase)
            new_candidates = []
            for spec in (data.get("new_strategies") or [])[:3]:
                if not isinstance(spec, dict):
                    continue
                try:
                    res = await strategy_lab.create_candidate(spec, source="ki")
                    if res.get("status") == "ok":
                        new_candidates.append(res["candidate"]["id"])
                except Exception as se:
                    logger.error(f"Strategie-Kandidat konnte nicht angelegt werden: {se}")

            # Self-Tuning: von der KI gewünschte Einstellungs-Änderungen verarbeiten
            cfg_results = []
            try:
                cfg_results = await self._handle_config_changes(
                    data.get("config_changes") or [], source="analysis")
            except Exception as ce:
                logger.error(f"AI config changes failed: {ce}")
            # Autonomie "auto": zurückgestellte Wünsche erneut prüfen und
            # anwenden, sobald die Datenlage sie bestätigt.
            try:
                await self.review_parked_proposals()
            except Exception as re_:
                logger.error(f"Autonomie-Review fehlgeschlagen: {re_}")

            feed_entry = {
                "id": str(uuid.uuid4()),
                "role": "analysis",
                "text": str(data.get("market_overview", ""))[:1200],
                "decisions": [{"symbol": x["symbol"], "action": x["action"],
                               "confidence": x["confidence"], "reasoning": x["reasoning"],
                               "signaled": x["signaled"]} for x in stored],
                "emitted": emitted,
                "config_changes": [{"symbol": p["symbol"], "changes": p["changes"],
                                    "status": p["status"]} for p in cfg_results],
                "new_candidates": new_candidates,
                "manual": manual,
                "model": model_used,
                "ts": now,
            }
            await self.db.ai_chat.insert_one(dict(feed_entry))
            self.last_run = now
            self.last_error = None
            logger.info(f"AI analysis done ({model_used}): {len(stored)} decisions, {len(emitted)} signals ({emitted})")
            return {"status": "ok", "decisions": len(stored), "signals": emitted,
                    "overview": feed_entry["text"], "model": model_used}
        except Exception as e:
            self.last_error = str(e)[:300]
            logger.error(f"AI analysis failed: {e}")
            return {"status": "error", "detail": self.last_error}
        finally:
            self._analyzing = False

    async def _today_risk(self) -> tuple:
        """Realisierter PnL und Trade-Anzahl der KI für den heutigen Handelstag."""
        try:
            day = self.scanner.berlin_date()
            rows = await self.db.auto_trades.find(
                {"strategy_id": "ai_trader", "trade_date": day},
                {"realized_pnl": 1, "status": 1}).to_list(300)
        except Exception as e:
            logger.warning(f"Tages-Risiko nicht ermittelbar: {e}")
            return None, None
        pnl = sum(float(r.get("realized_pnl") or 0) for r in rows)
        self._day_risk_cache = {"date": day, "realized_pnl": round(pnl, 4),
                                "trades": len(rows),
                                "limit_usdt": master_prompt.rules.get("max_daily_loss_usdt"),
                                "max_trades": master_prompt.rules.get("max_trades_per_day")}
        return round(pnl, 4), len(rows)

    async def _emit_signal(self, dec: Dict) -> bool:
        sym = dec["symbol"]
        # ---- MasterPrompt: oberstes Gebot, technisch erzwungen ----
        try:
            open_ai_trades = await self.db.auto_trades.count_documents(
                {"status": "open", "strategy_id": "ai_trader"})
        except Exception:
            open_ai_trades = None
        allowed, why = master_prompt.check_trade(
            sym, dec["action"], confidence=dec.get("confidence"), open_trades=open_ai_trades)
        if allowed:
            day_pnl, day_trades = await self._today_risk()
            allowed, why = master_prompt.check_day(day_pnl, day_trades)
        if not allowed:
            logger.info(f"AI-Signal blockiert ({sym} {dec['action']}): {why}")
            dec["blocked_by"] = why
            try:
                await self.db.ai_chat.insert_one({
                    "id": str(uuid.uuid4()), "role": "governance",
                    "text": f"Trade {dec['action']} {sym} blockiert – {why}",
                    "ts": _now_iso()})
            except Exception:
                pass
            return False
        cooldown = self.config.get("cooldown_min", 45) * 60
        if cooldown:
            max_per_coin = max(1, min(5, int(self.config.get("max_trades_per_coin", 1) or 1)))
            if max_per_coin > 1:
                # KI-Trader mit mehreren Slots: Cooldown gilt PRO TRADE statt pro
                # Coin. Solange auf dem Coin noch freie Slots (max_trades_per_coin)
                # offen sind, wird der Coin-Cooldown übersprungen, damit die Slots
                # zeitnah gefüllt werden. Erst wenn die Slots voll sind, bremst der
                # Cooldown (die Slot-Obergrenze setzt on_signal ohnehin durch).
                try:
                    open_count = await self.db.auto_trades.count_documents(
                        {"symbol": sym, "status": "open", "strategy_id": "ai_trader"})
                except Exception:
                    open_count = 0
                if open_count >= max_per_coin and \
                        (time.time() - self._last_signal_ts.get(sym, 0)) < cooldown:
                    return False
            elif (time.time() - self._last_signal_ts.get(sym, 0)) < cooldown:
                return False
        entry = float(dec["price"])
        if entry <= 0:
            return False
        # Makro-Parameter des Strategie-Kandidaten haben Vorrang vor den
        # spontanen Prozentwerten der Analyse (individuelle Feinjustierung
        # je eigener Strategie, siehe services/ai_strategy_lab.py).
        macro = strategy_lab.macro_params(dec.get("strategy_candidate_id"))
        sl_input = macro.get("sl_fixed_percent", dec["sl_pct"])
        sl_pct = max(0.15, min(5.0, float(sl_input))) / 100
        if macro.get("tp1_crv"):
            tp1_pct = min(0.08, sl_pct * float(macro["tp1_crv"]))
        else:
            tp1_pct = max(sl_pct * 1.2, min(0.08, dec["tp1_pct"] / 100))
        if macro.get("tpf_crv"):
            tpf_pct = min(0.15, max(tp1_pct, sl_pct * float(macro["tpf_crv"])))
        else:
            tpf_pct = max(tp1_pct, min(0.15, dec["tpf_pct"] / 100))
        sign = 1 if dec["action"] == "LONG" else -1
        sl = entry * (1 - sign * sl_pct)
        tp1 = entry * (1 + sign * tp1_pct)
        tpf = entry * (1 + sign * tpf_pct)
        crv = round(abs(tp1 - entry) / abs(entry - sl), 2) if entry != sl else 0
        # ---- Strategie-Labor: Kandidaten erst nach Ghost-Phase + Freigabe live ----
        cand_id = dec.get("strategy_candidate_id")
        stage = strategy_lab.execution_stage(cand_id) if cand_id else None
        if cand_id and stage in ("ghost", "live_pending", "unknown", "rejected", None):
            if stage in ("unknown", "rejected", None):
                logger.info(f"AI-Signal {sym}: Kandidat {cand_id} unbekannt/abgelehnt "
                            "-> Kandidaten-Bezug verworfen")
                cand_id, stage = None, None
            else:
                if (time.time() - self._last_ghost_ts.get(sym, 0)) < cooldown:
                    return False
                try:
                    await strategy_lab.record_ghost_trade(
                        cand_id, sym, dec["action"], entry, sl, tp1,
                        reason=dec.get("reasoning", ""))
                    # Eigener Cooldown für Ghost-Trades: ein simulierter Test darf
                    # echte Signale auf dem Coin nicht blockieren.
                    self._last_ghost_ts[sym] = time.time()
                except Exception as ge:
                    logger.error(f"Ghost-Trade fehlgeschlagen: {ge}")
                return False
        now = self.scanner.berlin_now()
        rules_met = {"ai_active": True, "ai_direction": True, "ai_confidence": True, "ai_news": True}
        signal = {
            "symbol": sym,
            "type": dec["action"],
            "signal_class": "SIGNAL",
            "entry_price": round(entry, 6),
            "stop_loss": round(sl, 6),
            "take_profit_1": round(tp1, 6),
            "take_profit_full": round(tpf, 6),
            "crv": crv,
            "rsi": dec.get("rsi", 0),
            "ema_fast": 0,
            "ema_slow": 0,
            "rules_met": rules_met,
            "rules_met_count": 4,
            "rules_total": 4,
            "timestamp": _now_iso(),
            "trade_date": self.scanner.berlin_date(),
            "hour": now.hour,
            "weekday": now.weekday(),
            "session": self.scanner.get_current_session(),
            "strategy_id": "ai_trader",
            "strategy_name": "KI Trader",
            "status": "active",
            "ai_confidence": dec["confidence"],
            "ai_reasoning": dec["reasoning"],
            "decision_id": dec.get("id"),
            "use_ai_levels": bool(self.config.get("use_ai_levels")),
            "ai_candidate_id": cand_id,
            "cfg_overrides": strategy_lab.trade_overrides(cand_id) if cand_id else None,
            "force_paper": bool(cand_id and stage == "paper"),
            "force_paper_reason": ("Strategie-Kandidat noch nicht für Live freigegeben"
                                  if cand_id and stage == "paper" else None),
        }
        try:
            ok = await self.signal_cb(signal)
            if ok:
                self._last_signal_ts[sym] = time.time()
                dec["signal_id"] = signal.get("id")
            return bool(ok)
        except Exception as e:
            logger.error(f"AI signal emit failed for {sym}: {e}")
            return False

    # ---------------- self-tuning (KI ändert eigene Trade-Einstellungen) ----------------
    async def _current_cfg_values(self, scope: str, symbol: Optional[str], keys) -> Dict:
        if scope == "engine":
            return {k: self.config.get(k) for k in keys}
        if scope == "candidate":
            # Makro-Parameter einer eigenen KI-Strategie (Kandidat)
            cand = await strategy_lab.get(symbol) or {}
            macro = cand.get("macro_params") or {}
            from core.defaults import DEFAULT_STRATEGY_COIN_CFG
            return {k: macro.get(k, DEFAULT_STRATEGY_COIN_CFG.get(k)) for k in keys}
        from core.defaults import DEFAULT_STRATEGY_COIN_CFG
        doc = await self.db.strategy_coin_configs.find_one({"_id": f"ai_trader_{symbol}"})
        saved = doc.get("config", {}) if doc else {}
        merged = {**DEFAULT_STRATEGY_COIN_CFG, **saved}
        return {k: merged.get(k) for k in keys}

    async def _apply_changes(self, scope: str, symbol: Optional[str], changes: Dict):
        if scope == "engine":
            await self.update_config(dict(changes))
            return
        if scope == "candidate":
            await strategy_lab.update_macro_params(symbol, dict(changes))
            return
        key = f"ai_trader_{symbol}"
        doc = await self.db.strategy_coin_configs.find_one({"_id": key})
        saved = doc.get("config", {}) if doc else {}
        saved.update(changes)
        await self.db.strategy_coin_configs.replace_one(
            {"_id": key}, {"_id": key, "config": saved}, upsert=True)
        try:
            from core.state import autotrader  # lazy: kein Zyklus beim Import
            autotrader.config.setdefault("strategy_coin_configs", {})[key] = saved
        except Exception:
            pass

    async def _macro_gate(self, prop: Dict, macro_keys: List[str], current: Dict,
                          stats: Dict, scope: str, symbol: Optional[str]):
        """Struktur-Parameter (SL, CRV, Hebel ...) brauchen mehrere Bestätigungen
        und dürfen nur in kleinen Schritten wandern.

        Die Bestätigungen werden aus früheren Vorschlägen derselben Richtung
        gezählt – ein einzelner (Verlust-)Trade verschiebt damit nichts."""
        window_days = int(validation_gate.settings.get("macro_confirm_window_days", 14))
        since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        sample = await self._macro_sample(stats, scope, symbol)
        worst = None
        clamped_any = False
        changes = dict(prop["changes"])
        for key in macro_keys:
            cur, proposed = current.get(key), changes[key]
            direction = "up" if (cur is None or float(proposed) > float(cur)) else "down"
            try:
                confirmations = 1 + await self.db.ai_proposals.count_documents({
                    "scope": scope,
                    "symbol": prop["symbol"],
                    f"changes.{key}": {"$exists": True},
                    "macro_direction": direction,
                    "ts": {"$gte": since},
                })
            except Exception:
                confirmations = 1
            gate = validation_gate.macro(sample, confirmations)
            gate["key"] = key
            gate["direction"] = direction
            if worst is None or (not gate["validated"] and worst["validated"]):
                worst = gate
            value, was_clamped = validation_gate.clamp(key, cur, proposed)
            changes[key] = value
            clamped_any = clamped_any or was_clamped
        prop["changes"] = changes
        prop["macro_direction"] = worst.get("direction") if worst else None
        return (worst or {"validated": True, "reason": "keine Makro-Parameter"}), clamped_any

    async def _macro_sample(self, stats: Dict, scope: str, symbol: Optional[str]) -> int:
        """Stichprobe für Makro-Änderungen – pro Kandidat aus dessen eigenen Trades."""
        if scope == "candidate" and symbol:
            try:
                return await self.db.auto_trades.count_documents(
                    {"ai_candidate_id": symbol, "status": "closed"})
            except Exception:
                return 0
        return ai_validation.sample_size(stats, scope, symbol)

    async def _handle_config_changes(self, raw_list: List, source: str = "analysis") -> List[Dict]:
        """Validiert KI-Änderungswünsche gegen die Whitelist und wendet sie an
        (autonomy=auto) bzw. legt sie als bestätigungspflichtige Vorschläge ab
        (autonomy=suggest). max_capital & mode sind hart gesperrt."""
        autonomy = self.config.get("autonomy", "suggest")
        if autonomy not in ("suggest", "auto") or not raw_list:
            return []
        upper_syms = {s.upper(): s for s in self.symbols}
        # Datenbasis für die Validierung (nur für KI-initiierte Änderungen nötig)
        stats: Dict = {}
        if source != "user" and self.learning:
            try:
                stats = await self.learning.gather_stats()
            except Exception as e:
                logger.warning(f"Validierungs-Statistik nicht verfügbar: {e}")
        results = []
        for item in raw_list[:6]:
            if not isinstance(item, dict):
                continue
            symbol_raw = str(item.get("symbol", "")).upper().strip()
            cand_ref = str(item.get("strategy_candidate_id") or "").strip() or (
                symbol_raw.lower() if symbol_raw.lower().startswith("cand_") else "")
            if cand_ref:
                scope, symbol = "candidate", cand_ref
                if not await strategy_lab.get(cand_ref):
                    logger.info(f"AI config change: Kandidat {cand_ref} unbekannt – übersprungen")
                    continue
            else:
                scope = "engine" if symbol_raw in ("ENGINE", "GLOBAL", "") else "coin"
                symbol = upper_syms.get(symbol_raw)
                if scope == "coin" and not symbol:
                    continue
            # Kandidaten nutzen dieselbe Whitelist wie Coin-Configs
            valid, rejected = validate_changes(item.get("changes") or {},
                                               scope="coin" if scope == "candidate" else scope)
            if rejected:
                logger.info(f"AI config change abgelehnt ({symbol_raw}): {rejected}")
            if not valid:
                continue
            current = await self._current_cfg_values(scope, symbol, valid.keys())
            valid = {k: v for k, v in valid.items() if current.get(k) != v}
            if not valid:
                continue
            prop = {
                "id": str(uuid.uuid4()),
                "ts": _now_iso(),
                "scope": scope,
                "symbol": symbol if scope in ("coin", "candidate") else "ENGINE",
                "changes": valid,
                "current": {k: current.get(k) for k in valid},
                "reason": str(item.get("reason", ""))[:300],
                "source": source,
                "status": "pending",
            }
            # 1. MasterPrompt (oberstes Gebot) – harte Sperre
            master_ok, master_why = master_prompt.check_changes(valid)
            if not master_ok and source != "user":
                prop["status"] = "blocked_master"
                prop["block_reason"] = master_why
                await self.db.ai_proposals.insert_one(dict(prop))
                results.append(prop)
                logger.info(f"AI config change durch MasterPrompt blockiert: {master_why}")
                continue
            # 2. Datenbasis-Validierung – ohne ausreichende Stichprobe nur parken
            if source != "user":
                macro_keys = [k for k in valid if ai_validation.is_macro_key(k)]
                normal_keys = [k for k in valid if k not in macro_keys]
                gate = validation_gate.change(stats, scope, symbol) if normal_keys else \
                    {"validated": True, "reason": "nur Struktur-Parameter", "sample": 0}
                prop["validation"] = gate
                if normal_keys and not gate.get("validated"):
                    prop["status"] = "needs_data"
                    await self.db.ai_proposals.insert_one(dict(prop))
                    results.append(prop)
                    logger.info(f"AI config change geparkt (needs_data): {gate.get('reason')}")
                    continue
                if macro_keys:
                    macro_gate, clamped = await self._macro_gate(
                        prop, macro_keys, current, stats, scope, symbol)
                    prop["macro_validation"] = macro_gate
                    prop["clamped"] = clamped
                    if not macro_gate.get("validated"):
                        prop["status"] = "needs_confirmation"
                        await self.db.ai_proposals.insert_one(dict(prop))
                        results.append(prop)
                        logger.info(f"AI Makro-Änderung geparkt: {macro_gate.get('reason')}")
                        continue
                    valid = prop["changes"]
            if autonomy == "auto" or source == "user":
                try:
                    await self._apply_changes(scope, symbol, valid)
                    prop["status"] = "auto_applied"
                    prop["decided_at"] = _now_iso()
                except Exception as e:
                    prop["status"] = "error"
                    prop["error"] = str(e)[:200]
            await self.db.ai_proposals.insert_one(dict(prop))
            results.append(prop)
        if results:
            applied = [p for p in results if p["status"] == "auto_applied"]
            pending = [p for p in results if p["status"] == "pending"]
            parked = [p for p in results if p["status"] == "needs_data"]
            # Autonomie "auto": geparkte Wünsche (needs_data/needs_confirmation)
            # still sammeln statt den Trader mit Hinweisen zu fluten – die KI
            # schlägt sie automatisch erneut vor, sobald die Daten reichen.
            if autonomy == "auto" and source != "user" and not applied and not pending \
                    and not any(p["status"] in ("blocked_master", "error") for p in results):
                return results
            unconfirmed = [p for p in results if p["status"] == "needs_confirmation"]
            blocked = [p for p in results if p["status"] == "blocked_master"]
            txt = []
            if applied:
                txt.append("Ich habe meine Trade-Einstellungen angepasst (Autonomie: automatisch).")
            if pending:
                txt.append("Ich schlage Änderungen an meinen Trade-Einstellungen vor – bitte bestätigen oder ablehnen.")
            if parked:
                txt.append(f"{len(parked)} Änderung(en) warten auf mehr Daten "
                           f"({parked[0].get('validation', {}).get('reason', '')}).")
            if unconfirmed:
                txt.append(f"{len(unconfirmed)} Struktur-Änderung(en) (SL/CRV/Hebel) warten auf "
                           f"weitere Bestätigungen: "
                           f"{unconfirmed[0].get('macro_validation', {}).get('reason', '')}")
            if blocked:
                txt.append(f"{len(blocked)} Änderung(en) verstoßen gegen den MasterPrompt "
                           f"und wurden verworfen ({blocked[0].get('block_reason', '')}).")
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "config",
                "text": " ".join(txt),
                "items": [{"proposal_id": p["id"], "symbol": p["symbol"],
                           "changes": p["changes"], "current": p["current"],
                           "reason": p["reason"], "status": p["status"],
                           "validation": p.get("validation"),
                           "macro_validation": p.get("macro_validation"),
                           "clamped": p.get("clamped"),
                           "block_reason": p.get("block_reason")} for p in results],
                "source": source, "ts": _now_iso(),
            })
        return results

    # ---------------- geparkte Vorschläge (Autonomie "auto") ----------------
    async def _close_proposal(self, pid: str, status: str, extra: Optional[Dict] = None):
        """Status eines Vorschlags final setzen und im KI-Feed spiegeln."""
        patch = {"status": status, "decided_at": _now_iso(), **(extra or {})}
        await self.db.ai_proposals.update_one({"id": pid}, {"$set": patch})
        try:
            await self.db.ai_chat.update_many(
                {"role": "config", "items.proposal_id": pid},
                {"$set": {"items.$.status": status}})
        except Exception:
            pass

    async def review_parked_proposals(self, limit: int = 30) -> Dict:
        """Autonomie "auto": geparkte Änderungswünsche (needs_data /
        needs_confirmation) erneut gegen die AKTUELLE Datenlage prüfen und
        automatisch anwenden, sobald die Validierung sie freigibt.

        Damit muss der Trader im autonomen Modus nichts mehr bestätigen – die
        bestehende Validierung (Stichprobe, Bestätigungen, Schrittweite,
        MasterPrompt) bleibt aber vollständig wirksam. Im Modus "suggest"
        passiert hier nichts: dort entscheidet der Trader per Karte."""
        if self.db is None or self.config.get("autonomy") != "auto":
            return {"reviewed": 0, "applied": 0}
        try:
            rows = await self.db.ai_proposals.find({
                "status": {"$in": ["needs_data", "needs_confirmation"]},
                "source": {"$ne": "user"},
            }).sort("ts", -1).limit(limit).to_list(limit)
        except Exception as e:
            logger.warning(f"Geparkte Vorschläge konnten nicht geladen werden: {e}")
            return {"reviewed": 0, "applied": 0}
        if not rows:
            return {"reviewed": 0, "applied": 0}
        stats: Dict = {}
        if self.learning:
            try:
                stats = await self.learning.gather_stats()
            except Exception as e:
                logger.warning(f"Validierungs-Statistik nicht verfügbar: {e}")
        applied: List[Dict] = []
        for prop in rows:
            prop.pop("_id", None)
            pid = prop.get("id")
            scope = prop.get("scope", "coin")
            sym_field = prop.get("symbol")
            symbol = None if scope == "engine" else sym_field
            changes = dict(prop.get("changes") or {})
            if not pid or not changes:
                continue
            ok, why = master_prompt.check_changes(changes)
            if not ok:
                await self._close_proposal(pid, "blocked_master", {"block_reason": why})
                continue
            macro_keys = [k for k in changes if ai_validation.is_macro_key(k)]
            normal_keys = [k for k in changes if k not in macro_keys]
            gate = validation_gate.change(stats, scope, sym_field) if normal_keys else \
                {"validated": True, "reason": "nur Struktur-Parameter", "sample": 0}
            if normal_keys and not gate.get("validated"):
                await self.db.ai_proposals.update_one(
                    {"id": pid}, {"$set": {"validation": gate, "reviewed_at": _now_iso()}})
                continue
            current = await self._current_cfg_values(scope, symbol, changes.keys())
            if macro_keys:
                probe = {"changes": changes, "symbol": sym_field}
                macro_gate, clamped = await self._macro_gate(
                    probe, macro_keys, current, stats, scope, sym_field)
                if not macro_gate.get("validated"):
                    await self.db.ai_proposals.update_one({"id": pid}, {"$set": {
                        "macro_validation": macro_gate, "clamped": clamped,
                        "reviewed_at": _now_iso()}})
                    continue
                changes = probe["changes"]
            changes = {k: v for k, v in changes.items() if current.get(k) != v}
            if not changes:
                await self._close_proposal(pid, "obsolete")
                continue
            try:
                await self._apply_changes(scope, symbol, changes)
            except Exception as e:
                await self._close_proposal(pid, "error", {"error": str(e)[:200]})
                continue
            await self._close_proposal(pid, "auto_applied", {
                "changes": changes, "validation": gate,
                "current": {k: current.get(k) for k in changes}})
            applied.append({"proposal_id": pid, "symbol": prop.get("symbol"),
                            "changes": changes,
                            "current": {k: current.get(k) for k in changes},
                            "reason": prop.get("reason", ""), "status": "auto_applied"})
        if applied:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "config",
                "text": f"{len(applied)} zurückgestellte Änderung(en) sind jetzt durch die "
                        "Datenlage bestätigt und wurden automatisch übernommen "
                        "(Autonomie: automatisch).",
                "items": applied, "source": "auto_review", "ts": _now_iso(),
            })
            logger.info(f"Autonomie-Review: {len(applied)} geparkte Änderung(en) angewendet")
        return {"reviewed": len(rows), "applied": len(applied)}

    async def actionable_proposals(self, limit: int = 30) -> List[Dict]:
        """Vorschläge, die WIRKLICH eine Entscheidung des Traders brauchen.

        Im autonomen Modus ist das immer eine leere Liste – dort verwaltet die
        KI ihre Wünsche selbst (siehe `review_parked_proposals`). Damit kann das
        Frontend die Karten ohne Race-Condition ausblenden."""
        if self.config.get("autonomy") == "auto":
            return []
        rows = await self.db.ai_proposals.find({
            "status": {"$in": ["pending", "needs_confirmation", "needs_data"]},
        }).sort("ts", -1).limit(max(1, min(100, limit))).to_list(100)
        for r in rows:
            r.pop("_id", None)
        return rows

    async def comment_on_user_change(self, topic: str, detail: str) -> Dict:
        """Der Trader hat etwas geändert – die KI sagt ehrlich ihre Meinung dazu.
        Blockiert nichts, landet als Eintrag (role='opinion') im KI-Feed."""
        if self.db is None:
            return {"status": "skipped", "detail": "keine DB"}
        if not self.key:
            return {"status": "skipped", "detail": "kein API-Key"}
        try:
            stats_txt = await self.learning.performance_text() if self.learning else ""
            lessons_txt = await self.learning.lessons_text() if self.learning else ""
            prompt = (
                f"{master_prompt.prompt_block()}\n\n"
                f"=== ÄNDERUNG DES TRADERS ({topic}) ===\n{detail}\n\n"
                f"=== DEINE PERFORMANCE ===\n{stats_txt}\n\n"
                f"=== DEINE LEKTIONEN ===\n{lessons_txt}\n\n"
                "Bewerte diese Änderung ehrlich. Sie gilt ohnehin – aber wenn deine Daten "
                "dagegen sprechen, sage klar warum."
            )
            text, provider, model = await self.generate_for_role(
                "chat", prompt, OPINION_SYSTEM, temperature=0.3)
            data = self._parse_json(text)
            entry = {
                "id": str(uuid.uuid4()), "role": "opinion",
                "topic": topic,
                "stance": str(data.get("stance", "neutral"))[:20],
                "text": str(data.get("comment", ""))[:900],
                "risk": str(data.get("risk", ""))[:300],
                "model": model, "ts": _now_iso(),
            }
            await self.db.ai_chat.insert_one(dict(entry))
            logger.info(f"KI-Meinung zu '{topic}': {entry['stance']} ({provider}/{model})")
            return {"status": "ok", **entry}
        except Exception as e:
            logger.warning(f"KI-Meinung zu '{topic}' fehlgeschlagen: {e}")
            return {"status": "error", "detail": str(e)[:200]}

    async def list_proposals(self, status: Optional[str] = None, limit: int = 40) -> List[Dict]:
        q = {"status": status} if status else {}
        rows = await self.db.ai_proposals.find(q).sort("ts", -1).limit(limit).to_list(limit)
        for r in rows:
            r.pop("_id", None)
        return rows

    async def decide_proposal(self, pid: str, approve: bool) -> Optional[Dict]:
        prop = await self.db.ai_proposals.find_one({"id": pid})
        # Der Trader darf auch geparkte Vorschläge (fehlende Daten/Bestätigungen)
        # freigeben – seine Entscheidung braucht keine Validierung.
        if not prop or prop.get("status") not in ("pending", "needs_data",
                                                 "needs_confirmation"):
            return None
        if approve:
            symbol = None if prop.get("scope") == "engine" else prop.get("symbol")
            await self._apply_changes(prop.get("scope", "coin"), symbol, prop.get("changes") or {})
        new_status = "applied" if approve else "rejected"
        await self.db.ai_proposals.update_one(
            {"id": pid}, {"$set": {"status": new_status, "decided_at": _now_iso()}})
        try:
            await self.db.ai_chat.update_many(
                {"role": "config", "items.proposal_id": pid},
                {"$set": {"items.$.status": new_status}})
        except Exception:
            pass
        prop.pop("_id", None)
        prop["status"] = new_status
        return prop

    # ---------------- housekeeping (hourly cleanup + daily reset + summary) ----------------
    async def _persist_housekeeping(self):
        try:
            await self.db.settings.update_one(
                {"_id": "ai_trader_housekeeping"},
                {"$set": {
                    "last_cleanup_hour": self._last_cleanup_hour,
                    "last_reset_date": self._last_reset_date,
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"AI housekeeping persist failed: {e}")

    async def _cleanup_old_analyses(self) -> int:
        """Löscht alle Nachrichten mit role='analysis' bis auf die neueste.
        User-, Assistant- und Summary-Nachrichten bleiben unangetastet."""
        try:
            latest = await self.db.ai_chat.find_one(
                {"role": "analysis"}, sort=[("ts", -1)],
            )
            if not latest:
                return 0
            query = {"role": "analysis"}
            if latest.get("id"):
                query["id"] = {"$ne": latest["id"]}
            else:
                query["_id"] = {"$ne": latest["_id"]}
            result = await self.db.ai_chat.delete_many(query)
            return result.deleted_count or 0
        except Exception as e:
            logger.error(f"AI hourly cleanup failed: {e}")
            return 0

    async def _collect_daily_facts(self, day_iso: str) -> Dict:
        """Sammelt die Fakten des abgelaufenen Tages aus ai_chat (vor dem Löschen)
        + ai_decisions. `day_iso` = YYYY-MM-DD (Berlin) des Tages, der zusammengefasst wird."""
        # Alles was aktuell im Chat liegt = Tages-Nachrichten (Hourly-Cleanup hat alte
        # analysis-Einträge bereits weg-geräumt, außerdem darf hier eine ältere Summary
        # liegen – die kommt in den Archivierungs-Snapshot).
        chat_docs = await self.db.ai_chat.find().sort("ts", 1).to_list(length=None)

        # ai_decisions: filtere nach Berlin-Datum. ts ist ISO in UTC.
        all_dec = await self.db.ai_decisions.find({"ts": {"$exists": True}}).sort("ts", 1).to_list(length=None)
        day_dec = []
        for d in all_dec:
            try:
                dt = datetime.fromisoformat(str(d.get("ts", "")).replace("Z", "+00:00"))
                if dt.astimezone(BERLIN_TZ).strftime("%Y-%m-%d") == day_iso:
                    day_dec.append(d)
            except Exception:
                continue

        analyses = [c for c in chat_docs if c.get("role") == "analysis"]
        directives = [c for c in chat_docs if c.get("role") == "user"]
        assistants = [c for c in chat_docs if c.get("role") == "assistant"]
        summaries_prev = [c for c in chat_docs if c.get("role") == "summary"]

        signals = [d for d in day_dec if d.get("signaled")]
        actions = {"LONG": 0, "SHORT": 0, "HOLD": 0}
        for d in day_dec:
            a = str(d.get("action", "HOLD")).upper()
            if a in actions:
                actions[a] += 1

        overviews = [str(a.get("text") or "").strip() for a in analyses if a.get("text")]

        return {
            "day": day_iso,
            "chat_docs": chat_docs,
            "day_decisions": day_dec,
            "counts": {
                "analyses": len(analyses),
                "decisions": len(day_dec),
                "signals": len(signals),
                "long": actions["LONG"],
                "short": actions["SHORT"],
                "hold": actions["HOLD"],
                "directives": len(directives),
                "assistant_msgs": len(assistants),
                "prev_summaries": len(summaries_prev),
            },
            "signals": [f"{s.get('symbol')} {s.get('action')} ({s.get('confidence')}%)" for s in signals],
            "directives": [str(d.get("text") or "").strip() for d in directives if d.get("text")],
            "overviews": overviews,
        }

    def _statistical_summary(self, facts: Dict) -> str:
        """Fallback-Zusammenfassung, wenn die LLM nicht erreichbar ist."""
        c = facts["counts"]
        cfg = self.config
        parts = [
            f"Tages-Zusammenfassung ({facts['day']}) – statistischer Fallback (LLM nicht erreichbar).",
            f"• Analysen: {c['analyses']} · Entscheidungen: {c['decisions']} "
            f"(LONG {c['long']} / SHORT {c['short']} / HOLD {c['hold']}) · "
            f"Ausgelöste Signale: {c['signals']}",
        ]
        if facts["signals"]:
            parts.append("• Signale: " + ", ".join(facts["signals"][:12]))
        if facts["overviews"]:
            latest_ov = facts["overviews"][-1][:220]
            parts.append(f"• Letzter Marktüberblick: {latest_ov}")
        if facts["directives"]:
            dirs = " | ".join(d[:120] for d in facts["directives"][-6:])
            parts.append(f"• Trader-Direktiven (aktuell aktiv): {dirs}")
        else:
            parts.append("• Trader-Direktiven: (keine vom Nutzer im Chat gesetzt)")
        parts.append(
            f"• Aktive Konfiguration: Provider {cfg.get('provider')} / Modell {cfg.get('model')} · "
            f"Intervall {cfg.get('interval_min')} min · Min. Konfidenz {cfg.get('min_confidence')}% · "
            f"Cooldown {cfg.get('cooldown_min')} min · News {'an' if cfg.get('news_enabled') else 'aus'}"
        )
        return "\n".join(parts)

    async def _llm_daily_summary(self, facts: Dict) -> Optional[str]:
        """Generiert die Zusammenfassung via aktivem LLM-Provider. Gibt None bei Fehler."""
        if not self.key:
            return None
        cfg = self.config
        c = facts["counts"]
        directives_block = "\n".join(f"- {d}" for d in facts["directives"][-15:]) or "(keine)"
        signals_block = "\n".join(f"- {s}" for s in facts["signals"][:20]) or "(keine)"
        overviews_block = "\n".join(f"- {o[:220]}" for o in facts["overviews"][-6:]) or "(keine)"
        prompt = (
            f"Zusammenfassung für Tag: {facts['day']} (Europe/Berlin)\n\n"
            f"KENNZAHLEN:\n"
            f"- Analysen: {c['analyses']}\n"
            f"- Entscheidungen: {c['decisions']} (LONG {c['long']} / SHORT {c['short']} / HOLD {c['hold']})\n"
            f"- Ausgelöste Signale: {c['signals']}\n\n"
            f"SIGNALE:\n{signals_block}\n\n"
            f"MARKTÜBERBLICKE (chronologisch, ältester zuerst):\n{overviews_block}\n\n"
            f"TRADER-DIREKTIVEN (vom Nutzer im Chat gesetzt, definieren wonach gerade getradet wird):\n{directives_block}\n\n"
            f"AKTIVE KONFIGURATION:\n"
            f"- Provider/Modell: {cfg.get('provider')} / {cfg.get('model')}\n"
            f"- Analyse-Intervall: {cfg.get('interval_min')} min\n"
            f"- Min. Konfidenz: {cfg.get('min_confidence')}%\n"
            f"- Trade-Cooldown: {cfg.get('cooldown_min')} min\n"
            f"- News-Feed: {'an' if cfg.get('news_enabled') else 'aus'}\n\n"
            f"Erstelle nun die kompakte deutsche Tages-Zusammenfassung wie im System-Prompt beschrieben."
        )
        provider = cfg.get("provider", "gemini")
        try:
            text, _p, _m = await self.generate_for_role(
                "summarizer", prompt, SUMMARY_SYSTEM, temperature=0.4, json_mode=False)
            return text or None
        except Exception as e:
            logger.warning(f"Daily summary {provider} failed: {e}")
            return None

    async def _daily_reset(self, prev_day_iso: str) -> Dict:
        """Archiviert Tages-Chat + Entscheidungen, generiert eine markierte
        Tages-Zusammenfassung und pinnt sie oben im Chat.

        Reihenfolge (WICHTIG: kein Datenverlust bei LLM- oder DB-Fehlern):
        1) Fakten sammeln
        2) Summary-Text generieren (LLM + Fallback)
        3) Archivieren
        4) Cutoff-Delete (nur Vortags-Nachrichten `ts < Mitternacht Berlin`) –
           nach Mitternacht neu eingetroffene Nachrichten bleiben erhalten
        5) Summary einfügen (ts = Mitternacht Berlin des neuen Tages, damit
           sie chronologisch VOR allen Neuer-Tag-Nachrichten liegt)
        6) Ältere gepinnte Summaries entpinnen (nur die neueste ist pinned)
        """
        # 1) Fakten sammeln – zwingend VOR jeglicher Löschaktion.
        facts = await self._collect_daily_facts(prev_day_iso)

        # 2) Zusammenfassung generieren (LLM + Fallback). Der Fallback liefert
        #    IMMER einen Text, damit wir nie mit leerer Summary weiterlaufen.
        text = await self._llm_daily_summary(facts)
        used_fallback = False
        if not text:
            text = self._statistical_summary(facts)
            used_fallback = True

        # Cutoff = Mitternacht Berlin des NEUEN Tages (= Ende von prev_day_iso).
        # Alle Nachrichten mit ts < cutoff gehören zum Vortag und werden gelöscht.
        try:
            cutoff_dt_berlin = datetime.strptime(prev_day_iso, "%Y-%m-%d") \
                .replace(tzinfo=BERLIN_TZ) + timedelta(days=1)
        except Exception:
            cutoff_dt_berlin = datetime.now(BERLIN_TZ)
        cutoff_utc_iso = cutoff_dt_berlin.astimezone(timezone.utc).isoformat()

        # 3) Archivieren – KI vergisst nichts.
        archive_batch = str(uuid.uuid4())
        archive_ts = _now_iso()
        archive_errors = False
        try:
            if facts["chat_docs"]:
                docs = []
                for c in facts["chat_docs"]:
                    d = dict(c)
                    d.pop("_id", None)
                    d["archive_batch"] = archive_batch
                    d["archive_day"] = prev_day_iso
                    d["archived_at"] = archive_ts
                    d["source"] = "ai_chat"
                    docs.append(d)
                await self.db.ai_chat_archive.insert_many(docs)
            if facts["day_decisions"]:
                docs = []
                for c in facts["day_decisions"]:
                    d = dict(c)
                    d.pop("_id", None)
                    d["archive_batch"] = archive_batch
                    d["archive_day"] = prev_day_iso
                    d["archived_at"] = archive_ts
                    d["source"] = "ai_decisions"
                    docs.append(d)
                await self.db.ai_chat_archive.insert_many(docs)
        except Exception as e:
            archive_errors = True
            logger.error(f"AI daily archive failed: {e}")
            # Best-Effort: Archiv-Fehler blockieren den Chat-Reset nicht,
            # sonst würde die Engine ewig mit vollem Chat weiterlaufen.

        # 4) Cutoff-Delete: nur echte Vortags-Nachrichten löschen. Verhindert,
        #    dass Nachrichten aus dem neuen Tag (Race Condition zwischen 00:00
        #    und dem Ende der Summary-Generierung) versehentlich mit-gelöscht
        #    werden.
        delete_ok = False
        try:
            await self.db.ai_chat.delete_many({"ts": {"$lt": cutoff_utc_iso}})
            delete_ok = True
        except Exception as e:
            logger.error(f"AI daily chat clear failed: {e}")
            # Wir versuchen trotzdem, die Summary einzufügen (siehe 5) – der
            # Nutzer soll wenigstens den Tages-Bericht sehen.

        # 5) Summary einfügen. ts = cutoff (Mitternacht Berlin des neuen Tages),
        #    dadurch sortiert die Summary chronologisch VOR allen neu
        #    eingetroffenen Nachrichten und bleibt auch beim `sort("ts", -1)`
        #    Fenster relevant, wenn wir sie in chat_history() explizit pinnen.
        cfg = self.config
        summary_doc = {
            "id": str(uuid.uuid4()),
            "role": "summary",
            "pinned": True,
            "text": text,
            "day": prev_day_iso,
            "counts": facts["counts"],
            "directives": facts["directives"][-15:],
            "active_config": {
                "provider": cfg.get("provider"),
                "model": cfg.get("model"),
                "interval_min": cfg.get("interval_min"),
                "min_confidence": cfg.get("min_confidence"),
                "cooldown_min": cfg.get("cooldown_min"),
                "news_enabled": cfg.get("news_enabled"),
            },
            "fallback": used_fallback,
            "archive_batch": archive_batch,
            "archive_errors": archive_errors,
            "ts": cutoff_utc_iso,
        }
        summary_inserted = False
        try:
            await self.db.ai_chat.insert_one(dict(summary_doc))
            summary_inserted = True
        except Exception as e:
            logger.error(f"AI daily summary insert failed: {e}")

        # 6) Nur die NEUESTE Summary bleibt gepinnt – alle älteren entpinnen.
        #    Verhindert Doppel-Pins nach mehreren Reset-Läufen und stellt sicher,
        #    dass das Frontend immer genau eine gepinnte Summary sieht.
        if summary_inserted:
            try:
                await self.db.ai_chat.update_many(
                    {"role": "summary", "pinned": True, "id": {"$ne": summary_doc["id"]}},
                    {"$set": {"pinned": False}},
                )
            except Exception as e:
                logger.warning(f"AI daily summary un-pin previous failed: {e}")

        logger.info(
            f"AI daily reset done for {prev_day_iso}: archived {len(facts['chat_docs'])} chat + "
            f"{len(facts['day_decisions'])} decisions, summary via "
            f"{'FALLBACK' if used_fallback else 'LLM'}, delete_ok={delete_ok}, "
            f"summary_inserted={summary_inserted}"
        )
        return {
            "day": prev_day_iso,
            "archived_chat": len(facts["chat_docs"]),
            "archived_decisions": len(facts["day_decisions"]),
            "fallback": used_fallback,
            "summary_id": summary_doc["id"],
            "summary_inserted": summary_inserted,
            "delete_ok": delete_ok,
            "archive_errors": archive_errors,
        }

    async def _run_housekeeping(self):
        """Wird vom run_loop jede Iteration angetriggert. Führt bei Bedarf
        (1) stündliches Analyse-Cleanup und (2) 00:00-Berlin Tages-Reset aus.

        Der Tages-Reset-Marker (`_last_reset_date`) wird AUSSCHLIESSLICH nach
        einem nachweislich erfolgreichen Reset fortgeschrieben – schlägt der
        Reset fehl (z. B. DB-Fehler beim Insert der Summary), wird er im
        nächsten Loop-Durchlauf automatisch erneut versucht. Nach 5 erfolglosen
        Versuchen wird der Marker zwangs-fortgeschrieben und ein Error geloggt,
        damit die Engine nicht dauerhaft blockiert bleibt."""
        async with self._housekeeping_lock:
            now_berlin = datetime.now(BERLIN_TZ)
            hour_key = now_berlin.strftime("%Y%m%d%H")
            date_key = now_berlin.strftime("%Y-%m-%d")

            # (A) Tages-Reset zuerst: neuer Kalendertag Berlin?
            if self._last_reset_date and date_key != self._last_reset_date:
                prev_day = self._last_reset_date

                # Retry-Zähler pro anstehendem Vortag verwalten.
                if self._reset_retry_day != prev_day:
                    self._reset_retry_day = prev_day
                    self._reset_retry_count = 0

                # Notbremse: nach 5 Fehlversuchen Marker fortschreiben, damit
                # die Engine nicht dauerhaft am selben Tag festhängt.
                if self._reset_retry_count >= 5:
                    logger.error(
                        f"Daily reset for {prev_day} skipped after "
                        f"{self._reset_retry_count} failed attempts – marker advanced."
                    )
                    self._last_reset_date = date_key
                    self._last_cleanup_hour = hour_key
                    self._reset_retry_day = None
                    self._reset_retry_count = 0
                    await self._persist_housekeeping()
                    return

                success = False
                try:
                    result = await self._daily_reset(prev_day)
                    # Erfolg = Summary konnte tatsächlich in ai_chat geschrieben
                    # werden. Nur dann darf der Marker fortgeschritten werden,
                    # sonst würde die Summary für diesen Tag ausfallen.
                    success = bool(result.get("summary_inserted"))
                except Exception as e:
                    logger.error(
                        f"Daily reset error "
                        f"(attempt {self._reset_retry_count + 1}/5) for {prev_day}: {e}"
                    )

                if not success:
                    self._reset_retry_count += 1
                    logger.warning(
                        f"Daily reset for {prev_day} not successful, "
                        f"will retry ({self._reset_retry_count}/5)."
                    )
                    # Marker NICHT fortschreiben -> nächster Loop-Durchlauf retried.
                    return

                # Erst nach echtem Erfolg: Lernlauf + Marker fortschreiben.
                try:
                    if self.learning and self.config.get("learning_enabled", True) and self.key:
                        await self.learning.run_learning(trigger="daily")
                except Exception as e:
                    logger.error(f"Daily learning error: {e}")
                self._last_reset_date = date_key
                # Nach Reset ist auch die aktuelle Stunde als 'gecleant' zu markieren
                # (der Chat ist ohnehin leer bis auf die Summary).
                self._last_cleanup_hour = hour_key
                self._reset_retry_day = None
                self._reset_retry_count = 0
                await self._persist_housekeeping()
                return

            # (B) Stündliches Cleanup – exakt zur vollen Stunde einmal pro Stunde.
            if self._last_cleanup_hour and hour_key != self._last_cleanup_hour:
                try:
                    removed = await self._cleanup_old_analyses()
                    if removed:
                        logger.info(f"AI hourly cleanup: {removed} alte Analyse-Nachricht(en) entfernt.")
                except Exception as e:
                    logger.error(f"Hourly cleanup error: {e}")
                self._last_cleanup_hour = hour_key
                await self._persist_housekeeping()

    async def force_daily_summary(self) -> Dict:
        """Manueller Trigger (Endpoint): erzwingt Reset + Summary für den 'aktuellen
        Berlin-Tag' (bzw. dem Marker `_last_reset_date`).

        Marker wird NUR nach nachweislich erfolgreichem Reset fortgeschrieben,
        damit ein Fehler nicht die reguläre Mitternachts-Logik überspringt."""
        prev_day = self._last_reset_date or datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")
        result = await self._daily_reset(prev_day)
        if result.get("summary_inserted"):
            self._last_reset_date = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")
            self._last_cleanup_hour = datetime.now(BERLIN_TZ).strftime("%Y%m%d%H")
            self._reset_retry_day = None
            self._reset_retry_count = 0
            await self._persist_housekeeping()
        return result

    # ---------------- deep analysis (Tiefen-Analyst) ----------------
    async def run_deep_analysis(self, manual: bool = False) -> Dict:
        """Sehr tiefe Analyse durch die 'deep_analyst'-Rolle. Erzeugt einen
        Report (kein direkter Trade), der die regulären Analysen speist."""
        try:
            symbols = [s for s in self.symbols
                       if len(self.scanner.candle_buffer.get(s, [])) >= 60]
            snaps = [self._snapshot(s) for s in symbols]
            snaps = [v for v in snaps if v]
            news_block = "(News deaktiviert)"
            if self.config.get("news_enabled"):
                news = await news_feed.get_headlines(25)
                news_block = "\n".join(f"- {n['title']} ({n['source']})" for n in news) or "(keine News)"
            macro = await self._macro_block()
            perf = await self._strategy_performance_text()
            directives = await self._user_directives()
            open_trades = await self._open_trades_text()
            lessons = await self.learning.lessons_text() if self.learning else "(keine)"
            research_block = ""
            try:
                from services.ai_research import research_analyst
                research_block = await research_analyst.context_text()
            except Exception:
                pass
            ml_block = ""
            try:
                from services.ai_ml_lab import ml_lab
                ml_block = await ml_lab.context_text()
            except Exception:
                pass
            from services.ai_news_watcher import news_watcher
            nw_block = await news_watcher.context_text() or "(keine relevanten Ereignisse)"
            berlin = self.scanner.berlin_now().strftime("%d.%m.%Y %H:%M")
            prompt = (
                f"{master_prompt.prompt_block()}\n\n"
                f"{self._role_context_block()}\n\n"
                f"Zeit (Berlin): {berlin}\n\n"
                f"=== MARKTDATEN (Multi-Timeframe) ===\n" +
                "\n".join(v["text"] for v in snaps) +
                (f"\n\n{macro}" if macro else "") +
                f"\n\n=== NEWS ===\n{news_block}\n\n"
                f"=== NEWS-WÄCHTER EREIGNISSE ===\n{nw_block}\n\n"
                f"=== PERFORMANCE ALLER STRATEGIEN DER PLATTFORM (lerne daraus) ===\n{perf}\n\n"
                f"=== GELERNTE LEKTIONEN ===\n{lessons}\n\n"
                + (f"{research_block}\n\n" if research_block else "")
                + (f"{ml_block}\n\n" if ml_block else "")
                + f"=== ANWEISUNGEN DES TRADERS ===\n{directives}\n\n"
                f"=== OFFENE POSITIONEN ===\n{open_trades}\n\n"
                "Erstelle jetzt die tiefe Marktanalyse als JSON."
            )
            text, provider, model = await self.generate_for_role(
                "deep_analyst", prompt, DEEP_ANALYSIS_SYSTEM, temperature=0.4)
            data = self._parse_json(text)
            now = _now_iso()
            doc = {
                "report": str(data.get("report", ""))[:4000],
                "outlook": [o for o in (data.get("outlook") or []) if isinstance(o, dict)][:15],
                "risks": [str(r)[:200] for r in (data.get("risks") or [])][:8],
                "recommendations": [str(r)[:250] for r in (data.get("recommendations") or [])][:8],
                "model": f"{provider}/{model}",
                "weight": ai_providers.model_weight(model),
                "weight_label": ai_providers.weight_label(model),
                "ts": now,
                "manual": manual,
            }
            await self.db.settings.update_one(
                {"_id": "ai_deep_report"}, {"$set": dict(doc)}, upsert=True)
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "deep_analysis",
                "text": doc["report"], "outlook": doc["outlook"], "risks": doc["risks"],
                "recommendations": doc["recommendations"], "model": doc["model"],
                "weight_label": doc["weight_label"], "manual": manual, "ts": now,
            })
            self.deep_last = now
            self.deep_last_error = None
            logger.info(f"Deep analysis done ({doc['model']}): "
                        f"{len(doc['outlook'])} Outlooks, {len(doc['recommendations'])} Empfehlungen")
            return {"status": "ok", "report": doc["report"], "model": doc["model"],
                    "ts": doc["ts"], "outlooks": len(doc["outlook"])}
        except Exception as e:
            self.deep_last_error = str(e)[:300]
            logger.error(f"Deep analysis failed: {e}")
            return {"status": "error", "detail": self.deep_last_error}

    async def _check_deep_schedule(self):
        """Feuert die Tiefenanalyse zu den konfigurierten Berlin-Uhrzeiten.
        Bereits vergangene Slots des Tages werden beim Boot übersprungen."""
        cfg = role_manager.role_cfg("deep_analyst")
        if not cfg.get("enabled", True):
            return
        times = cfg.get("schedule_times") or []
        if not times:
            return
        now_b = datetime.now(BERLIN_TZ)
        today = now_b.strftime("%Y-%m-%d")
        cur = now_b.strftime("%H:%M")
        for slot in times:
            if cur < slot:
                continue
            if slot not in self._deep_ran:
                self._deep_ran[slot] = today  # Boot: vergangenen Slot überspringen
                continue
            if self._deep_ran[slot] == today:
                continue
            self._deep_ran[slot] = today
            logger.info(f"Deep analysis Slot {slot} Berlin fällig – starte Tiefenanalyse")
            await self.run_deep_analysis(manual=False)

    # ---------------- background loop ----------------
    async def run_loop(self):
        self.running = True
        logger.info("AI Trader engine loop started (multi-provider: gemini/groq/openrouter/mistral)")
        while self.running:
            await asyncio.sleep(5)
            try:
                # Housekeeping läuft IMMER (auch wenn Engine aus ist / kein Key), damit
                # stündliches Analyse-Cleanup und der 00:00-Berlin-Reset zuverlässig feuern.
                try:
                    await self._run_housekeeping()
                except Exception as hk_err:
                    logger.error(f"AI housekeeping loop error: {hk_err}")

                # Lern-Modul: Ergebnisse synchronisieren + ggf. Lernlauf nach Trade-Close
                try:
                    if self.learning:
                        await self.learning.tick()
                except Exception as le:
                    logger.error(f"AI learning tick error: {le}")

                # Tiefen-Analyst: geplante Deep-Analysen (Berlin-Uhrzeiten)
                try:
                    await self._check_deep_schedule()
                except Exception as de:
                    logger.error(f"AI deep schedule error: {de}")

                # KI-Ökosystem: Markt-Beobachter (Datensammlung), Forschungs-Analyst
                # (Backtest-/Optimizer-Auswertung) und ML-Labor (Optuna/XGBoost).
                # Alle drei laufen unabhängig von der Analyse-Engine weiter.
                for name, mod_attr in (("market observer", "ai_market_observer.market_observer"),
                                       ("research analyst", "ai_research.research_analyst"),
                                       ("ml lab", "ai_ml_lab.ml_lab"),
                                       ("strategy lab", "ai_strategy_lab.strategy_lab"),
                                       ("trade manager", "ai_trade_manager.trade_manager")):
                    try:
                        mod_name, obj_name = mod_attr.split(".")
                        mod = __import__(f"services.{mod_name}", fromlist=[obj_name])
                        await getattr(mod, obj_name).tick()
                    except Exception as ex:
                        logger.error(f"AI {name} tick error: {ex}")
                try:
                    from services.ai_memory import memory
                    await memory.housekeeping()
                except Exception as me:
                    logger.error(f"AI memory housekeeping error: {me}")

                if not self.config.get("enabled") or not self.key:
                    self.next_run = None
                    continue
                now = time.time()
                if now >= self._next_due:
                    interval_min, window = self.current_interval()
                    interval = max(1, interval_min) * 60
                    self._next_due = now + interval
                    self.next_run = (datetime.now(timezone.utc)
                                     + timedelta(seconds=interval)).isoformat()
                    self.active_window = window
                    logger.info(f"AI Analyse-Zyklus ({window}: alle {interval_min} min)")
                    await self.run_analysis()
            except Exception as e:
                logger.error(f"AI loop error: {e}")

    # ---------------- chat ----------------
    async def chat_history(self, limit: int = 80) -> List[Dict]:
        """Liefert den Chatverlauf für das Frontend.

        Garantiert, dass die aktuelle gepinnte Tages-Summary IMMER als erstes
        Element enthalten ist – unabhängig vom Limit. Ohne diese Absicherung
        würde die Summary (älteste Nachricht des Tages) nach ~limit
        Neu-Nachrichten aus dem `sort("ts", -1).limit(limit)`-Fenster fallen
        und im Frontend nicht mehr angezeigt werden."""
        pinned = await self.db.ai_chat.find_one(
            {"role": "summary", "pinned": True}, sort=[("ts", -1)]
        )
        rows = await self.db.ai_chat.find().sort("ts", -1).limit(limit).to_list(limit)
        rows.reverse()
        for r in rows:
            r.pop("_id", None)
        if pinned:
            pinned.pop("_id", None)
            pinned_id = pinned.get("id")
            # Dedupe: falls die gepinnte Summary bereits im Fenster ist, entferne
            # sie dort – sie wird stattdessen garantiert an den Anfang gesetzt.
            if pinned_id:
                rows = [r for r in rows if r.get("id") != pinned_id]
            rows = [pinned] + rows
        return rows

    async def chat_stream(self, text: str, coins=None):
        """SSE-Streaming der KI-Antwort. Wechselt bei 429 automatisch das Modell
        innerhalb desselben Providers. Unterstützt Gemini + OpenAI-kompatible
        Provider (Groq, OpenRouter, Mistral).

        `coins`: optionale Liste der Symbole, auf die der Chat-Kontext
        eingegrenzt wird (leer / None / "ALL" => alle Coins)."""
        chain = role_manager.chain("chat", self.config)
        if not any(ai_providers.provider_keys(p) for p, _ in chain):
            yield "⚠️ Kein API-Key für die konfigurierten Provider gesetzt – bitte in Render EnvVars setzen."
            return

        hist_rows = await self.db.ai_chat.find({"role": {"$in": ["user", "assistant", "summary"]}}) \
            .sort("ts", -1).limit(14).to_list(14)
        hist_rows.reverse()
        def _role_label(r):
            role = r.get("role")
            if role == "user":
                return "Nutzer"
            if role == "summary":
                return f"KI-Tageszusammenfassung ({r.get('day', '')})"
            return "KI"
        history = "\n".join(
            f"{_role_label(r)}: {r.get('text', '')}" for r in hist_rows
        ) or "(noch keine Nachrichten)"
        await self.db.ai_chat.insert_one({
            "id": str(uuid.uuid4()), "role": "user", "text": text, "ts": _now_iso(),
        })

        # Trader-Anweisungen REAL ausführen (Positionen schließen, Lektionen,
        # Einstellungen ...), bevor die Antwort generiert wird. Die echten
        # Ergebnisse fließen in den Kontext ein – die KI berichtet nur Fakten.
        exec_block = ""
        try:
            from services.ai_chat_commands import chat_commands
            cmd_res = await chat_commands.run(self, text)
            if cmd_res and cmd_res.get("results_text"):
                exec_block = ("\n\n=== SOEBEN REAL AUSGEFÜHRTE AKTIONEN "
                              "(vom System verifiziert) ===\n"
                              + cmd_res["results_text"])
        except Exception as e:
            logger.error(f"Chat-Kommandos fehlgeschlagen: {e}")

        context = await self._context_brief(coins=coins)
        system = CHAT_SYSTEM_TEMPLATE.format(context=context, history=history) + exec_block

        acc = ""
        async for kind, payload in ai_providers.stream_chain(chain, text, system, temperature=0.6):
            if kind == "token":
                acc += payload
                yield payload
            elif kind == "meta":
                provider, model = payload
                self._effective_provider, self._effective_model = provider, model
                if model != self.config.get("model"):
                    logger.info(f"AI chat: genutzt {provider}/{model}")
            elif kind == "error":
                err = f"\n⚠️ {payload}"
                acc += err
                yield err

        if acc:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "assistant", "text": acc, "ts": _now_iso(),
            })

    async def clear_chat(self):
        await self.db.ai_chat.delete_many({})

    def current_interval(self) -> tuple:
        """Aktuelles Analyse-Intervall gemäß Zeitplan (Berlin-Zeit)."""
        now = self.scanner.berlin_now()
        minutes = now.hour * 60 + now.minute
        return ai_schedule.effective_interval(
            self.config.get("schedule"), self.config.get("interval_min", 10), minutes)

    def status(self) -> Dict:
        from services.ai_news_watcher import news_watcher
        return {
            "config": dict(self.config),
            "has_key": bool(self.key),
            "provider_keys": self._available_providers(),
            "backup_keys": ai_providers.backup_keys_info(),
            "analyzing": self._analyzing,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "last_error": self.last_error,
            "decisions": self.decisions,
            "allowed_models": ALLOWED_MODELS,
            "model_weights": ai_providers.MODEL_WEIGHTS,
            "effective_model": self._effective_model,
            "effective_provider": self._effective_provider,
            "learning": self.learning.summary() if self.learning else None,
            "roles": role_manager.snapshot(),
            "deep_last": self.deep_last,
            "deep_last_error": self.deep_last_error,
            "news_watcher": news_watcher.status(),
            "schedule_active": {
                "interval_min": self.current_interval()[0],
                "window": self.current_interval()[1],
                "text": ai_schedule.schedule_text(self.config.get("schedule"),
                                                 self.config.get("interval_min", 10)),
            },
            "providers_health": ai_providers.health_status(),
            "day_risk": self._day_risk_cache,
            "master_prompt": master_prompt.snapshot(),
            "validation": validation_gate.status(),
            "strategy_lab": strategy_lab.status(),
        }


ai_engine = AIEngine()
