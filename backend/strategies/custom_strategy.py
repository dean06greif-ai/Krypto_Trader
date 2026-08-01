"""
Custom, user-defined strategy engine.
A definition (stored in MongoDB) describes indicators + long/short rules.
Rules: {"indicator", "op", "value", "label"} where
  value: number OR indicator-name string
"""
import copy
import re
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy

INDICATORS = [
    "price", "rsi", "ema_fast", "ema_slow", "sma", "ema_gap_pct", "ha_color",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower", "bb_width_pct",
    "atr", "atr_pct", "vwap",
    "stoch_k", "stoch_d",
    "volume", "volume_sma", "rel_volume",
    "price_change_pct", "recent_high", "recent_low",
]

INDICATOR_META = {
    "price": {"label": "Preis", "group": "Preis"},
    "rsi": {"label": "RSI", "group": "Momentum"},
    "ema_fast": {"label": "EMA Fast", "group": "Trend"},
    "ema_slow": {"label": "EMA Slow", "group": "Trend"},
    "sma": {"label": "SMA", "group": "Trend"},
    "ema_gap_pct": {"label": "EMA Abstand %", "group": "Trend"},
    "ha_color": {"label": "HA Farbe (1=grün, 0=rot)", "group": "Preis"},
    "macd": {"label": "MACD Linie", "group": "Momentum"},
    "macd_signal": {"label": "MACD Signal", "group": "Momentum"},
    "macd_hist": {"label": "MACD Histogramm", "group": "Momentum"},
    "bb_upper": {"label": "Bollinger Oben", "group": "Volatilität"},
    "bb_middle": {"label": "Bollinger Mitte", "group": "Volatilität"},
    "bb_lower": {"label": "Bollinger Unten", "group": "Volatilität"},
    "bb_width_pct": {"label": "Bollinger Breite %", "group": "Volatilität"},
    "atr": {"label": "ATR", "group": "Volatilität"},
    "atr_pct": {"label": "ATR % vom Preis", "group": "Volatilität"},
    "vwap": {"label": "VWAP", "group": "Volumen"},
    "stoch_k": {"label": "Stochastik %K", "group": "Momentum"},
    "stoch_d": {"label": "Stochastik %D", "group": "Momentum"},
    "volume": {"label": "Volumen", "group": "Volumen"},
    "volume_sma": {"label": "Volumen Ø", "group": "Volumen"},
    "rel_volume": {"label": "Rel. Volumen (x Ø)", "group": "Volumen"},
    "price_change_pct": {"label": "Preisänderung % (Lookback)", "group": "Preis"},
    "recent_high": {"label": "Letztes Hoch (Lookback)", "group": "Struktur"},
    "recent_low": {"label": "Letztes Tief (Lookback)", "group": "Struktur"},
}

PERIOD_FIELDS = [
    {"key": "ema_fast_period", "label": "EMA Fast Periode", "default": 9},
    {"key": "ema_slow_period", "label": "EMA Slow Periode", "default": 50},
    {"key": "rsi_period", "label": "RSI Periode", "default": 14},
    {"key": "sma_period", "label": "SMA Periode", "default": 20},
    {"key": "macd_fast", "label": "MACD Fast", "default": 12},
    {"key": "macd_slow", "label": "MACD Slow", "default": 26},
    {"key": "macd_signal_period", "label": "MACD Signal", "default": 9},
    {"key": "bb_period", "label": "Bollinger Periode", "default": 20},
    {"key": "bb_std", "label": "Bollinger Std-Abw.", "default": 2.0},
    {"key": "atr_period", "label": "ATR Periode", "default": 14},
    {"key": "stoch_k_period", "label": "Stochastik %K", "default": 14},
    {"key": "stoch_d_period", "label": "Stochastik %D", "default": 3},
    {"key": "volume_sma_period", "label": "Volumen Ø Periode", "default": 20},
    {"key": "change_lookback", "label": "Preisänderung Lookback", "default": 5},
    {"key": "swing_lookback", "label": "Hoch/Tief Lookback", "default": 10},
]

OPERATORS = ["<", ">", "<=", ">=", "cross_above", "cross_below"]

