"""Regime-Engine v2 – deterministische, mathematisch begründete Marktphasen.

Warum neu (Probleme der alten K-Means-Erkennung):
- Cluster-Labels waren *relativ* zum Datensatz: in einem überwiegend fallenden
  Zeitraum wurde ein stark fallender Cluster als "leicht abwärts" beschriftet.
- Ein einziges Lookback-Fenster (3 Tage) kann weder "500 Tage langsam fallend"
  noch "3 Tage Ausbruch" erkennen.
- Häufig blieben nur 2 Cluster übrig, die nicht zum Chart passten.

Ansatz v2 (fest definierte Taxonomie statt Clustering):
1. TREND: rollierende lineare Regression über den Log-Kurs auf MEHREREN
   Horizonten (z.B. 5/10/20/50/100 Tage). Bewertet wird der t-Wert der
   Steigung (Steigung / Standardfehler) – skalenfrei, dadurch ist "langsam
   aber stetig fallend" ebenso klar erkennbar wie "schnell fallend".
   Zusätzlich: Übereinstimmung der Horizonte (Multi-Timeframe-Konsens),
   ADX/DI als Trendstärke-/Richtungsbestätigung, Kaufman-Effizienz.
2. VOLATILITÄT: ATR% (oder realisierte Vola) im Vergleich zum eigenen,
   rückblickenden Referenzfenster (z-Wert) -> niedrig / mittel / hoch.
3. TAXONOMIE: 3 Trendzustände x 3 Vola-Zustände = 9 Regime mit festen IDs und
   festen Labels (id = trend_idx * 3 + vol_idx). Zusätzlich wird jedes Regime
   auf die 3 NNFX-Regime gemappt (trend / range / breakout).
4. WECHSEL-LOGIK: Hysterese (Ein-/Ausstiegsschwellen unterschiedlich),
   Bestätigungsdauer, Mindesthaltedauer und Confidence-Score verhindern
   Flattern und späte/falsche Wechsel.

Kein Lookahead: alle Features sind rückblickend (services.regime_features),
der Zustandsautomat läuft strikt vorwärts. `ideal_labels()` ist die EINZIGE
Funktion mit Zukunftssicht – sie dient ausschließlich der visuellen Prüfung
und Validierung und wird nie in Backtests/Live verwendet.
"""
import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from services import regime_features as rf

logger = logging.getLogger(__name__)

ENGINE = "v2"

TREND_STATES = [("down", "Abwärtstrend"), ("side", "Seitwärtsmarkt"), ("up", "Aufwärtstrend")]
VOL_STATES = [("low", "niedrige Volatilität"), ("mid", "mittlere Volatilität"),
              ("high", "hohe Volatilität")]

# Alle Einstellungen sind bewusst in TAGEN angegeben (timeframe-unabhängig)
DEFAULT_CONFIG: Dict = {
    # --- Trend (Multi-Timeframe-Regression) ---
    "horizons_days": [5, 10, 20, 50, 100],
    "horizon_weights": None,          # None = längere Horizonte leicht höher gewichtet
    "trend_t": 2.0,                   # |Score| ab dem ein Trend gilt
    "trend_strong_t": 4.5,            # ab hier "starker" Trend (Anzeige/Stats)
    "t_clip": 12.0,                   # t-Werte kappen (Ausreißer)
    "agreement_weight": 0.35,         # Gewicht des Multi-Horizont-Konsens im Score
    "use_di": True,                   # DI+/DI- als Richtungsbestätigung
    "di_weight": 0.15,
    "require_adx": True,              # Trend nur mit ADX-Bestätigung ...
    "adx_min": 18.0,                  # ... oder wenn |Score| >= trend_strong_t
    "adx_period_days": 2.0,
    "require_efficiency": False,
    "efficiency_min": 0.12,
    "efficiency_days": 10.0,
    # Range-Filter: ein Trend muss auch am Rand der Handelsspanne stehen
    # (Donchian-Position). Verhindert, dass Schwingungen innerhalb einer Range
    # als Trend gelten – der klassische "Seitwärts wird Trend"-Fehler.
    "require_range_break": True,
    "range_break_pos": 0.72,
    "range_window_days": 0.0,         # 0 = längster Trend-Horizont
    "range_lag_days": 0.0,            # 0 = kürzester Trend-Horizont
    "long_confirm_frac": 0.75,        # Alternativ: langer Horizont bestätigt den Trend
    "gate_timeout_days": 12.0,        # Hält der Score so lange, wird der Range-Filter
                                      # übergangen (verhindert verpasste Trends)
    "strong_bypass_range": True,      # sehr starker Score übergeht den Range-Filter
    # Varianz-Verhältnis (Lo/MacKinlay): erkennt mean-reverting Märkte.
    # VR < vr_min => Range: Trend-Einstieg wird unterdrückt.
    "use_variance_ratio": False,      # experimentell: hilft bei choppigen Märkten
    "vr_k_days": 10.0,
    "vr_window_days": 0.0,            # 0 = längster Trend-Horizont
    "vr_min": 0.85,
    "smooth_days": 0.5,               # Glättung des Trend-Scores
    # --- Volatilität ---
    "vol_metric": "atr",              # atr | stdev
    "vol_window_days": 3.0,
    "vol_smooth_days": 5.0,           # Glättung des Vola-Maßes (gegen Flattern)
    "vol_ref_days": 90.0,             # Referenzfenster für den z-Wert
    "vol_low_z": -0.55,
    "vol_high_z": 0.65,
    "vol_hysteresis": 0.45,
    # --- Wechsel-Logik ---
    "hysteresis": 0.3,                # Ein-/Ausstieg: thr*(1+h) / thr*(1-h)
    "confirm_days": 0.5,              # Kandidat muss so lange stabil sein
    "min_hold_days": 2.0,             # Mindesthaltedauer des aktuellen Regimes
    "confidence_min": 0.55,           # Mindest-Confidence für einen Wechsel
    # --- Validierung ---
    "validate_side_max_pct_per_day": 0.35,
    "validate_side_t": 3.0,
    "validate_min_segment_days": 5.0,
    "validate_tol_pct": 0.5,
    "validate_tol_t": 0.5,
    "validate_vol_tol_mult": 1.5,     # Toleranz = x * Tagesvola * sqrt(Tage)
}

