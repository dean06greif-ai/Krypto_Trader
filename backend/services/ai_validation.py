"""Datenbasierte Freigabe (Validierung) von KI-Änderungen.

Problem vorher: die KI hat laufend Einstellungs-Vorschläge und neue Lektionen
produziert – auch ohne belastbare Datengrundlage. Jetzt gilt:

  * KI-initiierte Einstellungs-Änderungen brauchen eine Mindest-Stichprobe
    (geschlossene Trades gesamt bzw. pro Coin). Ohne sie landet der Wunsch als
    Vorschlag mit Status `needs_data` und wird NIE automatisch angewendet.
  * Neue/geänderte Lektionen der KI brauchen ebenfalls eine Mindestzahl an
    Ergebnissen, das Verwerfen bestehender Lektionen zusätzlich mehr Daten.
  * Vom Trader gewollte Änderungen laufen IMMER ohne Validierung durch – die KI
    darf dazu aber ihre Meinung sagen (`ai_engine.comment_on_user_change`).

Die Bewertungslogik ist rein und damit direkt testbar.
"""
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DOC_ID = "ai_validation"

DEFAULT_SETTINGS = {
    "enabled": True,
    "min_closed_trades": 15,      # Engine-weite Änderungen
    "min_symbol_trades": 8,       # Änderungen an einem Coin
    "min_lesson_results": 5,      # neue/geschärfte Lektion
    "min_removal_results": 12,    # bestehende Lektion verwerfen
}


def sample_size(stats: Dict, scope: str, symbol: Optional[str] = None) -> int:
    """Verfügbare Stichprobe für die Bewertung (rein, testbar)."""
    stats = stats or {}
    if scope == "coin" and symbol:
        d = (stats.get("by_symbol") or {}).get(symbol) or {}
        return int(d.get("trades") or 0)
    totals = stats.get("totals") or {}
    return int(totals.get("closed_trades") or 0)


def evaluate_change(settings: Dict, stats: Dict, scope: str,
                    symbol: Optional[str] = None) -> Dict:
    """Reicht die Datenlage für eine KI-Einstellungs-Änderung? (rein, testbar)"""
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    if not cfg.get("enabled", True):
        return {"validated": True, "sample": sample_size(stats, scope, symbol),
                "required": 0, "reason": "Validierung deaktiviert"}
    need = int(cfg["min_symbol_trades"] if scope == "coin" else cfg["min_closed_trades"])
    have = sample_size(stats, scope, symbol)
    if have >= need:
        return {"validated": True, "sample": have, "required": need,
                "reason": f"{have} geschlossene Trades als Grundlage"}
    label = f"für {symbol}" if scope == "coin" and symbol else "insgesamt"
    return {"validated": False, "sample": have, "required": need,
            "reason": f"Nur {have} geschlossene Trades {label} – "
                      f"mindestens {need} nötig, um diese Änderung zu validieren"}


def evaluate_lesson(settings: Dict, stats: Dict, removal: bool = False) -> Dict:
    """Reicht die Datenlage für eine neue Lektion bzw. deren Verwerfen?"""
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    totals = (stats or {}).get("totals") or {}
    have = int(totals.get("closed_trades") or 0) + \
        int(totals.get("signal_wins") or 0) + int(totals.get("signal_losses") or 0)
    if not cfg.get("enabled", True):
        return {"validated": True, "sample": have, "required": 0,
                "reason": "Validierung deaktiviert"}
    need = int(cfg["min_removal_results"] if removal else cfg["min_lesson_results"])
    if have >= need:
        return {"validated": True, "sample": have, "required": need,
                "reason": f"{have} ausgewertete Ergebnisse"}
    return {"validated": False, "sample": have, "required": need,
            "reason": f"Nur {have} ausgewertete Ergebnisse – mindestens {need} nötig"}


class ValidationGate:
    """Persistente Einstellungen der Validierungsschwellen."""

    def __init__(self):
        self.db = None
        self.settings: Dict = dict(DEFAULT_SETTINGS)

    def setup(self, db):
        self.db = db

    async def load(self) -> Dict:
        try:
            doc = await self.db.settings.find_one({"_id": DOC_ID})
        except Exception as e:
            logger.warning(f"Validierungs-Einstellungen laden fehlgeschlagen: {e}")
            return dict(self.settings)
        if doc:
            for k in DEFAULT_SETTINGS:
                if k in doc:
                    self.settings[k] = doc[k]
        else:
            await self.db.settings.update_one({"_id": DOC_ID}, {"$set": dict(self.settings)},
                                              upsert=True)
        return dict(self.settings)

    async def update(self, updates: Dict) -> Dict:
        if "enabled" in updates:
            self.settings["enabled"] = bool(updates["enabled"])
        for key, lo, hi in (("min_closed_trades", 0, 200), ("min_symbol_trades", 0, 100),
                            ("min_lesson_results", 0, 200), ("min_removal_results", 0, 300)):
            if key in updates:
                try:
                    self.settings[key] = max(lo, min(hi, int(updates[key])))
                except (TypeError, ValueError):
                    pass
        await self.db.settings.update_one({"_id": DOC_ID}, {"$set": dict(self.settings)},
                                          upsert=True)
        return dict(self.settings)

    def change(self, stats: Dict, scope: str, symbol: Optional[str] = None) -> Dict:
        return evaluate_change(self.settings, stats, scope, symbol)

    def lesson(self, stats: Dict, removal: bool = False) -> Dict:
        return evaluate_lesson(self.settings, stats, removal=removal)

    def prompt_block(self) -> str:
        s = self.settings
        if not s.get("enabled", True):
            return ("=== DATEN-VALIDIERUNG (AUS) ===\n"
                    "Deine Änderungen brauchen aktuell keine Mindest-Stichprobe – "
                    "bleibe trotzdem datenbasiert.")
        return (
            "=== DATEN-VALIDIERUNG (AKTIV – Pflicht) ===\n"
            f"Einstellungs-Änderungen brauchen mind. {s['min_closed_trades']} geschlossene "
            f"Trades (Engine) bzw. {s['min_symbol_trades']} pro Coin. Neue/geschärfte "
            f"Lektionen mind. {s['min_lesson_results']} ausgewertete Ergebnisse, das "
            f"Verwerfen einer Lektion mind. {s['min_removal_results']}.\n"
            "Ohne diese Datenbasis: KEINE Vorschläge, KEINE neuen Lektionen, KEIN Verwerfen – "
            "nicht validierte Wünsche werden automatisch als 'needs_data' geparkt.\n"
            "Änderungen, die der TRADER wünscht, gelten sofort und ohne Validierung. Du sollst "
            "sie aber nicht blind hinnehmen: sage ehrlich deine Meinung, wenn deine Daten "
            "dagegen sprechen."
        )

    def status(self) -> Dict:
        return {"settings": dict(self.settings)}


validation_gate = ValidationGate()
