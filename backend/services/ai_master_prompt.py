"""MasterPrompt des Traders – das oberste Gebot für ALLE KI-Rollen.

Nur der Trader (Admin) darf ihn ändern; keine KI-Rolle kann ihn überschreiben.
Der Text wird jedem KI-Prompt als erster Block vorangestellt, die optionalen
"harten Regeln" werden zusätzlich MASCHINELL erzwungen:

  * `check_trade_rules`   – vor jedem KI-Signal / KI-Custom-Trade,
  * `check_change_rules`  – vor jeder Einstellungs-Änderung der KI,
  * `check_lesson_rules`  – vor dem Speichern einer KI-Lektion.

Damit kann keine Lektion und kein Trade gegen die Vorgaben des Traders laufen.
Die reinen Prüf-Funktionen sind bewusst modul-global (ohne DB) und deshalb
direkt testbar.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DOC_ID = "ai_master_prompt"

DEFAULT_TEXT = (
    "1. Kapitalschutz vor Rendite: Kein Trade ohne klaren, begründeten Vorteil – im Zweifel HOLD.\n"
    "2. Der investierte Betrag (max_capital) und der Paper/Live-Modus werden ausschließlich vom "
    "Trader festgelegt.\n"
    "3. Keine Trades gegen einen klar laufenden höheren Trend ohne ausdrücklichen Anlass.\n"
    "4. Bei wichtigen News/Wirtschaftsdaten mit hoher Relevanz: defensiv agieren.\n"
    "5. Neue, noch nicht validierte Strategien zuerst als Ghost-/Paper-Trades testen."
)

DEFAULT_RULES: Dict = {
    "max_leverage": 25,          # 0 = keine Obergrenze
    "min_confidence": 0,         # zusätzliche Mindest-Konfidenz (0 = aus)
    "allowed_sides": ["LONG", "SHORT"],
    "blocked_symbols": [],       # z.B. ["DOGEUSDT"]
    "max_open_trades": 0,        # 0 = unbegrenzt (gilt für KI-Trades)
    "require_live_approval": True,   # neue KI-Strategien nur nach Freigabe live
}

RULE_LABELS = {
    "max_leverage": "Max. Hebel",
    "min_confidence": "Mindest-Konfidenz",
    "allowed_sides": "Erlaubte Richtungen",
    "blocked_symbols": "Gesperrte Coins",
    "max_open_trades": "Max. offene KI-Trades",
    "require_live_approval": "Live erst nach Freigabe",
}

# Erkennt Hebel-Angaben in Lektionen ("Hebel 40x", "leverage 40")
_LEV_RE = re.compile(r"(?:hebel|leverage)\D{0,12}(\d{1,3})", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_rules(raw: Optional[Dict]) -> Dict:
    """Rohes Regel-Dict auf das erlaubte Schema bringen (rein, testbar)."""
    rules = dict(DEFAULT_RULES)
    for key, lo, hi in (("max_leverage", 0, 125), ("min_confidence", 0, 100),
                        ("max_open_trades", 0, 50)):
        if raw and key in raw:
            try:
                rules[key] = max(lo, min(hi, int(float(raw[key]))))
            except (TypeError, ValueError):
                pass
    if raw and isinstance(raw.get("allowed_sides"), list):
        sides = [str(s).upper() for s in raw["allowed_sides"] if str(s).upper() in ("LONG", "SHORT")]
        rules["allowed_sides"] = sides or ["LONG", "SHORT"]
    if raw and isinstance(raw.get("blocked_symbols"), list):
        rules["blocked_symbols"] = [str(s).upper().strip() for s in raw["blocked_symbols"]
                                    if str(s).strip()][:40]
    if raw and "require_live_approval" in raw:
        rules["require_live_approval"] = bool(raw["require_live_approval"])
    return rules


def check_trade_rules(rules: Dict, symbol: str, side: str,
                      confidence: Optional[float] = None,
                      leverage: Optional[float] = None,
                      open_trades: Optional[int] = None) -> Tuple[bool, str]:
    """Harte MasterPrompt-Regeln vor einem KI-Trade prüfen (rein, testbar)."""
    r = normalize_rules(rules)
    sym = str(symbol or "").upper()
    if sym and sym in r["blocked_symbols"]:
        return False, f"MasterPrompt: {sym} ist gesperrt"
    s = str(side or "").upper()
    if s in ("LONG", "SHORT") and s not in r["allowed_sides"]:
        return False, f"MasterPrompt: {s}-Trades sind nicht erlaubt"
    if confidence is not None and r["min_confidence"] and float(confidence) < r["min_confidence"]:
        return False, (f"MasterPrompt: Konfidenz {confidence}% unter Mindestwert "
                       f"{r['min_confidence']}%")
    if leverage is not None and r["max_leverage"] and float(leverage) > r["max_leverage"]:
        return False, f"MasterPrompt: Hebel {leverage}x über Obergrenze {r['max_leverage']}x"
    if open_trades is not None and r["max_open_trades"] and int(open_trades) >= r["max_open_trades"]:
        return False, (f"MasterPrompt: bereits {open_trades} offene KI-Trades "
                       f"(max. {r['max_open_trades']})")
    return True, ""


def check_change_rules(rules: Dict, changes: Dict) -> Tuple[bool, str]:
    """Einstellungs-Änderung der KI gegen den MasterPrompt prüfen."""
    r = normalize_rules(rules)
    max_lev = r["max_leverage"]
    if max_lev:
        for key in ("leverage", "auto_lev_max"):
            if key in (changes or {}):
                try:
                    if float(changes[key]) > max_lev:
                        return False, (f"MasterPrompt: {key}={changes[key]} über "
                                       f"Hebel-Obergrenze {max_lev}x")
                except (TypeError, ValueError):
                    continue
    if r["min_confidence"] and "min_confidence" in (changes or {}):
        try:
            if float(changes["min_confidence"]) < r["min_confidence"]:
                return False, (f"MasterPrompt: min_confidence darf nicht unter "
                               f"{r['min_confidence']}% fallen")
        except (TypeError, ValueError):
            pass
    return True, ""


def check_lesson_rules(rules: Dict, title: str, detail: str) -> Tuple[bool, str]:
    """Lektion gegen den MasterPrompt prüfen (z.B. Hebel-Empfehlung über Limit)."""
    r = normalize_rules(rules)
    text = f"{title or ''} {detail or ''}"
    if r["max_leverage"]:
        for m in _LEV_RE.finditer(text):
            try:
                if int(m.group(1)) > r["max_leverage"]:
                    return False, (f"MasterPrompt: Lektion empfiehlt Hebel {m.group(1)}x "
                                   f"über Obergrenze {r['max_leverage']}x")
            except ValueError:
                continue
    for sym in r["blocked_symbols"]:
        base = sym.replace("USDT", "")
        if base and re.search(rf"\b{re.escape(base)}\b.{{0,40}}\b(long|short|traden|kaufen)\b",
                              text, re.IGNORECASE):
            return False, f"MasterPrompt: {sym} ist gesperrt, Lektion widerspricht dem"
    return True, ""


def rules_text(rules: Dict) -> str:
    r = normalize_rules(rules)
    parts = [
        f"Max. Hebel: {r['max_leverage']}x" if r["max_leverage"] else "Max. Hebel: keine Vorgabe",
        f"Erlaubte Richtungen: {', '.join(r['allowed_sides'])}",
    ]
    if r["min_confidence"]:
        parts.append(f"Mindest-Konfidenz: {r['min_confidence']}%")
    if r["blocked_symbols"]:
        parts.append("Gesperrte Coins: " + ", ".join(r["blocked_symbols"]))
    if r["max_open_trades"]:
        parts.append(f"Max. offene KI-Trades: {r['max_open_trades']}")
    parts.append("Neue KI-Strategien live: "
                 + ("nur nach Freigabe des Traders" if r["require_live_approval"]
                    else "nach bestandener Ghost-/Paper-Phase automatisch"))
    return " | ".join(parts)


class MasterPromptStore:
    """Persistenz + Prompt-Block des MasterPrompts."""

    def __init__(self):
        self.db = None
        self.text: str = DEFAULT_TEXT
        self.rules: Dict = dict(DEFAULT_RULES)
        self.version: int = 1
        self.updated_at: Optional[str] = None

    def setup(self, db):
        self.db = db

    async def load(self) -> Dict:
        if self.db is None:
            return self.snapshot()
        try:
            doc = await self.db.settings.find_one({"_id": DOC_ID})
        except Exception as e:
            logger.warning(f"MasterPrompt laden fehlgeschlagen: {e}")
            return self.snapshot()
        if not doc:
            await self.db.settings.update_one(
                {"_id": DOC_ID},
                {"$set": {"text": self.text, "rules": self.rules, "version": 1,
                          "updated_at": _now_iso()}}, upsert=True)
            return self.snapshot()
        self.text = str(doc.get("text") or DEFAULT_TEXT)
        self.rules = normalize_rules(doc.get("rules"))
        self.version = int(doc.get("version") or 1)
        self.updated_at = doc.get("updated_at")
        return self.snapshot()

    async def save(self, text: Optional[str] = None, rules: Optional[Dict] = None,
                   editor: str = "trader") -> Dict:
        """Nur der Trader speichert hier – KI-Rollen haben keinen Schreibpfad."""
        history_entry = {"text": self.text, "rules": dict(self.rules),
                         "version": self.version, "replaced_at": _now_iso()}
        if text is not None:
            self.text = str(text)[:8000]
        if rules is not None:
            self.rules = normalize_rules(rules)
        self.version += 1
        self.updated_at = _now_iso()
        await self.db.settings.update_one(
            {"_id": DOC_ID},
            {"$set": {"text": self.text, "rules": self.rules, "version": self.version,
                      "updated_at": self.updated_at, "editor": editor},
             "$push": {"history": {"$each": [history_entry], "$slice": -20}}},
            upsert=True)
        logger.info(f"MasterPrompt gespeichert (v{self.version}, {editor})")
        return self.snapshot()

    async def history(self, limit: int = 10) -> List[Dict]:
        doc = await self.db.settings.find_one({"_id": DOC_ID}) or {}
        return list(reversed(doc.get("history") or []))[:limit]

    # ---------------- Prompt-Injektion ----------------
    def prompt_block(self) -> str:
        return (
            "=== MASTERPROMPT DES TRADERS (OBERSTES GEBOT – NICHT VERHANDELBAR) ===\n"
            f"(Version {self.version}, zuletzt geändert {str(self.updated_at or '')[:16]}; "
            "nur der Trader darf ihn ändern – du NICHT.)\n"
            f"{self.text}\n"
            f"HARTE REGELN (werden technisch erzwungen): {rules_text(self.rules)}\n"
            "Diese Vorgaben stehen ÜBER allem: über gelernten Lektionen, über "
            "Empfehlungen anderer KI-Rollen, über deinen eigenen Analysen. Widersprechende "
            "Lektionen, Einstellungs-Vorschläge und Trades werden automatisch blockiert – "
            "erzeuge sie nicht."
        )

    # ---------------- Prüfungen (mit aktuellem Regelstand) ----------------
    def check_trade(self, symbol: str, side: str, confidence=None, leverage=None,
                    open_trades=None) -> Tuple[bool, str]:
        return check_trade_rules(self.rules, symbol, side, confidence, leverage, open_trades)

    def check_changes(self, changes: Dict) -> Tuple[bool, str]:
        return check_change_rules(self.rules, changes)

    def check_lesson(self, title: str, detail: str) -> Tuple[bool, str]:
        return check_lesson_rules(self.rules, title, detail)

    def snapshot(self) -> Dict:
        return {"text": self.text, "rules": dict(self.rules), "version": self.version,
                "updated_at": self.updated_at, "rule_labels": RULE_LABELS,
                "defaults": {"text": DEFAULT_TEXT, "rules": dict(DEFAULT_RULES)}}


master_prompt = MasterPromptStore()