CONFIG_META = [
    ("horizons_days", "Trend-Horizonte (Tage)", "Über diese Zeiträume wird die "
     "Regressions-Steigung gemessen (Multi-Timeframe-Konsens)."),
    ("trend_t", "Trend-Schwelle (t-Wert)", "Ab welcher statistischer Signifikanz "
     "ein Trend als Trend gilt (2 ≈ 95%)."),
    ("trend_strong_t", "Schwelle 'starker Trend'", "Ab hier gilt der Trend als stark; "
     "ADX-Bestätigung ist dann nicht mehr nötig."),
    ("agreement_weight", "Gewicht Horizont-Konsens", "Wie stark uneinige Horizonte "
     "den Score abschwächen."),
    ("require_adx", "ADX-Bestätigung", "Trend nur bei ADX >= Minimum."),
    ("adx_min", "ADX-Minimum", "Trendstärke-Filter (klassisch 20-25)."),
    ("adx_period_days", "ADX-Periode (Tage)", "Länge der ADX-Glättung."),
    ("require_efficiency", "Effizienz-Filter", "Trend nur bei glattem Verlauf "
     "(Kaufman-Effizienz)."),
    ("efficiency_min", "Effizienz-Minimum", "0 = Chop, 1 = perfekt glatter Trend."),
    ("vol_metric", "Vola-Maß", "atr = ATR%, stdev = realisierte Vola."),
    ("vol_window_days", "Vola-Fenster (Tage)", "Messfenster der Volatilität."),
    ("vol_smooth_days", "Vola-Glättung (Tage)", "Glättet das Vola-Maß – weniger "
     "Sprünge zwischen den Vola-Stufen."),
    ("vol_ref_days", "Vola-Referenz (Tage)", "Vergleichsfenster für hoch/niedrig "
     "(rückblickend, kein Lookahead)."),
    ("vol_low_z", "Grenze niedrige Vola (z)", "z-Wert-Grenze nach unten."),
    ("vol_high_z", "Grenze hohe Vola (z)", "z-Wert-Grenze nach oben."),
    ("hysteresis", "Trend-Hysterese", "Einstiegsschwelle höher als Ausstiegsschwelle "
     "-> kein Flattern."),
    ("vol_hysteresis", "Vola-Hysterese", "Wie oben, für die Vola-Grenzen."),
    ("confirm_days", "Bestätigungsdauer (Tage)", "So lange muss ein neues Regime "
     "stabil sein, bevor umgeschaltet wird."),
    ("min_hold_days", "Mindesthaltedauer (Tage)", "Mindestdauer des aktuellen Regimes."),
    ("confidence_min", "Mindest-Confidence", "Wechsel nur ab dieser Sicherheit."),
    ("smooth_days", "Score-Glättung (Tage)", "Glättung gegen Kerzen-Rauschen."),
    ("require_range_break", "Range-Filter (Ausbruch nötig)", "Trend-Einstieg nur bei "
     "neuem Hoch/Tief oder wenn der lange Horizont bestätigt – verhindert, dass "
     "Schwingungen in einer Range als Trend gelten."),
    ("range_window_days", "Range-Fenster (Tage)", "Spanne für den Ausbruch-Test "
     "(0 = zweitlängster Horizont)."),
    ("range_lag_days", "Range-Versatz (Tage)", "Wie weit die Vergleichsspanne in der "
     "Vergangenheit endet (0 = kürzester Horizont)."),
    ("long_confirm_frac", "Bestätigung langer Horizont", "Anteil der Trend-Schwelle, "
     "den der längste Horizont erreichen muss."),
    ("gate_timeout_days", "Range-Filter Timeout (Tage)", "Hält der Score so lange, "
     "wird der Range-Filter übergangen (kein verpasster Trend)."),
    ("strong_bypass_range", "Starker Trend übergeht Filter", "Sehr starke Scores "
     "dürfen den Range-Filter überspringen (schnelle Crashs/Rallys)."),
    ("use_variance_ratio", "Varianz-Verhältnis nutzen", "Experimentell: unterdrückt "
     "Trends in statistisch mean-reverting Phasen."),
    ("vr_k_days", "VR Intervall (Tage)", "k-Bar-Renditen für das Varianz-Verhältnis."),
    ("vr_min", "VR Minimum", "Unter diesem Wert gilt der Markt als mean-reverting."),
    ("validate_min_segment_days", "Prüfung: min. Abschnitt (Tage)", "Kürzere "
     "Abschnitte werden bei der Plausibilitätsprüfung ignoriert."),
    ("validate_side_max_pct_per_day", "Prüfung: max. Drift seitwärts (%/Tag)",
     "Ab dieser Drift gilt ein Seitwärts-Label als verdächtig."),
    ("validate_vol_tol_mult", "Prüfung: Toleranz (x Vola)", "Wie stark ein Abschnitt "
     "gegen sein Label 'atmen' darf."),
]


# ---------------------------------------------------------------- Taxonomie
def regime_id(trend_idx: int, vol_idx: int) -> int:
    return int(trend_idx) * 3 + int(vol_idx)


def split_id(rid: int) -> Tuple[int, int]:
    return int(rid) // 3, int(rid) % 3


def regime_label(rid: int) -> str:
    t, v = split_id(rid)
    return f"{TREND_STATES[t][1]} · {VOL_STATES[v][1]}"


def nnfx_regime(rid: int) -> str:
    """Abbildung der 9 Regime auf die 3 NNFX-Regime."""
    t, v = split_id(rid)
    if t != 1:
        return "trend"
    return "breakout" if v == 2 else "range"


NNFX_LABELS = {"trend": "Trend (NNFX)", "range": "Seitwärts (NNFX)",
               "breakout": "Volatilität/Breakout (NNFX)"}


def taxonomy() -> List[Dict]:
    out = []
    for t, (tk, tl) in enumerate(TREND_STATES):
        for v, (vk, vl) in enumerate(VOL_STATES):
            rid = regime_id(t, v)
            out.append({"id": rid, "key": f"{tk}_{vk}", "label": regime_label(rid),
                        "trend": tk, "vol": vk, "nnfx": nnfx_regime(rid),
                        "nnfx_label": NNFX_LABELS[nnfx_regime(rid)]})
    return out


