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
    "watchdog": True,             # Positions-Watchdog (fehlender SL, Übernahmen)
    "model_watch": True,          # Modell-Wächter (tote Modell-Slugs erkannt)
    "daily_summary": True,
    "token_alert": True,          # Token-Kosten-Wächter (ungewöhnlich hoher Verbrauch)
    "website_ai_failure": True,   # Meldung auf der Website bei KI-Ausfall
}

# Deutsche Rollen-Namen (identisch zum KI-Team im Frontend) für Meldungstexte
ROLE_LABELS = {
    "analyst": "Analyst",
    "deep_analyst": "Tiefen-Analyst",
    "research_analyst": "Forschungs-Analyst",
    "market_observer": "Markt-Beobachter",
    "trade_manager": "Trade-Manager",
    "news_watcher": "News-Wächter",
    "chat": "Chat-Assistent",
    "learner": "Lern-Modul",
    "summarizer": "Tages-Reporter",
}
REASON_TEXT = {
    "rate_limited": "Rate-Limit erreicht",
    "error": "Ausfall (Fehler/Timeout)",
    "skipped_too_large": "Prompt zu groß fürs Token-Budget",
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
                         cooldown_min: float = 30,
                         dedupe_key: Optional[str] = None,
                         source: Optional[str] = None,
                         meta: Optional[Dict] = None) -> bool:
    """Meldung für die Website (einmalig, mit Cooldown pro Typ).
    `source`/`meta` = Herkunfts-Analyse für die Glocke (wer hat's gemeldet,
    welches Modell/welche Rolle, Ursache). `popped=False` -> Popup nur 1×."""
    key = dedupe_key or f"web:{ntype}:{title}"
    now = time.time()
    if now - _last_sent.get(key, 0) < cooldown_min * 60:
        return False
    _last_sent[key] = now
    try:
        await db.app_notifications.insert_one({
            "id": uuid.uuid4().hex[:12], "type": ntype, "title": title,
            "message": message, "read": False, "popped": False,
            "source": source, "meta": meta or {},
            "created_at": datetime.now(timezone.utc).isoformat()})
        return True
    except Exception as e:
        logger.warning(f"website notify failed: {e}")
        return False


async def telegram_notify(db, telegram, ntype: str, text: str,
                          cooldown_min: float = 0,
                          dedupe_key: Optional[str] = None) -> bool:
    """Telegram-Nachricht, wenn der Toggle für `ntype` an ist."""
    cfg = await get_config(db)
    if not cfg.get(ntype, True):
        return False
    if cooldown_min > 0:
        key = dedupe_key or f"tg:{ntype}"
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


def _fail_lines(failures: Optional[list], limit: int = 6) -> list:
    """Pro Modell eine Ursachen-Zeile: 'provider/model: Ursache (Detail)'."""
    out = []
    for f in (failures or [])[:limit]:
        model = f.get("model") or "?"
        reason = REASON_TEXT.get(f.get("reason"), f.get("reason") or "Fehler")
        detail = str(f.get("detail") or "").strip()
        # Markdown-kritische Zeichen entschärfen (sonst scheitert Telegram-Send)
        detail = detail.replace("*", "").replace("_", " ").replace("`", "'").replace("[", "(").replace("]", ")")
        line = f"{model}: {reason}"
        if detail:
            line += f" – {detail[:140]}"
        out.append(line)
    return out