# Welche Perioden-Parameter beeinflussen welchen Indikator (für den Optimizer)
_PARAM_DEPS = {
    "rsi": ("rsi_period",),
    "ema_fast": ("ema_fast_period",), "ema_slow": ("ema_slow_period",),
    "ema_gap_pct": ("ema_fast_period", "ema_slow_period"),
    "sma": ("sma_period",),
    "macd": ("macd_fast", "macd_slow", "macd_signal_period"),
    "macd_signal": ("macd_fast", "macd_slow", "macd_signal_period"),
    "macd_hist": ("macd_fast", "macd_slow", "macd_signal_period"),
    "bb_upper": ("bb_period", "bb_std"), "bb_middle": ("bb_period", "bb_std"),
    "bb_lower": ("bb_period", "bb_std"), "bb_width_pct": ("bb_period", "bb_std"),
    "atr": ("atr_period",), "atr_pct": ("atr_period",),
    "stoch_k": ("stoch_k_period", "stoch_d_period"),
    "stoch_d": ("stoch_k_period", "stoch_d_period"),
    "volume_sma": ("volume_sma_period",), "rel_volume": ("volume_sma_period",),
    "price_change_pct": ("change_lookback",),
    "recent_high": ("swing_lookback",), "recent_low": ("swing_lookback",),
}
_BOUNDED_0_100 = {"rsi", "stoch_k", "stoch_d"}
_RULE_PARAM_KEY = re.compile(r"^rule_(long|short)_(\d+)_value$")


def _period_meta(key: str, value) -> Optional[Dict]:
    field = next((f for f in PERIOD_FIELDS if f["key"] == key), None)
    label = field["label"] if field else key
    if key == "bb_std":
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 2.0
        return {"label": label, "value": v, "min": 1.0, "max": 3.5, "step": 0.25}
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return None
    lo = max(2, int(round(v * 0.5)))
    hi = max(lo + 1, int(round(v * 2)))
    step = max(1, round((hi - lo) / 12))
    return {"label": label, "value": v, "min": lo, "max": hi, "step": step}


def _rule_value_meta(rule: Dict, side: str, idx: int) -> Optional[Dict]:
    try:
        v = float(rule.get("value"))
    except (TypeError, ValueError):
        return None
    if v == 0:
        return None
    ind = rule.get("indicator")
    label = f"{side.upper()}-Regel {idx + 1}: Schwelle ({ind} {rule.get('op')})"
    if ind in _BOUNDED_0_100:
        lo, hi, step = max(2.0, v - 20), min(98.0, v + 20), 2
    else:
        lo, hi = sorted((round(v * 0.5, 6), round(v * 1.5, 6)))
        step = round((hi - lo) / 10, 6)
    if step <= 0 or hi <= lo:
        return None
    return {"label": label, "value": v, "min": lo, "max": hi, "step": step}


def build_param_space(definition: Dict) -> Dict:
    """Optimierbarer Parameter-Raum einer Custom-/KI-Strategie – abgeleitet aus
    der Definition (genutzte Indikator-Perioden + numerische Regel-Schwellen).
    Gleiches Format wie DEFAULT_PARAMS der Built-ins ({value,min,max,step}),
    damit Optimizer & Regime-Optimizer sie ohne Sonderfälle rechnen können."""
    space: Dict = {}
    ind_cfg = definition.get("indicators") or {}
    used = set()
    for r in (definition.get("long_rules") or []) + (definition.get("short_rules") or []):
        used.add(r.get("indicator"))
        if isinstance(r.get("value"), str) and r["value"] in INDICATORS:
            used.add(r["value"])
    defaults = {f["key"]: f["default"] for f in PERIOD_FIELDS}
    for ind in used:
        for key in _PARAM_DEPS.get(ind, ()):
            if key in space:
                continue
            meta = _period_meta(key, ind_cfg.get(key, defaults.get(key)))
            if meta:
                space[key] = meta
    for side in ("long", "short"):
        for i, r in enumerate(definition.get(f"{side}_rules") or []):
            meta = _rule_value_meta(r, side, i)
            if meta:
                space[f"rule_{side}_{i}_value"] = meta
    return space