# ---------------------------------------------------------------- Konfiguration
def resolve_config(config: Optional[Dict], timeframe: str, n_bars: int = 10 ** 9) -> Dict:
    """Nutzer-Konfiguration validieren und Tages-Angaben in Bars umrechnen.
    Horizonte, die für die Datenmenge zu lang sind, werden verworfen (mind. 1)."""
    cfg = dict(DEFAULT_CONFIG)
    for k, v in (config or {}).items():
        if k in cfg and v is not None:
            cfg[k] = v
    bpd = rf.bars_per_day(timeframe)

    def _f(key, lo, hi):
        cfg[key] = float(min(max(float(cfg[key]), lo), hi))

    hz = [float(h) for h in (cfg.get("horizons_days") or [])
          if isinstance(h, (int, float)) and float(h) > 0]
    hz = sorted({round(min(max(h, 0.25), 1000.0), 3) for h in hz})[:8] or [10.0, 30.0]
    _f("trend_t", 0.5, 10.0)
    _f("trend_strong_t", cfg["trend_t"], 20.0)
    _f("t_clip", 3.0, 50.0)
    _f("agreement_weight", 0.0, 1.0)
    _f("di_weight", 0.0, 0.5)
    _f("adx_min", 0.0, 60.0)
    _f("adx_period_days", 0.1, 30.0)
    _f("efficiency_min", 0.0, 1.0)
    _f("efficiency_days", 0.5, 200.0)
    _f("range_break_pos", 0.5, 1.0)
    _f("long_confirm_frac", 0.0, 3.0)
    _f("gate_timeout_days", 0.0, 365.0)
    cfg["strong_bypass_range"] = bool(cfg["strong_bypass_range"])
    cfg["use_variance_ratio"] = bool(cfg["use_variance_ratio"])
    _f("vr_k_days", 1.0, 120.0)
    _f("vr_min", 0.0, 2.0)
    cfg["vr_window_days"] = float(min(max(float(cfg["vr_window_days"]), 0.0), 2000.0))
    cfg["range_lag_days"] = float(min(max(float(cfg["range_lag_days"]), 0.0), 1000.0))
    cfg["require_range_break"] = bool(cfg["require_range_break"])
    cfg["range_window_days"] = float(min(max(float(cfg["range_window_days"]), 0.0), 1000.0))
    _f("smooth_days", 0.0, 30.0)
    _f("vol_window_days", 0.25, 60.0)
    _f("vol_smooth_days", 0.0, 60.0)
    _f("vol_ref_days", 5.0, 720.0)
    _f("vol_low_z", -5.0, 0.0)
    _f("vol_high_z", 0.0, 5.0)
    _f("vol_hysteresis", 0.0, 2.0)
    _f("hysteresis", 0.0, 0.9)
    _f("confirm_days", 0.0, 60.0)
    _f("min_hold_days", 0.0, 120.0)
    _f("confidence_min", 0.0, 0.99)
    _f("validate_side_max_pct_per_day", 0.01, 5.0)
    _f("validate_side_t", 1.0, 10.0)
    _f("validate_min_segment_days", 0.5, 120.0)
    _f("validate_tol_pct", 0.0, 20.0)
    _f("validate_tol_t", 0.0, 5.0)
    _f("validate_vol_tol_mult", 0.0, 10.0)
    cfg["require_adx"] = bool(cfg["require_adx"])
    cfg["require_efficiency"] = bool(cfg["require_efficiency"])
    cfg["use_di"] = bool(cfg["use_di"])
    cfg["vol_metric"] = "stdev" if str(cfg["vol_metric"]).lower() == "stdev" else "atr"

    # Horizonte in Bars; zu lange Horizonte für die Datenmenge entfernen
    usable = [h for h in hz if int(h * bpd) + 5 < n_bars]
    if not usable:
        usable = [min(hz)]
    bars = [max(int(round(h * bpd)), 8) for h in usable]
    weights = cfg.get("horizon_weights")
    if not (isinstance(weights, (list, tuple)) and len(weights) == len(usable)):
        weights = [1.0 + 0.25 * i for i in range(len(usable))]  # längere leicht höher
    weights = [max(float(w), 0.0) for w in weights]
    if sum(weights) <= 0:
        weights = [1.0] * len(usable)

    cfg["horizons_days"] = usable
    cfg["horizon_weights"] = weights
    cfg["horizon_bars"] = bars
    cfg["bars_per_day"] = bpd
    cfg["adx_period_bars"] = max(int(round(cfg["adx_period_days"] * bpd)), 5)
    cfg["vol_window_bars"] = max(int(round(cfg["vol_window_days"] * bpd)), 5)
    cfg["vol_smooth_bars"] = max(int(round(cfg["vol_smooth_days"] * bpd)), 1)
    cfg["vol_ref_bars"] = max(int(round(cfg["vol_ref_days"] * bpd)), 20)
    cfg["efficiency_bars"] = max(int(round(cfg["efficiency_days"] * bpd)), 5)
    cfg["range_bars"] = (max(int(round(cfg["range_window_days"] * bpd)), 10)
                         if cfg["range_window_days"] > 0
                         else (bars[-2] if len(bars) > 1 else bars[-1]))
    cfg["range_lag_bars"] = (max(int(round(cfg["range_lag_days"] * bpd)), 1)
                             if cfg["range_lag_days"] > 0 else max(min(bars), 1))
    cfg["gate_timeout_bars"] = max(int(round(cfg["gate_timeout_days"] * bpd)), 1)
    cfg["vr_k_bars"] = max(int(round(cfg["vr_k_days"] * bpd)), 2)
    cfg["vr_window_bars"] = (max(int(round(cfg["vr_window_days"] * bpd)), 20)
                             if cfg["vr_window_days"] > 0 else max(bars))
    cfg["smooth_bars"] = max(int(round(cfg["smooth_days"] * bpd)), 1)
    cfg["confirm_bars"] = max(int(round(cfg["confirm_days"] * bpd)), 1)
    cfg["min_hold_bars"] = max(int(round(cfg["min_hold_days"] * bpd)), 1)
    cfg["warmup_bars"] = max(max(bars), cfg["adx_period_bars"] * 2,
                             cfg["vol_window_bars"] + 5)
    return cfg


