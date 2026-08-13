"""Strategie-Playbook des KI-Traders: vielseitige Daytrading-Setups mit
automatischem Performance-Tracking, Sperren schwacher Setups und Re-Tests.

Die KI wählt pro Trade ein Setup (JSON-Feld "setup"), das Setup wird am Trade
gespeichert (auto_trades.setup). Aus den ECHTEN Trade-Ergebnissen entsteht pro
Setup eine Statistik (Trades, Winrate, PnL) mit Urteil:
  bewährt  – bevorzugen
  neutral  – weiter nutzen, beobachten
  test     – zu wenig Daten, bewusst (klein) antesten
  schwach  – wird automatisch GESPERRT (technisch erzwungen) und nach
             RETEST_DAYS wieder für einen Re-Test freigegeben.

Zusätzlich enthält das Modul die Diversifikations-Guards gegen das beobachtete
Fehlverhalten "viele gleichgerichtete Trades / mehrere Einstiege in derselben
Preiszone" (rein & testbar, technisch in ai_engine._emit_signal erzwungen).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STATE_ID = "ai_playbook_state"
LOOKBACK_DAYS = 30
MIN_TRADES_FOR_VERDICT = 5
MIN_TRADES_FOR_BLOCK = 8
RETEST_DAYS = 14

# Kompakte Setup-Bibliothek (bewusst kurz gehalten – Token-Budget!).
SETUPS: Dict[str, str] = {
    "trend_follow": "Trendfolge-Scalp: Pullback an EMA/Struktur im laufenden Trend, SL hinter dem Pullback-Extrem.",
    "breakout": "Breakout: Ausbruch aus Range/Key-Level mit Momentum, Einstieg im Ausbruch/Retest, SL zurück im Level.",
    "squeeze_breakout": "Volatilitäts-Squeeze: enge Kompression (BB/Range), Einstieg beim Impuls-Ausbruch, weites Ziel.",
    "mean_reversion": "Mean-Reversion: überdehnter Move (RSI-Extrem/VWAP-Abstand), Gegenposition zurück zum Mittelwert, enges Ziel.",
    "range_fade": "Range-Fade: klare Seitwärtsrange, Einstieg an der Range-Kante gegen die Bewegung, SL knapp dahinter.",
    "liquidity_sweep": "Liquidity-Sweep: Stop-Jagd über markantes Hoch/Tief mit schneller Rückeroberung, Einstieg gegen den Sweep.",
    "momentum_news": "News-/Momentum: starker Impuls (News/Volumen), Einstieg in Impulsrichtung nach erster Konsolidierung.",
    "pullback": "Key-Level-Pullback: Einstieg an starkem Support/Resistance mit Bestätigung (Abweisung/Volumen).",
    "swing_trend": "Swing-Trendfolge (horizon=swing): übergeordneter HTF-Trend, weiter SL, gestaffelte Ziele/Runner.",
    "hedge": "Hedge: bewusste GEGENposition zur bestehenden Exposure zur Risikoreduktion (z.B. Short-Scalp gegen Swing-Long).",
}

SETUP_ENUM = "|".join(SETUPS.keys())

_ALIASES = (
    ("hedge", "hedge"), ("sweep", "liquidity_sweep"), ("ict", "liquidity_sweep"),
    ("squeeze", "squeeze_breakout"), ("break", "breakout"),
    ("reversion", "mean_reversion"), ("revert", "mean_reversion"),
    ("range", "range_fade"), ("fade", "range_fade"),
    ("news", "momentum_news"), ("momentum", "momentum_news"),
    ("swing", "swing_trend"), ("pull", "pullback"),
    ("trend", "trend_follow"), ("scalp", "trend_follow"),
)

# Cache der gesperrten Setups (von refresh() aktualisiert – einmal pro Analyse)
_disabled_cache: Dict[str, Dict] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_setup(raw) -> Optional[str]:
    """Freitext der KI auf eine bekannte Setup-ID mappen (rein, testbar)."""
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not s:
        return None
    if s in SETUPS:
        return s
    for needle, target in _ALIASES:
        if needle in s:
            return target
    return "other"


def verdict_for(trades: int, wins: int, pnl: float) -> str:
    """Urteil pro Setup aus echten Ergebnissen (rein, testbar)."""
    if trades < MIN_TRADES_FOR_VERDICT:
        return "test"
    wr = wins / trades * 100
    if wr >= 55 and pnl > 0:
        return "bewährt"
    if trades >= MIN_TRADES_FOR_BLOCK and (wr <= 35 or (pnl < 0 and wr < 45)):
        return "schwach"
    return "neutral"


def diversification_check(open_trades: List[Dict], symbol: str, side: str,
                          price: float, max_same_direction: int = 3,
                          min_dist_pct: float = 0.5,
                          setup: Optional[str] = None) -> Tuple[bool, str]:
    """Guards gegen Richtungs-Klumpen und Entry-Cluster (rein, testbar).

    1. Richtungs-Guard: max. N gleichzeitig offene KI-Trades in DIESELBE
       Richtung (0 = aus). Ein echter Hedge ist per Definition die Gegenrichtung
       und wird dadurch nie blockiert.
    2. Cluster-Guard: kein weiterer Einstieg auf demselben Symbol in dieselbe
       Richtung, wenn ein offener Entry näher als `min_dist_pct` % liegt –
       das war das beobachtete "mehrere Trades in derselben Zone"-Problem.
    """
    side = str(side or "").upper()
    if max_same_direction:
        same = [t for t in open_trades if str(t.get("side") or "").upper() == side]
        if len(same) >= max_same_direction:
            syms = ", ".join(sorted({str(t.get("symbol")) for t in same})[:6])
            return False, (f"Richtungs-Guard: bereits {len(same)} offene {side}-Trades "
                           f"({syms}) – Limit {max_same_direction}, kein weiterer "
                           f"gleichgerichteter Trade")
    try:
        px = float(price or 0)
    except (TypeError, ValueError):
        px = 0.0
    if min_dist_pct and px > 0:
        for t in open_trades:
            if str(t.get("symbol") or "") != str(symbol) \
                    or str(t.get("side") or "").upper() != side:
                continue
            try:
                entry = float(t.get("entry") or 0)
            except (TypeError, ValueError):
                continue
            if entry <= 0:
                continue
            dist = abs(entry - px) / px * 100
            if dist < float(min_dist_pct):
                return False, (f"Cluster-Guard: offener {side} auf {symbol} @ {entry} "
                               f"liegt nur {dist:.2f}% vom neuen Entry entfernt "
                               f"(Mindestabstand {min_dist_pct}%)")
    return True, ""


def disabled_reason(setup: Optional[str]) -> Optional[str]:
    """Gesperrtes Setup? (nutzt den von refresh() gepflegten Cache)."""
    if not setup:
        return None
    d = _disabled_cache.get(setup)
    if d:
        return (f"Playbook: Setup '{setup}' ist gesperrt (schwache Performance: "
                f"{d.get('reason', '')}) – Re-Test ab {str(d.get('retest_at', ''))[:10]}")
    return None


async def setup_stats(db, days: int = LOOKBACK_DAYS) -> Dict[str, Dict]:
    """Echte Trade-Ergebnisse des KI-Traders pro Setup aggregieren."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await db.auto_trades.aggregate([
        {"$match": {"strategy_id": "ai_trader", "status": "closed",
                    "opened_at": {"$gte": cutoff}, "setup": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$setup", "trades": {"$sum": 1},
                    "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}},
                    "pnl": {"$sum": "$realized_pnl"}}},
    ]).to_list(50)
    out = {}
    for r in rows:
        sid = str(r["_id"])
        out[sid] = {"trades": int(r["trades"]), "wins": int(r["wins"]),
                    "pnl": round(float(r.get("pnl") or 0), 2),
                    "verdict": verdict_for(int(r["trades"]), int(r["wins"]),
                                           float(r.get("pnl") or 0))}
    return out


