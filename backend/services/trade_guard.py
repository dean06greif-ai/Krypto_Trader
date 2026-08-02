"""Risiko-Schutzschicht für das Auto-Trading.

1. Kill-Switch (Drawdown-Guard): pausiert das Auto-Trading automatisch bei
   X % Tagesverlust ODER Y Verlust-Trades in Folge – bis Mitternacht (UTC),
   danach automatisch wieder aktiv. Werte im UI änderbar.

2. Anti-Stacking: gleiche Richtung + gleiches Asset + gleicher Timeframe
   -> für `cooldown_min` blockiert. Anderer Timeframe oder Gegenrichtung
   (Hedge) bleibt IMMER erlaubt (bewusste Design-Entscheidung: Multi-Timeframe-
   Einstiege und übergeordnete Hedges sollen möglich bleiben).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_ID = "trade_guard_config"
STATE_ID = "trade_guard_state"

DEFAULT_CONFIG = {
    "kill_switch_enabled": True,
    "max_daily_loss_pct": 5.0,       # % Tagesverlust (bezogen auf ref_capital)
    "max_consecutive_losses": 3,     # Verlust-Trades in Folge
    "ref_capital": 0.0,              # 0 = automatisch (Summe max_capital der Tages-Trades)
    "anti_stacking_enabled": True,
    "stacking_cooldown_min": 30,
}

_cfg_cache: Optional[Dict] = None


async def get_config(db) -> Dict:
    global _cfg_cache
    if _cfg_cache is not None:
        return _cfg_cache
    cfg = dict(DEFAULT_CONFIG)
    doc = await db.settings.find_one({"_id": CONFIG_ID})
    if doc:
        doc.pop("_id", None)
        for k in DEFAULT_CONFIG:
            if k in doc:
                cfg[k] = doc[k]
    _cfg_cache = cfg
    return cfg


async def update_config(db, updates: Dict) -> Dict:
    global _cfg_cache
    clean = {}
    for k, v in (updates or {}).items():
        if k not in DEFAULT_CONFIG:
            continue
        if k in ("kill_switch_enabled", "anti_stacking_enabled"):
            clean[k] = bool(v)
        else:
            try:
                clean[k] = max(0.0, float(v))
            except (TypeError, ValueError):
                continue
    if clean:
        await db.settings.update_one({"_id": CONFIG_ID}, {"$set": clean}, upsert=True)
    _cfg_cache = None
    return await get_config(db)


async def get_state(db) -> Dict:
    doc = await db.settings.find_one({"_id": STATE_ID}) or {}
    doc.pop("_id", None)
    paused_until = doc.get("paused_until")
    active = False
    if paused_until:
        try:
            active = datetime.fromisoformat(paused_until) > datetime.now(timezone.utc)
        except ValueError:
            active = False
    return {"paused": active, "paused_until": paused_until if active else None,
            "reason": doc.get("reason") if active else None,
            "triggered_at": doc.get("triggered_at") if active else None}


async def resume(db) -> Dict:
    """Kill-Switch manuell aufheben."""
    await db.settings.update_one({"_id": STATE_ID},
                                 {"$set": {"paused_until": None, "reason": None}},
                                 upsert=True)
    return await get_state(db)


def _next_midnight_utc() -> str:
    now = datetime.now(timezone.utc)
    nm = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nm.isoformat()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _trigger(db, telegram, reason: str):
    until = _next_midnight_utc()
    await db.settings.update_one(
        {"_id": STATE_ID},
        {"$set": {"paused_until": until, "reason": reason,
                  "triggered_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True)
    logger.warning(f"KILL-SWITCH ausgelöst: {reason} – Auto-Trading pausiert bis {until}")
    from services import notifications
    await notifications.website_notify(
        db, "kill_switch", "Kill-Switch ausgelöst",
        f"{reason}. Auto-Trading pausiert bis Mitternacht (UTC), danach automatisch wieder aktiv.",
        cooldown_min=5)
    await notifications.telegram_notify(
        db, telegram, "kill_switch",
        f"🛑 *KILL-SWITCH AUSGELÖST*\n{reason}\n"
        f"Auto-Trading pausiert bis Mitternacht (UTC), danach automatisch wieder aktiv.")


async def check_open_allowed(db, signal: Dict, timeframe: str) -> Tuple[bool, str]:
    """Vor jedem Auto-Trade: Kill-Switch aktiv? Anti-Stacking-Cooldown?"""
    cfg = await get_config(db)

    if cfg.get("kill_switch_enabled"):
        st = await get_state(db)
        if st["paused"]:
            return False, f"Kill-Switch aktiv bis {st['paused_until']} ({st['reason']})"

    if cfg.get("anti_stacking_enabled"):
        cooldown = float(cfg.get("stacking_cooldown_min", 30) or 30)
        since = (datetime.now(timezone.utc) - timedelta(minutes=cooldown)).isoformat()
        recent = await db.auto_trades.find_one({
            "symbol": signal["symbol"], "side": signal["type"],
            "timeframe": timeframe,
            "opened_at": {"$gte": since},
        })
        if recent:
            return False, (f"Anti-Stacking: {signal['type']} {signal['symbol']} "
                           f"({timeframe}) bereits vor <{int(cooldown)} Min eröffnet – "
                           "anderer Timeframe oder Gegenrichtung bleibt erlaubt")
    return True, ""


async def on_trade_closed(db, telegram, trade: Dict):
    """Nach jedem geschlossenen Auto-Trade: Zähler aktualisieren, Limits prüfen."""
    cfg = await get_config(db)
    if not cfg.get("kill_switch_enabled"):
        return
    today = _today_utc()
    rows = await db.auto_trades.find(
        {"status": "closed", "closed_at": {"$gte": f"{today}T00:00:00"}},
        {"realized_pnl": 1, "result": 1, "closed_at": 1, "max_capital": 1}
    ).sort("closed_at", 1).to_list(1000)

    consecutive = 0
    for r in rows:
        res = r.get("result")
        if res == "loss":
            consecutive += 1
        elif res in ("win", "breakeven"):
            consecutive = 0

    max_losses = int(cfg.get("max_consecutive_losses", 3) or 0)
    if max_losses and consecutive >= max_losses:
        await _trigger(db, telegram,
                       f"{consecutive} Verlust-Trades in Folge (Limit {max_losses})")
        return

    daily_pnl = sum(float(r.get("realized_pnl") or 0) for r in rows)
    ref = float(cfg.get("ref_capital") or 0)
    if ref <= 0:
        ref = sum(float(r.get("max_capital") or 0) for r in rows) or 100.0
    loss_limit = float(cfg.get("max_daily_loss_pct", 5.0) or 0)
    if loss_limit and daily_pnl < 0 and abs(daily_pnl) / ref * 100 >= loss_limit:
        await _trigger(db, telegram,
                       f"Tagesverlust {round(abs(daily_pnl) / ref * 100, 2)}% "
                       f"(Limit {loss_limit}%, PnL {round(daily_pnl, 2)} USDT)")