# ---------------------------------------------------------------- Features
def compute_matrix(candles, cfg: Dict) -> Dict[str, np.ndarray]:
    """Alle Regime-Features je Kerze (rein rückblickend)."""
    high, low, close, _vol = rf.ohlc(candles)
    n = len(close)
    logc = np.log(np.maximum(close, rf.EPS))
    tmax = cfg["t_clip"]
    bars, weights = cfg["horizon_bars"], cfg["horizon_weights"]
    wsum = float(sum(weights)) or 1.0

    t_stack, r2_stack, ret_stack = [], [], []
    for w in bars:
        _slope, t, r2 = rf.ols_stats(logc, w)
        t_stack.append(np.clip(t, -tmax, tmax))
        r2_stack.append(r2)
        ret = np.full(n, np.nan)
        if n > w:
            ret[w:] = (close[w:] / np.maximum(close[:-w], rf.EPS) - 1.0) * 100.0
        ret_stack.append(ret)
    T = np.vstack(t_stack) if t_stack else np.zeros((1, n))
    W = np.array(weights, dtype=float).reshape(-1, 1)
    t_w = np.nansum(T * W, axis=0) / wsum
    t_w = np.where(np.all(np.isnan(T), axis=0), np.nan, t_w)

    sign_w = np.sign(t_w)
    agree_num = np.nansum(np.where(np.sign(T) == sign_w, W, 0.0), axis=0)
    agreement = np.where(np.abs(sign_w) > 0, agree_num / wsum, 0.0)

    adx, pdi, mdi = rf.adx_di(high, low, close, cfg["adx_period_bars"])
    eff = rf.efficiency_ratio(close, cfg["efficiency_bars"])
    range_pos = rf.range_position(close, cfg["range_bars"])
    hi_lag, lo_lag = rf.donchian_lagged(close, cfg["range_bars"], cfg["range_lag_bars"])
    new_high = close >= hi_lag * 0.999
    new_low = close <= lo_lag * 1.001
    new_high = np.where(np.isfinite(hi_lag), new_high, False)
    new_low = np.where(np.isfinite(lo_lag), new_low, False)
    vr = (rf.variance_ratio(close, cfg["vr_k_bars"], cfg["vr_window_bars"])
          if cfg.get("use_variance_ratio") else np.full(n, np.nan))
    gate_ready = np.isfinite(hi_lag) & np.isfinite(lo_lag) & np.isfinite(t_stack[-1])

    if cfg["vol_metric"] == "stdev":
        vol_raw = rf.realized_vol_pct(close, cfg["vol_window_bars"], cfg["bars_per_day"])
    else:
        vol_raw = rf.atr_pct(high, low, close, cfg["vol_window_bars"])
    if cfg.get("vol_smooth_bars", 1) > 1:
        vmask = np.isfinite(vol_raw)
        if vmask.any():
            vs = vol_raw.copy()
            vs[vmask] = rf.ema(vol_raw[vmask], cfg["vol_smooth_bars"])
            vol_raw = vs
    vol_z = rf.rolling_zscore(vol_raw, cfg["vol_ref_bars"])
    daily_vol = rf.realized_vol_pct(close, cfg["vol_window_bars"], cfg["bars_per_day"])

    aw = cfg["agreement_weight"]
    score = t_w * ((1.0 - aw) + aw * agreement)
    if cfg["use_di"]:
        di_dir = np.sign(np.nan_to_num(pdi) - np.nan_to_num(mdi))
        match = np.where(di_dir == np.sign(score), 1.0, -1.0)
        score = score * (1.0 + cfg["di_weight"] * match)
    if cfg["smooth_bars"] > 1:
        valid = np.isfinite(score)
        if valid.any():
            sm = score.copy()
            sm[valid] = rf.ema(score[valid], cfg["smooth_bars"])
            score = sm

    primary = int(np.argmax(np.array(bars)))       # längster Horizont für Statistik
    trend_pct_per_day = ret_stack[primary] / max(cfg["horizons_days"][primary], 1e-9)
    t_long = t_stack[primary]

    return {"score": score, "t_weighted": t_w, "agreement": agreement,
            "adx": adx, "plus_di": pdi, "minus_di": mdi, "efficiency": eff,
            "range_pos": range_pos, "new_high": new_high, "new_low": new_low,
            "variance_ratio": vr, "gate_ready": gate_ready,
            "t_long": t_long,
            "vol_raw": vol_raw, "vol_z": vol_z, "daily_vol_pct": daily_vol,
            "r2": r2_stack[primary], "trend_pct_per_day": trend_pct_per_day,
            "t_per_horizon": T, "ret_per_horizon": np.vstack(ret_stack),
            "close": close}


def _bands(f: Dict[str, np.ndarray], cfg: Dict):
    """Ein-/Ausstiegs-Bänder (Hysterese) + Confidence je Kerze."""
    score = f["score"]
    adx = f["adx"]
    eff = f["efficiency"]
    vz = f["vol_z"]
    thr, h = cfg["trend_t"], cfg["hysteresis"]
    strong = cfg["trend_strong_t"]

    adx_ok = np.ones(len(score), dtype=bool)
    if cfg["require_adx"]:
        adx_ok = (np.nan_to_num(adx, nan=0.0) >= cfg["adx_min"]) | \
                 (np.abs(np.nan_to_num(score)) >= strong)
    eff_ok = np.ones(len(score), dtype=bool)
    if cfg["require_efficiency"]:
        eff_ok = np.nan_to_num(eff, nan=0.0) >= cfg["efficiency_min"]
    ok = adx_ok & eff_ok

    up_ok = ok.copy()
    dn_ok = ok.copy()
    raw_up = score >= thr * (1 + h)
    raw_dn = score <= -thr * (1 + h)
    if cfg.get("require_range_break"):
        # Trend-EINSTIEG nur bei neuem Extrem (Ausbruch aus der alten Spanne)
        # ODER wenn der längste Horizont den Trend bestätigt ODER wenn der Score
        # lange genug hält / sehr stark ist. So werden Range-Schwingungen
        # gefiltert, echte Trendwenden aber nicht verschluckt.
        tl = np.nan_to_num(f.get("t_long"), nan=0.0)
        lc = cfg.get("long_confirm_frac", 0.75) * thr
        timeout = cfg.get("gate_timeout_bars", 10 ** 9)
        hold_up = rf.run_length(np.nan_to_num(raw_up, nan=False)) >= timeout
        hold_dn = rf.run_length(np.nan_to_num(raw_dn, nan=False)) >= timeout
        strong_ok = (np.abs(np.nan_to_num(score)) >= strong) \
            if cfg.get("strong_bypass_range") else np.zeros(len(score), dtype=bool)
        # Solange die Filter-Daten (langer Horizont / Spanne) noch nicht
        # vorliegen (Aufwärmphase), darf der Filter nicht blockieren.
        not_ready = ~np.asarray(f.get("gate_ready",
                                      np.ones(len(score), dtype=bool)), dtype=bool)
        up_ok = up_ok & (f["new_high"].astype(bool) | (tl >= lc) | hold_up
                        | strong_ok | not_ready)
        dn_ok = dn_ok & (f["new_low"].astype(bool) | (tl <= -lc) | hold_dn
                        | strong_ok | not_ready)
    if cfg.get("use_variance_ratio"):
        # Mean-reverting Markt (VR < vr_min) => kein Trend-Einstieg (Range)
        vr = f.get("variance_ratio")
        vr_ok = ~(np.isfinite(vr) & (vr < cfg["vr_min"]))
        up_ok = up_ok & vr_ok
        dn_ok = dn_ok & vr_ok

    enter_up = raw_up & up_ok
    enter_dn = raw_dn & dn_ok
    stay_up = score >= thr * (1 - h)
    stay_dn = score <= -thr * (1 - h)

    hv = cfg["vol_hysteresis"]
    lo_z, hi_z = cfg["vol_low_z"], cfg["vol_high_z"]
    enter_low = vz <= lo_z - hv
    stay_low = vz <= lo_z + hv
    enter_high = vz >= hi_z + hv
    stay_high = vz >= hi_z - hv

    d_trend = np.clip(np.abs(np.abs(score) - thr) / max(thr, 1e-9), 0.0, 1.0)
    d_edge = np.minimum(np.abs(vz - lo_z), np.abs(vz - hi_z))
    d_vol = np.clip(d_edge / 0.6, 0.0, 1.0)
    conf = np.clip(0.5 * d_trend + 0.25 * d_vol + 0.25 * f["agreement"], 0.0, 1.0)

    valid = np.isfinite(score) & np.isfinite(vz) & (np.isfinite(adx) | ~cfg["require_adx"])
    return {"enter_up": np.nan_to_num(enter_up, nan=False).astype(bool),
            "enter_dn": np.nan_to_num(enter_dn, nan=False).astype(bool),
            "stay_up": np.nan_to_num(stay_up, nan=False).astype(bool),
            "stay_dn": np.nan_to_num(stay_dn, nan=False).astype(bool),
            "enter_low": np.nan_to_num(enter_low, nan=False).astype(bool),
            "stay_low": np.nan_to_num(stay_low, nan=False).astype(bool),
            "enter_high": np.nan_to_num(enter_high, nan=False).astype(bool),
            "stay_high": np.nan_to_num(stay_high, nan=False).astype(bool),
            "conf": np.nan_to_num(conf, nan=0.0), "valid": valid}