async def refresh(db) -> Dict:
    """Statistik neu berechnen, schwache Setups sperren, Re-Tests freigeben.

    Läuft einmal pro Analyse-Zyklus (über context_text) und hält den
    Sperr-Cache für _emit_signal aktuell."""
    global _disabled_cache
    stats = await setup_stats(db)
    doc = await db.settings.find_one({"_id": STATE_ID}) or {}
    disabled: Dict[str, Dict] = dict(doc.get("disabled") or {})
    changed = False
    now = datetime.now(timezone.utc)
    # Re-Test: Sperre nach RETEST_DAYS automatisch aufheben
    for sid in list(disabled.keys()):
        try:
            retest = datetime.fromisoformat(str(disabled[sid].get("retest_at")))
        except (TypeError, ValueError):
            retest = now
        if retest <= now:
            disabled.pop(sid)
            changed = True
            logger.info(f"Playbook: Setup '{sid}' wieder freigegeben (Re-Test)")
    # Schwache Setups sperren
    for sid, st in stats.items():
        if st["verdict"] == "schwach" and sid not in disabled and sid in SETUPS:
            wr = round(st["wins"] / st["trades"] * 100) if st["trades"] else 0
            disabled[sid] = {
                "at": _now_iso(),
                "retest_at": (now + timedelta(days=RETEST_DAYS)).isoformat(),
                "reason": f"{st['trades']} Trades, Winrate {wr}%, PnL {st['pnl']:+.2f} USDT",
            }
            changed = True
            logger.warning(f"Playbook: Setup '{sid}' gesperrt ({disabled[sid]['reason']})")
    if changed or "disabled" not in doc:
        await db.settings.update_one(
            {"_id": STATE_ID},
            {"$set": {"disabled": disabled, "updated_at": _now_iso()}}, upsert=True)
    _disabled_cache = disabled
    return {"stats": stats, "disabled": disabled}