class CustomStrategy(BaseStrategy):
    IS_CUSTOM = True
    STRATEGY_TIMEFRAME = "1m"
    DEFAULT_PARAMS = {}

    def __init__(self, definition: Dict):
        super().__init__()
        self.definition = definition
        self.STRATEGY_ID = definition["id"]
        self.STRATEGY_NAME = definition.get("name", "Custom")
        self.STRATEGY_DESCRIPTION = definition.get("description", "Custom Strategie")
        # Fix: Discovery-/Custom-Strategien nutzen den Timeframe aus der Definition
        # (vorher wurde er ignoriert und immer 1m verwendet)
        self.STRATEGY_TIMEFRAME = definition.get("timeframe") or "1m"
        # Optimierbarer Parameter-Raum aus der Definition (Instanz-Attribut,
        # damit Optimizer/Settings-Overrides auch für Custom-Strategien greifen)
        self.DEFAULT_PARAMS = build_param_space(definition)

    def effective_definition(self, params: Optional[Dict]) -> Dict:
        """Definition mit angewendeten Parameter-Overrides (Optimizer/Settings).
        Ohne abweichende Werte kommt die Original-Definition zurück (kein Kopieren)."""
        if not params or not self.DEFAULT_PARAMS:
            return self.definition
        base = {k: meta["value"] for k, meta in self.DEFAULT_PARAMS.items()}
        try:
            changed = {k: v for k, v in params.items()
                       if k in base and v is not None and float(v) != float(base[k])}
        except (TypeError, ValueError):
            changed = {k: v for k, v in params.items() if k in base and v is not None}
        if not changed:
            return self.definition
        d = copy.deepcopy(self.definition)
        ind = dict(d.get("indicators") or {})
        for k, v in changed.items():
            m = _RULE_PARAM_KEY.match(k)
            if m:
                rules = d.get(f"{m.group(1)}_rules") or []
                idx = int(m.group(2))
                if idx < len(rules):
                    rules[idx]["value"] = v
            else:
                ind[k] = v
        d["indicators"] = ind
        return d

    def _used_indicators(self, definition: Optional[Dict] = None):
        definition = definition or self.definition
        used = set()
        for r in (definition.get("long_rules", []) + definition.get("short_rules", [])):
            used.add(r.get("indicator"))
            v = r.get("value")
            if isinstance(v, str) and v in INDICATORS:
                used.add(v)
        return used

    def _series(self, candles, definition: Optional[Dict] = None):
        definition = definition or self.definition
        d = definition.get("indicators", {})

        def p(key, default):
            try:
                return int(d.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        def pf(key, default):
            try:
                return float(d.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        used = self._used_indicators(definition)
        closes = [c["close"] for c in candles]
        ind = self.indicators

        ema_f = ind.calculate_ema(closes, p("ema_fast_period", 9))
        ema_s = ind.calculate_ema(closes, p("ema_slow_period", 50))
        rsi = ind.calculate_rsi(closes, p("rsi_period", 14))
        ha = ind.calculate_heikin_ashi(candles)

        sma = ind.calculate_sma(closes, p("sma_period", 20)) if "sma" in used else None
        macd = macd_sig = macd_hist = None
        if used & {"macd", "macd_signal", "macd_hist"}:
            macd, macd_sig, macd_hist = ind.calculate_macd(
                closes, p("macd_fast", 12), p("macd_slow", 26), p("macd_signal_period", 9))
        bb_u = bb_m = bb_l = None
        if used & {"bb_upper", "bb_middle", "bb_lower", "bb_width_pct"}:
            bb_u, bb_m, bb_l = ind.calculate_bollinger(closes, p("bb_period", 20), pf("bb_std", 2.0))
        atr = ind.calculate_atr(candles, p("atr_period", 14)) if used & {"atr", "atr_pct"} else None
        vwap = ind.calculate_vwap(candles) if "vwap" in used else None
        st_k = st_d = None
        if used & {"stoch_k", "stoch_d"}:
            st_k, st_d = ind.calculate_stochastic(candles, p("stoch_k_period", 14), p("stoch_d_period", 3))
        vsp = p("volume_sma_period", 20)
        chg_lb = p("change_lookback", 5)
        swing_lb = p("swing_lookback", 10)

        def snap(i):
            n = len(candles)
            idx = i if i >= 0 else n + i
            ef, es = ema_f[i], ema_s[i]
            gap = ((ef - es) / es * 100) if (ef and es) else None
            s = {"rsi": rsi[i], "ema_fast": ef, "ema_slow": es,
                 "price": closes[i], "ha_color": 1 if ha[i]["is_green"] else 0,
                 "ema_gap_pct": gap,
                 "volume": candles[i].get("volume", 0)}
            if sma is not None:
                s["sma"] = sma[i]
            if macd is not None:
                s["macd"] = macd[i]
                s["macd_signal"] = macd_sig[i]
                s["macd_hist"] = macd_hist[i]
            if bb_u is not None:
                s["bb_upper"] = bb_u[i]
                s["bb_middle"] = bb_m[i]
                s["bb_lower"] = bb_l[i]
                if bb_u[i] is not None and bb_l[i] is not None and bb_m[i]:
                    s["bb_width_pct"] = (bb_u[i] - bb_l[i]) / bb_m[i] * 100
                else:
                    s["bb_width_pct"] = None
            if atr is not None:
                s["atr"] = atr[i]
                s["atr_pct"] = (atr[i] / closes[i] * 100) if atr[i] else None
            if vwap is not None:
                s["vwap"] = vwap[i]
            if st_k is not None:
                s["stoch_k"] = st_k[i]
                s["stoch_d"] = st_d[i]
            if used & {"volume_sma", "rel_volume"}:
                seg = candles[max(0, idx - vsp + 1):idx + 1]
                vols = [c.get("volume", 0) or 0 for c in seg]
                vavg = sum(vols) / len(vols) if vols else None
                s["volume_sma"] = vavg
                s["rel_volume"] = (s["volume"] / vavg) if vavg else None
            if "price_change_pct" in used:
                j = idx - chg_lb
                s["price_change_pct"] = ((closes[idx] - closes[j]) / closes[j] * 100) if j >= 0 and closes[j] else None
            if used & {"recent_high", "recent_low"}:
                seg = candles[max(0, idx - swing_lb):idx]
                s["recent_high"] = max((c["high"] for c in seg), default=None)
                s["recent_low"] = min((c["low"] for c in seg), default=None)
            return s

        return snap(-1), snap(-2)

    def _resolve(self, snap, value):
        if isinstance(value, str) and value in INDICATORS:
            return snap.get(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _eval_rule(self, rule, cur, prev):
        ind = rule.get("indicator")
        op = rule.get("op")
        left = cur.get(ind)
        left_prev = prev.get(ind)
        right = self._resolve(cur, rule.get("value"))
        right_prev = self._resolve(prev, rule.get("value"))
        if left is None or right is None:
            return False
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == ">=":
            return left >= right
        if op == "cross_above":
            return left_prev is not None and right_prev is not None and left_prev <= right_prev and left > right
        if op == "cross_below":
            return left_prev is not None and right_prev is not None and left_prev >= right_prev and left < right
        return False

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        d = self.effective_definition(params)
        need = max(int(d.get("indicators", {}).get("ema_slow_period", 50)) + 10, 60)
        if len(candles) < need:
            return None
        cur, prev = self._series(candles, d)
        if cur["rsi"] is None or cur["ema_slow"] is None:
            return None

        long_rules = d.get("long_rules", [])
        short_rules = d.get("short_rules", [])
        rules = []
        long_evals, short_evals = [], []
        for i, r in enumerate(long_rules):
            ev = self._eval_rule(r, cur, prev)
            long_evals.append(ev)
            rules.append({"id": f"L{i}", "label": r.get("label") or self._auto_label(r),
                          "description": "LONG Bedingung", "long": ev, "short": False})
        for i, r in enumerate(short_rules):
            ev = self._eval_rule(r, cur, prev)
            short_evals.append(ev)
            rules.append({"id": f"S{i}", "label": r.get("label") or self._auto_label(r),
                          "description": "SHORT Bedingung", "long": False, "short": ev})

        signal_type = None
        if long_evals and all(long_evals):
            signal_type = "LONG"
        elif short_evals and all(short_evals):
            signal_type = "SHORT"

        long_cnt = sum(long_evals)
        short_cnt = sum(short_evals)
        bias = "LONG" if long_cnt > short_cnt else ("SHORT" if short_cnt > long_cnt else None)

        levels = self._levels(candles, cur["price"], signal_type, d) if signal_type else None

        return {
            "indicators": {"rsi": round(cur["rsi"], 2),
                           "ema_fast": round(cur["ema_fast"], 6) if cur["ema_fast"] else 0,
                           "ema_slow": round(cur["ema_slow"], 6) if cur["ema_slow"] else 0,
                           "price": round(cur["price"], 6)},
            "rules": rules, "bias": bias,
            "long_count": long_cnt, "short_count": short_cnt,
            "rules_total": len(long_rules) if signal_type == "LONG" else (len(short_rules) or len(long_rules)),
            "signal_type": signal_type, "is_pre_signal": False, "levels": levels,
        }

    def _auto_label(self, r):
        meta = INDICATOR_META.get(r.get("indicator"), {})
        v = r.get("value")
        v_label = INDICATOR_META.get(v, {}).get("label", v) if isinstance(v, str) else v
        return f"{meta.get('label', r.get('indicator'))} {r.get('op')} {v_label}"

    def _levels(self, candles, entry, side, definition: Optional[Dict] = None):
        d = definition or self.definition
        crv = float(d.get("crv_target", 2.0))
        if d.get("sl_mode", "percent") == "structure":
            lookback = int(d.get("structure_lookback", 10))
            ticks = int(d.get("sl_ticks", 4))
            tick = entry * 0.0001
            if side == "LONG":
                sl = self.indicators.get_recent_low(candles, lookback) - ticks * tick
            else:
                sl = self.indicators.get_recent_high(candles, lookback) + ticks * tick
        else:
            pct = float(d.get("sl_percent", 2.0)) / 100
            sl = entry * (1 - pct) if side == "LONG" else entry * (1 + pct)
        risk = abs(entry - sl)
        if side == "LONG":
            tp1, tpf = entry + risk, entry + risk * crv
        else:
            tp1, tpf = entry - risk, entry - risk * crv
        return {"entry": round(entry, 6), "stop_loss": round(sl, 6),
                "take_profit_1": round(tp1, 6), "take_profit_full": round(tpf, 6),
                "crv": round(self.indicators.calculate_crv(entry, sl, tpf), 2)}
