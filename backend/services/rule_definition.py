"""Normalisierung & Validierung KI-erzeugter Regel-Definitionen.

Problem vorher: Die KI (Strategie-Labor) lieferte rule_definitions mit
Indikatoren/Operatoren, die die Custom-Strategy-Engine nicht kennt (z.B. "adx",
"crosses_above"). Solche Regeln waren im Backtester/Optimizer IMMER False –
Ergebnis: 0 Trades / 0 PnL überall ("00 0"-Bug).

Jetzt gilt: Jede KI-Definition läuft durch normalize_rule_definition().
Bekannte Alias-Schreibweisen werden auf die unterstützten Namen gemappt,
alles andere wird mit klarer Begründung entfernt. Bleibt keine gültige Regel
übrig, ist der Kandidat "nicht backtestbar" (statt still 0-Ergebnisse).

Alle Funktionen sind rein und damit direkt testbar.
"""
from typing import Dict, List, Optional, Tuple

from strategies.custom_strategy import INDICATORS, OPERATORS, PERIOD_FIELDS
from services.timeframes import TIMEFRAMES

_PERIOD_KEYS = {f["key"] for f in PERIOD_FIELDS}

# Häufige Alias-Schreibweisen der KI -> unterstützter Indikator-Name
INDICATOR_ALIASES = {
    "close": "price", "close_price": "price", "preis": "price", "kurs": "price",
    "ema": "ema_fast", "ema_short": "ema_fast", "ema_long": "ema_slow",
    "fast_ema": "ema_fast", "slow_ema": "ema_slow",
    "macd_line": "macd", "macd_signal_line": "macd_signal",
    "signal_line": "macd_signal", "macd_histogram": "macd_hist",
    "histogram": "macd_hist", "macd_h": "macd_hist",
    "bollinger_upper": "bb_upper", "bollinger_lower": "bb_lower",
    "bollinger_middle": "bb_middle", "bollinger_mid": "bb_middle",
    "bb_up": "bb_upper", "bb_low": "bb_lower", "bb_mid": "bb_middle",
    "bb_width": "bb_width_pct", "bollinger_width": "bb_width_pct",
    "stochastic_k": "stoch_k", "stochastic_d": "stoch_d", "stoch": "stoch_k",
    "volume_avg": "volume_sma", "avg_volume": "volume_sma",
    "volume_average": "volume_sma", "volume_ma": "volume_sma",
    "relative_volume": "rel_volume", "vol_ratio": "rel_volume",
    "atr_percent": "atr_pct", "atr_percentage": "atr_pct",
    "price_change": "price_change_pct", "momentum": "price_change_pct",
    "roc": "price_change_pct", "change_pct": "price_change_pct",
    "highest_high": "recent_high", "lowest_low": "recent_low",
    "swing_high": "recent_high", "swing_low": "recent_low",
    "heikin_ashi": "ha_color", "ha": "ha_color", "heikin_ashi_color": "ha_color",
}

OP_ALIASES = {
    "lt": "<", "gt": ">", "lte": "<=", "gte": ">=", "le": "<=", "ge": ">=",
    "less_than": "<", "greater_than": ">", "less": "<", "greater": ">",
    "above": ">", "below": "<", "over": ">", "under": "<",
    "crosses_above": "cross_above", "cross_up": "cross_above",
    "crossover": "cross_above", "cross_over": "cross_above",
    "crosses_below": "cross_below", "cross_down": "cross_below",
    "crossunder": "cross_below", "cross_under": "cross_below",
}


def _norm_indicator(name) -> Optional[str]:
    key = str(name or "").strip().lower()
    if key in INDICATORS:
        return key
    return INDICATOR_ALIASES.get(key)


def _norm_op(op) -> Optional[str]:
    key = str(op or "").strip().lower()
    if key in OPERATORS:
        return key
    return OP_ALIASES.get(key)


def _norm_value(value):
    """Zahl ODER unterstützter Indikator-Name; sonst None (ungültig)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        ind = _norm_indicator(value)
        if ind is not None:
            return ind
        try:
            return float(value.strip().replace(",", "."))
        except (TypeError, ValueError):
            return None
    return None


def normalize_rule_definition(rd) -> Tuple[Optional[Dict], List[str]]:
    """Regel-Definition säubern & gegen die unterstützte Engine prüfen.

    Rückgabe: (normalisierte Definition oder None, Liste der Probleme).
    None => nicht backtestbar (keine einzige gültige Regel übrig)."""
    errors: List[str] = []
    if not isinstance(rd, dict):
        return None, ["rule_definition ist kein Objekt"]
    out = dict(rd)

    tf = str(out.get("timeframe") or "1m")
    if tf not in TIMEFRAMES:
        errors.append(f"Timeframe '{tf}' unbekannt – auf 1m gesetzt")
        tf = "1m"
    out["timeframe"] = tf

    ind_cfg: Dict = {}
    raw_ind = out.get("indicators")
    for k, v in (raw_ind.items() if isinstance(raw_ind, dict) else []):
        key = str(k).strip().lower()
        if key not in _PERIOD_KEYS:
            errors.append(f"Perioden-Schlüssel '{k}' unbekannt – ignoriert")
            continue
        try:
            ind_cfg[key] = float(v) if key == "bb_std" else int(float(v))
        except (TypeError, ValueError):
            errors.append(f"Perioden-Wert '{k}={v}' ungültig – ignoriert")
    out["indicators"] = ind_cfg

    for side in ("long_rules", "short_rules"):
        rules = out.get(side)
        clean: List[Dict] = []
        for i, r in enumerate(rules if isinstance(rules, list) else []):
            label = f"{'LONG' if side == 'long_rules' else 'SHORT'}-Regel {i + 1}"
            if not isinstance(r, dict):
                errors.append(f"{label}: kein Objekt – entfernt")
                continue
            ind = _norm_indicator(r.get("indicator"))
            if ind is None:
                errors.append(f"{label}: Indikator '{r.get('indicator')}' "
                              "nicht unterstützt – entfernt")
                continue
            op = _norm_op(r.get("op"))
            if op is None:
                errors.append(f"{label}: Operator '{r.get('op')}' "
                              "nicht unterstützt – entfernt")
                continue
            val = _norm_value(r.get("value"))
            if val is None:
                errors.append(f"{label}: Wert '{r.get('value')}' ungültig "
                              "(Zahl oder unterstützter Indikator-Name nötig) – entfernt")
                continue
            rule: Dict = {"indicator": ind, "op": op, "value": val}
            if r.get("label"):
                rule["label"] = str(r["label"])[:80]
            clean.append(rule)
        out[side] = clean

    if not (out["long_rules"] or out["short_rules"]):
        return None, errors or ["keine gültigen Regeln enthalten"]
    return out, errors


def supported_summary() -> str:
    """Exakte Liste der unterstützten Bausteine – für KI-Prompts (eine Quelle)."""
    return (
        "Erlaubte Indikatoren (NUR EXAKT diese Namen): " + ", ".join(INDICATORS) + ". "
        "Erlaubte Operatoren: " + ", ".join(OPERATORS) + ". "
        "Erlaubte Perioden-Schlüssel unter \"indicators\": "
        + ", ".join(sorted(_PERIOD_KEYS)) + ". "
        "Erlaubte Timeframes: " + ", ".join(TIMEFRAMES) + ". "
        "Andere Indikatoren/Operatoren werden NICHT ausgewertet."
    )