def _raw_state(b: Dict, i: int) -> Tuple[int, int]:
    t = 2 if b["enter_up"][i] else (0 if b["enter_dn"][i] else 1)
    v = 2 if b["enter_high"][i] else (0 if b["enter_low"][i] else 1)
    return t, v


def _desired_state(b: Dict, i: int, cur_t: int, cur_v: int) -> Tuple[int, int]:
    if cur_t == 2:
        t = 2 if b["stay_up"][i] else (0 if b["enter_dn"][i] else 1)
    elif cur_t == 0:
        t = 0 if b["stay_dn"][i] else (2 if b["enter_up"][i] else 1)
    else:
        t = 2 if b["enter_up"][i] else (0 if b["enter_dn"][i] else 1)
    if cur_v == 2:
        v = 2 if b["stay_high"][i] else (0 if b["enter_low"][i] else 1)
    elif cur_v == 0:
        v = 0 if b["stay_low"][i] else (2 if b["enter_high"][i] else 1)
    else:
        v = 2 if b["enter_high"][i] else (0 if b["enter_low"][i] else 1)
    return t, v


def classify_arrays(f: Dict[str, np.ndarray], cfg: Dict,
                    conf_min: float = None, min_hold_bars: int = None):
    """Zustandsautomat mit Hysterese, Bestätigungsdauer, Mindesthaltedauer und
    Confidence-Filter. Rückgabe: (regime-ids (-1 = unbekannt), confidence)."""
    b = _bands(f, cfg)
    n = len(f["score"])
    cmin = cfg["confidence_min"] if conf_min is None else float(conf_min)
    hold = cfg["min_hold_bars"] if min_hold_bars is None else max(int(min_hold_bars), 1)
    confirm = cfg["confirm_bars"]
    out = np.full(n, -1, dtype=int)
    conf_out = np.zeros(n)
    cur_t = cur_v = None
    since = 0
    pend = None
    pend_n = 0
    for i in range(n):
        if not b["valid"][i]:
            out[i] = -1 if cur_t is None else regime_id(cur_t, cur_v)
            conf_out[i] = 0.0 if cur_t is None else b["conf"][i]
            continue
        if cur_t is None:
            cur_t, cur_v = _raw_state(b, i)
            since = i
        else:
            dt, dv = _desired_state(b, i, cur_t, cur_v)
            if (dt, dv) == (cur_t, cur_v):
                pend, pend_n = None, 0
            else:
                if pend == (dt, dv):
                    pend_n += 1
                else:
                    pend, pend_n = (dt, dv), 1
                if pend_n >= confirm and (i - since) >= hold and b["conf"][i] >= cmin:
                    cur_t, cur_v = dt, dv
                    since = i
                    pend, pend_n = None, 0
        out[i] = regime_id(cur_t, cur_v)
        conf_out[i] = b["conf"][i]
    return out, conf_out


def classify_series(model: Dict, candles, conf_min: float = None,
                    min_hold_days: float = None) -> List[Optional[int]]:
    cfg = dict(model["config"])
    hold = None
    if min_hold_days is not None:
        hold = max(int(round(float(min_hold_days) * cfg["bars_per_day"])), 1)
    f = compute_matrix(candles, cfg)
    ids, _conf = classify_arrays(f, cfg, conf_min, hold)
    return [None if r < 0 else int(r) for r in ids]


def segments_from_labels(labels) -> List[Tuple[int, int, int]]:
    segs = []
    start, cur = None, None
    for i, r in enumerate(labels):
        if r is None or r < 0:
            continue
        if cur is None:
            start, cur = i, r
        elif r != cur:
            segs.append((start, i, cur))
            start, cur = i, r
    if cur is not None:
        segs.append((start, len(labels), cur))
    return segs


# ---------------------------------------------------------------- Modell bauen
def _regime_stats(f: Dict, ids: np.ndarray, cfg: Dict) -> Dict[int, Dict]:
    out = {}
    for rid in sorted(set(int(x) for x in np.unique(ids) if x >= 0)):
        m = ids == rid
        if not m.any():
            continue
        def _mean(key):
            v = f[key][m]
            v = v[np.isfinite(v)]
            return float(np.mean(v)) if len(v) else 0.0
        out[rid] = {"bars": int(m.sum()),
                    "score": round(_mean("score"), 3),
                    "trend_pct_per_day": round(_mean("trend_pct_per_day"), 4),
                    "adx": round(_mean("adx"), 2),
                    "efficiency": round(_mean("efficiency"), 3),
                    "daily_vol_pct": round(_mean("daily_vol_pct"), 3),
                    "vol_pct": round(_mean("daily_vol_pct"), 3),
                    "vol_z": round(_mean("vol_z"), 3),
                    "agreement": round(_mean("agreement"), 3)}
    return out


