"""API: Website-Benachrichtigungen, Telegram-Meldungs-Toggles, Kill-Switch/Anti-Stacking."""
from typing import Dict

from fastapi import APIRouter, Depends

from core import state
from core.auth import require_admin
from services import notifications, trade_guard

router = APIRouter(tags=["notify"])


@router.get("/api/notifications")
async def get_notifications(unread_only: bool = True, limit: int = 50):
    q = {"read": False} if unread_only else {}
    rows = await state.db.app_notifications.find(q, {"_id": 0}) \
        .sort("created_at", -1).to_list(max(1, min(limit, 200)))
    return {"notifications": rows}


@router.post("/api/notifications/popped")
async def mark_notifications_popped(body: Dict = None):
    """Popup wurde gezeigt -> nie wieder aufpoppen (bleibt in der Glocke lesbar)."""
    ids = (body or {}).get("ids") or []
    if not ids:
        return {"status": "ok", "updated": 0}
    res = await state.db.app_notifications.update_many(
        {"id": {"$in": ids}}, {"$set": {"popped": True}})
    return {"status": "ok", "updated": res.modified_count}


@router.post("/api/notifications")
async def add_notification(body: Dict = None):
    """Von der UI genutzt: unterdrückte doppelte Fehler-Popups landen hier,
    damit sie in der Glocke nachlesbar bleiben (Toast-Dedupe)."""
    body = body or {}
    msg = str(body.get("message") or "").strip()[:400]
    if not msg:
        return {"status": "ignored"}
    await notifications.website_notify(
        state.db, str(body.get("kind") or "error")[:20],
        str(body.get("title") or "Fehler")[:80], msg)
    return {"status": "ok"}


@router.post("/api/notifications/read")
async def mark_notifications_read(body: Dict = None):
    ids = (body or {}).get("ids")
    q = {"id": {"$in": ids}} if ids else {}
    res = await state.db.app_notifications.update_many(q, {"$set": {"read": True}})
    return {"status": "ok", "updated": res.modified_count}


@router.get("/api/telegram/notify-config")
async def get_telegram_notify_config():
    return await notifications.get_config(state.db)


@router.post("/api/telegram/notify-config")
async def set_telegram_notify_config(body: Dict, _: bool = Depends(require_admin)):
    return await notifications.update_config(state.db, body)


@router.get("/api/trade-guard")
async def get_trade_guard():
    return {"config": await trade_guard.get_config(state.db),
            "state": await trade_guard.get_state(state.db)}


@router.post("/api/trade-guard/config")
async def set_trade_guard_config(body: Dict, _: bool = Depends(require_admin)):
    return {"config": await trade_guard.update_config(state.db, body),
            "state": await trade_guard.get_state(state.db)}


@router.post("/api/trade-guard/resume")
async def resume_trade_guard(_: bool = Depends(require_admin)):
    return {"state": await trade_guard.resume(state.db)}
