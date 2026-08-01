"""Tests für die Liquidations-Heatmap und die Liquidity-Level (reine Rechenkerne)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.liquidation_heatmap import build_heatmap, build_levels, _rel_deltas


def _candles(prices, vol=100.0, start=1_700_000_000_000, step=3_600_000):
    out = []
    for i, p in enumerate(prices):
        out.append({"timestamp": start + i * step, "open": p, "high": p * 1.002,
                    "low": p * 0.998, "close": p, "volume": vol})
    return out


def test_heatmap_empty_on_short_history():
    hm = build_heatmap(_candles([100] * 5))
    assert hm["buckets"] == []


def test_heatmap_has_buckets_and_clusters():
    prices = [100 + (i % 7) for i in range(120)]
    hm = build_heatmap(_candles(prices))
    assert hm["buckets"], "es müssen Buckets entstehen"
    assert hm["price"] == prices[-1]
    cl = hm["clusters"]
    assert all(c["price"] < hm["price"] for c in cl["below_price"])
    assert all(c["price"] > hm["price"] for c in cl["above_price"])
    assert all(0 < b["intensity"] <= 1 for b in hm["buckets"])


def test_heatmap_oi_calibration_scales_total():
    prices = [100 + (i % 5) for i in range(150)]
    raw = build_heatmap(_candles(prices))
    calib = build_heatmap(_candles(prices), oi_usd=1_000_000)
    assert raw["total_open_liq_usd"] != calib["total_open_liq_usd"]
    assert abs(calib["total_open_liq_usd"] - 1_000_000) / 1_000_000 < 0.01
    assert calib["oi_calibrated"] is True


def test_heatmap_removes_mitigated_levels():
    # Starker Abwärtstrend: Long-Liquidationen unterhalb wurden alle abgeräumt,
    # Short-Liquidationen oberhalb bleiben offen.
    down = [200 - i for i in range(120)]
    hm = build_heatmap(_candles(down))
    below = sum(b["long_usd"] for b in hm["buckets"] if b["price_mid"] < hm["price"])
    above = sum(b["short_usd"] for b in hm["buckets"] if b["price_mid"] > hm["price"])
    assert above > below


def test_rel_deltas():
    assert _rel_deltas([100, 110, 99]) == [0.1, (99 - 110) / 110]


def test_levels_detect_untested_swings():
    # Zickzack mit einem finalen Hoch, das nicht mehr getestet wurde
    prices = []
    for i in range(10):
        prices += [100, 104, 100, 96]
    prices += [100, 108, 104]
    lv = build_levels(_candles(prices))
    assert lv["price"] == prices[-1]
    types = {l["type"] for l in lv["levels"]}
    assert types, "es müssen Level erkannt werden"
    # Alle gelieferten Level sind unberührt -> Widerstände liegen über dem Preis
    for l in lv["levels"]:
        if l["side"] == "resistance":
            assert l["price"] >= lv["price"]
        else:
            assert l["price"] <= lv["price"]


def test_levels_merge_equal_highs():
    prices = []
    for _ in range(6):
        prices += [100, 110, 100, 90]
    prices += [100, 105]
    lv = build_levels(_candles(prices))
    strengths = [l for l in lv["levels"] if l["touches"] > 1]
    assert all(l["type"] in ("equal_highs", "equal_lows") for l in strengths)


def test_levels_short_history():
    assert build_levels(_candles([100, 101, 102]))["levels"] == []