def build_model(histories: Dict[str, List[Dict]], timeframe: str,
                config: Dict = None) -> Optional[Dict]:
    """Regime-"Modell" v2: die Taxonomie ist fest, trainiert werden nur die
    Statistiken/Anteile (für Anzeige und Plausibilitätsprüfung)."""
    lens = [len(c) for c in (histories or {}).values() if c is not None]
    if not lens or max(lens) < 60:
        return None
    cfg = resolve_config(config, timeframe, max(lens))
    agg: Dict[int, Dict] = {}
    total_bars = 0
    for candles in histories.values():
        if len(candles) < cfg["warmup_bars"] + 20:
            continue
        f = compute_matrix(candles, cfg)
        ids, _ = classify_arrays(f, cfg)
        stats = _regime_stats(f, ids, cfg)
        for rid, st in stats.items():
            a = agg.setdefault(rid, {"bars": 0, "acc": {}})
            a["bars"] += st["bars"]
            for k, v in st.items():
                if k == "bars":
                    continue
                a["acc"][k] = a["acc"].get(k, 0.0) + v * st["bars"]
        total_bars += int((ids >= 0).sum())
    if not agg or total_bars <= 0:
        return None
    regimes = []
    for rid in sorted(agg.keys()):
        a = agg[rid]
        stats = {k: round(v / max(a["bars"], 1), 4) for k, v in a["acc"].items()}
        strength = ("stark" if abs(stats.get("score", 0)) >= cfg["trend_strong_t"]
                    else "moderat")
        t, _v = split_id(rid)
        regimes.append({
            "id": rid, "label": regime_label(rid),
            "trend": TREND_STATES[t][0], "vol": VOL_STATES[split_id(rid)[1]][0],
            "nnfx": nnfx_regime(rid), "nnfx_label": NNFX_LABELS[nnfx_regime(rid)],
            "strength": strength if t != 1 else "-",
            "share_pct": round(a["bars"] / total_bars * 100, 1),
            "bars": a["bars"],
            "features": {"trend_pct": round(stats.get("trend_pct_per_day", 0)
                                            * cfg["horizons_days"][-1], 3),
                         "vol_pct": stats.get("daily_vol_pct", 0),
                         "efficiency": stats.get("efficiency", 0),
                         "rel_volume": 1.0},
            "stats": {**stats, "trend_strength": abs(stats.get("score", 0))},
        })
    return {"engine": ENGINE, "timeframe": timeframe, "config": cfg,
            "lookback_bars": int(cfg["warmup_bars"]),
            "lookback_days": round(max(cfg["horizons_days"]), 2),
            "bars_per_day": cfg["bars_per_day"],
            "taxonomy": taxonomy(), "regimes": regimes,
            "n_samples": int(total_bars), "symbols": list((histories or {}).keys())}


# ---------------------------------------------------------------- Frühwarnung
def early_warning(f: Dict, cfg: Dict, i: int, cur_t: int, cur_v: int,
                  b: Dict = None) -> Dict:
    """Frühwarnung für einen Regime-Wechsel – bevor Bestätigungsdauer und
    Mindesthaltedauer greifen.

    Drei unabhängige Bausteine (rein mathematisch, kein Lookahead):
    1. KANDIDAT: welcher Zustand wäre nach den Hysterese-Bändern gerade gewollt
       und wie lange hält dieser Wunsch schon an (pending_bars).
    2. ABSTAND: wie weit ist der Trend-Score von der nächsten Schwelle entfernt
       (normiert auf die Schwelle).
    3. MOMENTUM: Steigung des Scores pro Tag (Regression über das
       Bestätigungsfenster) -> geschätzte Tage bis zur Schwelle (ETA).
    Daraus ein Wahrscheinlichkeits-Score 0..100 (Heuristik, klar dokumentiert).
    """
    b = b if b is not None else _bands(f, cfg)
    thr, h = cfg["trend_t"], cfg["hysteresis"]
    score = f["score"]
    n = len(score)
    if i < 5 or not np.isfinite(score[i]):
        return {"active": False}
    dt, dv = _desired_state(b, i, cur_t, cur_v)
    pending = (dt, dv) != (cur_t, cur_v)
    pending_bars = 0
    if pending:
        j = i
        while j > 0 and b["valid"][j] and _desired_state(b, j, cur_t, cur_v) == (dt, dv):
            pending_bars += 1
            j -= 1
            if pending_bars > 5 * max(cfg["confirm_bars"], 1):
                break

    # Momentum des Scores über das Bestätigungsfenster
    w = max(int(cfg["confirm_bars"]) * 2, 6)
    lo = max(i - w + 1, 0)
    seg = score[lo:i + 1]
    seg = seg[np.isfinite(seg)]
    slope_per_day = 0.0
    if len(seg) >= 4:
        x = np.arange(len(seg), dtype=float)
        x -= x.mean()
        denom = float((x * x).sum()) or 1.0
        slope_per_bar = float((x * (seg - seg.mean())).sum() / denom)
        slope_per_day = slope_per_bar * cfg["bars_per_day"]

    s_now = float(score[i])
    # Nächste relevante Schwelle in Richtung der Bewegung
    if cur_t == 1:
        target = thr * (1 + h) if slope_per_day >= 0 else -thr * (1 + h)
        target_t = 2 if slope_per_day >= 0 else 0
    elif cur_t == 2:
        target = thr * (1 - h)          # nach unten verlassen
        target_t = 1
    else:
        target = -thr * (1 - h)
        target_t = 1
    gap = target - s_now
    eta_days = None
    if slope_per_day != 0 and (gap > 0) == (slope_per_day > 0):
        eta_days = round(abs(gap) / abs(slope_per_day), 1)
    dist_norm = min(abs(gap) / max(thr, 1e-9), 3.0)

    prob = 0.0
    if pending:
        prob += 45.0 * min(pending_bars / max(cfg["confirm_bars"], 1), 1.0)
    prob += 35.0 * max(0.0, 1.0 - dist_norm)
    if eta_days is not None:
        prob += 20.0 * max(0.0, 1.0 - min(eta_days / 10.0, 1.0))
    prob = round(min(prob, 99.0), 1)

    next_t = dt if pending else target_t
    next_v = dv if pending else cur_v
    rid_next = regime_id(next_t, next_v)
    return {"active": prob >= 25.0 or pending,
            "next_regime": rid_next, "next_label": regime_label(rid_next),
            "next_nnfx": nnfx_regime(rid_next),
            "probability_pct": prob,
            "eta_days": eta_days,
            "pending": bool(pending), "pending_bars": int(pending_bars),
            "pending_days": round(pending_bars / max(cfg["bars_per_day"], 1e-9), 2),
            "confirm_days": round(cfg["confirm_bars"] / max(cfg["bars_per_day"], 1e-9), 2),
            "score": round(s_now, 3),
            "score_slope_per_day": round(slope_per_day, 3),
            "distance_to_threshold": round(float(gap), 3),
            "reason": _warning_text(rid_next, prob, eta_days, pending, pending_bars,
                                    cfg, slope_per_day)}


