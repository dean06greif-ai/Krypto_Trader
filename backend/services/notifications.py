"""Zentrale Benachrichtigungen: Website-Meldungen + Telegram (per Toggle).

Telegram-Toggles (Reiter Telegram in den Einstellungen):
  ai_failure      – KI-Ausfall (Primär + Backup gescheitert, Fallback übernimmt)
  backtest_done   – Backtest fertig
  optimizer_done  – Optimizer fertig
  trade_opened    – Trade eröffnet
  trade_closed    – Trade geschlossen (SL/TP/manuell)
  kill_switch     – Kill-Switch / Risiko-Notbremse ausgelöst
  daily_summary   – tägliche Zusammenfassung um Mitternacht

Website-Meldungen landen in `app_notifications` (Frontend pollt /api/notifications).
"""
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_ID = "telegram_notify_config"
DEFAULT_CONFIG = {
    "ai_failure": True,
    "backtest_done": True,
    "optimizer_done": True,
    "trade_opened": True,
    "trade_closed": True,
    "kill_switch": True,
    "daily_summary": True,
    "website_ai_failure": True,   # Meldung auf der Website bei KI-Ausfall
}

_cfg_cache: Optional[Dict] = None
_cfg_ts = 0.0
# Cooldown gegen Meldungs-Spam (z.B. KI-Ausfall bei jedem Call)
_last_sent: Dict[str, float] = {}


async def get_config(db) -> Dict:
    global _cfg_cache, _cfg_ts
    if _cfg_cache is not None and time.time() - _cfg_ts < 30:
        return _cfg_cache
    cfg = dict(DEFAULT_CONFIG)
    try:
        doc = await db.settings.find_one({"_id": CONFIG_ID})
        if doc:
            doc.pop("_id", None)
            cfg.update({k: bool(v) for k, v in doc.items() if k in DEFAULT_CONFIG})
    except Exception as e:
        logger.warning(f"notify config load failed: {e}")
    _cfg_cache, _cfg_ts = cfg, time.time()
    return cfg


async def update_config(db, updates: Dict) -> Dict:
    global _cfg_cache, _cfg_ts
    clean = {k: bool(v) for k, v in (updates or {}).items() if k in DEFAULT_CONFIG}
    if clean:
        await db.settings.update_one({"_id": CONFIG_ID}, {"$set": clean}, upsert=True)
    _cfg_cache, _cfg_ts = None, 0.0
    return await get_config(db)


async def website_notify(db, ntype: str, title: str, message: str,
                         cooldown_min: float = 30) -> bool:
    """Meldung für die Website (einmalig, mit Cooldown pro Typ)."""
    key = f"web:{ntype}:{title}"
    now = time.time()
    if now - _last_sent.get(key, 0) < cooldown_min * 60:
        return False
    _last_sent[key] = now
    try:
        await db.app_notifications.insert_one({
            "id": uuid.uuid4().hex[:12], "type": ntype, "title": title,
            "message": message, "read": False,
            "created_at": datetime.now(timezone.utc).isoformat()})
        return True
    except Exception as e:
        logger.warning(f"website notify failed: {e}")
        return False


async def telegram_notify(db, telegram, ntype: str, text: str,
                          cooldown_min: float = 0) -> bool:
    """Telegram-Nachricht, wenn der Toggle für `ntype` an ist."""
    cfg = await get_config(db)
    if not cfg.get(ntype, True):
        return False
    if cooldown_min > 0:
        key = f"tg:{ntype}"
        now = time.time()
        if now - _last_sent.get(key, 0) < cooldown_min * 60:
            return False
        _last_sent[key] = now
    bot = getattr(telegram, "bot", None)
    chat_id = getattr(telegram, "chat_id", None)
    if not bot or not chat_id:
        return False
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown",
                               disable_web_page_preview=True)
        return True
    except Exception as e:
        logger.warning(f"telegram notify ({ntype}) failed: {e}")
        return False


async def notify_ai_failure(role: str, failed_models: list, fallback_model: Optional[str]):
    """KI-Ausfall: Primär + Backup gescheitert -> Website + Telegram (Toggle).
    Lazy imports, damit ai_providers keine harten Abhängigkeiten bekommt."""
    try:
        from core import state
        db = state.db
        if db is None:
            return
        failed = ", ".join(failed_models[:4])
        if fallback_model:
            title = f"KI-Ausfall: {role}"
            msg = (f"Primär- und Backup-KI ausgefallen ({failed}). "
                   f"Notfall-Fallback übernimmt: {fallback_model}.")
        else:
            title = f"KI komplett ausgefallen: {role}"
            msg = f"Alle Modelle gescheitert ({failed}). Keine Antwort möglich."
        cfg = await get_config(db)
        if cfg.get("website_ai_failure", True):
            await website_notify(db, "ai_failure", title, msg, cooldown_min=30)
        await telegram_notify(db, state.telegram, "ai_failure",
                              f"🤖⚠️ *{title}*\n{msg}", cooldown_min=30)
    except Exception as e:
        logger.warning(f"notify_ai_failure failed: {e}")