async def context_text(db) -> str:
    """Kompakter Prompt-Block: Playbook + echte Performance pro Setup."""
    data = await refresh(db)
    stats, disabled = data["stats"], data["disabled"]
    lines = ["=== STRATEGIE-PLAYBOOK (Feld \"setup\" – Pflicht bei LONG/SHORT) ==="]
    for sid, desc in SETUPS.items():
        lines.append(f"- {sid}: {desc}")
    lines.append("PERFORMANCE PRO SETUP (echte KI-Trades, letzte "
                 f"{LOOKBACK_DAYS} Tage):")
    if stats:
        for sid, st in sorted(stats.items(), key=lambda x: -x[1]["pnl"]):
            wr = round(st["wins"] / st["trades"] * 100) if st["trades"] else 0
            mark = st["verdict"].upper() if st["verdict"] in ("bewährt", "schwach") else st["verdict"]
            if sid in disabled:
                mark = "GESPERRT – nicht nutzen"
            lines.append(f"- {sid}: {st['trades']} Trades, WR {wr}%, "
                         f"PnL {st['pnl']:+.2f} USDT → {mark}")
    else:
        lines.append("- (noch keine Setup-Daten – Statistik entsteht mit jedem Trade)")
    if disabled:
        lines.append("GESPERRTE SETUPS (technisch blockiert): "
                     + ", ".join(f"{s} (Re-Test ab {str(d.get('retest_at', ''))[:10]})"
                                 for s, d in disabled.items()))
    lines.append("REGELN: Wähle das Setup passend zur Marktphase. Bevorzuge BEWÄHRTE "
                 "Setups, meide gesperrte. Setups ohne Daten bewusst klein antesten "
                 "(capital_pct niedrig), damit die Statistik wachsen kann. Nutze die "
                 "ganze Bandbreite (auch swing_trend und hedge) statt immer dasselbe Muster.")
    return "\n".join(lines)


async def status(db) -> Dict:
    """Für API/UI: Playbook, Statistik und Sperren."""
    data = await refresh(db)
    return {"setups": SETUPS, "stats": data["stats"], "disabled": data["disabled"],
            "lookback_days": LOOKBACK_DAYS, "retest_days": RETEST_DAYS}
