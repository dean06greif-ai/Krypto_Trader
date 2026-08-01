"""Liquiditäts-Endpoints: Liquidations-Heatmap + eigene „Liquidity Levels".

Zwei Datenquellen, beide FREI und ohne Fremd-Keys:
  * ``services/liquidity_data.py``  – Multi-Exchange-Kontext (Binance/OKX/Bybit):
    Long/Short-Ratios, Open Interest, Orderbook-Wände, modellierte
    Liquidations-Cluster, Live-Liquidationen.
  * ``services/liquidity_levels.py`` – aus Kerzen berechnete Liquiditäts-Level
    (Swings/EQH/EQL/FVG/Volumen-Profil) als Ersatz für „X-Ray Pro".

Der KI Trader nutzt denselben Kontext über ``AIEngine._liquidity_block()``.
"""
import logging
from typing import Optional

import aiohttp
from fastapi import APIRouter, HTTPException, Query

from services import liquidity_data as ld
from services import liquidity_levels as ll
from services import macro_context as mc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/liquidity", tags=["liquidity"])

DEFAULT_INTERVAL = "15m"
MAX_CANDLES = 400


async def _candles(symbol: str, interval: str, limit: int):
    data = await mc.historical_candles(symbol.upper(), interval=interval,
                                       limit=min(limit, MAX_CANDLES))
    return data.get("candles") or []


@router.get("/context")
async def liquidity_context(symbols: Optional[str] = Query(
        None, description="Komma-Liste, z.B. BTCUSDT,ETHUSDT (Default BTC/ETH/SOL)")):
    """Kompletter Liquiditäts-Kontext (~2 KB) – genau der Block, den die KI sieht."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    try:
        return await ld.get_liquidity_context(syms)
    except Exception as e:  # noqa: BLE001
        logger.error(f"liquidity context failed: {e}")
        raise HTTPException(status_code=502, detail=str(e)[:200])


@router.get("/levels/{symbol}")
async def liquidity_levels(symbol: str, interval: str = DEFAULT_INTERVAL,
                           limit: int = 300):
    """„Liquidity Levels" (X-Ray-Pro-Äquivalent) aus freien Kerzendaten."""
    try:
        candles = await _candles(symbol, interval, limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)[:200])
    if not candles:
        raise HTTPException(status_code=502, detail="Keine Kerzendaten verfügbar")
    out = ll.liquidity_levels(candles)
    return {"symbol": symbol.upper(), "interval": interval,
            "candles": len(candles), **out}


@router.get("/heatmap/{symbol}")
async def liquidity_heatmap(symbol: str, interval: str = DEFAULT_INTERVAL,
                            limit: int = 300, bins: int = 40):
    """Liquidations-Heatmap (Eigenbau, keyless): modellierte Liquidations-Cluster
    + Volumen-Profil + Liquiditäts-Level zu Preis-Buckets verdichtet."""
    sym = symbol.upper()
    try:
        candles = await _candles(sym, interval, limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)[:200])
    if not candles:
        raise HTTPException(status_code=502, detail="Keine Kerzendaten verfügbar")
    price = float(candles[-1]["close"])
    clusters, oi, walls, liqs = {}, {}, {}, {}
    async with aiohttp.ClientSession(headers=ld._HEADERS) as session:
        try:
            oi = await ld.aggregated_oi(session, sym, price)
            clusters = await ld.liquidation_clusters(session, sym, price, oi.get("oi_usd"))
            walls = await ld.orderbook_liquidity(session, sym, price)
        except Exception as e:  # noqa: BLE001 – Heatmap bleibt aus Kerzen nutzbar
            logger.warning(f"heatmap venue data failed {sym}: {e}")
    try:
        liqs = await ld.recent_liquidations(sym)
    except Exception:  # noqa: BLE001
        liqs = {}
    hm = ll.heatmap(candles, clusters, price, bins=max(10, min(bins, 80)))
    return {"symbol": sym, "interval": interval, "clusters": clusters,
            "oi_usd": oi.get("oi_usd"), "oi_trend": oi.get("trend"),
            "orderbook_walls": {"bids": walls.get("bids", []),
                                "asks": walls.get("asks", [])},
            "recent_liquidations_5m": liqs, **hm}


@router.get("/live/{symbol}")
async def liquidity_live(symbol: str, seconds: int = 300):
    """Live-Liquidationen (3-Venue-WebSocket-Ringpuffer) der letzten Sekunden."""
    return {"symbol": symbol.upper(), "seconds": seconds,
            **await ld.recent_liquidations(symbol.upper(), seconds)}