async def notify_ai_failure(role: str, failed_models: list,
                            fallback_model: Optional[str],
                            failures: Optional[list] = None):
    """KI-Ausfall: Primär + Backup gescheitert -> Website + Telegram (Toggle).
    `failures` = Detail-Analyse pro Modell (Ursache + Fehlertext), damit
    nachvollziehbar ist, WARUM jedes Modell ausgefallen ist.
    Lazy imports, damit ai_providers keine harten Abhängigkeiten bekommt."""
    try:
        from core import state
        db = state.db
        if db is None:
            return
        role_label = ROLE_LABELS.get(role, role)
        failed = ", ".join(failed_models[:4])
        if fallback_model:
            title = f"KI-Ausfall: {role_label}"
            msg = (f"Primär- und Backup-KI ausgefallen ({failed}). "
                   f"Notfall-Fallback übernimmt: {fallback_model}.")
        else:
            title = f"KI komplett ausgefallen: {role_label}"
            msg = f"Alle Modelle gescheitert ({failed}). Keine Antwort möglich."
        lines = _fail_lines(failures)
        detail_txt = ""
        if lines:
            detail_txt = "\nUrsachen im Detail:\n" + "\n".join(f"• {ln}" for ln in lines)
        try:
            from core.timeutil import now_berlin
            ts_txt = now_berlin().strftime("%d.%m.%Y %H:%M:%S")
        except Exception:
            ts_txt = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC")
        cfg = await get_config(db)
        if cfg.get("website_ai_failure", True):
            await website_notify(db, "ai_failure", title, msg + detail_txt,
                                 cooldown_min=30,
                                 source="KI-Team (Fallback-Kette)",
                                 meta={"role": role_label,
                                       "failed_models": failed_models[:6],
                                       "failures": (failures or [])[:6],
                                       "fallback": fallback_model})
        await telegram_notify(db, state.telegram, "ai_failure",
                              f"🤖⚠️ *{title}*\n{msg}{detail_txt}\n_{ts_txt} Uhr_",
                              cooldown_min=30)
    except Exception as e:
        logger.warning(f"notify_ai_failure failed: {e}")


async def notify_model_failure(role: Optional[str], provider: str, model: str,
                               reason: str, detail: str = "",
                               fallback: Optional[str] = None):
    """Spiegelt einzelne Modell-Ausfälle in die Website-Glocke – mit denselben
    Details wie im Modell-Status (betroffener Assistent, Ursache, Fallback).
    Cooldown 15 min pro Modell+Ursache gegen Spam."""
    try:
        from core import state
        db = state.db
        if db is None:
            return
        cfg = await get_config(db)
        if not cfg.get("website_ai_failure", True):
            return
        role_label = ROLE_LABELS.get(role or "", role or "unbekannte Rolle")
        reason_text = REASON_TEXT.get(reason, reason)
        msg = f"{provider}/{model} – {reason_text}."
        if detail and reason not in ("rate_limited", "skipped_too_large"):
            msg += f" Details: {str(detail)[:120]}."
        msg += (f" Fallback übernimmt: {fallback}." if fallback
                else " Die Fallback-Kette übernimmt automatisch.")
        await website_notify(
            db, "ai_failure", f"KI-Warnung: {role_label}", msg, cooldown_min=15,
            dedupe_key=f"web:ai_fail:{provider}/{model}:{reason}",
            source="Modell-Verwaltung (ai_providers)",
            meta={"role": role_label, "provider": provider, "model": model,
                  "reason": reason_text, "detail": str(detail)[:200],
                  "fallback": fallback})
    except Exception as e:
        logger.warning(f"notify_model_failure failed: {e}")


def _de_num(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


async def notify_token_spike(role: str, today_tokens: int, baseline: int, days: int):
    """Token-Kosten-Wächter: Website-Glocke + Telegram (Toggle `token_alert`),
    max. 1× pro Rolle und Tag."""
    try:
        from core import state
        db = state.db
        if db is None:
            return
        role_label = ROLE_LABELS.get(role, role)
        title = f"Hoher Token-Verbrauch: {role_label}"
        if baseline > 0:
            msg = (f"Heute bereits ~{_de_num(today_tokens)} Tokens – das "
                   f"{today_tokens / baseline:.1f}-fache des Schnitts der letzten "
                   f"{days} Tage (~{_de_num(baseline)}/Tag). Modell/Intervall der "
                   f"Rolle im KI-Team prüfen.")
        else:
            msg = (f"Heute bereits ~{_de_num(today_tokens)} Tokens (noch keine "
                   f"Vergleichstage). Modell/Intervall der Rolle im KI-Team prüfen.")
        day = datetime.now(timezone.utc).date().isoformat()
        await website_notify(db, "token_alert", title, msg, cooldown_min=1440,
                             dedupe_key=f"web:token:{role}:{day}",
                             source="Token-Kosten-Wächter",
                             meta={"role": role_label,
                                   "today_tokens": int(today_tokens),
                                   "baseline": int(baseline)})
        await telegram_notify(db, state.telegram, "token_alert",
                              f"📈⚠️ *{title}*\n{msg}", cooldown_min=1440,
                              dedupe_key=f"tg:token:{role}:{day}")
    except Exception as e:
        logger.warning(f"notify_token_spike failed: {e}")
