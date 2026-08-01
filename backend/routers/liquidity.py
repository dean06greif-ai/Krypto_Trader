"""Liquidations-Heatmap, Liquidity-Level und Börsen-Liquiditätskontext.

Alle Quellen sind kostenlos & ohne API-Key (OKX, Binance, Bybit).
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services import liquidation_heatmap as lh
from services import liquidity_data as ld

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/liquidity", tags=["liquidity"])

ALLOWED_INTERVALS = ("5m", "15m", "30m", "1h", "4h", "1d")


def _check_interval(interval: str) -> str:
    if interval not in ALLOWED_INTERVALS:
        raise HTTPException(status_code=400,
                            detail=f"interval muss aus {ALLOWED_INTERVALS} sein")
    return interval


@router.get("/heatmap/{symbol}")
async def liquidation_heatmap(symbol: str, interval: str = "1h",
                              bars: int = Query(240, ge=60, le=500)):
    """Geschätzte Liquidations-Heatmap (Preis-Buckets + Magnet-Cluster)."""
    _check_interval(interval)
    return await lh.heatmap(symbol, interval, bars)


@router.get("/levels/{symbol}")
async def levels(symbol: str, interval: str = "1h",
                 bars: int = Query(300, ge=80, le=500),
                 pivot_lookback: int = Query(3, ge=2, le=10)):
    """Unberührte Liquiditäts-Level (Swing-Hochs/Tiefs, Equal Highs/Lows)."""
    _check_interval(interval)
    return await lh.liquidity_levels(symbol, interval, bars, pivot_lookback)


@router.get("/context")
async def liquidity_context(symbols: Optional[str] = Query(
        None, description="Komma-Liste, z.B. BTCUSDT,ETHUSDT")):
    """Open Interest, Long/Short-Ratios, Orderbook-Walls, Live-Liquidationen."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    return await ld.get_liquidity_context(syms)


@router.get("/{symbol}")
async def liquidity_all(symbol: str, interval: str = "1h",
                        bars: int = Query(240, ge=60, le=500)):
    """Alles zu einem Symbol: Heatmap + Level + Börsen-Kontext (ein Aufruf)."""
    _check_interval(interval)
    return await lh.combined(symbol, interval, bars)
