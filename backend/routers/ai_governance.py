"""Governance-Endpunkte des KI Traders.

Bündelt die Bereiche, in denen der TRADER das letzte Wort hat:
  * MasterPrompt (oberstes Gebot, nur Admin änderbar)
  * Lektionen (anlegen / bearbeiten / löschen – bleiben danach KI-geschützt)
  * Daten-Validierung der KI-Änderungen (Schwellen)
  * Strategie-Labor (Ghost-Phase, Freigabe neuer KI-Strategien)

Bewusst als eigener Router, damit `routers/ai.py` und `routers/ai_lab.py`
unverändert bleiben. GET öffentlich (wie im Rest der App), Schreiben nur Admin.
"""
import asyncio
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_admin
from services.ai_engine import ai_engine
from services.ai_lessons import lesson_store
from services.ai_master_prompt import master_prompt
from services.ai_strategy_lab import strategy_lab
from services.ai_validation import validation_gate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai-governance"])


def _opinion(topic: str, detail: str):
    """KI im Hintergrund um ihre Meinung zu einer Trader-Änderung bitten."""
    try:
        asyncio.create_task(ai_engine.comment_on_user_change(topic, detail))
    except Exception as e:
        logger.warning(f"KI-Meinung konnte nicht angefragt werden: {e}")


# ---------------- MasterPrompt ----------------
@router.get("/api/ai/master-prompt")
async def get_master_prompt(history: bool = False):
    out = {"master_prompt": master_prompt.snapshot()}
    if history:
        out["history"] = await master_prompt.history()
    return out


@router.post("/api/ai/master-prompt")
async def set_master_prompt(body: Dict, _: bool = Depends(require_admin)):
    if "text" not in body and "rules" not in body:
        raise HTTPException(status_code=400, detail="text oder rules erforderlich")
    snap = await master_prompt.save(text=body.get("text"), rules=body.get("rules"))
    _opinion("MasterPrompt", f"Neuer MasterPrompt (v{snap['version']}):\n{snap['text']}\n"
                             f"Harte Regeln: {snap['rules']}")
    return {"status": "success", "master_prompt": snap}


# ---------------- Lektionen ----------------
@router.get("/api/ai/lessons")
async def list_lessons():
    return {"lessons": await lesson_store.all()}


@router.post("/api/ai/lessons")
async def create_lesson(body: Dict, _: bool = Depends(require_admin)):
    title = str(body.get("title") or "").strip()
    detail = str(body.get("detail") or "").strip()
    if not title or not detail:
        raise HTTPException(status_code=400, detail="title und detail erforderlich")
    lesson = await lesson_store.create(title, detail, weight=body.get("weight", 3))
    if ai_engine.learning:
        ai_engine.learning.invalidate_lessons()
    _opinion("Neue Lektion des Traders", f"{title}: {detail}")
    return {"status": "success", "lesson": lesson}


@router.patch("/api/ai/lessons/{lesson_id}")
async def update_lesson(lesson_id: str, body: Dict, _: bool = Depends(require_admin)):
    lesson = await lesson_store.update(lesson_id, body)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lektion nicht gefunden")
    if ai_engine.learning:
        ai_engine.learning.invalidate_lessons()
    _opinion("Vom Trader bearbeitete Lektion",
             f"{lesson['title']}: {lesson['detail']} (ist jetzt für dich unveränderlich)")
    return {"status": "success", "lesson": lesson}


@router.delete("/api/ai/lessons/{lesson_id}")
async def delete_lesson(lesson_id: str, _: bool = Depends(require_admin)):
    ok = await lesson_store.delete(lesson_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lektion nicht gefunden")
    if ai_engine.learning:
        ai_engine.learning.invalidate_lessons()
    _opinion("Vom Trader gelöschte Lektion", f"Lektion {lesson_id} wurde entfernt")
    return {"status": "success"}


# ---------------- Daten-Validierung ----------------
@router.get("/api/ai/validation")
async def get_validation():
    return validation_gate.status()


@router.post("/api/ai/validation")
async def set_validation(updates: Dict, _: bool = Depends(require_admin)):
    return {"status": "success", "settings": await validation_gate.update(updates)}


# ---------------- Strategie-Labor ----------------
@router.get("/api/ai/strategies")
async def list_candidates(include_rejected: bool = True):
    return {"candidates": await strategy_lab.list_candidates(include_rejected),
            "status": strategy_lab.status()}


@router.post("/api/ai/strategies")
async def create_candidate(body: Dict, _: bool = Depends(require_admin)):
    res = await strategy_lab.create_candidate(body, source=str(body.get("source") or "trader"))
    if res.get("status") != "ok":
        raise HTTPException(status_code=400, detail=res.get("detail"))
    _opinion("Neue Strategie-Vorgabe des Traders",
             f"{res['candidate']['name']}: {res['candidate']['thesis']} "
             f"Regeln: {res['candidate']['rules_text']}")
    return {"status": "success", **res}


@router.post("/api/ai/strategies/settings")
async def candidate_settings(updates: Dict, _: bool = Depends(require_admin)):
    return {"status": "success", "settings": await strategy_lab.update_settings(updates)}


@router.post("/api/ai/strategies/{cid}/decide")
async def decide_candidate(cid: str, body: Dict, _: bool = Depends(require_admin)):
    res = await strategy_lab.decide(cid, str(body.get("action") or ""),
                                    note=str(body.get("note") or ""))
    if res.get("status") != "ok":
        raise HTTPException(status_code=400, detail=res.get("detail"))
    return {"status": "success", **res}


@router.post("/api/ai/strategies/{cid}/register-test")
async def register_candidate_for_test(cid: str, _: bool = Depends(require_admin)):
    res = await strategy_lab.register_for_testing(cid)
    if res.get("status") == "error":
        raise HTTPException(status_code=404, detail=res.get("detail"))
    return res


@router.get("/api/ai/strategies/ghost-trades")
async def ghost_trades(candidate_id: Optional[str] = None, limit: int = 50):
    return {"ghost_trades": await strategy_lab.ghost_trades(candidate_id, limit)}
