"""KI-Rollen-Verwaltung ("KI-Team").

Das KI-Ökosystem besteht aus spezialisierten Rollen, die zusammenarbeiten:
  - analyst      : regelmäßige Markt-Analysen (bestehender Analyse-Loop)
  - deep_analyst : sehr tiefe Analysen zu konfigurierbaren Uhrzeiten
  - news_watcher : überwacht News + Wirtschaftskalender 24/7
  - chat         : beantwortet User-Anfragen im KI-Chat
  - learner      : Lernläufe (Lektionen aus echten Ergebnissen)
  - summarizer   : Tages-Zusammenfassung um Mitternacht

Jede Rolle kann ein eigenes Modell, aktive Handelszeiten (Europe/Berlin) und
ein Fallback-Modell haben. Ohne eigene Konfiguration erbt die Rolle das
Haupt-Modell der Engine (=> volle Rückwärtskompatibilität).
"""
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from services import ai_providers

logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

ROLE_LABELS = {
    "analyst": "Analyst – regelmäßige Analysen",
    "deep_analyst": "Tiefen-Analyst – geplante Deep-Analysen",
    "news_watcher": "News-Wächter – News + Wirtschaftskalender 24/7",
    "chat": "Chat-Assistent – User-Anfragen",
    "learner": "Lern-Modul – Lektionen aus Ergebnissen",
    "summarizer": "Tages-Reporter – Mitternachts-Zusammenfassung",
}

# Basis-Felder jeder Rolle. provider/model = None => erbt Haupt-Modell.
_BASE_ROLE = {
    "enabled": True,
    "provider": None,
    "model": None,
    "active_hours": None,           # {"start": "08:00", "end": "22:00"} Berlin oder None (=immer)
    "fallback_provider": None,      # greift außerhalb active_hours oder wenn Primär komplett scheitert
    "fallback_model": None,
}

DEFAULT_ROLES_CONFIG: Dict[str, Dict] = {
    "analyst": dict(_BASE_ROLE),
    "deep_analyst": {**_BASE_ROLE, "schedule_times": ["08:00", "20:00"]},
    "news_watcher": {**_BASE_ROLE, "interval_min": 15, "auto_analysis": True},
    "chat": dict(_BASE_ROLE),
    "learner": dict(_BASE_ROLE),
    "summarizer": dict(_BASE_ROLE),
}

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _valid_time(t) -> bool:
    return isinstance(t, str) and bool(_TIME_RE.match(t))


def _minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def in_active_hours(active_hours: Optional[Dict], now: Optional[datetime] = None) -> bool:
    """True, wenn `now` (Berlin) im Fenster liegt. Fenster über Mitternacht erlaubt."""
    if not active_hours:
        return True
    start, end = active_hours.get("start"), active_hours.get("end")
    if not (_valid_time(start) and _valid_time(end)):
        return True
    now = now or datetime.now(BERLIN_TZ)
    cur = now.hour * 60 + now.minute
    s, e = _minutes(start), _minutes(end)
    if s == e:
        return True
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e  # über Mitternacht


class AIRoleManager:
    def __init__(self):
        self.config: Dict[str, Dict] = {r: dict(c) for r, c in DEFAULT_ROLES_CONFIG.items()}

    async def load(self, db):
        try:
            doc = await db.settings.find_one({"_id": "ai_roles_config"})
            if doc:
                doc.pop("_id", None)
                for role, cfg in doc.items():
                    if role in self.config and isinstance(cfg, dict):
                        self.config[role].update(self._sanitize(role, cfg))
        except Exception as e:
            logger.warning(f"AI roles load failed: {e}")

    def _sanitize(self, role: str, updates: Dict) -> Dict:
        out = {}
        if "enabled" in updates:
            out["enabled"] = bool(updates["enabled"])
        prov, mod = updates.get("provider"), updates.get("model")
        if prov is None and mod is None and ("provider" in updates or "model" in updates):
            out["provider"] = None
            out["model"] = None
        elif mod:
            p = prov if prov in ai_providers.ALLOWED_MODELS else ai_providers.provider_for_model(mod)
            if p and mod in ai_providers.ALLOWED_MODELS[p]:
                out["provider"], out["model"] = p, mod
        if "active_hours" in updates:
            ah = updates["active_hours"]
            if ah and isinstance(ah, dict) and _valid_time(ah.get("start")) and _valid_time(ah.get("end")):
                out["active_hours"] = {"start": ah["start"], "end": ah["end"]}
            else:
                out["active_hours"] = None
        fp, fm = updates.get("fallback_provider"), updates.get("fallback_model")
        if "fallback_model" in updates:
            if fm:
                p = fp if fp in ai_providers.ALLOWED_MODELS else ai_providers.provider_for_model(fm)
                if p and fm in ai_providers.ALLOWED_MODELS[p]:
                    out["fallback_provider"], out["fallback_model"] = p, fm
            else:
                out["fallback_provider"] = None
                out["fallback_model"] = None
        if role == "deep_analyst" and "schedule_times" in updates:
            times = [t for t in (updates["schedule_times"] or []) if _valid_time(t)]
            out["schedule_times"] = sorted(set(times))[:6]
        if role == "news_watcher":
            if "interval_min" in updates:
                try:
                    out["interval_min"] = max(5, min(120, int(updates["interval_min"])))
                except Exception:
                    pass
            if "auto_analysis" in updates:
                out["auto_analysis"] = bool(updates["auto_analysis"])
        return out

    async def update(self, db, updates: Dict) -> Dict:
        for role, cfg in (updates or {}).items():
            if role in self.config and isinstance(cfg, dict):
                self.config[role].update(self._sanitize(role, cfg))
        await db.settings.update_one(
            {"_id": "ai_roles_config"},
            {"$set": {r: dict(c) for r, c in self.config.items()}}, upsert=True)
        return self.snapshot()

    def snapshot(self) -> Dict:
        return {r: dict(c) for r, c in self.config.items()}

    def role_cfg(self, role: str) -> Dict:
        return self.config.get(role) or dict(_BASE_ROLE)

    def chain(self, role: str, engine_cfg: Dict,
              now: Optional[datetime] = None) -> List[Tuple[str, str]]:
        """(provider, model)-Kette für eine Rolle.

        Primär = Rollen-Modell (oder Haupt-Modell der Engine) + Provider-interne
        Fallbacks. Außerhalb der aktiven Handelszeiten übernimmt direkt die
        Fallback-KI. Die Fallback-KI hängt immer als letzte Stufe an der Kette."""
        cfg = self.role_cfg(role)
        provider = cfg.get("provider") or engine_cfg.get("provider", "gemini")
        model = cfg.get("model") or (engine_cfg.get("model")
                                     if not cfg.get("provider") else None)
        primary = ai_providers.same_provider_chain(provider, model)

        fallback: List[Tuple[str, str]] = []
        if cfg.get("fallback_model"):
            fb_prov = cfg.get("fallback_provider") or \
                ai_providers.provider_for_model(cfg["fallback_model"])
            if fb_prov:
                fallback = ai_providers.same_provider_chain(fb_prov, cfg["fallback_model"])

        active = in_active_hours(cfg.get("active_hours"), now)
        chain = (fallback + primary) if (not active and fallback) else (primary + fallback)
        seen, out = set(), []
        for pm in chain:
            if pm not in seen:
                seen.add(pm)
                out.append(pm)
        return out


role_manager = AIRoleManager()
