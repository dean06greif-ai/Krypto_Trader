"""Lektionen-Speicher des KI Traders (settings/ai_lessons.lessons).

Bisher konnte nur die KI selbst Lektionen schreiben. Jetzt gilt:
  * Der Trader kann Lektionen anlegen, bearbeiten (Stift) und löschen.
  * Vom Trader angelegte/bearbeitete Lektionen sind `locked` – die KI darf sie
    weder überschreiben noch verwerfen; sie sieht die Markierung im Prompt und
    weiß dadurch, dass der Trader eingegriffen hat.
  * Lektionen, die dem MasterPrompt widersprechen, werden nicht gespeichert.

Alle Funktionen sind bewusst schlank und ohne Seiteneffekte auf den Engine-State,
damit sie sowohl vom Lernlauf (`services/ai_learning.py`) als auch von den
Endpunkten (`routers/ai_governance.py`) genutzt werden können.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DOC_ID = "ai_lessons"
WEIGHT_LABELS = {1: "basis", 2: "mittel", 3: "hoch", 4: "sehr hoch"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(lesson: Dict) -> Dict:
    """Lektion auf das aktuelle Schema bringen (Altbestand hat keine id/origin)."""
    out = dict(lesson or {})
    out.setdefault("id", f"les_{uuid.uuid4().hex[:10]}")
    out["title"] = str(out.get("title", ""))[:120]
    out["detail"] = str(out.get("detail", ""))[:600]
    try:
        out["weight"] = int(out.get("weight", 2) or 2)
    except (TypeError, ValueError):
        out["weight"] = 2
    out.setdefault("weight_label", WEIGHT_LABELS.get(out["weight"], "mittel"))
    out.setdefault("origin", "ai")
    out["locked"] = bool(out.get("locked"))
    out.setdefault("updated_at", _now_iso())
    return out


def normalize_all(lessons) -> List[Dict]:
    return [normalize(l) for l in (lessons or []) if isinstance(l, dict) and l.get("title")]


_STOPWORDS = {
    "der", "die", "das", "und", "oder", "im", "in", "bei", "beim", "mit",
    "von", "für", "auf", "zu", "zur", "zum", "den", "dem", "des", "ein",
    "eine", "einer", "einem", "am", "an", "als", "ist", "sind", "wird",
    "werden", "über", "unter", "nach", "vor", "aus", "bis", "es", "sich",
    "auch", "noch", "sehr", "wenn", "dann", "wie", "the", "and", "of",
}


def _tokens(text: str):
    import re
    return {w for w in re.findall(r"[a-zä-üß0-9]+", str(text).lower())
            if w not in _STOPWORDS}


def _similar(a: Dict, b: Dict) -> bool:
    """Nahezu identische Titel erkennen (Wort-Überschneidung)."""
    ta, tb = _tokens(a.get("title", "")), _tokens(b.get("title", ""))
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    if inter < 2:
        return False
    if inter / len(ta | tb) >= 0.65:
        return True
    # Titel-Enthaltensein: 'SOL Shorts meiden' vs 'SOL Shorts meiden im Uptrend'
    return inter == min(len(ta), len(tb)) and inter >= 3


def _validation_rank(l: Dict):
    """Sortierschlüssel: am besten validiert zuerst
    (vom Trader gesperrt > Bestätigungen > Modell-Gewicht > Aktualität)."""
    return (1 if l.get("locked") else 0,
            int(l.get("confirmations", 0) or 0),
            int(l.get("weight", 2) or 2),
            str(l.get("updated_at", "")))


def dedupe_lessons(lessons: List[Dict]):
    """Doppelte/nahezu identische Lektionen zusammenführen.

    Bug-Report: manche Lektionen waren doppelt bzw. widersprachen sich.
    Es bleibt nur die am besten VALIDIERTE Version (locked > Bestätigungen >
    Gewicht > Aktualität); vom Trader gesperrte Lektionen werden nie entfernt.
    Rückgabe: (bereinigte Liste, Liste der entfernten Duplikate)."""
    ordered = sorted(normalize_all(lessons), key=_validation_rank, reverse=True)
    kept: List[Dict] = []
    dropped: List[Dict] = []
    for l in ordered:
        dup = next((k for k in kept if _similar(k, l)), None)
        if dup is None or l.get("locked"):
            kept.append(l)
        else:
            dropped.append({"title": l.get("title"), "kept": dup.get("title")})
    return kept, dropped


def merge_lessons(old: List[Dict], new: List[Dict], removed: List[str],
                  max_lessons: int) -> List[Dict]:
    """Lernlauf-Ergebnis mit dem Wissensstand zusammenführen.

    Bestehende Lektionen bleiben erhalten, solange die KI sie nicht ausdrücklich
    verwirft. NEU: `locked` (vom Trader angelegte/bearbeitete) Lektionen sind
    unantastbar – sie können von der KI weder ersetzt noch entfernt werden und
    zählen nicht gegen das Limit weg.
    """
    old_n = normalize_all(old)
    locked = [l for l in old_n if l.get("locked")]
    locked_titles = {l["title"].strip().lower() for l in locked}
    drop = {str(t).strip().lower() for t in (removed or []) if str(t).strip()}
    drop -= locked_titles

    merged: List[Dict] = []
    seen = set(locked_titles)
    for lesson in normalize_all(new) + old_n:
        if lesson.get("locked"):
            continue
        key = lesson["title"].strip().lower()
        if not key or key in seen or key in drop:
            continue
        seen.add(key)
        merged.append(lesson)
    merged.sort(key=lambda l: (int(l.get("weight", 2) or 2), str(l.get("updated_at", ""))),
                reverse=True)
    deduped, dropped = dedupe_lessons(locked + merged)
    if dropped:
        logger.info("Lektionen-Dedupe: " + "; ".join(
            f"'{d['title']}' entfernt (behalten: '{d['kept']}')" for d in dropped[:5]))
    locked_out = [l for l in deduped if l.get("locked")]
    ai_out = [l for l in deduped if not l.get("locked")]
    limit = max(1, int(max_lessons))
    return locked_out + ai_out[:limit]


def lessons_text(lessons: List[Dict]) -> str:
    """Prompt-Block: Lektionen inkl. Herkunfts-Markierung."""
    if not lessons:
        return "(noch keine Lektionen – zu wenige abgeschlossene Ergebnisse)"
    ordered = sorted(normalize_all(lessons),
                     key=lambda l: (1 if l.get("locked") else 0, int(l.get("weight", 2))),
                     reverse=True)
    out = []
    for i, l in enumerate(ordered):
        label = WEIGHT_LABELS.get(int(l.get("weight", 2)), "mittel")
        if l.get("locked"):
            mark = ("[VOM TRADER FESTGELEGT/ANGEPASST – unveränderlich, befolgen]"
                    if l.get("origin") != "user" else "[VOM TRADER SELBST GESCHRIEBEN – befolgen]")
        else:
            mark = f"[Gewicht: {label}]"
        out.append(f"{i + 1}. {mark} {l.get('title')}: {l.get('detail')}")
    return "\n".join(out)


class LessonStore:
    """CRUD auf settings/ai_lessons – gemeinsame Quelle für UI und Lernlauf."""

    def __init__(self):
        self.db = None

    def setup(self, db):
        self.db = db

    async def _doc(self) -> Dict:
        return await self.db.settings.find_one({"_id": DOC_ID}) or {}

    async def all(self) -> List[Dict]:
        return normalize_all((await self._doc()).get("lessons"))

    async def save_all(self, lessons: List[Dict]) -> List[Dict]:
        lessons = normalize_all(lessons)
        await self.db.settings.update_one(
            {"_id": DOC_ID}, {"$set": {"lessons": lessons, "updated_at": _now_iso()}},
            upsert=True)
        return lessons

    async def create(self, title: str, detail: str, weight: int = 3) -> Dict:
        lesson = normalize({
            "id": f"les_{uuid.uuid4().hex[:10]}", "title": title, "detail": detail,
            "weight": max(1, min(4, int(weight or 3))), "origin": "user", "locked": True,
            "updated_at": _now_iso(), "user_edited_at": _now_iso(),
        })
        lessons = await self.all()
        lessons.insert(0, lesson)
        await self.save_all(lessons)
        return lesson

    async def update(self, lesson_id: str, fields: Dict) -> Optional[Dict]:
        lessons = await self.all()
        found = None
        for l in lessons:
            if l.get("id") != lesson_id:
                continue
            if "title" in fields and str(fields["title"]).strip():
                l["title"] = str(fields["title"])[:120]
            if "detail" in fields:
                l["detail"] = str(fields["detail"])[:600]
            if "weight" in fields:
                try:
                    l["weight"] = max(1, min(4, int(fields["weight"])))
                    l["weight_label"] = WEIGHT_LABELS.get(l["weight"], "mittel")
                except (TypeError, ValueError):
                    pass
            if "locked" in fields:
                l["locked"] = bool(fields["locked"])
            else:
                l["locked"] = True
            l["origin"] = "user_edited" if l.get("origin") == "ai" else l.get("origin", "user")
            l["user_edited_at"] = _now_iso()
            l["updated_at"] = _now_iso()
            found = l
            break
        if not found:
            return None
        await self.save_all(lessons)
        return found

    async def delete(self, lesson_id: str) -> bool:
        lessons = await self.all()
        rest = [l for l in lessons if l.get("id") != lesson_id]
        if len(rest) == len(lessons):
            return False
        await self.save_all(rest)
        return True

    async def audit_against_master(self) -> Dict:
        """ALLE gespeicherten Lektionen gegen den MasterPrompt prüfen.

        Der MasterPrompt steht über allem (Vorgabe des Traders): Lektionen,
        die ihm widersprechen, werden GELÖSCHT – auch gesperrte/vom Trader
        angelegte (der Trader hat den MasterPrompt bewusst geändert).
        Wird nach jeder MasterPrompt-Änderung und vor jedem Lernlauf ausgeführt."""
        from services.ai_master_prompt import master_prompt
        lessons = await self.all()
        kept, removed = [], []
        for l in lessons:
            ok, why = master_prompt.check_lesson(l.get("title", ""), l.get("detail", ""))
            if ok:
                kept.append(l)
            else:
                removed.append({"id": l.get("id"), "title": l.get("title"),
                                "locked": bool(l.get("locked")), "why": why})
        # Zusätzlich: Doppelungen bereinigen – nur die am besten validierte
        # Version einer Lektion bleibt (Bug-Report: doppelte/widersprüchliche
        # Lektionen im Bestand).
        kept, dropped = dedupe_lessons(kept)
        for d in dropped:
            removed.append({"id": None, "title": d["title"], "locked": False,
                            "why": f"Doppelung – besser validierte Version bleibt: "
                                   f"'{d['kept']}'"})
        if removed:
            await self.save_all(kept)
            logger.info(f"Lektionen-Audit: {len(removed)} Lektionen entfernt "
                        f"(MasterPrompt-Verstoß oder Doppelung): "
                        f"{[r['title'] for r in removed]}")
        return {"checked": len(lessons), "removed": removed}


lesson_store = LessonStore()