def _warning_text(rid_next: int, prob: float, eta: Optional[float], pending: bool,
                  pending_bars: int, cfg: Dict, slope: float) -> str:
    parts = []
    if pending:
        d = pending_bars / max(cfg["bars_per_day"], 1e-9)
        parts.append(f"Kandidat {regime_label(rid_next)} seit {d:.1f} Tagen "
                     f"(Bestätigung ab {cfg['confirm_bars'] / max(cfg['bars_per_day'], 1e-9):.1f} Tagen)")
    else:
        parts.append(f"Nächster wahrscheinlicher Zustand: {regime_label(rid_next)}")
    parts.append(f"Score-Momentum {slope:+.2f}/Tag")
    if eta is not None:
        parts.append(f"Schwelle in ca. {eta:.1f} Tagen")
    parts.append(f"Wahrscheinlichkeit {prob:.0f}%")
    return " · ".join(parts)


# ---------------------------------------------------------------- Live-Status
def current_regime(model: Dict, candles, conf_min: float = None,
                   min_hold_days: float = None) -> Dict:
    cfg = dict(model["config"])
    hold = None
    if min_hold_days is not None:
        hold = max(int(round(float(min_hold_days) * cfg["bars_per_day"])), 1)
    f = compute_matrix(candles, cfg)
    ids, conf = classify_arrays(f, cfg, conf_min, hold)
    last = next((i for i in range(len(ids) - 1, -1, -1) if ids[i] >= 0), None)
    if last is None:
        return {"regime": None, "confidence": 0.0, "similarities": [],
                "last_switch": None, "reason": "Zu wenig Daten für die Klassifikation"}
    rid = int(ids[last])
    switch_i = last
    while switch_i > 0 and ids[switch_i - 1] == rid:
        switch_i -= 1
    ts = candles[switch_i]["timestamp"] if switch_i < len(candles) else None
    score = float(f["score"][last]) if np.isfinite(f["score"][last]) else 0.0
    detail = {"score": round(score, 3),
              "trend_t": round(float(np.nan_to_num(f["t_weighted"][last])), 3),
              "agreement_pct": round(float(np.nan_to_num(f["agreement"][last])) * 100, 1),
              "adx": round(float(np.nan_to_num(f["adx"][last])), 2),
              "efficiency": round(float(np.nan_to_num(f["efficiency"][last])), 3),
              "vol_z": round(float(np.nan_to_num(f["vol_z"][last])), 3),
              "daily_vol_pct": round(float(np.nan_to_num(f["daily_vol_pct"][last])), 3),
              "per_horizon": [{"days": cfg["horizons_days"][h],
                               "t": round(float(np.nan_to_num(f["t_per_horizon"][h][last])), 2),
                               "change_pct": round(float(np.nan_to_num(
                                   f["ret_per_horizon"][h][last])), 2)}
                              for h in range(len(cfg["horizons_days"]))]}
    strength = ("stark" if abs(score) >= cfg["trend_strong_t"] else
                ("moderat" if abs(score) >= cfg["trend_t"] else "schwach"))
    t_idx, v_idx = split_id(rid)
    warn = early_warning(f, cfg, last, t_idx, v_idx)
    if warn.get("active") is not None:
        active_days = (last - switch_i) / max(cfg["bars_per_day"], 1e-9)
        warn["min_hold_days"] = round(cfg["min_hold_bars"] / max(cfg["bars_per_day"], 1e-9), 2)
        warn["hold_remaining_days"] = max(round(warn["min_hold_days"] - active_days, 2), 0.0)
    return {"regime": rid, "label": regime_label(rid),
            "nnfx": nnfx_regime(rid), "nnfx_label": NNFX_LABELS[nnfx_regime(rid)],
            "strength": strength,
            "confidence": round(float(conf[last]) * 100, 1),
            "similarities": [], "details": detail,
            "early_warning": warn,
            "reason": _reason_text(rid, detail, strength),
            "last_switch": ts, "active_since_bars": int(last - switch_i)}


def _reason_text(rid: int, d: Dict, strength: str) -> str:
    t, v = split_id(rid)
    dirn = {0: "fallend", 1: "seitwärts", 2: "steigend"}[t]
    hz = " · ".join(f"{h['days']:g}d {h['change_pct']:+.1f}%" for h in d["per_horizon"])
    return (f"Kurs {dirn} ({strength}) · Trend-t {d['trend_t']:+.2f} · "
            f"Konsens {d['agreement_pct']:.0f}% · ADX {d['adx']:.1f} · "
            f"Vola z {d['vol_z']:+.2f} · {hz}")


