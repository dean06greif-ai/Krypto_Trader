"""Liquidations-Heatmap & Liquidity-Levels aus FREIEN Börsendaten (keine API-Keys).

Zwei Bausteine:

1) `heatmap(symbol, interval, bars)` – Schätzung der Liquidations-Dichte pro
   Preis-Bucket. Ansatz (öffentlich nachvollziehbar, kein CoinGlass nötig):
   - Für jede Kerze wird das neu eröffnete gehebelte Volumen geschätzt
     (Volumen × Preis, Long/Short-Split nach Kerzenrichtung; verstärkt bzw.
     abgeschwächt durch die Open-Interest-Veränderung derselben Periode).
   - Dieses Volumen wird auf typische Hebelstufen verteilt (100x/50x/25x/10x)
     und je Stufe der Liquidationspreis berechnet
     (Long: entry × (1 − (1/lev − MM)), Short: entry × (1 + (1/lev − MM))).
   - Wurde dieser Preis SPÄTER gehandelt, sind die Positionen dort schon
     liquidiert ("mitigated") → sie fallen aus der Heatmap.
   - Der Rest wird in Preis-Buckets summiert → Heatmap + Magnet-Level.

2) `liquidity_levels(symbol, interval, bars)` – Level-Logik im Stil eines
   Liquidity-Indikators: unberührte Swing-Hochs/Tiefs (Fraktal-Pivots),
   Equal Highs/Lows (mehrfach getestete Cluster) und deren Abstand zum Preis.

Beide Funktionen liefern immer ein benutzbares Dict (fail-soft) und cachen kurz.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional

import aiohttp

from services import macro_context as mc

logger = logging.getLogger(__name__)

# Hebelstufen mit Gewicht (wie stark sich Positionen dort typischerweise ballen)
LEVERAGE_WEIGHTS = [(100, 0.30), (50, 0.28), (25, 0.24), (10, 0.18)]
MAINT_MARGIN = 0.005          # ~0,5% Erhaltungsmarge
LEVERAGED_SHARE = 0.35        # Anteil des Volumens, der gehebelt/Perp ist
BUCKETS = 90                  # Auflösung der Heatmap
CACHE_TTL = 120               # s

_cache: Dict[str, tuple] = {}


def _cached(key: str, ttl: int = CACHE_TTL):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _store(key: str, value):
    _cache[key] = (time.time(), value)


async def _oi_deltas(session, symbol: str, period: str, limit: int) -> List[float]:
    """Relative OI-Veränderung je Periode (neueste zuletzt). Leer, wenn nicht verfügbar.
    Binance-Futures zuerst; ist die Region gesperrt, greift OKX (Rubik)."""
    try:
        d = await mc._get_json(session, "https://fapi.binance.com/futures/data/openInterestHist",
                               {"symbol": symbol, "period": period, "limit": min(limit, 500)})
        vals = [float(r.get("sumOpenInterestValue") or 0) for r in (d or [])
                if isinstance(r, dict)]
        if len(vals) >= 3:
            return _rel_deltas(vals)
    except Exception as e:
        logger.debug(f"binance oi hist {symbol} {period}: {e}")
    # OKX Rubik: Open Interest je Coin (frei, keyless)
    try:
        okx_period = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H",
                      "4h": "4H", "1d": "1D"}.get(period, "1H")
        d = await mc._get_json(session,
                               "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume",
                               {"ccy": mc._base_ccy(symbol) if hasattr(mc, "_base_ccy")
                                else symbol.replace("USDT", ""), "period": okx_period})
        rows = d.get("data") if isinstance(d, dict) else None
        if rows:
            rows = sorted(rows, key=lambda r: int(r[0]))
            vals = [float(r[1]) for r in rows]
            if len(vals) >= 3:
                return _rel_deltas(vals)
    except Exception as e:
        logger.debug(f"okx oi hist {symbol} {period}: {e}")
    return []


def _rel_deltas(vals: List[float]) -> List[float]:
    out = []
    for i in range(1, len(vals)):
        prev = vals[i - 1]
        out.append((vals[i] - prev) / prev if prev else 0.0)
    return out


def _suffix_extremes(candles: List[Dict]):
    """future_low[i] / future_high[i] = Extrem ALLER Kerzen NACH i."""
    n = len(candles)
    fut_low = [None] * n
    fut_high = [None] * n
    lo = hi = None
    for i in range(n - 1, -1, -1):
        fut_low[i] = lo
        fut_high[i] = hi
        c = candles[i]
        lo = c["low"] if lo is None else min(lo, c["low"])
        hi = c["high"] if hi is None else max(hi, c["high"])
    return fut_low, fut_high


def build_heatmap(candles: List[Dict], oi_deltas: List[float] = None,
                  oi_usd: Optional[float] = None) -> Dict:
    """Reine Berechnung (testbar): Kerzen -> Heatmap-Buckets + Cluster.

    `oi_usd` kalibriert die Absolutwerte: die Summe aller offenen Liquidations-
    Ziele kann nicht größer sein als das gesamte Open Interest.
    """
    if len(candles) < 20:
        return {"buckets": [], "clusters": {"below_price": [], "above_price": []},
                "price": None, "bucket_size": 0}

    price = candles[-1]["close"]
    fut_low, fut_high = _suffix_extremes(candles)
    oi_deltas = oi_deltas or []
    oi_off = len(candles) - len(oi_deltas)

    lows = [c["low"] for c in candles]
    highs = [c["high"] for c in candles]
    lo_b = min(lows) * 0.97
    hi_b = max(highs) * 1.03
    step = (hi_b - lo_b) / BUCKETS
    if step <= 0:
        return {"buckets": [], "clusters": {"below_price": [], "above_price": []},
                "price": price, "bucket_size": 0}

    long_usd = [0.0] * BUCKETS
    short_usd = [0.0] * BUCKETS

    for i, c in enumerate(candles[:-1]):
        entry = (c["high"] + c["low"] + c["close"]) / 3
        notional = c["volume"] * entry * LEVERAGED_SHARE
        if notional <= 0:
            continue
        bull = c["close"] >= c["open"]
        long_share, short_share = (0.6, 0.4) if bull else (0.4, 0.6)

        # OI-Veränderung schärft die Schätzung: OI steigend = neue Positionen,
        # OI fallend = Positionen wurden geschlossen (weniger offene Liq-Ziele).
        j = i - oi_off
        if 0 <= j < len(oi_deltas):
            d = oi_deltas[j]
            if d > 0:
                notional *= min(1.0 + d * 8, 2.0)
                if bull:
                    long_share, short_share = 0.7, 0.3
                else:
                    long_share, short_share = 0.3, 0.7
            elif d < 0:
                notional *= max(1.0 + d * 8, 0.3)

        for lev, w in LEVERAGE_WEIGHTS:
            dist = (1.0 / lev) - MAINT_MARGIN
            if dist <= 0:
                continue
            # Longs werden UNTER dem Einstieg liquidiert
            liq_l = entry * (1 - dist)
            if fut_low[i] is None or fut_low[i] > liq_l:      # noch nicht abgeräumt
                b = int((liq_l - lo_b) / step)
                if 0 <= b < BUCKETS:
                    long_usd[b] += notional * long_share * w
            # Shorts werden ÜBER dem Einstieg liquidiert
            liq_s = entry * (1 + dist)
            if fut_high[i] is None or fut_high[i] < liq_s:
                b = int((liq_s - lo_b) / step)
                if 0 <= b < BUCKETS:
                    short_usd[b] += notional * short_share * w

    peak = max(max(long_usd), max(short_usd), 1.0)
    # Kalibrierung an das echte Open Interest (sonst sind die USD-Werte beliebig groß)
    est_total = sum(long_usd) + sum(short_usd)
    scale = 1.0
    if oi_usd and est_total > 0:
        scale = max(min(oi_usd / est_total, 100.0), 0.0001)
        long_usd = [v * scale for v in long_usd]
        short_usd = [v * scale for v in short_usd]
        peak = max(max(long_usd), max(short_usd), 1.0)
    buckets = []
    for b in range(BUCKETS):
        total = long_usd[b] + short_usd[b]
        if total <= 0:
            continue
        p_lo = lo_b + b * step
        buckets.append({
            "price_low": round(p_lo, 4),
            "price_high": round(p_lo + step, 4),
            "price_mid": round(p_lo + step / 2, 4),
            "long_usd": round(long_usd[b]),
            "short_usd": round(short_usd[b]),
            "total_usd": round(total),
            "intensity": round(total / peak, 3),
            "side": "long" if long_usd[b] >= short_usd[b] else "short",
        })

    below = sorted([b for b in buckets if b["price_mid"] < price],
                   key=lambda x: -x["total_usd"])[:6]
    above = sorted([b for b in buckets if b["price_mid"] > price],
                   key=lambda x: -x["total_usd"])[:6]

    def _cl(rows):
        return [{"price": r["price_mid"], "usd": r["total_usd"],
                 "side": r["side"], "intensity": r["intensity"],
                 "dist_pct": round((r["price_mid"] - price) / price * 100, 2)}
                for r in rows]

    return {
        "price": price,
        "bucket_size": round(step, 4),
        "buckets": buckets,
        "clusters": {"below_price": _cl(below), "above_price": _cl(above)},
        "total_open_liq_usd": round(sum(b["total_usd"] for b in buckets)),
        "oi_calibrated": bool(oi_usd),
    }


async def heatmap(symbol: str, interval: str = "1h", bars: int = 240) -> Dict:
    """Liquidations-Heatmap eines Symbols (geschätzt, freie Daten)."""
    symbol = symbol.upper()
    key = f"hm:{symbol}:{interval}:{bars}"
    hit = _cached(key)
    if hit is not None:
        return hit
    period = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h",
              "4h": "4h", "1d": "1d"}.get(interval, "1h")
    from services import liquidity_data as ld
    async with aiohttp.ClientSession(headers=mc._HEADERS) as session:
        candles, oi = await asyncio.gather(
            mc.fetch_klines(session, symbol, interval, bars),
            _oi_deltas(session, symbol, period, bars),
            return_exceptions=True,
        )
        candles = [] if isinstance(candles, Exception) else candles
        oi = [] if isinstance(oi, Exception) else oi
        oi_usd = None
        try:
            price = candles[-1]["close"] if candles else None
            agg = await ld.aggregated_oi(session, symbol, price)
            oi_usd = (agg or {}).get("oi_usd")
        except Exception as e:
            logger.debug(f"aggregated oi {symbol}: {e}")
    out = build_heatmap(candles, oi, oi_usd)
    out.update({"symbol": symbol, "interval": interval, "bars": len(candles),
                "source": "geschätzt (OKX/Binance Kerzen + Open Interest)",
                "oi_usd": oi_usd, "oi_used": bool(oi)})
    _store(key, out)
    return out


# --------------------------------------------------------------------------- #
#  Liquidity Levels (unberührte Swings, Equal Highs/Lows)
# --------------------------------------------------------------------------- #
def build_levels(candles: List[Dict], pivot_lookback: int = 3,
                 eq_tolerance_pct: float = 0.12) -> Dict:
    """Reine Berechnung (testbar): Pivots -> unberührte Level + Equal H/L."""
    n = len(candles)
    if n < pivot_lookback * 2 + 5:
        return {"levels": [], "price": candles[-1]["close"] if candles else None}

    price = candles[-1]["close"]
    fut_low, fut_high = _suffix_extremes(candles)
    k = pivot_lookback
    raw: List[Dict] = []
    for i in range(k, n - k):
        win = candles[i - k:i + k + 1]
        c = candles[i]
        if c["high"] >= max(w["high"] for w in win):
            raw.append({"price": c["high"], "kind": "swing_high", "idx": i,
                        "ts": c["timestamp"]})
        if c["low"] <= min(w["low"] for w in win):
            raw.append({"price": c["low"], "kind": "swing_low", "idx": i,
                        "ts": c["timestamp"]})

    levels: List[Dict] = []
    for p in raw:
        i = p["idx"]
        if p["kind"] == "swing_high":
            untested = fut_high[i] is None or fut_high[i] < p["price"]
        else:
            untested = fut_low[i] is None or fut_low[i] > p["price"]
        if not untested:
            continue          # Level wurde bereits abgeholt -> keine Liquidität mehr
        levels.append({
            "price": round(p["price"], 4),
            "type": p["kind"],
            "side": "resistance" if p["kind"] == "swing_high" else "support",
            "ts": p["ts"],
            "touches": 1,
            "dist_pct": round((p["price"] - price) / price * 100, 2),
        })

    # Equal Highs / Lows: nahe beieinander liegende Level zusammenfassen
    merged: List[Dict] = []
    for lv in sorted(levels, key=lambda x: x["price"]):
        if merged and merged[-1]["type"] == lv["type"] and \
                abs(lv["price"] - merged[-1]["price"]) / lv["price"] * 100 <= eq_tolerance_pct:
            m = merged[-1]
            m["touches"] += 1
            m["price"] = round((m["price"] * (m["touches"] - 1) + lv["price"]) / m["touches"], 4)
            m["ts"] = max(m["ts"], lv["ts"])
            m["dist_pct"] = round((m["price"] - price) / price * 100, 2)
            m["type"] = "equal_highs" if m["side"] == "resistance" else "equal_lows"
        else:
            merged.append(dict(lv))

    for m in merged:
        m["strength"] = "high" if m["touches"] >= 3 else ("medium" if m["touches"] == 2 else "low")
    merged.sort(key=lambda x: abs(x["dist_pct"]))
    return {"price": price, "levels": merged}


async def liquidity_levels(symbol: str, interval: str = "1h", bars: int = 300,
                           pivot_lookback: int = 3) -> Dict:
    """Unberührte Liquiditäts-Level (Swing-Hochs/Tiefs, Equal Highs/Lows)."""
    symbol = symbol.upper()
    key = f"lv:{symbol}:{interval}:{bars}:{pivot_lookback}"
    hit = _cached(key)
    if hit is not None:
        return hit
    async with aiohttp.ClientSession(headers=mc._HEADERS) as session:
        candles = await mc.fetch_klines(session, symbol, interval, bars)
    out = build_levels(candles or [], pivot_lookback=pivot_lookback)
    out.update({"symbol": symbol, "interval": interval, "bars": len(candles or [])})
    _store(key, out)
    return out


async def combined(symbol: str, interval: str = "1h", bars: int = 240) -> Dict:
    """Heatmap + Level + Live-Liquiditätskontext eines Symbols in einem Aufruf."""
    from services import liquidity_data as ld
    hm, lv, ctx = await asyncio.gather(
        heatmap(symbol, interval, bars),
        liquidity_levels(symbol, interval, max(bars, 300)),
        ld.get_liquidity_context([symbol.upper()]),
        return_exceptions=True,
    )
    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "heatmap": {} if isinstance(hm, Exception) else hm,
        "levels": {} if isinstance(lv, Exception) else lv,
        "market": {} if isinstance(ctx, Exception) else (ctx.get(symbol.upper()) or {}),
    }