# ---------------------------------------------------------------- Validierung
def ideal_labels(model: Dict, candles) -> List[Optional[int]]:
    """NUR für die visuelle Prüfung: zentrierte Regression (mit Zukunftssicht!).
    Zeigt, wo die Phasen im Rückblick "wirklich" lagen – niemals für Backtests
    oder Live verwenden."""
    cfg = dict(model["config"])
    f = compute_matrix(candles, cfg)
    shift = max(cfg["horizon_bars"][0] // 2, 1)
    n = len(f["score"])
    for key in ("score", "adx", "efficiency", "vol_z", "agreement", "t_weighted",
                "t_long", "range_pos"):
        a = f[key]
        b = np.full(n, np.nan)
        if n > shift:
            b[:n - shift] = a[shift:]
        f[key] = b
    for key in ("new_high", "new_low"):
        a = np.asarray(f[key], dtype=bool)
        b = np.zeros(n, dtype=bool)
        if n > shift:
            b[:n - shift] = a[shift:]
        f[key] = b
    ids, _ = classify_arrays(f, {**cfg, "confirm_bars": 1, "min_hold_bars": 1,
                                 "confidence_min": 0.0})
    return [None if r < 0 else int(r) for r in ids]


def validate_labels(candles, labels, model: Dict) -> Dict:
    """Logische Prüfung der fertigen Regime: Passt das Label zum Kursverlauf?

    - Aufwärts-Regime müssen im Abschnitt netto gestiegen sein (Toleranz),
      Abwärts-Regime gefallen, Seitwärts darf pro Tag nur wenig driften.
    - Vola-Stufe muss zur gemessenen Volatilität des Abschnitts passen.
    Rückgabe enthält Verstöße (max. 25 Beispiele) + Kennzahlen.
    """
    cfg = dict(model["config"])
    _h, _l, close, _v = rf.ohlc(candles)
    bpd = cfg["bars_per_day"]
    tol_pct = cfg["validate_tol_pct"]
    tol_t = cfg["validate_tol_t"]
    side_max = cfg["validate_side_max_pct_per_day"]
    f = compute_matrix(candles, cfg)
    segs = segments_from_labels(labels)
    ctx_bars = cfg["horizon_bars"][0]      # Sichtfenster des Klassifikators
    long_bars = cfg["range_bars"]          # langer Kontext (Range vs. echter Trend)
    min_days = cfg["validate_min_segment_days"]
    vmult = cfg["validate_vol_tol_mult"]
    violations, checked, bad_bars, total_bars = [], 0, 0, 0
    dir_ok = 0
    for (s, e, rid) in segs:
        bars = e - s
        total_bars += bars
        if bars / max(bpd, 1e-9) < min_days:
            continue
        checked += 1
        t_idx, v_idx = split_id(rid)
        net = (float(close[e - 1]) / max(float(close[s]), rf.EPS) - 1.0) * 100.0
        days = bars / max(bpd, 1e-9)
        per_day = net / max(days, 1e-9)
        _sl, tseg, _r2 = rf.ols_stats(np.log(np.maximum(close[s:e], rf.EPS)), bars)
        t_seg = float(np.nan_to_num(tseg[-1]))
        vzs = f["vol_z"][s:e]
        vz = float(np.nanmean(vzs)) if np.isfinite(vzs).any() else 0.0
        dv = f["daily_vol_pct"][s:e]
        dvol = float(np.nanmean(dv)) if np.isfinite(dv).any() else 1.0
        # Toleranz: kurze Abschnitte dürfen gegen den Trend "atmen" (Vola-skaliert)
        tol = max(tol_pct, vmult * dvol * math.sqrt(max(days, 1.0)))
        c0 = max(s - ctx_bars, 0)
        ctx_net = (float(close[e - 1]) / max(float(close[c0]), rf.EPS) - 1.0) * 100.0
        l0 = max(s - long_bars, 0)
        long_net = (float(close[e - 1]) / max(float(close[l0]), rf.EPS) - 1.0) * 100.0
        long_days = (e - l0) / max(bpd, 1e-9)
        long_per_day = long_net / max(long_days, 1e-9)
        problems = []
        if t_idx == 2 and net < -tol and ctx_net < -tol and t_seg < -tol_t \
                and long_net < 0:
            problems.append(f"Label steigend, tatsächlich {net:+.2f}% im Abschnitt / "
                            f"{ctx_net:+.2f}% im Sichtfenster (t {t_seg:+.2f})")
        if t_idx == 0 and net > tol and ctx_net > tol and t_seg > tol_t \
                and long_net > 0:
            problems.append(f"Label fallend, tatsächlich {net:+.2f}% im Abschnitt / "
                            f"{ctx_net:+.2f}% im Sichtfenster (t {t_seg:+.2f})")
        # Seitwärts ist nur dann falsch, wenn der Kurs AUCH im langen Kontext
        # klar in dieselbe Richtung läuft (sonst ist es eine normale Schwingung
        # innerhalb einer Range).
        if t_idx == 1 and abs(per_day) > side_max \
                and abs(t_seg) > cfg["validate_side_t"] and abs(net) > tol \
                and abs(long_per_day) > side_max / 2 \
                and (long_per_day > 0) == (per_day > 0):
            problems.append(f"Label seitwärts, tatsächlich {per_day:+.2f}%/Tag "
                            f"(t {t_seg:+.2f}, langer Kontext {long_per_day:+.2f}%/Tag)")
        if t_idx != 1 and not problems:
            dir_ok += 1
        if v_idx == 0 and vz > cfg["vol_low_z"] + 1.0:
            problems.append(f"Label niedrige Vola, gemessen z {vz:+.2f}")
        if v_idx == 2 and vz < cfg["vol_high_z"] - 1.0:
            problems.append(f"Label hohe Vola, gemessen z {vz:+.2f}")
        if problems:
            bad_bars += bars
            if len(violations) < 25:
                violations.append({
                    "from_ts": int(candles[s]["timestamp"]),
                    "to_ts": int(candles[min(e, len(candles) - 1)]["timestamp"]),
                    "regime": rid, "label": regime_label(rid),
                    "days": round(days, 1), "net_pct": round(net, 2),
                    "context_net_pct": round(ctx_net, 2),
                    "pct_per_day": round(per_day, 3), "t": round(t_seg, 2),
                    "vol_z": round(vz, 2), "problems": problems})
    trend_segs = sum(1 for (_s, _e, r) in segs if split_id(r)[0] != 1)
    return {"segments": len(segs), "checked": checked,
            "violations": violations, "violation_count": len(violations),
            "violation_bars_pct": round(bad_bars / max(total_bars, 1) * 100, 2),
            "direction_accuracy_pct": (round(dir_ok / trend_segs * 100, 1)
                                       if trend_segs else None),
            "regimes_seen": sorted({int(r) for (_s, _e, r) in segs}),
            "avg_segment_days": (round(sum(e - s for (s, e, _r) in segs)
                                       / max(len(segs), 1) / max(bpd, 1e-9), 2)
                                 if segs else 0.0),
            "switches": max(len(segs) - 1, 0),
            "passed": bad_bars / max(total_bars, 1) <= 0.05}


def agreement_with_ideal(labels, ideal) -> Dict:
    """Übereinstimmung der Live-Erkennung mit der (nur zur Prüfung erlaubten)
    Rückblick-Sicht – misst, wie schnell/genau die Erkennung ist."""
    n = min(len(labels), len(ideal))
    both = [(labels[i], ideal[i]) for i in range(n)
            if labels[i] is not None and ideal[i] is not None]
    if not both:
        return {"bars": 0}
    same = sum(1 for a, b in both if a == b)
    same_dir = sum(1 for a, b in both if split_id(a)[0] == split_id(b)[0])
    return {"bars": len(both),
            "exact_pct": round(same / len(both) * 100, 1),
            "direction_pct": round(same_dir / len(both) * 100, 1)}


def engine_defaults() -> Dict:
    return {"config": {k: v for k, v in DEFAULT_CONFIG.items()},
            "meta": [{"key": k, "label": lbl, "help": hlp} for k, lbl, hlp in CONFIG_META],
            "taxonomy": taxonomy(),
            "nnfx_labels": NNFX_LABELS}


def summarize(model: Dict) -> str:
    cfg = model.get("config") or {}
    hz = ", ".join(f"{h:g}d" for h in cfg.get("horizons_days") or [])
    return (f"Engine v2 · Horizonte {hz} · Trend-Schwelle t={cfg.get('trend_t')} · "
            f"ADX≥{cfg.get('adx_min')} · {len(model.get('regimes') or [])} Regime aktiv")
